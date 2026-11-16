import sqlite3

import pytest

from app import incremental_scan
from core.scanner.scanner import scan_directory
from database import db
from database.scan_sessions import get_latest_scan_session


@pytest.fixture
def identity_root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    return root


def metadata(path, value):
    return {
        "path": str(path), "name": path.name, "extension": ".txt", "size": 1,
        "modified_at": "2026-11-16T00:00:00+00:00", "created_at": None,
        "file_type": "TEXT", "scanned_at": "2026-11-16T00:00:00+00:00",
        "device_id": value, "inode": value,
    }


@pytest.mark.parametrize("value", [None, 0, 2**63 - 1, 2**63, 2**63 + 1, 2**64 - 1, 2**127 + 3, 2**128 - 1, -(2**63), -(2**63) - 1])
def test_identity_roundtrip_preserves_precision_and_row_api(identity_root, value):
    db.initialize_database()
    file = metadata(identity_root / "sample.txt", value)
    db.save_files([file])
    db.initialize_database()
    with db.get_connection() as connection:
        row = connection.execute("SELECT device_id, inode FROM files").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["device_id"] == value
        assert row[1] == value
    existing = incremental_scan.get_existing_files([file])[file["path"]]
    assert existing["inode"] == value
    assert db.get_active_files_under(identity_root)[0]["device_id"] == value
    with sqlite3.connect(db.DB_PATH) as raw_connection:
        storage = raw_connection.execute("SELECT typeof(device_id), typeof(inode) FROM files").fetchone()
    expected = "null" if value is None else "integer" if -(2**63) <= value < 2**63 else "text"
    assert storage == (expected, expected)


def test_large_identity_updates_and_moves_without_losing_record(identity_root):
    db.initialize_database()
    path = identity_root / "before.txt"
    file = metadata(path, 2**127)
    db.save_files([file])
    record_id = db.get_active_files_under(identity_root)[0]["id"]
    updated = {**file, "device_id": 2**64 + 1, "inode": 2**127 + 1}
    db.save_files([updated])
    destination = identity_root / "after.txt"
    moved = {**updated, "path": str(destination), "name": destination.name, "inode": 2**127 + 2}
    assert db.update_moved_file(str(path), moved) == 1
    row = db.get_active_files_under(identity_root)[0]
    assert row["id"] == record_id
    assert row["inode"] == 2**127 + 2
    assert row["device_id"] == 2**64 + 1
    assert row["path"] == str(destination)


def high_identity_scan(*args, **kwargs):
    for batch in scan_directory(*args, **kwargs):
        for file in batch:
            file["device_id"] = 2**64 + 1
            file["inode"] += 2**127
        yield batch


def test_basic_cli_and_incremental_scans_accept_large_identities(identity_root, monkeypatch):
    from app import main as basic_cli

    path = identity_root / "before.txt"
    path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(basic_cli, "scan_directory", high_identity_scan)
    monkeypatch.setattr(incremental_scan, "scan_directory", high_identity_scan)
    monkeypatch.setattr("sys.argv", ["main", str(identity_root)])
    basic_cli.main()
    original_id = db.get_active_files_under(identity_root)[0]["id"]
    basic_cli.main()
    incremental_scan.run_incremental_scan(identity_root)
    assert get_latest_scan_session()["unchanged_count"] == 1
    path.rename(identity_root / "after.txt")
    incremental_scan.run_incremental_scan(identity_root)
    assert get_latest_scan_session()["moved_count"] == 1
    assert db.get_file_count() == 1
    assert db.get_active_files_under(identity_root)[0]["id"] == original_id


def test_adjacent_large_identities_remain_distinct_for_moves(identity_root, monkeypatch):
    identities = {"a.txt": 2**127, "aa.txt": 2**127, "b.txt": 2**127 + 1, "bb.txt": 2**127 + 1}

    def adjacent_identities(*args, **kwargs):
        for batch in scan_directory(*args, **kwargs):
            for file in batch:
                file["device_id"] = 2**64
                file["inode"] = identities[file["name"]]
            yield batch

    monkeypatch.setattr(incremental_scan, "scan_directory", adjacent_identities)
    for name in ("a.txt", "b.txt"):
        (identity_root / name).write_text("hello", encoding="utf-8")
    incremental_scan.run_incremental_scan(identity_root)
    original_ids = {row["inode"]: row["id"] for row in db.get_active_files_under(identity_root)}
    for before, after in (("a.txt", "aa.txt"), ("b.txt", "bb.txt")):
        (identity_root / before).rename(identity_root / after)
    incremental_scan.run_incremental_scan(identity_root)
    assert get_latest_scan_session()["moved_count"] == 2
    assert get_latest_scan_session()["deleted_count"] == 0
    assert {row["inode"]: row["id"] for row in db.get_active_files_under(identity_root)} == original_ids


def test_large_identities_preserve_content_and_search_invalidation(identity_root, monkeypatch):
    from app.analyze_content import run_content_analysis
    from database.content_search import search_content

    monkeypatch.setattr(incremental_scan, "scan_directory", high_identity_scan)
    path = identity_root / "before.txt"
    path.write_text("original keyword", encoding="utf-8")
    run_content_analysis(identity_root)
    original_id = search_content("original")[0]["file_id"]
    destination = identity_root / "after.txt"
    path.rename(destination)
    assert run_content_analysis(identity_root)["unchanged"] == 1
    assert search_content("original")[0]["file_id"] == original_id
    destination.write_text("replacement content longer", encoding="utf-8")
    incremental_scan.run_incremental_scan(identity_root)
    assert search_content("original") == []
    assert run_content_analysis(identity_root)["extracted"] == 1
    assert search_content("replacement")[0]["file_id"] == original_id
