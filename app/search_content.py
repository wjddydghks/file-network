import argparse
import json
import sqlite3

from app.extract_text import positive_integer
from database.content_search import MAX_RESULTS, search_content


def main():
    parser = argparse.ArgumentParser(description="File Network - Content Search")
    parser.add_argument("query", help="본문 검색어")
    parser.add_argument("--limit", type=positive_integer, default=20, help=f"최대 결과 수 (1~{MAX_RESULTS})")
    parser.add_argument("--root", help="검색할 디렉터리 범위 (생략 시 전체 DB)")
    args = parser.parse_args()
    try:
        results = search_content(args.query, limit=args.limit, root_path=args.root)
    except ValueError as error:
        parser.error(str(error))
    except (RuntimeError, sqlite3.DatabaseError) as error:
        parser.exit(1, f"검색 실패: {error}\n")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
