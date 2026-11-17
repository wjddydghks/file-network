import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.operations import OperationCancelled
from database import db
from database.content_search import initialize_content_search
from database.related_documents import MAX_KEYWORDS, find_related_documents


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    root = tmp_path / 'scan'
    root.mkdir()
    return root


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def identity(root, name='source.txt'):
    return next(row['id'] for row in db.get_active_files_under(root) if row['name'] == name)


def test_related_results_explain_real_shared_body_terms_and_ignore_names(root):
    write(root, 'source.txt', 'galaxy telescope astronomy')
    write(root, 'related.txt', 'galaxy telescope science')
    write(root, 'galaxy telescope.txt', 'unrelated cooking kitchen')
    write(root, 'galaxy telescope.png', 'unsupported')
    run_content_analysis(root)
    result = find_related_documents(identity(root))
    assert result['relation_type'] == 'shared_keywords'
    assert {k['term'] for k in result['keywords']} == {'galaxy', 'telescope'}
    assert [r['name'] for r in result['results']] == ['related.txt']
    row = result['results'][0]
    assert set(row['shared_keywords']) == {'galaxy', 'telescope'}
    assert '[galaxy]' in row['snippet'] and row['analyzed_at']
    assert result['source']['analyzed_at'] and result['source']['file_id'] == identity(root)


def test_unicode_stop_words_numbers_and_source_only_words(root):
    write(root, 'source.txt', '한글 CAFÉ the and import 12345 uniqueonly')
    write(root, 'other.txt', '한글 cafe the and import 12345')
    run_content_analysis(root)
    result = find_related_documents(identity(root))
    assert {k['term'] for k in result['keywords']} == {'한글', 'cafe'}
    assert result['results'][0]['shared_keywords']


def test_candidate_scope_limit_and_stable_order(root):
    write(root, 'source.txt', 'galaxy telescope')
    write(root, 'nested/a.txt', 'galaxy telescope')
    write(root, 'nested/b.txt', 'galaxy telescope')
    sibling = root.parent / 'scan-other'
    write(sibling, 'c.txt', 'galaxy telescope')
    run_content_analysis(root)
    run_content_analysis(sibling)
    file_id = identity(root)
    assert len(find_related_documents(file_id)['results']) == 3
    scoped = find_related_documents(file_id, root_path=root / 'nested')['results']
    assert [r['name'] for r in scoped] == ['a.txt', 'b.txt']
    assert len(find_related_documents(file_id, limit=1, root_path=root)['results']) == 1
    assert find_related_documents(file_id, root_path=root / 'missing')['results'] == []


def test_unique_or_stopword_only_source_has_no_candidates(root):
    write(root, 'source.txt', 'uniquetoken the and 123')
    write(root, 'other.txt', 'separateword the and 123')
    run_content_analysis(root)
    result = find_related_documents(identity(root))
    assert result['keywords'] == [] and result['results'] == []


def test_keyword_count_is_bounded_and_actual_overlap_uses_or(root):
    words = ['term' + chr(97 + i) for i in range(20)]
    write(root, 'source.txt', ' '.join(words + [words[0]]))
    write(root, 'other.txt', ' '.join(words))
    write(root, 'one.txt', words[0])
    run_content_analysis(root)
    result = find_related_documents(identity(root))
    assert len(result['keywords']) == MAX_KEYWORDS
    one = next(r for r in result['results'] if r['name'] == 'one.txt')
    assert one['shared_keywords'] == [words[0]]


def test_modified_deleted_and_source_stale_are_excluded(root):
    source = write(root, 'source.txt', 'galaxy telescope')
    candidate = write(root, 'other.txt', 'galaxy telescope')
    run_content_analysis(root)
    file_id = identity(root)
    assert find_related_documents(file_id)['results']
    candidate.write_text('replacement longer and unrelated', encoding='utf-8')
    run_incremental_scan(root)
    assert find_related_documents(file_id)['results'] == []
    write(root, 'new.txt', 'galaxy telescope')
    run_content_analysis(root)
    assert find_related_documents(file_id)['results']
    (root / 'new.txt').unlink()
    run_incremental_scan(root)
    assert find_related_documents(file_id)['results'] == []
    source.write_text('updated body longer', encoding='utf-8')
    run_incremental_scan(root)
    with pytest.raises(ValueError, match='분석'):
        find_related_documents(file_id)


def test_snapshots_need_no_sources_and_add_no_permanent_index(root):
    a = write(root, 'source.txt', 'galaxy telescope')
    b = write(root, 'other.txt', 'galaxy telescope')
    run_content_analysis(root)
    file_id = identity(root)
    initialize_content_search()
    before = hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
    a.unlink()
    b.unlink()
    assert find_related_documents(file_id)['results']
    assert hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest() == before
    with db.get_connection() as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name IN ('related_source','source_vocab','corpus_vocab')").fetchall()


def test_pending_missing_and_cancelled_requests(root):
    write(root, 'source.txt', 'galaxy telescope')
    run_incremental_scan(root)
    with pytest.raises(ValueError, match='분석'):
        find_related_documents(identity(root))
    with pytest.raises(ValueError, match='분석'):
        find_related_documents(10000)
    with pytest.raises(OperationCancelled):
        find_related_documents(identity(root), is_cancelled=lambda: True)


@pytest.mark.parametrize('file_id,limit', [(True, 20), (0, 20), ('1', 20), (2**63, 20), (1, True), (1, 0), (1, 101), (1, 1.0)])
def test_invalid_inputs_before_database_creation(root, file_id, limit):
    with pytest.raises(ValueError):
        find_related_documents(file_id, limit=limit)
    assert not db.DB_PATH.exists()


def test_cli_json_with_unicode_and_scope(root):
    write(root, 'source.txt', '한글 telescope')
    write(root, 'other.txt', '한글 telescope')
    run_content_analysis(root)
    program = "from pathlib import Path; from database import db; db.DB_PATH=Path(__import__('sys').argv.pop(1)); from app.related_documents import main; main()"
    result = subprocess.run([sys.executable, '-c', program, str(db.DB_PATH), str(identity(root)), '--limit', '1', '--root', str(root)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', env=dict(os.environ, PYTHONIOENCODING='cp1252'))
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)
    assert row['results'][0]['name'] == 'other.txt'
    assert '한글' in row['results'][0]['shared_keywords']


def test_cancel_during_sqlite_work_preserves_database(root):
    words = ' '.join(f'term{i:05}' for i in range(5000))
    write(root, 'source.txt', words)
    write(root, 'other.txt', words)
    run_content_analysis(root)
    file_id = identity(root)
    initialize_content_search()
    before = hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
    calls = []
    def cancelled():
        calls.append(True)
        return len(calls) >= 2
    with pytest.raises(OperationCancelled):
        find_related_documents(file_id, is_cancelled=cancelled)
    assert hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest() == before
    assert find_related_documents(file_id)['results']
