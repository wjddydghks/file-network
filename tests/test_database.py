
from database import db


def test_initialize_database(tmp_path, monkeypatch):
    """데이터베이스가 정상적으로 생성되는지 검증한다."""

    test_db = tmp_path / "test.db"

    monkeypatch.setattr(db, "DB_PATH", test_db)

    db.initialize_database()

    assert test_db.exists()

    connection = db.get_connection()

    try:
        result = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='files'"
        ).fetchone()

        assert result is not None

    finally:
        connection.close()


def test_save_and_get_files(tmp_path, monkeypatch):
    """파일 메타데이터가 DB에 정상 저장되는지 검증한다."""

    test_db = tmp_path / "test.db"

    monkeypatch.setattr(db, "DB_PATH", test_db)

    db.initialize_database()

    files = [
        {
            "path": "/test/first.txt",
            "name": "first.txt",
            "extension": ".txt",
            "size": 100,
            "modified_at": "2026-11-12T10:00:00",
            "created_at": None,
            "file_type": "TEXT",
            "scanned_at": "2026-11-12T10:00:00"
        },
        {
            "path": "/test/second.py",
            "name": "second.py",
            "extension": ".py",
            "size": 200,
            "modified_at": "2026-11-12T10:00:00",
            "created_at": None,
            "file_type": "CODE",
            "scanned_at": "2026-11-12T10:00:00"
        }
    ]

    db.save_files(files)

    assert db.get_file_count() == 2

    connection = db.get_connection()

    try:
        result = connection.execute(
            "SELECT name, extension, size, file_type "
            "FROM files WHERE path = ?",
            ("/test/first.txt",)
        ).fetchone()

        assert result["name"] == "first.txt"
        assert result["extension"] == ".txt"
        assert result["size"] == 100
        assert result["file_type"] == "TEXT"

    finally:
        connection.close()


def test_save_files_updates_existing_path(tmp_path, monkeypatch):
    """동일한 경로에 저장하면 중복 행 대신 기존 데이터가 갱신되는지 검증한다."""

    test_db = tmp_path / "test.db"

    monkeypatch.setattr(db, "DB_PATH", test_db)

    db.initialize_database()

    first_file = {
        "path": "/test/sample.txt",
        "name": "sample.txt",
        "extension": ".txt",
        "size": 100,
        "modified_at": "2026-11-12T10:00:00",
        "created_at": None,
        "file_type": "TEXT",
        "scanned_at": "2026-11-12T10:00:00"
    }

    updated_file = {
        **first_file,
        "size": 300,
        "scanned_at": "2026-11-12T11:00:00"
    }

    db.save_files([first_file])
    db.save_files([updated_file])

    assert db.get_file_count() == 1

    connection = db.get_connection()

    try:
        result = connection.execute(
            "SELECT size FROM files WHERE path = ?",
            ("/test/sample.txt",)
        ).fetchone()

        assert result["size"] == 300

    finally:
        connection.close()

