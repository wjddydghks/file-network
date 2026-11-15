from pathlib import Path
from unittest.mock import Mock

import pytest

from core.scanner.scanner import scan_directory
from database import db


@pytest.mark.parametrize(
    "root, separator",
    [
        ("/scan", "/"),
        ("/", "/"),
        ("C:\\scan", "\\"),
        ("C:\\", "\\"),
        ("\\\\server\\share\\scan", "\\"),
        ("\\\\server\\share\\", "\\"),
    ],
)
def test_scope_uses_native_path_boundaries(tmp_path, monkeypatch, root, separator):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.initialize_database()
    prefix = root.rstrip(separator) + separator
    active = prefix + "nested" + separator + "active.txt"
    deleted = prefix + "deleted.txt"
    paths = [active, deleted]
    if not root.endswith(separator):
        paths.append(root + "-other" + separator + "outside.txt")

    db.save_files([
        {
            "path": path,
            "name": "sample.txt",
            "extension": ".txt",
            "size": 1,
            "modified_at": None,
            "created_at": None,
            "file_type": "TEXT",
            "scanned_at": "2026-11-15T00:00:00+00:00",
        }
        for path in paths
    ])
    db.mark_files_deleted([deleted])

    # 경로 정규화만 대체하여 다른 OS의 DB 경로 조회도 검증한다.
    path_factory = Mock()
    path_factory.return_value.expanduser.return_value.resolve.return_value = root
    monkeypatch.setattr(db, "Path", path_factory)
    monkeypatch.setattr(db, "sep", separator, raising=False)
    assert {row["path"] for row in db.get_active_files_under(root)} == {active}


def test_scope_matches_scanner_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.initialize_database()
    root = tmp_path / "scan"
    (root / "nested").mkdir(parents=True)
    path = root / "nested" / "sample.txt"
    path.write_text("hello", encoding="utf-8")
    for batch in scan_directory(root):
        db.save_files(batch)

    assert {row["path"] for row in db.get_active_files_under(root)} == {
        str(Path(path).resolve())
    }
