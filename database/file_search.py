"""본문 추출 여부와 무관하게 저장된 파일명을 검색한다."""
from os import sep
from pathlib import Path
from core.filenames import fold_filename
from core.search_queries import parse_query_terms

from database import db
from database.content_search import MAX_RESULTS


def search_filename(query, limit=20, root_path=None):
    query_terms = parse_query_terms(query)
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"검색 결과 수는 1~{MAX_RESULTS} 사이의 정수여야 합니다.")

    terms = [fold_filename(term) for term in query_terms]
    conditions = ["instr(files.name_fold, ?) > 0" for _ in terms]
    parameters = list(terms)
    if root_path is not None:
        root = str(Path(root_path).expanduser().resolve())
        prefix = root.rstrip(sep) + sep
        conditions.append("(files.path = ? OR substr(files.path, 1, ?) = ?)")
        parameters.extend((root, len(prefix), prefix))
    parameters.append(limit)

    db.initialize_database()
    with db.get_connection() as connection:
        rows = connection.execute(f"""
            SELECT files.id AS file_id, files.path, files.name,
                   files.name AS snippet, 0.0 AS score
            FROM files
            WHERE files.scan_status != 'DELETED'
              AND {' AND '.join(conditions)}
            ORDER BY files.path
            LIMIT ?
        """, parameters).fetchall()
        return [dict(row) for row in rows]
