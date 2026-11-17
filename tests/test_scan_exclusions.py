import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from app.gui_settings import GuiSettings, load_settings, save_settings
from core.scanner.exclusions import ExcludedDirectories, parse_excluded_dirs, validate_excluded_dirs
from core.scanner.scanner import scan_directory
from database import db, file_contents
from database.content_search import search_content
from database.document_chunks import get_document_chunk
from database.scan_sessions import get_latest_scan_session


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    root = tmp_path / 'scan'
    root.mkdir()
    return root


def write(root, relative, text='keyword'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_scanner_prunes_exact_folder_names_at_every_depth(root, monkeypatch):
    for name in ('node_modules/a.txt', 'sub/node_modules/b.txt', 'node_modules-old/c.txt', 'sub/keep.txt', 'node_modules.txt'):
        write(root, name)
    actual = os.scandir
    visited = []
    def checked(path):
        visited.append(Path(path))
        assert Path(path).name != 'node_modules'
        return actual(path)
    monkeypatch.setattr('core.scanner.scanner.os.scandir', checked)
    stats = {}
    rows = [item for batch in scan_directory(root, excluded_dirs=['node_modules'], scan_stats=stats) for item in batch]
    assert {Path(row['path']).relative_to(root).as_posix() for row in rows} == {'node_modules-old/c.txt', 'sub/keep.txt', 'node_modules.txt'}
    assert stats['excluded_directory_count'] == 2 and stats['error_count'] == 0
    assert len(visited) == 3


def test_explicit_root_is_never_excluded_and_default_scans_all(root):
    folder = root / 'skip'
    write(folder, 'child.txt')
    assert len(list(scan_directory(folder, excluded_dirs=['skip']))[0]) == 1
    assert len(list(scan_directory(root))[0]) == 1


def test_excluded_existing_data_is_preserved_until_exclusion_removed(root):
    keep = write(root, 'keep.txt', 'keepkeyword')
    changed = write(root, 'skip/changed.txt', 'oldkeyword')
    deleted = write(root, 'skip/deleted.txt', 'deletedkeyword')
    run_content_analysis(root)
    old = {r['path']: r for r in db.get_active_files_under(root)}
    changed_id, deleted_id = old[str(changed)]['id'], old[str(deleted)]['id']
    before = get_document_chunk(changed_id)
    changed.write_text('replacementkeyword longer', encoding='utf-8')
    deleted.unlink()
    keep.unlink()
    counts = run_content_analysis(root, excluded_dirs=['skip'])
    assert counts == {'extracted': 0, 'failed': 0, 'skipped': 0, 'unchanged': 0}
    assert get_document_chunk(changed_id) == before
    assert get_document_chunk(deleted_id)['text'] == 'deletedkeyword'
    session = get_latest_scan_session()
    assert session['deleted_count'] == 1 and session['scan_complete'] == 1
    assert len(search_content('oldkeyword')) == 1  # Exclusions do not hide stored search results.
    assert run_content_analysis(root)['extracted'] == 1
    assert get_document_chunk(changed_id)['text'] == 'replacementkeyword longer'
    assert get_document_chunk(deleted_id) is None
    assert search_content('oldkeyword') == []


def test_move_out_of_excluded_folder_does_not_reassign_its_cached_id(root):
    source = write(root, 'skip/source.txt')
    run_content_analysis(root)
    old_id = db.get_active_files_under(root)[0]['id']
    target = source.rename(root / 'outside.txt')
    run_content_analysis(root, excluded_dirs=['skip'])
    rows = {row['path']: row['id'] for row in db.get_active_files_under(root)}
    assert rows[str(source)] == old_id and rows[str(target)] != old_id
    assert get_latest_scan_session()['moved_count'] == 0
    assert get_document_chunk(old_id)['text'] == 'keyword'


def test_cancelled_filtered_scan_does_not_delete_missing_files(root):
    path = write(root, 'missing.txt')
    write(root, 'skip/cached.txt')
    run_content_analysis(root)
    path.unlink()
    with pytest.raises(KeyboardInterrupt):
        run_incremental_scan(root, excluded_dirs=['skip'], is_cancelled=lambda: True)
    assert db.get_active_file_count() == 2
    assert get_latest_scan_session()['status'] == 'CANCELLED'
    assert get_latest_scan_session()['deleted_count'] == 0


def test_incomplete_filtered_scan_preserves_other_missing_files(root, monkeypatch):
    from core.scanner import scanner
    path = write(root, 'missing.txt')
    write(root, 'skip/cache.txt')
    write(root, 'denied/keep.txt')
    run_content_analysis(root)
    path.unlink()
    actual = scanner.os.scandir
    def denied(path):
        if Path(path).name == 'denied':
            raise PermissionError('denied')
        return actual(path)
    monkeypatch.setattr(scanner.os, 'scandir', denied)
    run_incremental_scan(root, excluded_dirs=['skip'])
    assert db.get_active_file_count() == 3
    session = get_latest_scan_session()
    assert session['scan_complete'] == 0 and session['error_count'] == 1 and session['deleted_count'] == 0


@pytest.mark.parametrize('names', ['skip', None, [None], [''], ['.'], ['..'], ['../skip'], ['a/b'], ['a\\b'], ['*.git'], ['a?'], ['a[b]'], ['a,b'], ['C:'], ['bad\x00'], ['a\nb'], [' padded'], ['\ud800'], ['x'*256], ['a']*101])
def test_invalid_names_are_rejected_before_database_mutation(root, names):
    with pytest.raises(ValueError):
        run_content_analysis(root, excluded_dirs=names)
    assert not db.DB_PATH.exists()


def test_parser_and_platform_case_rules(root):
    assert parse_excluded_dirs(' .git, node_modules, .git, ') == ('.git', 'node_modules')
    assert validate_excluded_dirs([]) == ()
    exclusion = ExcludedDirectories(root, ['SKIP'])
    assert exclusion.contains_file(root / 'SKIP' / 'x.txt')
    assert exclusion.contains_file(root / 'skip' / 'x.txt') == (os.name == 'nt')
    assert not exclusion.contains_file(root / 'SKIP.txt')
    assert not exclusion.contains_file(root.parent / 'SKIP' / 'x.txt')


def test_exclusions_settings_roundtrip_legacy_and_invalid_field(tmp_path):
    path = tmp_path / 'settings.json'
    settings = GuiSettings(excluded_dirs='.git, node_modules, 한글 폴더')
    save_settings(path, settings)
    assert load_settings(path) == (settings, None)
    path.write_text(json.dumps({'folder': 'kept', 'excluded_dirs': '../bad'}), encoding='utf-8')
    result, notice = load_settings(path)
    assert notice and result.folder == 'kept' and result.excluded_dirs == ''
    path.write_text('{"folder":"legacy"}', encoding='utf-8')
    result, notice = load_settings(path)
    assert not notice and result.excluded_dirs == ''


@pytest.mark.parametrize('module', ['app.main', 'app.incremental_scan', 'app.analyze_content'])
def test_cli_repeatable_exclusions(root, module):
    write(root, 'skip/a.txt')
    write(root, 'cache/b.txt')
    write(root, 'kept.txt')
    program = "from pathlib import Path; from database import db; db.DB_PATH=Path(__import__('sys').argv.pop(1)); from " + module + " import main; main()"
    command = [sys.executable, '-c', program, str(db.DB_PATH), str(root), '--exclude-dir', 'skip', '--exclude-dir', 'cache']
    env = dict(os.environ, PYTHONIOENCODING='cp1252')
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert '완료' in result.stdout
    assert [r['name'] for r in db.get_active_files_under(root)] == ['kept.txt']
