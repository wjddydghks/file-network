"""저장된 본문의 공유 키워드를 근거로 다른 문서를 탐색한다."""
from math import log1p
from os import sep
from pathlib import Path
import sqlite3

from core.operations import check_cancelled
from database import db
from database.content_search import initialize_content_search, literal_match_query


MAX_RELATED_RESULTS = 100
MAX_KEYWORDS = 8
MAX_TERM_CANDIDATES = 256
STOP_WORDS = frozenset('''the and for with from this that these those are was were
    have has had not but you your our their into its can will would should could
    return import def class self else elif while true false none
    그리고 하지만 대한 위한 있는 합니다 하는 에서 으로 또는'''.split())


def find_related_documents(file_id, limit=20, root_path=None, is_cancelled=None):
    if type(file_id) is not int or not 1 <= file_id < 2**63:
        raise ValueError('올바른 파일 ID를 지정하세요.')
    if type(limit) is not int or not 1 <= limit <= MAX_RELATED_RESULTS:
        raise ValueError(f'관련 문서 수는 1~{MAX_RELATED_RESULTS} 사이여야 합니다.')
    scope_sql = ''
    scope_parameters = []
    if root_path is not None:
        root = str(Path(root_path).expanduser().resolve())
        prefix = root.rstrip(sep) + sep
        scope_sql = 'AND (f.path=? OR substr(f.path,1,?)=?)'
        scope_parameters = [root, len(prefix), prefix]
    check_cancelled(is_cancelled)
    initialize_content_search()
    with db.get_connection() as connection:
        connection.execute('PRAGMA temp_store = MEMORY')
        connection.execute('BEGIN')
        if is_cancelled is not None:
            connection.set_progress_handler(lambda: int(bool(is_cancelled())), 1000)
        try:
            source = connection.execute("""
                SELECT f.id AS file_id, f.name, f.path, fc.analyzed_at
                FROM files f JOIN file_contents fc ON fc.file_id=f.id
                WHERE f.id=? AND f.scan_status!='DELETED' AND fc.status='EXTRACTED'
            """, (file_id,)).fetchone()
            if source is None:
                raise ValueError('본문 분석이 완료된 활성 파일을 선택하세요.')
            response = {'source': dict(source), 'keywords': [], 'results': [], 'relation_type': 'shared_keywords'}
            connection.execute("CREATE VIRTUAL TABLE temp.related_source USING fts5(text, content='', tokenize='unicode61')")
            connection.execute('INSERT INTO related_source(rowid,text) SELECT 1,text FROM file_contents WHERE file_id=?', (file_id,))
            connection.execute('CREATE VIRTUAL TABLE temp.source_vocab USING fts5vocab(temp, related_source, row)')
            connection.execute('CREATE VIRTUAL TABLE temp.corpus_vocab USING fts5vocab(main, file_content_search, row)')
            terms = connection.execute('''
                SELECT term,cnt FROM source_vocab WHERE length(term) BETWEEN 2 AND 64
                ORDER BY cnt DESC,term LIMIT ?
            ''', (MAX_TERM_CANDIDATES,)).fetchall()
            terms = [row for row in terms if row['term'] not in STOP_WORDS and not row['term'].isdecimal()]
            if not terms:
                return response
            placeholders = ','.join('?' for _ in terms)
            frequencies = {row['term']: row['doc'] for row in connection.execute(
                f'SELECT term,doc FROM corpus_vocab WHERE term IN ({placeholders})',
                [row['term'] for row in terms],
            )}
            total = connection.execute("""
                SELECT COUNT(*) FROM file_contents fc JOIN files f ON f.id=fc.file_id
                WHERE fc.status='EXTRACTED' AND f.scan_status!='DELETED'
            """).fetchone()[0]
            keywords = [
                {'term': row['term'], 'frequency': row['cnt'], 'document_frequency': frequencies[row['term']],
                 'weight': row['cnt'] * log1p(total / frequencies[row['term']])}
                for row in terms if frequencies.get(row['term'], 0) >= 2
            ]
            keywords.sort(key=lambda term: (-term['weight'], term['term']))
            keywords = keywords[:MAX_KEYWORDS]
            response['keywords'] = keywords
            if not keywords:
                return response
            query = ' OR '.join(literal_match_query(term['term']) for term in keywords)
            rows = connection.execute(f"""
                SELECT f.id AS file_id,f.name,f.path,fc.analyzed_at,
                       snippet(file_content_search,0,'[',']',' … ',24) AS snippet,
                       bm25(file_content_search) AS score
                FROM file_content_search JOIN file_contents fc ON fc.file_id=file_content_search.rowid
                JOIN files f ON f.id=fc.file_id
                WHERE file_content_search MATCH ? AND f.id!=?
                    AND fc.status='EXTRACTED' AND f.scan_status!='DELETED' {scope_sql}
                ORDER BY score,f.path LIMIT ?
            """, [query, file_id, *scope_parameters, limit]).fetchall()
            results = [dict(row, shared_keywords=[]) for row in rows]
            if results:
                ids = [row['file_id'] for row in results]
                placeholders = ','.join('?' for _ in ids)
                for keyword in keywords:
                    matches = {row[0] for row in connection.execute(f'''
                        SELECT rowid FROM file_content_search
                        WHERE file_content_search MATCH ? AND rowid IN ({placeholders})
                    ''', [literal_match_query(keyword['term']), *ids])}
                    for row in results:
                        if row['file_id'] in matches:
                            row['shared_keywords'].append(keyword['term'])
            response['results'] = results
            check_cancelled(is_cancelled)
            return response
        except sqlite3.OperationalError:
            check_cancelled(is_cancelled)
            raise
        finally:
            connection.set_progress_handler(None, 0)
