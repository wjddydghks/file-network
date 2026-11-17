import argparse
from app.console import configure_console

from database.db import (
    initialize_database,
    save_files,
    get_file_count,
    get_active_file_count
)
from database.db import database_artifact_paths

from core.scanner.scanner import scan_directory
from core.scanner.exclusions import validate_excluded_dirs


def main():
    configure_console()
    parser = argparse.ArgumentParser(
        description="File Network - File Scanner"
    )

    parser.add_argument(
        "path",
        help="스캔할 디렉터리 경로"
    )

    parser.add_argument('--exclude-dir', action='append', default=[], help='모든 깊이에서 제외할 폴더명 (반복 가능)')
    args = parser.parse_args()
    try:
        excluded_dirs = validate_excluded_dirs(args.exclude_dir)
    except ValueError as error:
        parser.error(str(error))

    print("=" * 50)
    print("       FILE NETWORK SCANNER")
    print("=" * 50)

    initialize_database()

    total_saved = 0

    options = {'excluded_dirs': excluded_dirs}
    internal_files = database_artifact_paths(args.path)
    if internal_files:
        options['excluded_files'] = internal_files
    for batch in scan_directory(args.path, **options):
        saved = save_files(batch)
        total_saved += saved

        print(
            f"저장 진행: {total_saved:,}개",
            end="\r"
        )

    print("\n\n스캔 완료!")
    print(f"이번 실행에서 저장 처리한 파일: {total_saved:,}개")
    print(f"DB 활성 파일: {get_active_file_count():,}개")
    print(f"DB 전체 레코드: {get_file_count():,}개")


if __name__ == "__main__":
    main()
