import argparse
import json
import sqlite3
from app.console import configure_console

from app.extract_text import positive_integer
from database.content_search import MAX_RESULTS, search_content
from database.file_search import search_filename
from database.unified_search import search_all


def main():
    configure_console()
    parser = argparse.ArgumentParser(description="File Network - Content Search")
    parser.add_argument("query", help="검색어")
    parser.add_argument("--target", choices=("content", "name", "all"), default="content", help="검색 대상: 본문(content, 기본값), 파일명(name), 통합(all)")
    parser.add_argument("--limit", type=positive_integer, default=20, help=f"최대 결과 수 (1~{MAX_RESULTS})")
    parser.add_argument("--root", help="검색할 디렉터리 범위 (생략 시 전체 DB)")
    args = parser.parse_args()
    try:
        search = {"content": search_content, "name": search_filename, "all": search_all}[args.target]
        results = search(args.query, limit=args.limit, root_path=args.root)
    except ValueError as error:
        parser.error(str(error))
    except (RuntimeError, sqlite3.DatabaseError) as error:
        parser.exit(1, f"검색 실패: {error}\n")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
