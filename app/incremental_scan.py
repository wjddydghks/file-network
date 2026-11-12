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
    if not files:
        return {}

    paths = [file["path"] for file in files]
    placeholders = ",".join("?" for _ in paths)

    connection = db.get_connection()

    try:
        rows = connection.execute(
            f"""
            SELECT path, name, extension, size,
                   modified_at, file_type, device_id, inode
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


def get_identity(file):
    device_id = file.get("device_id")
    inode = file.get("inode")

    if device_id is None or inode is None:
        return None

    if inode == 0:
        return None

    return device_id, inode


def run_incremental_scan(root_path):
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

    scan_stats = {}

    # 이동 판정 전까지 신규 파일을 보관한다.
    new_files = []
    seen_paths = set()

    try:
        for batch in scan_directory(
            str(root),
            batch_size=500,
            scan_stats=scan_stats
        ):
            discovered_count += len(batch)

            existing_files = get_existing_files(batch)
            changed_files = []

            for file in batch:
                seen_paths.add(file["path"])

                old_file = existing_files.get(file["path"])

                if old_file is None:
                    new_count += 1
                    new_files.append(file)

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

        error_count = scan_stats["error_count"]

        # 기존 DB에서 이번 스캔에 발견되지 않은 파일을 조회한다.
        old_files = db.get_active_files_under(str(root))

        missing_files = [
            file for file in old_files
            if file["path"] not in seen_paths
        ]

        # 이동 후보를 식별자 기준으로 그룹화한다.
        old_identity_groups = {}

        for old_file in missing_files:
            identity = get_identity(old_file)

            if identity is not None:
                old_identity_groups.setdefault(
                    identity, []
                ).append(old_file)

        new_identity_groups = {}

        for new_file in new_files:
            identity = get_identity(new_file)

            if identity is not None:
                new_identity_groups.setdefault(
                    identity, []
                ).append(new_file)

        moved_old_paths = set()
        moved_new_paths = set()

        # 기존 파일 1개와 신규 파일 1개가
        # 동일한 파일 식별자를 가진 경우에만 이동으로 판정한다.
        for identity, old_group in old_identity_groups.items():
            new_group = new_identity_groups.get(identity, [])

            if len(old_group) != 1 or len(new_group) != 1:
                continue

            old_file = old_group[0]
            new_file = new_group[0]

            if db.update_moved_file(
                old_file["path"],
                new_file
            ):
                moved_old_paths.add(old_file["path"])
                moved_new_paths.add(new_file["path"])

                new_count -= 1

                print(
                    f"이동 감지: {old_file['path']} "
                    f"-> {new_file['path']}"
                )

        # 이동으로 처리되지 않은 신규 파일을 저장한다.
        remaining_new_files = [
            file for file in new_files
            if file["path"] not in moved_new_paths
        ]

        if remaining_new_files:
            db.save_files(remaining_new_files)

        # 접근 오류가 없다면 삭제 파일을 판정한다.
        deleted_count = 0

        if error_count == 0:
            deleted_paths = [
                file["path"]
                for file in missing_files
                if file["path"] not in moved_old_paths
            ]

            deleted_count = db.mark_files_deleted(
                deleted_paths
            )

        scan_complete = error_count == 0

        status = (
            "COMPLETED"
            if scan_complete
            else "COMPLETED_WITH_ERRORS"
        )

        finish_scan_session(
            session_id,
            status,
            discovered_count,
            new_count,
            updated_count,
            unchanged_count,
            error_count,
            scan_complete
        )

    except Exception:
        finish_scan_session(
            session_id,
            "FAILED",
            discovered_count,
            new_count,
            updated_count,
            unchanged_count,
            scan_stats.get("error_count", 0),
            False
        )
        raise

    print("\n========== 증분 스캔 완료 ==========")
    print(f"발견한 파일: {discovered_count:,}개")
    print(f"신규 파일: {new_count:,}개")
    print(f"변경 파일: {updated_count:,}개")
    print(f"변경 없는 파일: {unchanged_count:,}개")
    print(f"접근 오류: {scan_stats['error_count']:,}건")
    print(f"삭제 파일: {deleted_count:,}개")
    print(f"스캔 완전성: {'완료' if scan_complete else '불완전'}")
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
