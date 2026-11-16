import json

import pytest

from app import search_content as cli
from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from database import db
from database.content_search import search_content
from database.file_search import search_filename
from database.unified_search import search_all


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    folder = tmp_path / 'scan'
    folder.mkdir()
    return folder


def make_files(root):
    (root / '0-needle.txt').write_text('needle needle needle', encoding='utf-8')
    (root / 'content.txt').write_text('needle ' + 'other ' * 30, encoding='utf-8')
    (root / '1-needle.png').write_bytes(b'\xff')
    (root / '2-needle.zip').write_bytes(b'\xff')
    run_content_analysis(root)


def test_combines_content_and_metadata_matches_without_duplicates(root):
    make_files(root)
    rows = search_all('needle')
    assert len(rows) == 4
    assert len({row['file_id'] for row in rows}) == 4
    content = search_content('needle')
    assert [{k: v for k, v in row.items() if k != 'match_source'} for row in rows[:2]] == content
    assert [row['match_source'] for row in rows] == ['content', 'content', 'name', 'name']
    assert [row['name'] for row in rows[2:]] == ['1-needle.png', '2-needle.zip']
    assert '[needle]' in rows[0]['snippet']
    assert rows[2]['snippet'] == '1-needle.png'
    assert rows[2]['score'] == 0.0
    assert all('match_source' not in row for row in search_filename('needle'))
    assert all('match_source' not in row for row in search_content('needle'))


@pytest.mark.parametrize('limit', [1, 2, 3, 4, 5])
def test_one_global_limit_after_deduplication(root, limit):
    make_files(root)
    rows = search_all('needle', limit=limit)
    assert len(rows) == min(limit, 4)
    assert len({row['file_id'] for row in rows}) == len(rows)
    assert rows[0]['match_source'] == 'content'


def test_metadata_only_scope_nested_boundary_and_deletion(root):
    child = root / 'nested'
    child.mkdir()
    sibling = root.parent / 'scan-other'
    sibling.mkdir()
    for folder in (root, child, sibling):
        (folder / '한글 Needle.png').write_bytes(b'\xff')
    run_incremental_scan(root)
    run_incremental_scan(sibling)
    assert len(search_all('글 nEed')) == 3
    assert len(search_all('글 nEed', root_path=root)) == 2
    assert len(search_all('글 nEed', root_path=child)) == 1
    (child / '한글 Needle.png').unlink()
    run_incremental_scan(root)
    assert len(search_all('글 nEed', root_path=root)) == 1


def test_all_terms_must_match_within_one_target_and_syntax_is_literal(root):
    (root / 'alpha.txt').write_text('beta', encoding='utf-8')
    (root / 'alpha beta.png').write_bytes(b'\xff')
    (root / 'body.txt').write_text('alpha beta', encoding='utf-8')
    (root / '100%_done.png').write_bytes(b'\xff')
    run_content_analysis(root)
    rows = search_all('alpha beta')
    assert [row['name'] for row in rows] == ['body.txt', 'alpha beta.png']
    assert search_all('alpha OR missing') == []
    assert search_all("'; DROP TABLE files; --") == []
    assert [row['name'] for row in search_all('%_')] == ['100%_done.png']
    assert db.get_file_count() == 4


def test_changed_content_falls_back_to_current_name(root):
    path = root / 'needle.txt'
    path.write_text('needle body', encoding='utf-8')
    run_content_analysis(root)
    assert search_all('needle')[0]['match_source'] == 'content'
    moved = root / 'needle-renamed.txt'
    path.rename(moved)
    run_incremental_scan(root)
    assert search_all('needle')[0]['path'] == str(moved)
    moved.write_text('new longer unrelated contents', encoding='utf-8')
    run_incremental_scan(root)
    row = search_all('needle')[0]
    assert row['match_source'] == 'name'
    assert row['snippet'] == moved.name


@pytest.mark.parametrize('query,limit', [('', 20), ('  ', 20), (None, 20), ('x', 0), ('x', 1001), ('x', True), ('x', 1.5)])
def test_invalid_arguments_do_not_create_db(root, query, limit):
    with pytest.raises(ValueError):
        search_all(query, limit=limit)
    assert not db.DB_PATH.exists()


def test_empty_db_and_cli_json(root, monkeypatch, capsys):
    assert search_all('needle') == []
    make_files(root)
    capsys.readouterr()
    monkeypatch.setattr('sys.argv', ['search_content', 'needle', '--target', 'all', '--root', str(root), '--limit', '3'])
    cli.main()
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 3
    assert [row['match_source'] for row in rows] == ['content', 'content', 'name']
