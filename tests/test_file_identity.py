import os

import pytest

from app.incremental_scan import is_file_changed, run_incremental_scan
from database import db
from database.scan_sessions import get_latest_scan_session


@pytest.mark.parametrize(
    "identity, expected",
    [
        ({"device_id": 1, "inode": 10}, False),
        ({"device_id": 2, "inode": 10}, True),
        ({"device_id": 1, "inode": 11}, True),
        ({"device_id": None, "inode": None}, True),
    ],
)
def test_identity_changes_require_refresh(identity, expected):
    old_file = {
        "name": "sample.txt",
        "extension": ".txt",
        "size": 3,
        "modified_at": "2026-11-15T00:00:00+00:00",
        "file_type": "TEXT",
        "scan_status": "DISCOVERED",
        "device_id": 1,
        "inode": 10,
    }
    assert is_file_changed({**old_file, **identity}, old_file) is expected


def test_replaced_file_keeps_id_on_subsequent_move(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    path = root / "sample.txt"
    path.write_text("old", encoding="utf-8")
    original_stat = path.stat()

    def files():
        with db.get_connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM files")]

    run_incremental_scan(root)
    original_id = files()[0]["id"]

    # 기존 파일이 존재할 때 생성하여 inode 재사용을 피한다.
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("new", encoding="utf-8")
    os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    replacement.replace(path)
    current_stat = path.stat()
    if not current_stat.st_ino or current_stat.st_ino == original_stat.st_ino:
        pytest.skip("이 파일 시스템에서는 식별자 교체를 검증할 수 없습니다.")

    run_incremental_scan(root)
    refreshed = files()[0]
    assert refreshed["id"] == original_id
    assert refreshed["inode"] == current_stat.st_ino
    assert refreshed["device_id"] == current_stat.st_dev
    assert get_latest_scan_session()["updated_count"] == 1
    assert get_latest_scan_session()["unchanged_count"] == 0

    run_incremental_scan(root)
    assert get_latest_scan_session()["unchanged_count"] == 1

    destination = root / "renamed.txt"
    path.rename(destination)
    run_incremental_scan(root)
    moved = files()
    assert len(moved) == 1
    assert moved[0]["id"] == original_id
    assert moved[0]["path"] == str(destination)
    assert moved[0]["scan_status"] == "DISCOVERED"
    assert get_latest_scan_session()["new_count"] == 0
