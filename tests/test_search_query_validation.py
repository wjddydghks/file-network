import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.analyze_content import run_content_analysis
from core.search_queries import MAX_QUERY_CHARS, MAX_QUERY_TERMS, parse_query_terms
from database import db
from database.content_search import search_content
from database.file_search import search_filename
from database.unified_search import search_all
from database.document_chunks import find_matching_citation


SEARCHES = [search_content, search_filename, search_all, lambda query: find_matching_citation(1, query)]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    return tmp_path


@pytest.mark.parametrize('search', SEARCHES)
@pytest.mark.parametrize('query', ['x' * (MAX_QUERY_CHARS + 1), ' '.join(['word'] * (MAX_QUERY_TERMS + 1)), 'hello\x00world', '\ud800'])
def test_all_searches_reject_bad_input_before_creating_database(isolated, search, query):
    with pytest.raises(ValueError):
        search(query)
    assert not db.DB_PATH.exists()


@pytest.mark.parametrize('search', [search_content, search_filename, search_all])
def test_boolean_result_limits_are_consistently_rejected(isolated, search):
    with pytest.raises(ValueError):
        search('word', limit=True)
    assert not db.DB_PATH.exists()


def test_character_boundary_and_unicode_whitespace(isolated):
    assert parse_query_terms(' \t한글\u00a0café\n😀 ') == ['한글', 'café', '😀']
    assert parse_query_terms('  ' + '😀' * MAX_QUERY_CHARS + '  ') == ['😀' * MAX_QUERY_CHARS]
    for search in (search_content, search_filename, search_all):
        assert search('x' * MAX_QUERY_CHARS) == []


def test_maximum_terms_execute_without_sqlite_expression_errors(isolated):
    root = isolated / 'scan'
    root.mkdir()
    query = ' '.join('w' + str(index) for index in range(MAX_QUERY_TERMS))
    (root / 'note.txt').write_text(query, encoding='utf-8')
    run_content_analysis(root)
    rows = search_content(query)
    assert len(rows) == 1
    assert search_filename(query) == []
    assert search_all(query)[0]['file_id'] == rows[0]['file_id']
    assert find_matching_citation(rows[0]['file_id'], query)['segment'] == 0


@pytest.mark.parametrize('target', ['content', 'name', 'all'])
def test_cli_invalid_query_is_utf8_usage_error_without_database(isolated, target):
    program = "from pathlib import Path; from database import db; db.DB_PATH=Path(__import__('sys').argv.pop(1)); from app.search_content import main; main()"
    result = subprocess.run([sys.executable, '-c', program, str(db.DB_PATH), 'x' * (MAX_QUERY_CHARS + 1), '--target', target], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', env=dict(os.environ, PYTHONIOENCODING='cp1252'))
    assert result.returncode == 2
    assert '검색어' in result.stderr and str(MAX_QUERY_CHARS) in result.stderr
    assert 'Traceback' not in result.stderr and not db.DB_PATH.exists()
