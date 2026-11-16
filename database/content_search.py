from os import sep
from pathlib import Path
import sqlite3

from database import db, file_contents


MAX_RESULTS = 1000


def initialize_content_search():
    db.initialize_database()
    file_contents.initialize_file_contents()
    with db.get_connection() as connection:
        # 인덱스·트리거 생성과 최초 색인을 함께 완료하거나 되돌린다.
        connection.execute("BEGIN")
        existing = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'file_content_search'"
        ).fetchone()
        try:
            connection.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS file_content_search USING fts5(
                    text, content='file_contents', content_rowid='file_id',
                    tokenize='unicode61'
                )
            """)
        except sqlite3.OperationalError as error:
            if "no such module: fts5" in str(error):
                raise RuntimeError("현재 SQLite는 FTS5를 지원하지 않습니다.") from error
            raise

        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS index_file_content_insert
            AFTER INSERT ON file_contents BEGIN
                INSERT INTO file_content_search(rowid, text)
                VALUES (NEW.file_id, NEW.text);
            END
        """)
        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS index_file_content_delete
            AFTER DELETE ON file_contents BEGIN
                INSERT INTO file_content_search(file_content_search, rowid, text)
                VALUES ('delete', OLD.file_id, OLD.text);
            END
        """)
        connection.execute("""
            CREATE TRIGGER IF NOT EXISTS index_file_content_update
            AFTER UPDATE ON file_contents BEGIN
                INSERT INTO file_content_search(file_content_search, rowid, text)
                VALUES ('delete', OLD.file_id, OLD.text);
                INSERT INTO file_content_search(rowid, text)
                VALUES (NEW.file_id, NEW.text);
            END
        """)
        if not existing:
            connection.execute("""
                INSERT INTO file_content_search(file_content_search) VALUES ('rebuild')
            """)


def search_content(query, limit=20, root_path=None):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("검색어를 입력하세요.")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"검색 결과 수는 1~{MAX_RESULTS} 사이의 정수여야 합니다.")

    # 사용자 입력을 FTS 문법이 아닌 리터럴 단어/구로 검색한다.
    match_query = " AND ".join('"' + term.replace('"', '""') + '"' for term in query.split())
    initialize_content_search()
    scope_sql = ""
    parameters = [match_query]
    if root_path is not None:
        root = str(Path(root_path).expanduser().resolve())
        prefix = root.rstrip(sep) + sep
        scope_sql = "AND (files.path = ? OR substr(files.path, 1, ?) = ?)"
        parameters.extend((root, len(prefix), prefix))
    parameters.append(limit)

    with db.get_connection() as connection:
        rows = connection.execute(f"""
            SELECT files.id AS file_id, files.path, files.name,
                   snippet(file_content_search, 0, '[', ']', ' … ', 24) AS snippet,
                   bm25(file_content_search) AS score
            FROM file_content_search
            JOIN file_contents ON file_contents.file_id = file_content_search.rowid
            JOIN files ON files.id = file_contents.file_id
            WHERE file_content_search MATCH ?
              AND file_contents.status = 'EXTRACTED'
              AND files.scan_status != 'DELETED'
              {scope_sql}
            ORDER BY score, files.path
            LIMIT ?
        """, parameters).fetchall()
        return [dict(row) for row in rows]
