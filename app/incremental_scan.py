
import argparse
from pathlib import Path

from database import db
from database.scan_sessions import (
    initialize_scan_sessions,
    start_scan_session,
    finish_scan_session
)
from core.scanner.scanner import scan_directory


def get_existing_files(files):
    """현재 배치에 포함된 경로의 기존 DB 정보를 조회한다."""

    if not files:
        return {}

    paths = [file["path"] for file in files]
    placeholders = ",".join("?" for _ in paths)

    connection = db.get_connection()

    try:
        rows = connection.execute(
            f"""
            SELECT path, name, extension, size,
                   modified_at, file_type
            FROM files
            WHERE path IN ({placeholders})
            """,
            paths
        ).fetchall()

        return {
            row["path"]: dict(row)
            for row in rows
        }

    finally:
        connection.close()


def is_file_changed(new_file, old_file):
    """파일의 메타데이터가 기존 정보와 달라졌는지 확인한다."""

    if old_file is None:
        return True

    compared_fields = (
        "name",
        "extension",
        "size",
        "modified_at",
        "file_type"
    )

    return any(
        new_file[field] != old_file[field]
        for field in compared_fields
    )


def run_incremental_scan(root_path):
    """전체 파일을 탐색하되 신규 및 변경 파일만 DB에 저장한다."""

    root = Path(root_path).expanduser().resolve()

    if not root.exists():
        raise FileNotFoundError(f"경로가 존재하지 않습니다: {root}")

    if not root.is_dir():
        raise NotADirectoryError(f"디렉터리가 아닙니다: {root}")

    db.initialize_database()
    initialize_scan_sessions()

    session_id = start_scan_session(str(root))

    discovered_count = 0
    new_count = 0
    updated_count = 0
    unchanged_count = 0

    try:
        for batch in scan_directory(str(root), batch_size=500):

            discovered_count += len(batch)

            existing_files = get_existing_files(batch)

            changed_files = []

            for file in batch:
                old_file = existing_files.get(file["path"])

                if old_file is None:
                    new_count += 1
                    changed_files.append(file)

                elif is_file_changed(file, old_file):
                    updated_count += 1
                    changed_files.append(file)

                else:
                    unchanged_count += 1

            if changed_files:
                db.save_files(changed_files)

            print(
                f"진행: {discovered_count:,}개 | "
                f"신규: {new_count:,}개 | "
                f"변경: {updated_count:,}개 | "
                f"변경 없음: {unchanged_count:,}개"
            )

        finish_scan_session(
            session_id,
            "COMPLETED",
            discovered_count,
            new_count,
            updated_count,
            unchanged_count
        )

    except BaseException:
        finish_scan_session(
            session_id,
            "FAILED",
            discovered_count,
            new_count,
            updated_count,
            unchanged_count
        )
        raise

    print("\n========== 증분 스캔 완료 ==========")
    print(f"발견한 파일: {discovered_count:,}개")
    print(f"신규 파일: {new_count:,}개")
    print(f"변경 파일: {updated_count:,}개")
    print(f"변경 없는 파일: {unchanged_count:,}개")
    print(f"DB 전체 파일: {db.get_file_count():,}개")
    print(f"스캔 세션 ID: {session_id}")


def main():
    parser = argparse.ArgumentParser(
        description="File Network Incremental Scanner"
    )

    parser.add_argument(
        "path",
        help="스캔할 디렉터리 경로"
    )

    args = parser.parse_args()

    run_incremental_scan(args.path)


if __name__ == "__main__":
    main()

