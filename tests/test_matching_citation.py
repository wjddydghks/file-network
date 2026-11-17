import hashlib

import pytest

from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.operations import OperationCancelled
from core.extractors.text import TextExtractionResult
from database import db, file_contents
from database.content_search import search_content
from database import document_chunks
from database.document_chunks import find_matching_citation, get_document_chunk
from tests.test_pdf_extraction import make_pdf
from tests.test_office_citations import make_sections


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    root = tmp_path / 'scan'
    root.mkdir()
    return root


def file_id(root):
    return db.get_active_files_under(root)[0]['id']


def test_pdf_finds_actual_matching_page_without_reading_source_or_growing_db(root):
    path = make_pdf(root / 'note.pdf', ('unrelated', '', 'Hello cafe', 'Hello again'))
    run_content_analysis(root)
    before = hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
    path.unlink()
    result = find_matching_citation(file_id(root), 'HELLO CAFÉ')
    assert result['page'] == 3 and result['segment'] == 1
    assert result['text'] == 'Hello cafe'
    assert hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest() == before
    with db.get_connection() as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'citation_matches%'").fetchall() == []


@pytest.mark.parametrize('extension,kind,label', [('.xlsx', 'sheet', '찾을 자료'), ('.pptx', 'slide', '3')])
def test_matching_office_location(root, extension, kind, label):
    make_sections(root / ('note' + extension), [('처음', 'unrelated'), ('빈 내용', ''), ('찾을 자료', 'needlekeyword'), ('다음', 'needlekeyword again')])
    run_content_analysis(root)
    result = find_matching_citation(file_id(root), 'needlekeyword')
    assert result['source_kind'] == kind and result['source_label'] == label
    assert 'needlekeyword' in result['text']


def test_literal_and_query_does_not_execute_fts_operators(root):
    make_pdf(root / 'note.pdf', ('alpha only', 'alpha OR beta', 'alpha beta'))
    run_content_analysis(root)
    result = find_matching_citation(file_id(root), 'alpha OR beta')
    assert result['page'] == 2
    assert find_matching_citation(file_id(root), '"alpha" "beta"')['page'] == 2
    assert find_matching_citation(file_id(root), "'; DROP TABLE files; --") is None
    assert db.get_file_count() == 1


def test_full_document_match_can_have_no_single_matching_citation(root):
    (root / 'note.txt').write_text('alpha ' + 'x ' * 1100 + 'beta', encoding='utf-8')
    run_content_analysis(root)
    assert len(search_content('alpha beta')) == 1
    assert find_matching_citation(file_id(root), 'alpha beta') is None
    assert find_matching_citation(file_id(root), 'beta')['segment'] == 1


def test_missing_legacy_pending_stale_and_deleted_content(root):
    path = root / 'note.txt'
    path.write_text('needle', encoding='utf-8')
    run_incremental_scan(root)
    identity = file_id(root)
    assert find_matching_citation(identity, 'needle') is None
    run_content_analysis(root)
    with db.get_connection() as connection:
        connection.execute('DELETE FROM document_chunks')
    assert find_matching_citation(identity, 'needle') is None
    file_contents.save_file_content(identity, TextExtractionResult(str(path), 'EXTRACTED', text='needle'), 'snapshot')
    path.write_text('changed content', encoding='utf-8')
    run_incremental_scan(root)
    assert find_matching_citation(identity, 'needle') is None
    path.unlink()
    run_incremental_scan(root)
    assert find_matching_citation(identity, 'needle') is None
    assert find_matching_citation(identity + 1, 'needle') is None


def test_changed_revision_between_match_and_preview_is_rejected(root, monkeypatch):
    path = root / 'note.txt'
    path.write_text('needle original', encoding='utf-8')
    run_content_analysis(root)
    original = document_chunks.get_document_chunk
    def changed(*args, **kwargs):
        file_contents.save_file_content(args[0], TextExtractionResult(str(path), 'EXTRACTED', text='replacement'), 'new')
        return original(*args, **kwargs)
    monkeypatch.setattr(document_chunks, 'get_document_chunk', changed)
    with pytest.raises(ValueError, match='변경'):
        find_matching_citation(file_id(root), 'needle')


def test_moved_file_keeps_matching_citation_identity(root):
    path = root / 'note.txt'
    path.write_text('needle', encoding='utf-8')
    run_content_analysis(root)
    identity = file_id(root)
    destination = path.rename(root / 'moved.txt')
    run_incremental_scan(root)
    result = find_matching_citation(identity, 'needle')
    assert result['path'] == str(destination) and result['file_id'] == identity


def test_cancellation_leaves_stored_document_intact(root):
    path = root / 'note.txt'
    path.write_text('needle word ' * 20000, encoding='utf-8')
    run_content_analysis(root)
    identity = file_id(root)
    before = file_contents.get_file_content(identity)
    calls = []
    def cancelled():
        calls.append(True)
        return len(calls) >= 3
    with pytest.raises(OperationCancelled):
        find_matching_citation(identity, 'needle', is_cancelled=cancelled)
    assert file_contents.get_file_content(identity) == before
    assert get_document_chunk(identity)['text']


@pytest.mark.parametrize('identity,query', [(True, 'x'), (0, 'x'), (2**63, 'x'), ('1', 'x'), (1, None), (1, ''), (1, '  ')])
def test_invalid_input_does_not_create_db(root, identity, query):
    with pytest.raises(ValueError):
        find_matching_citation(identity, query)
    assert not db.DB_PATH.exists()
