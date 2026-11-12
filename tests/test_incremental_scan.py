
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

