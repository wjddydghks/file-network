import pytest

from app import incremental_scan
from database import db, scan_sessions


def test_existing_scan_sessions_are_migrated_without_data_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with db.get_connection() as connection:
        connection.execute("""
            CREATE TABLE scan_sessions (
                id TEXT PRIMARY KEY, root_path TEXT, status TEXT,
                started_at TEXT, completed_at TEXT,
                discovered_count INTEGER DEFAULT 0, new_count INTEGER DEFAULT 0,
                updated_count INTEGER DEFAULT 0, unchanged_count INTEGER DEFAULT 0
            )
        """)
        connection.execute("""
            INSERT INTO scan_sessions (id, root_path, status, discovered_count)
            VALUES ('legacy', '/scan', 'COMPLETED', 5)
        """)
    scan_sessions.initialize_scan_sessions()
    scan_sessions.initialize_scan_sessions()
    session = scan_sessions.get_latest_scan_session()
    assert session["id"] == "legacy"
    assert session["discovered_count"] == 5
    assert session["moved_count"] == 0
    assert session["deleted_count"] == 0
    assert session["error_count"] == 0
    assert session["scan_complete"] == 0


@pytest.mark.parametrize("mode", ["complete", "access_error", "failed", "cancelled"])
def test_scan_statistics_track_mixed_changes(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    for name in ("move.txt", "delete.txt", "change.txt", "same.txt"):
        (root / name).write_text(name, encoding="utf-8")
    incremental_scan.run_incremental_scan(root)
    with db.get_connection() as connection:
        moved_id = connection.execute(
            "SELECT id FROM files WHERE name = 'move.txt'"
        ).fetchone()["id"]

    # 신규 파일을 먼저 생성하여 삭제 파일의 inode 재사용을 방지한다.
    (root / "fresh.txt").write_text("new", encoding="utf-8")
    (root / "move.txt").rename(root / "moved.txt")
    (root / "delete.txt").unlink()
    (root / "change.txt").write_text("changed and longer", encoding="utf-8")

    if mode == "access_error":
        original_scan = incremental_scan.scan_directory

        def scan_with_error(*args, **kwargs):
            yield from original_scan(*args, **kwargs)
            kwargs["scan_stats"]["error_count"] += 1

        monkeypatch.setattr(incremental_scan, "scan_directory", scan_with_error)
    elif mode in ("failed", "cancelled"):
        original_save = db.save_files

        def fail_on_new_files(files):
            if any(file["name"] == "fresh.txt" for file in files):
                if mode == "cancelled":
                    raise KeyboardInterrupt
                raise RuntimeError("simulated write failure")
            return original_save(files)

        monkeypatch.setattr(db, "save_files", fail_on_new_files)

    if mode == "cancelled":
        with pytest.raises(KeyboardInterrupt):
            incremental_scan.run_incremental_scan(root)
    elif mode == "failed":
        with pytest.raises(RuntimeError, match="simulated write failure"):
            incremental_scan.run_incremental_scan(root)
    else:
        incremental_scan.run_incremental_scan(root)

    session = scan_sessions.get_latest_scan_session()
    assert session["discovered_count"] == 4
    assert session["new_count"] == 1
    assert session["updated_count"] == 1
    assert session["unchanged_count"] == 1
    assert session["moved_count"] == 1
    assert session["deleted_count"] == (1 if mode == "complete" else 0)
    assert session["status"] == {
        "complete": "COMPLETED",
        "access_error": "COMPLETED_WITH_ERRORS",
        "failed": "FAILED",
        "cancelled": "CANCELLED",
    }[mode]
    assert session["scan_complete"] == (mode == "complete")
    assert session["error_count"] == (mode == "access_error")
    assert session["discovered_count"] == sum(
        session[key] for key in (
            "new_count", "updated_count", "unchanged_count", "moved_count"
        )
    )
    with db.get_connection() as connection:
        assert connection.execute(
            "SELECT id FROM files WHERE name = 'moved.txt'"
        ).fetchone()["id"] == moved_id
    assert db.get_file_count() == (4 if mode in ("failed", "cancelled") else 5)
    assert db.get_active_file_count() == (5 if mode == "access_error" else 4)
    if mode not in ("failed", "cancelled"):
        output = capsys.readouterr().out
        assert "이동 파일: 1개" in output
        assert "DB 활성 파일:" in output
        assert "DB 전체 레코드:" in output


def test_session_finish_defaults_remain_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    scan_sessions.initialize_scan_sessions()
    session_id = scan_sessions.start_scan_session("/scan")
    scan_sessions.finish_scan_session(session_id, "COMPLETED", 0, 0, 0, 0)
    session = scan_sessions.get_latest_scan_session()
    assert session["moved_count"] == 0
    assert session["deleted_count"] == 0
