"""본문 일치를 우선하고 파일명 일치를 보충하는 로컬 통합 검색."""
from database.content_search import MAX_RESULTS, search_content
from database.file_search import search_filename


def search_all(query, limit=20, root_path=None):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("검색어를 입력하세요.")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"검색 결과 수는 1~{MAX_RESULTS} 사이의 정수여야 합니다.")

    content = search_content(query, limit=limit, root_path=root_path)
    results = [dict(row, match_source="content") for row in content]
    if len(results) == limit:
        return results
    seen = {row["file_id"] for row in results}
    # 각 대상에서 limit개면 중복을 제외하고도 전체 limit개를 채우기에 충분하다.
    for row in search_filename(query, limit=limit, root_path=root_path):
        if row["file_id"] not in seen:
            results.append(dict(row, match_source="name"))
            seen.add(row["file_id"])
            if len(results) == limit:
                break
    return results
