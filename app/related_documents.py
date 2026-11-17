import argparse
import json
import sqlite3

from app.console import configure_console
from app.extract_text import positive_integer
from database.related_documents import MAX_RELATED_RESULTS, find_related_documents


def main():
    configure_console()
    parser = argparse.ArgumentParser(description='File Network - Related Documents')
    parser.add_argument('file_id', type=positive_integer, help='기준 문서의 file_id')
    parser.add_argument('--limit', type=positive_integer, default=20, help=f'최대 결과 수 (1~{MAX_RELATED_RESULTS})')
    parser.add_argument('--root', help='후보 문서의 디렉터리 범위 (생략 시 전체 DB)')
    args = parser.parse_args()
    try:
        result = find_related_documents(args.file_id, limit=args.limit, root_path=args.root)
    except ValueError as error:
        parser.error(str(error))
    except (RuntimeError, sqlite3.DatabaseError) as error:
        parser.exit(1, f'관련 문서 조회 실패: {error}\n')
    except KeyboardInterrupt:
        parser.exit(130, '조회가 취소되었습니다.\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
