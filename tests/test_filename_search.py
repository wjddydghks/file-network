import json
import unicodedata

import pytest

from app import search_content as cli
from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from database import db
from database.file_search import search_filename


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'files.db')
    folder = tmp_path / 'scan'
    folder.mkdir()
    return folder


def test_metadata_only_files_and_unicode_literal_substrings(root):
    names = ['한글 Project.PNG', '한글 other.zip', '100%_done.txt', "owner's.txt", 'Straße.txt']
    for name in names:
        (root / name).write_bytes(b'\xff')
    run_incremental_scan(root)
    assert [row['name'] for row in search_filename('프로젝트')] == []
    assert [row['name'] for row in search_filename('글 proJ')] == ['한글 Project.PNG']
    assert [row['name'] for row in search_filename('STRASSE')] == ['Straße.txt']
    assert [row['name'] for row in search_filename('%_')] == ['100%_done.txt']
    assert [row['name'] for row in search_filename("owner's")] == ["owner's.txt"]
    assert search_filename("'; DROP TABLE files; --") == []
    assert search_filename('Project OR missing') == []
    assert db.get_file_count() == len(names)
    # Filename search creates no content table or FTS index and reads no source.
    with db.get_connection() as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'file_contents'").fetchone() is None
    result = search_filename('Project')[0]
    assert result['snippet'] == result['name']
    assert result['score'] == 0.0
    assert set(result) == {'file_id', 'path', 'name', 'snippet', 'score'}


def test_unicode_normalization_handles_decomposed_names(root):
    name = unicodedata.normalize('NFD', '한글 café.txt')
    (root / name).write_text('body', encoding='utf-8')
    run_incremental_scan(root)
    assert len(search_filename('한글 CAFÉ')) == 1
    assert len(search_filename(unicodedata.normalize('NFD', 'café'))) == 1


def test_scope_subdirectories_limit_and_deterministic_order(root):
    child = root / 'nested'
    child.mkdir()
    sibling = root.parent / 'scan-other'
    sibling.mkdir()
    for folder in (root, child, sibling):
        (folder / 'image.png').write_bytes(b'\xff')
    run_incremental_scan(root)
    run_incremental_scan(sibling)
    assert len(search_filename('image')) == 3
    matches = search_filename('image', root_path=root)
    assert len(matches) == 2
    assert [row['path'] for row in matches] == sorted(row['path'] for row in matches)
    assert len(search_filename('image', root_path=child)) == 1
    assert len(search_filename('image', root_path=root, limit=1)) == 1


def test_move_delete_and_restore_use_latest_metadata(root):
    path = root / 'before.png'
    path.write_bytes(b'\xff')
    run_incremental_scan(root)
    file_id = search_filename('before')[0]['file_id']
    destination = root / 'after.png'
    path.rename(destination)
    run_incremental_scan(root)
    assert search_filename('before') == []
    assert search_filename('after')[0]['file_id'] == file_id
    destination.unlink()
    run_incremental_scan(root)
    assert search_filename('after') == []
    destination.write_bytes(b'\xff')
    run_incremental_scan(root)
    assert len(search_filename('after')) == 1


def test_extracted_skipped_failed_and_stale_files_are_searchable(root):
    (root / 'good.txt').write_text('hello', encoding='utf-8')
    (root / 'bad.txt').write_bytes(b'\xff')
    (root / 'image.png').write_bytes(b'\xff')
    run_content_analysis(root)
    for name in ('good.txt', 'bad.txt', 'image.png'):
        assert len(search_filename(name)) == 1
    (root / 'good.txt').write_text('changed body longer', encoding='utf-8')
    run_incremental_scan(root)
    assert len(search_filename('good.txt')) == 1


@pytest.mark.parametrize('query,limit', [('', 20), ('  ', 20), (None, 20), ('file', 0), ('file', 1001), ('file', True), ('file', 1.5)])
def test_invalid_input_does_not_create_db(root, query, limit):
    with pytest.raises(ValueError):
        search_filename(query, limit=limit)
    assert not db.DB_PATH.exists()


def test_empty_database(root):
    assert search_filename('anything') == []


def test_cli_filename_target_scope_and_limit(root, monkeypatch, capsys):
    (root / '한글 image.png').write_bytes(b'\xff')
    run_incremental_scan(root)
    capsys.readouterr()
    monkeypatch.setattr('sys.argv', ['search_content', '한글', '--target', 'name', '--root', str(root), '--limit', '1'])
    cli.main()
    assert json.loads(capsys.readouterr().out)[0]['name'] == '한글 image.png'
    monkeypatch.setattr('sys.argv', ['search_content', '한글', '--target', 'invalid'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
