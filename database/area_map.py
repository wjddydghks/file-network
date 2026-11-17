"""로컬 저장 영역의 구조/키워드 스냅샷. 본문은 메모리에 읽지 않는다."""
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from core.filenames import fold_filename
from core.operations import check_cancelled
from core.search_queries import parse_query_terms
from database import db
from database.content_search import initialize_content_search, literal_match_query
from database.workspace_status import _contains, _key

GROUP_PAGE_SIZE = 40
FILE_PAGE_SIZE = 80


def get_area_map(root, *, focus=None, interest='', selected_type=None,
                 group_offset=0, file_offset=0, is_cancelled=None):
    if not isinstance(root, (str, Path)) or not str(root).strip():
        raise ValueError('지도로 볼 폴더를 선택하세요.')
    root = str(Path(root).expanduser().resolve())
    focus = str(Path(focus).expanduser().resolve()) if focus is not None else root
    if not _contains(root, focus):
        raise ValueError('선택한 영역 밖으로 이동할 수 없습니다.')
    if not isinstance(interest, str):
        raise ValueError('관심 키워드를 입력하세요.')
    interest = interest.strip()
    terms = [fold_filename(t) for t in parse_query_terms(interest)] if interest else []
    if selected_type is not None and (not isinstance(selected_type, str) or len(selected_type) > 260):
        raise ValueError('올바른 파일 형식을 선택하세요.')
    if any(type(v) is not int or not 0 <= v < 2**63 for v in (group_offset, file_offset)):
        raise ValueError('올바른 페이지 위치를 지정하세요.')
    result = dict(root=root, focus=focus, interest=interest, selected_type=selected_type,
                  total_files=0, matched_files=0, selected_files=0, total_bytes=0,
                  states={}, groups=[], group_count=0, group_offset=group_offset,
                  files=[], file_offset=file_offset, created_at=datetime.now(timezone.utc).isoformat())
    check_cancelled(is_cancelled)
    path = Path(db.DB_PATH).expanduser().resolve()
    if not path.is_file():
        return result
    # 관심 키워드는 일반 본문 검색과 동일하게 최초 영구 FTS를 로컬 초기화한다.
    if interest:
        initialize_content_search()
    groups = {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as conn:
        conn.row_factory = sqlite3.Row
        conn.create_function('scope_key', 1, _key)
        conn.create_function('name_matches', 1, lambda name: int(all(t in fold_filename(name) for t in terms)))
        conn.set_progress_handler(lambda: int(bool(is_cancelled and is_cancelled())), 1000)
        try:
            conn.execute('BEGIN')
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'files' not in tables:
                return result
            has_content = 'file_contents' in tables
            join = 'LEFT JOIN file_contents fc ON fc.file_id=f.id' if has_content else ''
            status = "COALESCE(fc.status, 'PENDING')" if has_content else "'PENDING'"
            revision = 'fc.analyzed_at' if has_content else 'NULL'
            params = []
            cte, hit_join, body_hit = '', '', '0'
            if interest:
                cte = 'WITH hits AS MATERIALIZED (SELECT rowid AS id FROM file_content_search WHERE file_content_search MATCH ?)'
                params.append(literal_match_query(interest))
                hit_join = 'LEFT JOIN hits ON hits.id=f.id'
                body_hit = f"(hits.id IS NOT NULL AND {status}='EXTRACTED')"
            prefix = _key(focus).rstrip(os.sep) + os.sep
            params.append(prefix)
            rows = conn.execute(f'''{cte} SELECT f.id AS file_id, f.name, f.path, f.extension, f.size,
                {status} AS status, {revision} AS analyzed_at,
                {body_hit} AS body_hit, name_matches(f.name) AS name_hit
                FROM files f {join} {hit_join}
                WHERE f.scan_status!='DELETED' AND instr(scope_key(f.path),?)=1 ORDER BY f.path''', params)
            for index, raw in enumerate(rows):
                if index % 128 == 0:
                    check_cancelled(is_cancelled)
                row = dict(raw)
                relative = Path(row['path']).relative_to(focus)
                direct = len(relative.parts) == 1
                extension = (row['extension'] or '').lower()
                kind = 'type' if direct else 'folder'
                key = extension if direct else str(Path(focus) / relative.parts[0])
                group = groups.setdefault((kind, key), dict(kind=kind, key=key,
                    label=(extension or '확장자 없음') if direct else relative.parts[0],
                    count=0, matched=0, extracted=0, bytes=0))
                size = max(0, row['size'] or 0)
                result['total_files'] += 1
                result['total_bytes'] += size
                result['states'][row['status']] = result['states'].get(row['status'], 0) + 1
                group['count'] += 1
                group['bytes'] += size
                group['extracted'] += row['status'] == 'EXTRACTED'
                matches = not interest or row['body_hit'] or row['name_hit']
                if not matches:
                    continue
                group['matched'] += 1
                result['matched_files'] += 1
                if selected_type is not None and (not direct or extension != selected_type):
                    continue
                result['selected_files'] += 1
                if file_offset < result['selected_files'] <= file_offset + FILE_PAGE_SIZE:
                    row['match_source'] = ('content' if row['body_hit'] else 'name') if interest else None
                    del row['body_hit'], row['name_hit']
                    result['files'].append(row)
            check_cancelled(is_cancelled)
        except sqlite3.OperationalError:
            check_cancelled(is_cancelled)
            raise
        finally:
            conn.set_progress_handler(None, 0)
    ordered = sorted(groups.values(), key=lambda g: (-g['count'], g['kind'], g['label'], g['key']))
    result['group_count'] = len(ordered)
    result['groups'] = ordered[group_offset:group_offset + GROUP_PAGE_SIZE]
    return result
