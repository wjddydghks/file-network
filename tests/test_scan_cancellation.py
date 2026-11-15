import pytest

from app import incremental_scan
from database import db
from database.scan_sessions import get_latest_scan_session


@pytest.mark.parametrize("after_batch", [False, True])
def test_cancelled_scan_preserves_partial_work_and_can_restart(
    tmp_path, monkeypatch, capsys, after_batch
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    changed = root / "changed.txt"
    missing = root / "missing.txt"
    changed.write_text("before", encoding="utf-8")
    missing.write_text("keep", encoding="utf-8")
    incremental_scan.run_incremental_scan(root)
    changed.write_text("longer after modification", encoding="utf-8")
    missing.unlink()
    capsys.readouterr()
    original_scan = incremental_scan.scan_directory

    def interrupted_scan(root_path, batch_size, scan_stats):
        if after_batch:
            yield from original_scan(root_path, batch_size=1, scan_stats=scan_stats)
        # 첫 yield 전의 취소는 scan_stats가 비어 있는 경우도 처리해야 한다.
        raise KeyboardInterrupt

    monkeypatch.setattr(incremental_scan, "scan_directory", interrupted_scan)
    with pytest.raises(KeyboardInterrupt):
        incremental_scan.run_incremental_scan(root)

    cancelled = get_latest_scan_session()
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["completed_at"] is not None
    assert cancelled["scan_complete"] == 0
    assert cancelled["discovered_count"] == int(after_batch)
    assert cancelled["updated_count"] == int(after_batch)
    for key in ("new_count", "unchanged_count", "moved_count", "deleted_count", "error_count"):
        assert cancelled[key] == 0
    assert db.get_active_file_count() == 2
    with db.get_connection() as connection:
        saved = connection.execute(
            "SELECT size FROM files WHERE path = ?", (str(changed),)
        ).fetchone()
    assert saved["size"] == (changed.stat().st_size if after_batch else len("before"))
    assert "증분 스캔 완료" not in capsys.readouterr().out

    monkeypatch.setattr(incremental_scan, "scan_directory", original_scan)
    incremental_scan.run_incremental_scan(root)
    restarted = get_latest_scan_session()
    assert restarted["id"] != cancelled["id"]
    assert restarted["status"] == "COMPLETED"
    assert restarted["scan_complete"] == 1
    assert restarted["deleted_count"] == 1
    assert db.get_active_file_count() == 1
    with db.get_connection() as connection:
        assert connection.execute(
            "SELECT status FROM scan_sessions WHERE id = ?", (cancelled["id"],)
        ).fetchone()["status"] == "CANCELLED"


def test_cli_cancellation_exits_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    monkeypatch.setattr("sys.argv", ["incremental_scan", str(root)])

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(incremental_scan, "scan_directory", interrupt)
    with pytest.raises(SystemExit) as exit_info:
        incremental_scan.main()
    assert exit_info.value.code == 130
    output = capsys.readouterr()
    assert "취소" in output.out
    assert "Traceback" not in output.err
    assert get_latest_scan_session()["status"] == "CANCELLED"


def test_ordinary_scan_error_still_records_failed_session(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()

    def fail(*args, **kwargs):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(incremental_scan, "scan_directory", fail)
    with pytest.raises(RuntimeError, match="scan failed"):
        incremental_scan.run_incremental_scan(root)
    session = get_latest_scan_session()
    assert session["status"] == "FAILED"
    assert session["completed_at"] is not None
    assert session["scan_complete"] == 0
