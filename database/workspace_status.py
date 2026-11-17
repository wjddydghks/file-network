"""원본 파일을 스캔하지 않는 읽기 전용 범위 요약. DB가 없으면 생성하지 않는다."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

from core.operations import check_cancelled
from database import db


def _key(path):
    return os.path.normcase(os.path.normpath(path))


def _contains(parent, child):
    parent, child = _key(parent), _key(child)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def get_workspace_status(root=None, is_cancelled=None):
    check_cancelled(is_cancelled)
    scope = str(Path(root).expanduser().resolve()) if root else None
    available = Path(scope).is_dir() if scope else None
    result = dict(root_available=available, root=scope, files=0, extracted=0, pending=0, stale=0, failed=0, skipped=0,
                  last_saved_at=None, scan=None, folders=[])
    path = Path(db.DB_PATH).expanduser().resolve()
    if not path.is_file():
        return result
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
        connection.row_factory = sqlite3.Row
        connection.create_function('scope_key', 1, _key)
        connection.set_progress_handler(lambda: int(bool(is_cancelled and is_cancelled())), 1000)
        try:
            connection.execute('BEGIN')
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'scan_sessions' in tables:
                roots = connection.execute('''SELECT root_path, MAX(started_at) AS last_scanned_at
                    FROM scan_sessions GROUP BY root_path ORDER BY last_scanned_at DESC LIMIT 100''')
                result['folders'] = [dict(r) for r in roots]
                scans = connection.execute('SELECT * FROM scan_sessions ORDER BY started_at DESC')
                for row in scans:
                    check_cancelled(is_cancelled)
                    if scope is None or _contains(row['root_path'], scope):
                        result['scan'] = dict(row)
                        break
            if 'files' not in tables:
                return result
            where = "f.scan_status != 'DELETED'"
            params = []
            if scope is not None:
                key = _key(scope)
                where += ' AND (scope_key(f.path)=? OR instr(scope_key(f.path), ?)=1)'
                params = [key, key.rstrip(os.sep) + os.sep]
            if 'file_contents' in tables:
                join = 'LEFT JOIN file_contents fc ON fc.file_id=f.id'
                status = "COALESCE(fc.status, 'PENDING')"
                saved = 'MAX(fc.analyzed_at)'
            else:
                join, status, saved = '', "'PENDING'", 'NULL'
            row = connection.execute(f'''SELECT COUNT(*) AS files,
                SUM({status}='EXTRACTED') AS extracted, SUM({status}='PENDING') AS pending,
                SUM({status}='STALE') AS stale, SUM({status}='FAILED') AS failed,
                SUM({status}='SKIPPED') AS skipped, {saved} AS last_saved_at
                FROM files f {join} WHERE {where}''', params).fetchone()
            result.update({k: (v if k == 'last_saved_at' else v or 0) for k, v in dict(row).items()})
            check_cancelled(is_cancelled)
        except sqlite3.OperationalError:
            check_cancelled(is_cancelled)
            raise
        finally:
            connection.set_progress_handler(None, 0)
    return result
