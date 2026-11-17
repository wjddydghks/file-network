import sqlite3

import pytest

from database import db


def test_context_commits_and_closes_even_when_connection_is_still_referenced(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    with db.get_connection() as conn:
        conn.execute('CREATE TABLE sample(value TEXT)')
        conn.execute('INSERT INTO sample VALUES (?)', ('saved',))
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')
    with db.get_connection() as verify:
        row = verify.execute('SELECT value FROM sample').fetchone()
        assert row['value'] == 'saved' and row[0] == 'saved'
        assert verify.execute('PRAGMA foreign_keys').fetchone()[0] == 1


def test_exception_rolls_back_and_closes_while_traceback_keeps_connection_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    with db.get_connection() as setup:
        setup.execute('CREATE TABLE sample(value TEXT)')
    with pytest.raises(RuntimeError) as error:
        with db.get_connection() as conn:
            conn.execute('INSERT INTO sample VALUES (?)', ('rollback',))
            raise RuntimeError('retained traceback')
    assert error.value.__traceback__ is not None
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')
    with db.get_connection() as verify:
        assert verify.execute('SELECT COUNT(*) FROM sample').fetchone()[0] == 0
    # This must work on Windows even though conn and traceback remain referenced.
    db.DB_PATH.unlink()
    assert not db.DB_PATH.exists()


def test_direct_connections_can_commit_and_close_explicitly(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    conn = db.get_connection()
    try:
        conn.execute('CREATE TABLE sample(value TEXT)')
        conn.execute('INSERT INTO sample VALUES (?)', ('manual',))
        conn.commit()
    finally:
        conn.close()
    with db.get_connection() as verify:
        assert verify.execute('SELECT value FROM sample').fetchone()[0] == 'manual'


def test_commit_failure_rolls_back_and_closes_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    with db.get_connection() as setup:
        setup.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
        setup.execute('CREATE TABLE child(parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)')
    with pytest.raises(sqlite3.IntegrityError):
        with db.get_connection() as conn:
            conn.execute('INSERT INTO child VALUES (99)')
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')
    with db.get_connection() as verify:
        assert verify.execute('SELECT COUNT(*) FROM child').fetchone()[0] == 0
