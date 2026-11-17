"""지식 그래프용 자동 본문 준비와 로컬 진행/결과 저장."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import time
import uuid

from core.ai_questions import sensitive_categories, validate_text
from core.knowledge_graph import Document, MAX_FILES, canonical
from core.operations import check_cancelled
from core.scanner.exclusions import ExcludedDirectories, validate_excluded_dirs
from database import db
from database.workspace_status import _key

# 파일마다 처음/가운데/끝을 합해 최대 900자만 자동 준비한다.
SAMPLE_SQL = """CASE WHEN length(fc.text)<=900 THEN COALESCE(fc.text,'') ELSE
    substr(fc.text,1,294)||char(10)||'[발췌 경계]'||char(10)||
    substr(fc.text,max(295,CAST(length(fc.text)/2 AS INTEGER)-146),294)||
    char(10)||'[발췌 경계]'||char(10)||substr(fc.text,-294) END"""
SAMPLE_SQL = SAMPLE_SQL.replace('fc.text', "replace(COALESCE(fc.text,''),char(12),'[발췌 경계]')")


def read_documents(root, is_cancelled=None, progress=None, excluded_dirs=()):
    check_cancelled(is_cancelled)
    root = str(Path(root).expanduser().resolve())
    exclusions = ExcludedDirectories(root,validate_excluded_dirs(excluded_dirs),files=db.database_artifact_paths(root))
    path = Path(db.DB_PATH).expanduser().resolve()
    if not path.is_file():
        return ()
    result = []
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.create_function('scope_key', 1, _key)
        conn.set_progress_handler(lambda: int(bool(is_cancelled and is_cancelled())), 1000)
        try:
            conn.execute('BEGIN')
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'files' not in tables:
                return ()
            has_content = 'file_contents' in tables
            fields = f"COALESCE(fc.status,'PENDING') AS status, fc.analyzed_at, {SAMPLE_SQL} AS sample" if has_content else "'PENDING' AS status, NULL AS analyzed_at, '' AS sample"
            join = 'LEFT JOIN file_contents fc ON fc.file_id=f.id' if has_content else ''
            rows = conn.execute(f'''SELECT f.id,f.path,f.name,{fields} FROM files f {join}
                WHERE f.scan_status!='DELETED' AND instr(scope_key(f.path),?)=1 ORDER BY f.path LIMIT ?''',
                (_key(root).rstrip(os.sep) + os.sep, MAX_FILES + 1))
            for index, row in enumerate(rows):
                check_cancelled(is_cancelled)
                if index >= MAX_FILES:
                    raise ValueError(f'영역당 최대 {MAX_FILES:,}개 저장 파일을 지원합니다. 하위 폴더를 선택하세요.')
                sample = (row['sample'] or '') if row['status'] == 'EXTRACTED' else ''
                blocked = ''
                if sample:
                    try:
                        validate_text(sample, 900, '분석용 본문')
                        blocked = ', '.join(sensitive_categories(sample))
                    except ValueError:
                        blocked = '본문 문자 형식 확인 필요'
                if exclusions.contains_file(row['path']):
                    blocked = '분석 설정의 제외 폴더 또는 앱 내부 파일'
                result.append(Document(f'F{index+1}', row['id'], row['path'], row['name'], row['status'],
                                       row['analyzed_at'], sample, blocked))
                if progress and index % 128 == 0:
                    progress({'phase': 'prepare', 'files': index + 1})
        except sqlite3.OperationalError:
            check_cancelled(is_cancelled)
            raise
        finally:
            conn.set_progress_handler(None, 0)
    return tuple(result)


def read_cache(root, interest, model):
    path = Path(db.DB_PATH).expanduser().resolve()
    if not path.is_file():
        return ''
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_graphs'").fetchone():
            return ''
        row = conn.execute('SELECT state_json FROM knowledge_graphs WHERE root=? AND interest=? AND model=?',
                           (root, interest, model)).fetchone()
        return row[0] if row else ''


def latest_config(root):
    path = Path(db.DB_PATH).expanduser().resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_graphs'").fetchone():
            return None
        row = conn.execute('SELECT interest,model FROM knowledge_graphs WHERE root=? ORDER BY updated_at DESC LIMIT 1', (root,)).fetchone()
        return tuple(row) if row else None


def acquire(plan):
    token = uuid.uuid4().hex
    with db.get_connection() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS knowledge_graphs (
            root TEXT NOT NULL, interest TEXT NOT NULL, model TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '', lock_token TEXT, lease_until REAL, updated_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(root,interest,model))''')
        conn.execute('BEGIN IMMEDIATE')
        key = (plan.root, plan.interest, plan.model)
        conn.execute('INSERT OR IGNORE INTO knowledge_graphs(root,interest,model) VALUES(?,?,?)', key)
        row = conn.execute('SELECT state_json,lock_token,lease_until FROM knowledge_graphs WHERE root=? AND interest=? AND model=?', key).fetchone()
        if row['lock_token'] and (row['lease_until'] or 0) > time.time():
            raise ValueError('이 영역의 같은 관심사 분석이 이미 실행 중입니다. 강제 종료 후에는 최대 3분 뒤 다시 준비하세요.')
        if row['state_json'] != plan.cached_json:
            raise ValueError('다른 실행에서 저장 결과가 변경되었습니다. 영역 분석을 다시 준비하세요.')
        conn.execute('UPDATE knowledge_graphs SET lock_token=?,lease_until=? WHERE root=? AND interest=? AND model=?',
                     (token, time.time() + 180, *key))
    return token


def save(plan, token, state):
    with db.get_connection() as conn:
        updated = conn.execute('''UPDATE knowledge_graphs SET state_json=?,lease_until=?,updated_at=?
            WHERE root=? AND interest=? AND model=? AND lock_token=?''',
            (canonical(state), time.time() + 180, time.time(), plan.root, plan.interest, plan.model, token)).rowcount
        if updated != 1:
            raise ValueError('분석 실행 잠금이 변경되었습니다. 다시 준비하세요.')


def release(plan, token):
    with db.get_connection() as conn:
        conn.execute('UPDATE knowledge_graphs SET lock_token=NULL,lease_until=NULL WHERE root=? AND interest=? AND model=? AND lock_token=?',
                     (plan.root, plan.interest, plan.model, token))


def verify_documents(documents, is_cancelled=None):
    check_cancelled(is_cancelled)
    if not documents:
        return
    with db.get_connection() as conn:
        rows = conn.execute(f'''SELECT f.id,f.path,fc.analyzed_at, {SAMPLE_SQL} AS sample
            FROM files f JOIN file_contents fc ON fc.file_id=f.id
            WHERE f.id IN ({','.join('?' for _ in documents)}) AND f.scan_status!='DELETED' AND fc.status='EXTRACTED' ''',
            [d.file_id for d in documents]).fetchall()
    current = {r['id']: r for r in rows}
    for doc in documents:
        check_cancelled(is_cancelled)
        row = current.get(doc.file_id)
        if not row or row['path'] != doc.path or row['analyzed_at'] != doc.analyzed_at or row['sample'] != doc.sample:
            raise ValueError('승인 후 파일의 저장 내용이나 경로가 변경되었습니다. 영역 분석을 다시 준비하세요.')


def verify_quotes(documents, pairs):
    sources = {d.alias: d for d in documents}
    with db.get_connection() as conn:
        for alias, quote in pairs:
            doc = sources[alias]
            row = conn.execute("SELECT instr(text,?) AS found,analyzed_at FROM file_contents WHERE file_id=? AND status='EXTRACTED'", (quote,doc.file_id)).fetchone()
            if not row or not row['found'] or row['analyzed_at'] != doc.analyzed_at:
                raise ValueError('원본 저장 본문에서 분류 근거를 확인하지 못했습니다. 다시 준비하세요.')
