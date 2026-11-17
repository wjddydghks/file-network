from pathlib import Path
import sqlite3
from threading import Event

import pytest

from app import analyze_content
from app.incremental_scan import run_incremental_scan
from database import db, file_contents
from database.content_search import search_content


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    folder = tmp_path / 'scan'
    folder.mkdir()
    return folder


def seed_states(root, count):
    db.initialize_database()
    file_contents.initialize_file_contents()
    db.save_files([
        {'path': str(root / f'{i}.txt'), 'name': f'{i}.txt', 'extension': '.txt',
         'size': 1, 'modified_at': None, 'created_at': None, 'file_type': 'TEXT', 'scanned_at': 'test'}
        for i in range(count)
    ])
    with db.get_connection() as conn:
        ids = [row[0] for row in conn.execute('SELECT id FROM files ORDER BY id')]
        conn.executemany('INSERT INTO file_contents(file_id,status,text,source_fingerprint,analyzed_at) VALUES (?,?,?,?,?)',
                         [(file_id, 'EXTRACTED' if file_id % 2 else 'FAILED', 'private body', f'fingerprint-{file_id}', 'test') for file_id in ids])
    return ids


def test_bulk_states_multiple_batches_duplicates_missing_and_no_body(root, monkeypatch):
    ids = seed_states(root, file_contents.CONTENT_STATE_BATCH_SIZE * 2 + 1)
    original = db.get_connection
    connections = []
    queries = []

    def connection():
        conn = original()
        conn.set_authorizer(lambda action, table, column, *args:
                            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'file_contents' and column == 'text' else sqlite3.SQLITE_OK)
        conn.set_trace_callback(queries.append)
        connections.append(conn)
        return conn

    monkeypatch.setattr(db, 'get_connection', connection)
    result = file_contents.get_content_states(ids + [ids[0], ids[-1] + 1])
    assert len(result) == len(ids)
    assert len(connections) == 1
    assert len(queries) == 3
    assert result[ids[0]] == {'status': 'EXTRACTED', 'source_fingerprint': f'fingerprint-{ids[0]}'}
    assert result[ids[1]]['status'] == 'FAILED'
    assert all(set(state) == {'status', 'source_fingerprint'} for state in result.values())
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute('SELECT 1')


def test_empty_bulk_state_lookup_never_opens_db(root, monkeypatch):
    monkeypatch.setattr(db, 'get_connection', lambda: pytest.fail('empty lookup opened DB'))
    assert file_contents.get_content_states([]) == {}
    assert not db.DB_PATH.exists()


def test_bulk_state_query_error_closes_connection(root, monkeypatch):
    db.initialize_database()  # Deliberately leave the content table absent.
    original = db.get_connection
    connections = []
    def connection():
        conn = original()
        connections.append(conn)
        return conn
    monkeypatch.setattr(db, 'get_connection', connection)
    with pytest.raises(sqlite3.OperationalError):
        file_contents.get_content_states([1])
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute('SELECT 1')


def test_cached_analysis_uses_bounded_state_batches_instead_of_per_file_connections(root, monkeypatch):
    count = file_contents.CONTENT_STATE_BATCH_SIZE + 1
    for i in range(count):
        (root / f'note-{i}.txt').write_bytes(b'hello')
    run_incremental_scan(root)
    file_contents.initialize_file_contents()
    records = db.get_active_files_under(root)
    with db.get_connection() as conn:
        conn.executemany('INSERT INTO file_contents(file_id,status,text,source_fingerprint,analyzed_at) VALUES (?,?,?,?,?)',
                         [(row['id'], 'EXTRACTED', 'hello', analyze_content.source_fingerprint(Path(row['path']), 1024*1024), 'test') for row in records])
    original = db.get_connection
    connection_count = 0
    def connection():
        nonlocal connection_count
        connection_count += 1
        return original()
    monkeypatch.setattr(db, 'get_connection', connection)
    monkeypatch.setattr(analyze_content, 'extract_content', lambda *a, **k: pytest.fail('unchanged source re-extracted'))
    batches = []
    original_states = file_contents.get_content_states
    def states(ids):
        batches.append(len(ids))
        return original_states(ids)
    monkeypatch.setattr(file_contents, 'get_content_states', states)
    result = analyze_content.run_content_analysis(root)
    assert result == {'extracted': 0, 'failed': 0, 'skipped': 0, 'unchanged': count}
    assert batches == [file_contents.CONTENT_STATE_BATCH_SIZE, 1]
    assert connection_count < 30  # Guards against O(files) connection setup.


def test_source_changed_after_batch_state_read_is_still_reanalyzed(root, monkeypatch):
    for name in ('a.txt', 'b.txt'):
        (root / name).write_bytes(b'old keyword')
    analyze_content.run_content_analysis(root)
    original = file_contents.get_content_states
    def states(ids):
        result = original(ids)
        (root / 'b.txt').write_bytes(b'replacement longer contents')
        return result
    monkeypatch.setattr(file_contents, 'get_content_states', states)
    result = analyze_content.run_content_analysis(root)
    assert result['extracted'] == 1 and result['unchanged'] == 1
    assert [row['name'] for row in search_content('replacement')] == ['b.txt']
    assert [row['name'] for row in search_content('old')] == ['a.txt']


def test_cancel_during_batch_lookup_stops_before_opening_source(root, monkeypatch):
    (root / 'note.txt').write_bytes(b'hello')
    cancelled = Event()
    original = file_contents.get_content_states
    def states(ids):
        result = original(ids)
        cancelled.set()
        return result
    monkeypatch.setattr(file_contents, 'get_content_states', states)
    monkeypatch.setattr(analyze_content, 'extract_content', lambda *a, **k: pytest.fail('source opened after cancellation'))
    with pytest.raises(KeyboardInterrupt):
        analyze_content.run_content_analysis(root, is_cancelled=cancelled.is_set)
    with db.get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM file_contents').fetchone()[0] == 0
