
from database import db
from database.scan_sessions import get_latest_scan_session
from app.incremental_scan import run_incremental_scan


def test_incremental_scan_detects_new_and_unchanged_files(
    tmp_path,
    monkeypatch
):
    """첫 스캔은 신규 파일, 두 번째 스캔은 변경 없는 파일로 분류한다."""

    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)

    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()

    (scan_dir / "sample.txt").write_text(
        "File Network",
        encoding="utf-8"
    )

    # 첫 번째 스캔
    run_incremental_scan(str(scan_dir))

    first_session = get_latest_scan_session()

    assert first_session["status"] == "COMPLETED"
    assert first_session["discovered_count"] == 1
    assert first_session["new_count"] == 1
    assert first_session["unchanged_count"] == 0
    assert db.get_file_count() == 1

    # 두 번째 스캔
    run_incremental_scan(str(scan_dir))

    second_session = get_latest_scan_session()

    assert second_session["status"] == "COMPLETED"
    assert second_session["discovered_count"] == 1
    assert second_session["new_count"] == 0
    assert second_session["updated_count"] == 0
    assert second_session["unchanged_count"] == 1
    assert db.get_file_count() == 1


def test_incremental_scan_detects_modified_file(
    tmp_path,
    monkeypatch
):
    """파일 크기가 변경되면 변경 파일로 분류한다."""

    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)

    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()

    test_file = scan_dir / "sample.txt"

    test_file.write_text("before", encoding="utf-8")

    # 최초 등록
    run_incremental_scan(str(scan_dir))

    # 파일 내용 및 크기 변경
    test_file.write_text(
        "after modification",
        encoding="utf-8"
    )

    # 변경 감지
    run_incremental_scan(str(scan_dir))

    session = get_latest_scan_session()

    assert session["status"] == "COMPLETED"
    assert session["new_count"] == 0
    assert session["updated_count"] == 1
    assert session["unchanged_count"] == 0
    assert db.get_file_count() == 1



def read_files():
    with db.get_connection() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM files ORDER BY id"
        )]


def test_deleted_file_restored_with_identical_metadata(tmp_path, monkeypatch):
    """메타데이터가 같아도 재발견된 파일은 활성 상태로 복원한다."""
    import os

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    path = root / "sample.txt"
    path.write_text("hello", encoding="utf-8")
    original_stat = path.stat()
    run_incremental_scan(root)
    original_id = read_files()[0]["id"]

    path.unlink()
    run_incremental_scan(root)
    assert read_files()[0]["scan_status"] == "DELETED"

    path.write_text("hello", encoding="utf-8")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    run_incremental_scan(root)

    restored = read_files()[0]
    assert restored["id"] == original_id
    assert restored["scan_status"] == "DISCOVERED"
    assert restored["inode"] == path.stat().st_ino
    assert get_latest_scan_session()["updated_count"] == 1
    assert get_latest_scan_session()["unchanged_count"] == 0

    run_incremental_scan(root)
    assert get_latest_scan_session()["unchanged_count"] == 1


def test_move_preserves_file_id(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    source = root / "before.txt"
    source.write_text("hello", encoding="utf-8")
    run_incremental_scan(root)
    original_id = read_files()[0]["id"]

    destination = root / "after.txt"
    source.rename(destination)
    run_incremental_scan(root)

    files = read_files()
    assert len(files) == 1
    assert files[0]["id"] == original_id
    assert files[0]["path"] == str(destination)
    assert files[0]["scan_status"] == "DISCOVERED"
    assert get_latest_scan_session()["new_count"] == 0


def test_deletion_is_limited_to_scanned_root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    sibling = tmp_path / "scan-other"
    root.mkdir()
    sibling.mkdir()
    path = root / "sample.txt"
    path.write_text("hello", encoding="utf-8")
    (sibling / "other.txt").write_text("keep", encoding="utf-8")
    run_incremental_scan(root)
    run_incremental_scan(sibling)

    path.unlink()
    run_incremental_scan(root)

    states = {file["path"]: file["scan_status"] for file in read_files()}
    assert states[str(path)] == "DELETED"
    assert states[str(sibling / "other.txt")] == "DISCOVERED"
    assert get_latest_scan_session()["scan_complete"] == 1


def test_access_error_prevents_deletion(tmp_path, monkeypatch):
    from app import incremental_scan

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    (root / "sample.txt").write_text("hello", encoding="utf-8")
    run_incremental_scan(root)

    def inaccessible_scan(root_path, batch_size, scan_stats):
        scan_stats.update(error_count=1, directory_error_count=1, file_error_count=0)
        yield from ()

    monkeypatch.setattr(incremental_scan, "scan_directory", inaccessible_scan)
    run_incremental_scan(root)

    assert read_files()[0]["scan_status"] == "DISCOVERED"
    session = get_latest_scan_session()
    assert session["status"] == "COMPLETED_WITH_ERRORS"
    assert session["scan_complete"] == 0
    assert session["error_count"] == 1
