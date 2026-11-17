"""SQLite 쿼리를 만들기 전에 검증하는 공통 검색 입력 규칙."""
MAX_QUERY_CHARS = 4096
MAX_QUERY_TERMS = 100


def parse_query_terms(query):
    if not isinstance(query, str) or not query.strip():
        raise ValueError('검색어를 입력하세요.')
    query = query.strip()
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f'검색어는 앞뒤 공백을 제외하고 {MAX_QUERY_CHARS}자 이하여야 합니다.')
    if '\x00' in query:
        raise ValueError('검색어에 NUL 문자를 사용할 수 없습니다.')
    try:
        query.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('검색어에 올바르지 않은 Unicode 문자가 있습니다.') from None
    terms = query.split()
    if len(terms) > MAX_QUERY_TERMS:
        raise ValueError(f'검색어는 공백으로 구분한 {MAX_QUERY_TERMS}개 이하여야 합니다.')
    return terms
