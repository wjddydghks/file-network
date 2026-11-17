import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import analyze_content
from app.incremental_scan import run_incremental_scan
from core.citations import MAX_CHUNK_CHARS, build_chunks
from core.extractors.text import SourceSpan, TextExtractionResult
from database import db, file_contents
from database.document_chunks import get_document_chunk
from database.file_search import search_filename
from tests.test_pdf_extraction import make_pdf


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    folder = tmp_path / 'scan'
    folder.mkdir()
    return folder


def get_id():
    return search_filename('note')[0]['file_id']


def stored_count(file_id):
    with db.get_connection() as connection:
        return connection.execute('SELECT COUNT(*) FROM document_chunks WHERE file_id=?', (file_id,)).fetchone()[0]


def test_chunks_preserve_unicode_crlf_and_literal_form_feed(root):
    path = root / 'note.txt'
    text = '한😀\r\n' * 1300 + '\n\f\nnot a PDF page'
    path.write_bytes(text.encode('utf-8'))
    analyze_content.run_content_analysis(root)
    first = get_document_chunk(get_id())
    chunks = [get_document_chunk(get_id(), i, first['analyzed_at']) for i in range(first['chunk_count'])]
    assert ''.join(c['text'] for c in chunks) == text
    assert all(0 < len(c['text']) <= MAX_CHUNK_CHARS for c in chunks)
    assert all(c['page'] is None for c in chunks)
    assert all(text[c['start_char']:c['end_char']] == c['text'] for c in chunks)
    assert first['source_fingerprint'] == analyze_content.source_fingerprint(path, 1024 * 1024)
    path.unlink()  # Stored snapshot, no source file access.
    assert get_document_chunk(get_id())['text'] == chunks[0]['text']


def test_pdf_citations_use_actual_page_numbers_and_do_not_split_on_text_markers(root):
    path = make_pdf(root / 'note.pdf', ('', 'Second searchable page', '', 'Fourth page'))
    analyze_content.run_content_analysis(root)
    file_id = get_id()
    a, b = get_document_chunk(file_id), get_document_chunk(file_id, 1)
    assert a['chunk_count'] == 2
    assert (a['page'], b['page']) == (2, 4)
    assert a['text'] == 'Second searchable page' and b['text'] == 'Fourth page'
    full = file_contents.get_file_content(file_id)['text']
    assert full[a['start_char']:a['end_char']] == a['text']
    assert full[b['start_char']:b['end_char']] == b['text']
    path.unlink()
    assert get_document_chunk(file_id, 1)['page'] == 4


def test_page_longer_than_chunk_limit_never_crosses_page_boundary():
    text = 'a' * 4001 + '\n\f\n' + 'b' * 3
    result = TextExtractionResult('x.pdf', 'EXTRACTED', text=text,
        source_spans=(SourceSpan(0, 4001, 1), SourceSpan(4004, 4007, 3)))
    chunks = build_chunks(result)
    assert chunks == [(0, 0, 2000, 1, 'page', None), (1, 2000, 4000, 1, 'page', None), (2, 4000, 4001, 1, 'page', None), (3, 4004, 4007, 3, 'page', None)]


def test_chunk_lifecycle_move_stale_reanalysis_and_delete(root):
    path = root / 'note.txt'
    path.write_text('old content', encoding='utf-8')
    analyze_content.run_content_analysis(root)
    file_id = get_id()
    first = get_document_chunk(file_id)
    moved = path.rename(root / 'renamed.txt')
    assert analyze_content.run_content_analysis(root)['unchanged'] == 1
    assert get_document_chunk(file_id)['path'] == str(moved)
    assert get_document_chunk(file_id)['analyzed_at'] == first['analyzed_at']
    moved.write_text('new content longer', encoding='utf-8')
    run_incremental_scan(root)
    stale = get_document_chunk(file_id)
    assert stale['status'] == 'STALE' and stale['text'] == '' and stored_count(file_id) == 0
    analyze_content.run_content_analysis(root)
    with pytest.raises(ValueError, match='변경'):
        get_document_chunk(file_id, analyzed_at=first['analyzed_at'])
    assert get_document_chunk(file_id)['text'] == 'new content longer'
    moved.unlink()
    run_incremental_scan(root)
    assert stored_count(file_id) == 0
    assert get_document_chunk(file_id) is None


@pytest.mark.parametrize('status', ['SKIPPED', 'FAILED', 'STALE'])
def test_replacing_with_non_extracted_state_clears_chunks(root, status):
    (root / 'note.txt').write_text('old', encoding='utf-8')
    analyze_content.run_content_analysis(root)
    file_id = get_id()
    file_contents.save_file_content(file_id, TextExtractionResult('note.txt', status, text='obsolete'), 'test')
    result = get_document_chunk(file_id)
    assert stored_count(file_id) == 0
    assert result['text'] == '' and result['page'] is None
    assert result['chunk_count'] == 0 and result['segment_count'] == 1


def test_existing_database_is_preserved_and_version_upgrade_builds_citations(root):
    path = root / 'note.txt'
    path.write_text('preserved content', encoding='utf-8')
    analyze_content.run_content_analysis(root)
    file_id = get_id()
    with db.get_connection() as connection:
        connection.execute('DROP TRIGGER invalidate_document_chunks')
        connection.execute('DROP TABLE document_chunks')
        fingerprint = json.loads(analyze_content.source_fingerprint(path, 1024 * 1024))
        fingerprint['extractor_version'] = 4
        connection.execute('UPDATE file_contents SET source_fingerprint=? WHERE file_id=?',
            (json.dumps(fingerprint, sort_keys=True), file_id))
    before = file_contents.get_file_content(file_id)
    missing = get_document_chunk(file_id)
    assert missing['chunk_count'] == 0 and missing['text'] == ''
    assert file_contents.get_file_content(file_id) == before
    assert analyze_content.run_content_analysis(root)['extracted'] == 1
    assert get_document_chunk(file_id)['text'] == 'preserved content'
    assert analyze_content.run_content_analysis(root)['unchanged'] == 1


def test_content_and_chunks_roll_back_together_on_write_failure(root):
    (root / 'note.txt').write_text('original', encoding='utf-8')
    analyze_content.run_content_analysis(root)
    file_id = get_id()
    before = get_document_chunk(file_id)
    with db.get_connection() as connection:
        connection.execute("CREATE TRIGGER fail_chunk BEFORE INSERT ON document_chunks BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='test failure'):
        file_contents.save_file_content(file_id, TextExtractionResult('note.txt', 'EXTRACTED', text='replacement'), 'new')
    assert get_document_chunk(file_id) == before
    with db.get_connection() as connection:
        connection.execute('DELETE FROM file_contents WHERE file_id=?', (file_id,))
    assert stored_count(file_id) == 0


@pytest.mark.parametrize('span', [SourceSpan(-1, 2, 1), SourceSpan(0, 4, 1), SourceSpan(2, 1, 1), SourceSpan(0, 2, 0), SourceSpan(True, 2, 1)])
def test_invalid_page_metadata_is_rejected(span):
    with pytest.raises(ValueError):
        build_chunks(TextExtractionResult('x.pdf', 'EXTRACTED', text='abc', source_spans=(span,)))


@pytest.mark.parametrize('field,value', [('file_id', True), ('file_id', 0), ('file_id', 2**63), ('file_id', '1'), ('segment', -1), ('segment', True), ('segment', 2**63)])
def test_invalid_queries_do_not_create_database(root, field, value):
    arguments = {'file_id': 1, 'segment': 0, field: value}
    with pytest.raises(ValueError):
        get_document_chunk(**arguments)
    assert not db.DB_PATH.exists()


def test_pending_empty_missing_and_out_of_range(root):
    (root / 'note.txt').write_bytes(b'')
    run_incremental_scan(root)
    file_id = get_id()
    assert get_document_chunk(file_id)['status'] == 'PENDING'
    analyze_content.run_content_analysis(root)
    assert get_document_chunk(file_id)['chunk_count'] == 0
    assert get_document_chunk(file_id + 1) is None
    file_contents.save_file_content(file_id, TextExtractionResult('note.txt', 'EXTRACTED', text='hello'), 'new')
    with pytest.raises(ValueError, match='존재하지 않는'):
        get_document_chunk(file_id, 1)


def test_citation_cli_outputs_json_and_rejects_changed_revision(root):
    (root / 'note.txt').write_text('한글 😀', encoding='utf-8')
    analyze_content.run_content_analysis(root)
    file_id = get_id()
    # Override only DB path in the child; production CLI has no user-data side effects here.
    program = "from pathlib import Path; from database import db; db.DB_PATH=Path(__import__('sys').argv.pop(1)); from app.document_chunks import main; main()"
    command = [sys.executable, '-c', program, str(db.DB_PATH), str(file_id)]
    cwd = Path(__file__).resolve().parents[1]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['text'] == '한글 😀'
    result = subprocess.run(command + ['--revision', 'obsolete'], cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert result.returncode == 2
