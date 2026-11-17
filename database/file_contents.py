from datetime import datetime, timezone
from contextlib import closing
from os import sep
from pathlib import Path

from database import db
from core.citations import build_chunks


def initialize_file_contents():
    with db.get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS file_contents (
                file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                text TEXT,
                reason TEXT,
                error TEXT,
                source_fingerprint TEXT,
                analyzed_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS invalidate_file_contents
            AFTER UPDATE ON files
            WHEN NEW.scan_status = 'DELETED'
              OR OLD.size IS NOT NEW.size
              OR OLD.modified_at IS NOT NEW.modified_at
              OR OLD.extension IS NOT NEW.extension
              OR OLD.device_id IS NOT NEW.device_id
              OR OLD.inode IS NOT NEW.inode
            BEGIN
                UPDATE file_contents
                SET status = 'STALE', text = NULL, error = NULL,
                    reason = CASE WHEN NEW.scan_status = 'DELETED'
                                  THEN 'FILE_DELETED' ELSE 'FILE_CHANGED' END
                WHERE file_id = NEW.id;
            END
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                file_id INTEGER NOT NULL REFERENCES file_contents(file_id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
                start_char INTEGER NOT NULL CHECK (start_char >= 0),
                end_char INTEGER NOT NULL CHECK (end_char > start_char),
                page INTEGER CHECK (page > 0),
                source_kind TEXT,
                source_label TEXT,
                PRIMARY KEY (file_id, chunk_index)
            )
        """)
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(document_chunks)')}
        for column in ('source_kind', 'source_label'):
            if column not in columns:
                connection.execute(f'ALTER TABLE document_chunks ADD COLUMN {column} TEXT')
        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS invalidate_document_chunks
            AFTER UPDATE ON file_contents BEGIN
                DELETE FROM document_chunks WHERE file_id = NEW.file_id;
            END
        """)


def get_file_content(file_id):
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM file_contents WHERE file_id = ?", (file_id,)
        ).fetchone()
        return dict(row) if row else None


def get_content_state(file_id):
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT status, source_fingerprint FROM file_contents WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        return dict(row) if row else None


CONTENT_STATE_BATCH_SIZE = 500


def get_content_states(file_ids):
    """파일 ID 묶음의 상태·지문만 읽고 연결을 명시적으로 닫는다."""
    if not file_ids:
        return {}
    states = {}
    with closing(db.get_connection()) as connection:
        for start in range(0, len(file_ids), CONTENT_STATE_BATCH_SIZE):
            batch = file_ids[start:start + CONTENT_STATE_BATCH_SIZE]
            placeholders = ','.join('?' for _ in batch)
            rows = connection.execute(f"""
                SELECT file_id, status, source_fingerprint
                FROM file_contents WHERE file_id IN ({placeholders})
            """, batch).fetchall()
            for row in rows:
                states[row['file_id']] = {
                    'status': row['status'],
                    'source_fingerprint': row['source_fingerprint'],
                }
    return states


def save_file_content(file_id, result, source_fingerprint):
    chunks = build_chunks(result)
    with db.get_connection() as connection:
        connection.execute("""
            INSERT INTO file_contents (
                file_id, status, text, reason, error, source_fingerprint, analyzed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                status = excluded.status, text = excluded.text,
                reason = excluded.reason, error = excluded.error,
                source_fingerprint = excluded.source_fingerprint,
                analyzed_at = excluded.analyzed_at
        """, (
            file_id, result.status, result.text, result.reason, result.error,
            source_fingerprint, datetime.now(timezone.utc).isoformat(),
        ))
        connection.executemany("""
            INSERT INTO document_chunks (file_id, chunk_index, start_char, end_char, page, source_kind, source_label)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [(file_id, *chunk) for chunk in chunks])


ANALYSIS_STATUSES = ("EXTRACTED", "SKIPPED", "FAILED", "STALE", "PENDING")
MAX_PREVIEW_CHARS = 100_000


def get_content_preview(file_id, segment=0, analyzed_at=None):
    """활성 파일의 마지막 분석 결과를 반환하며 본문은 표시 길이만 조회한다."""
    if type(file_id) is not int or not 1 <= file_id < 2**63:
        raise ValueError("올바른 파일 ID를 지정하세요.")
    if type(segment) is not int or not 0 <= segment < (2**63 - 1) // MAX_PREVIEW_CHARS:
        raise ValueError("올바른 본문 구간을 지정하세요.")
    db.initialize_database()
    initialize_file_contents()
    with db.get_connection() as connection:
        row = connection.execute("""
            SELECT files.id AS file_id, files.name, files.path,
                   COALESCE(file_contents.status, 'PENDING') AS status,
                   file_contents.reason, file_contents.error, file_contents.analyzed_at,
                   CASE WHEN file_contents.status = 'EXTRACTED'
                        THEN COALESCE(substr(file_contents.text, ?, ?), '')
                        ELSE '' END AS text,
                   CASE WHEN file_contents.status = 'EXTRACTED'
                        THEN COALESCE(length(file_contents.text), 0)
                        ELSE 0 END AS total_chars
            FROM files LEFT JOIN file_contents ON files.id = file_contents.file_id
            WHERE files.id = ? AND files.scan_status != 'DELETED'
        """, (segment * MAX_PREVIEW_CHARS + 1, MAX_PREVIEW_CHARS, file_id)).fetchone()
        if row is None:
            return None
        result = dict(row)
        if analyzed_at is not None and result['analyzed_at'] != analyzed_at:
            raise ValueError("분석 결과가 변경되었습니다. 처음부터 새로 읽기를 누르세요.")
        result['segment_count'] = max(1, (result['total_chars'] + MAX_PREVIEW_CHARS - 1) // MAX_PREVIEW_CHARS)
        if result['status'] != 'EXTRACTED':
            segment = 0
        elif segment >= result['segment_count']:
            raise ValueError("존재하지 않는 본문 구간입니다. 처음부터 새로 읽기를 누르세요.")
        result['segment'] = segment
        result['truncated'] = result['total_chars'] > MAX_PREVIEW_CHARS
        return result


def list_analysis_results(root_path=None, status=None, limit=100):
    """본문을 읽지 않고 활성 파일의 마지막 분석 상태를 조회한다."""
    if status is not None and status not in ANALYSIS_STATUSES:
        raise ValueError("지원하지 않는 분석 상태입니다.")
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("조회 건수는 1~1000 사이의 정수여야 합니다.")
    db.initialize_database()
    initialize_file_contents()
    conditions = ["files.scan_status != 'DELETED'"]
    parameters = []
    if root_path is not None:
        root = str(Path(root_path).expanduser().resolve())
        prefix = root.rstrip(sep) + sep
        conditions.append("(files.path = ? OR substr(files.path, 1, ?) = ?)")
        parameters.extend((root, len(prefix), prefix))
    if status is not None:
        conditions.append("COALESCE(file_contents.status, 'PENDING') = ?")
        parameters.append(status)
    parameters.append(limit)
    with db.get_connection() as connection:
        rows = connection.execute(f"""
            SELECT files.id AS file_id, files.name, files.path,
                   COALESCE(file_contents.status, 'PENDING') AS status,
                   file_contents.reason, file_contents.error, file_contents.analyzed_at
            FROM files LEFT JOIN file_contents ON files.id = file_contents.file_id
            WHERE {' AND '.join(conditions)}
            ORDER BY CASE COALESCE(file_contents.status, 'PENDING')
                WHEN 'FAILED' THEN 0 WHEN 'SKIPPED' THEN 1
                WHEN 'STALE' THEN 2 WHEN 'PENDING' THEN 3 ELSE 4 END,
                files.path
            LIMIT ?
        """, parameters).fetchall()
        return [dict(row) for row in rows]
