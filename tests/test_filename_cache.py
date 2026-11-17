import sqlite3
import unicodedata

import pytest

from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.filenames import fold_filename
from database import db, file_contents
from database.document_chunks import get_document_chunk
from database.file_search import search_filename


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    root = tmp_path / 'scan'
    root.mkdir()
    return root


def drop_cache_schema():
    with db.get_connection() as connection:
        connection.execute('DROP TRIGGER fold_inserted_filename')
        connection.execute('DROP TRIGGER fold_updated_filename')
        connection.execute('DROP INDEX idx_files_unfolded')
        connection.execute('ALTER TABLE files DROP COLUMN name_fold')


def test_cached_search_avoids_per_file_python_normalization(root, monkeypatch):
    for name in ('Straße.txt', unicodedata.normalize('NFD', '한글 café.txt'), 'other.txt'):
        (root / name).write_text('body', encoding='utf-8')
    run_content_analysis(root)
    def forbidden(value):
        raise AssertionError('Stored filenames must not be normalized during search')
    monkeypatch.setattr(db, 'fold_filename', forbidden)
    assert search_filename('STRASSE')[0]['name'] == 'Straße.txt'
    assert len(search_filename('한글 CAFÉ')) == 1
    assert len(search_filename('.txt')) == 3
    db.initialize_database()  # Repeated initialization must not refill existing caches.


def test_legacy_migration_preserves_content_and_citation_revision(root):
    path = root / '한글 Straße.txt'
    path.write_text('original document', encoding='utf-8')
    run_content_analysis(root)
    file_id = search_filename('STRASSE')[0]['file_id']
    content = file_contents.get_file_content(file_id)
    citation = get_document_chunk(file_id)
    drop_cache_schema()
    db.initialize_database()
    assert search_filename('STRASSE')[0]['file_id'] == file_id
    assert file_contents.get_file_content(file_id) == content
    assert get_document_chunk(file_id) == citation
    assert run_content_analysis(root)['unchanged'] == 1


def test_backfill_multiple_batches_and_transactional_rollback(root, monkeypatch):
    db.initialize_database()
    with db.get_connection() as connection:
        connection.executemany("INSERT INTO files(path,name,size,scanned_at) VALUES(?,?,0,'test')",
            [(str(root / f'{i}.txt'), f'한글 Straße {i}.txt') for i in range(1005)])
    drop_cache_schema()
    original = db.fold_filename
    calls = []
    def fail_midway(value):
        calls.append(value)
        if len(calls) == 700:
            raise RuntimeError('migration interrupted')
        return original(value)
    monkeypatch.setattr(db, 'fold_filename', fail_midway)
    with pytest.raises(RuntimeError, match='interrupted'):
        db.initialize_database()
    with db.get_connection() as connection:
        assert 'name_fold' not in {r['name'] for r in connection.execute('PRAGMA table_info(files)')}
        assert connection.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 1005
    monkeypatch.setattr(db, 'fold_filename', original)
    db.initialize_database()
    with db.get_connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM files WHERE name_fold IS NULL').fetchone()[0] == 0
        assert connection.execute('SELECT name_fold FROM files ORDER BY id LIMIT 1').fetchone()[0] == '한글 strasse 0.txt'
        plan = connection.execute('EXPLAIN QUERY PLAN SELECT id,name FROM files WHERE name_fold IS NULL LIMIT 500').fetchall()
        assert any('idx_files_unfolded' in row['detail'] for row in plan)
    assert len(search_filename('STRASSE', limit=1000)) == 1000


def test_insert_update_move_and_content_change_refresh_only_when_needed(root, monkeypatch):
    path = root / 'before.txt'
    path.write_text('body', encoding='utf-8')
    run_content_analysis(root)
    file_id = search_filename('before')[0]['file_id']
    before = get_document_chunk(file_id)
    path.rename(root / 'AFTER.txt')
    run_incremental_scan(root)
    assert search_filename('before') == []
    assert search_filename('after')[0]['file_id'] == file_id
    assert get_document_chunk(file_id)['analyzed_at'] == before['analyzed_at']
    # Direct metadata edits also maintain the cache, through DB triggers.
    with db.get_connection() as connection:
        connection.execute('UPDATE files SET name=? WHERE id=?', ('Straße.txt', file_id))
    assert search_filename('STRASSE')[0]['file_id'] == file_id
    with db.get_connection() as connection:
        connection.execute('UPDATE files SET name_fold=NULL WHERE id=?', (file_id,))
    assert search_filename('STRASSE')[0]['file_id'] == file_id  # Repair an unfinished cache.


def test_normalization_failure_rolls_back_metadata_and_cached_name(root, monkeypatch):
    db.initialize_database()
    def failed(value):
        raise ValueError('normalization failed')
    monkeypatch.setattr(db, 'fold_filename', failed)
    with pytest.raises(sqlite3.OperationalError):
        with db.get_connection() as connection:
            connection.execute("INSERT INTO files(path,name,size,scanned_at) VALUES('test','x',0,'test')")
    assert db.get_file_count() == 0
