from contextlib import contextmanager
from itertools import chain
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

import pytest

from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.operations import OperationCancelled
from core.scanner import scanner
from database import db, file_contents
from database.scan_sessions import get_latest_scan_session
from core.extractors.text import TextExtractionResult


@pytest.fixture
def root(tmp_path, monkeypatch):
    folder = (tmp_path / 'scan').resolve()
    folder.mkdir()
    monkeypatch.setattr(db, 'DB_PATH', folder / 'state.sqlite3')
    return folder


def names(root):
    return {Path(r['path']).relative_to(root).as_posix() for r in db.get_active_files_under(root)}


def test_app_database_inside_root_is_not_registered_and_cache_is_stable(root):
    path = root / 'note.txt'
    path.write_text('user document', encoding='utf-8')
    first = run_content_analysis(root)
    cached = run_content_analysis(root)
    assert first == {'extracted': 1, 'skipped': 0, 'failed': 0, 'unchanged': 0}
    assert cached == {'extracted': 0, 'skipped': 0, 'failed': 0, 'unchanged': 1}
    assert names(root) == {'note.txt'}
    assert db.DB_PATH.is_file() and path.read_text(encoding='utf-8') == 'user document'
    assert get_latest_scan_session()['scan_complete'] == 1


def test_wal_sidecars_are_excluded_before_and_after_checkpoint(root):
    (root / 'note.txt').write_text('document', encoding='utf-8')
    db.initialize_database()
    reader = db.get_connection()
    try:
        assert reader.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        reader.execute('BEGIN')
        reader.execute('SELECT COUNT(*) FROM files').fetchone()
        with db.get_connection() as writer:
            writer.execute('PRAGMA user_version=17')
        assert Path(str(db.DB_PATH) + '-wal').exists()
        assert Path(str(db.DB_PATH) + '-shm').exists()
        assert run_content_analysis(root)['extracted'] == 1
        assert names(root) == {'note.txt'}
        assert get_latest_scan_session()['error_count'] == 0
    finally:
        reader.close()
    run_incremental_scan(root)
    assert names(root) == {'note.txt'}
    assert get_latest_scan_session()['deleted_count'] == 0


def test_excluded_transient_entry_is_not_inspected(root, monkeypatch):
    (root / 'note.txt').write_text('document', encoding='utf-8')
    class Transient:
        path = str(db.DB_PATH) + '-journal'
        name = db.DB_PATH.name + '-journal'
        def is_symlink(self):
            raise AssertionError('Transient SQLite artifacts must not be inspected')
    actual = scanner.os.scandir
    @contextmanager
    def entries(path):
        with actual(path) as records:
            yield chain([Transient()], records)
    monkeypatch.setattr(scanner.os, 'scandir', entries)
    stats = {}
    batches = list(scanner.scan_directory(root, excluded_files=db.database_artifact_paths(root), scan_stats=stats))
    assert [row['name'] for batch in batches for row in batch] == ['note.txt']
    assert stats['excluded_file_count'] == 1 and stats['error_count'] == 0


def test_other_user_databases_and_similar_paths_are_retained(root):
    for name in ('state.sqlite3-backup', 'state.sqlite3-wal.txt', 'other.db', 'child/state.sqlite3'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'user data')
    run_content_analysis(root)
    assert names(root) == {'state.sqlite3-backup', 'state.sqlite3-wal.txt', 'other.db', 'child/state.sqlite3'}


def test_previously_registered_internal_record_is_preserved_but_not_analyzed(root):
    (root / 'note.txt').write_text('user document', encoding='utf-8')
    db.initialize_database()
    for batch in scanner.scan_directory(root):  # Simulate metadata from an older version.
        db.save_files(batch)
    old_id = next(row['id'] for row in db.get_active_files_under(root) if row['path'] == str(db.DB_PATH))
    file_contents.initialize_file_contents()
    file_contents.save_file_content(old_id, TextExtractionResult(str(db.DB_PATH), 'SKIPPED', reason='UNSUPPORTED_TYPE'), 'old')
    before = file_contents.get_file_content(old_id)
    counts = run_content_analysis(root)
    assert counts == {'extracted': 1, 'skipped': 0, 'failed': 0, 'unchanged': 0}
    assert file_contents.get_file_content(old_id) == before
    assert get_latest_scan_session()['deleted_count'] == 0
    assert db.DB_PATH.is_file()


def test_artifact_paths_respect_root_boundaries_and_unicode(root, monkeypatch):
    sibling = root.parent / 'scan-other' / '상태.sqlite3'
    monkeypatch.setattr(db, 'DB_PATH', sibling)
    assert db.database_artifact_paths(root) == ()
    paths = db.database_artifact_paths(sibling.parent)
    assert set(paths) == {str(sibling) + suffix for suffix in ('', '-journal', '-wal', '-shm')}


def test_cancellation_and_restart_with_database_under_large_root(root):
    for index in range(501):
        (root / f'note-{index:04}.txt').write_text('original', encoding='utf-8')
    assert run_content_analysis(root)['extracted'] == 501
    (root / 'new.txt').write_text('new document', encoding='utf-8')
    (root / 'note-0500.txt').unlink()
    (root / 'note-0000.txt').write_text('modified longer text', encoding='utf-8')
    cancelled = Event()
    def progress(value):
        if value['phase'] == 'scan' and value['processed'] >= 500:
            cancelled.set()
    with pytest.raises(OperationCancelled):
        run_content_analysis(root, is_cancelled=cancelled.is_set, progress_callback=progress)
    assert get_latest_scan_session()['status'] == 'CANCELLED'
    assert get_latest_scan_session()['deleted_count'] == 0
    counts = run_content_analysis(root)
    assert counts == {'extracted': 2, 'skipped': 0, 'failed': 0, 'unchanged': 499}
    assert len(names(root)) == 501 and db.DB_PATH.name not in names(root)
    assert get_latest_scan_session()['deleted_count'] == 1


@pytest.mark.parametrize('module', ['app.main', 'app.incremental_scan', 'app.analyze_content'])
def test_all_scan_clis_exclude_their_own_database(root, module):
    (root / 'note.txt').write_text('document', encoding='utf-8')
    program = "from pathlib import Path; from database import db; db.DB_PATH=Path(__import__('sys').argv.pop(1)); from " + module + " import main; main()"
    result = subprocess.run([sys.executable, '-c', program, str(db.DB_PATH), str(root)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', env=dict(os.environ, PYTHONIOENCODING='cp1252'))
    assert result.returncode == 0, result.stderr
    assert names(root) == {'note.txt'} and db.DB_PATH.is_file()
