import argparse

from database.db import (
    initialize_database,
    save_files,
    get_file_count
)

from core.scanner.scanner import scan_directory


def main():
    parser = argparse.ArgumentParser(
        description="File Network - File Scanner"
    )

    parser.add_argument(
        "path",
        help="스캔할 디렉터리 경로"
    )

    args = parser.parse_args()

    print("=" * 50)
    print("       FILE NETWORK SCANNER")
    print("=" * 50)

    initialize_database()

    total_saved = 0

    for batch in scan_directory(args.path):
        saved = save_files(batch)
        total_saved += saved

        print(
            f"저장 진행: {total_saved:,}개",
            end="\r"
        )

    print("\n\n스캔 완료!")
    print(f"이번 실행에서 저장 처리한 파일: {total_saved:,}개")
    print(f"DB에 등록된 전체 파일: {get_file_count():,}개")


if __name__ == "__main__":
    main()
