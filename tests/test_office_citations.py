import json
from pathlib import Path
import subprocess
import sys

import pytest
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from app import analyze_content
from app.incremental_scan import run_incremental_scan
from core.citations import build_chunks
from core.extractors.content import extract_content
from core.extractors.text import SourceSpan, TextExtractionResult
from database import db, file_contents
from database.content_search import search_content
from database.document_chunks import get_document_chunk
from tests.office_fixtures import make_office
from tests.test_pdf_extraction import make_pdf


def make_sections(path, sections):
    if path.suffix == '.xlsx':
        workbook = Workbook()
        workbook.remove(workbook.active)
        for title, text in sections:
            sheet = workbook.create_sheet(title)
            if text:
                sheet['A1'] = text
        workbook.save(path)
        workbook.close()
    else:
        presentation = Presentation()
        for _, text in sections:
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            if text:
                slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(4)).text = text
        presentation.save(path)
    return path


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    folder = tmp_path / 'scan'
    folder.mkdir()
    return folder


@pytest.mark.parametrize('extension,kind,labels', [('.xlsx', 'sheet', ['자료', "<b>한글'&>"]), ('.pptx', 'slide', ['2', '4'])])
def test_actual_sections_empty_gaps_and_spoofed_headers(root, extension, kind, labels):
    path = make_sections(root / ('note' + extension), [('빈 시작', ''), ('자료', 'keyword 시트: fake\n슬라이드: 999'), ('빈 중간', ''), ("<b>한글'&>", 'last 한글 😀')])
    original = path.read_bytes()
    result = extract_content(path)
    assert result.status == 'EXTRACTED'
    assert [s.label for s in result.source_spans] == labels
    assert all(s.kind == kind and s.page is None for s in result.source_spans)
    assert analyze_content.run_content_analysis(root)['extracted'] == 1
    file_id = search_content('keyword')[0]['file_id']
    first = get_document_chunk(file_id)
    chunks = [get_document_chunk(file_id, i) for i in range(first['chunk_count'])]
    assert [c['source_label'] for c in chunks] == labels
    assert all(c['source_kind'] == kind and c['page'] is None for c in chunks)
    assert all(result.text[c['start_char']:c['end_char']] == c['text'] for c in chunks)
    assert '슬라이드: 999' in chunks[0]['text']
    assert path.read_bytes() == original
    path.unlink()
    assert get_document_chunk(file_id)['source_label'] == labels[0]  # Snapshot, not source access.


@pytest.mark.parametrize('extension,kind', [('.xlsx', 'sheet'), ('.pptx', 'slide')])
def test_long_sections_split_without_crossing_original_boundary(root, extension, kind):
    path = make_sections(root / ('long' + extension), [('첫째', 'x' * 4500), ('둘째', 'secondkeyword')])
    analyze_content.run_content_analysis(root)
    file_id = search_content('secondkeyword')[0]['file_id']
    first = get_document_chunk(file_id)
    assert first['chunk_count'] == 4
    chunks = [get_document_chunk(file_id, i) for i in range(4)]
    assert all(c['source_label'] == chunks[0]['source_label'] for c in chunks[:3])
    assert chunks[3]['source_label'] != chunks[0]['source_label']
    assert all(len(c['text']) <= 2000 and c['source_kind'] == kind for c in chunks)
    assert 'secondkeyword' not in ''.join(c['text'] for c in chunks[:3])
    assert 'secondkeyword' in chunks[3]['text']


@pytest.mark.parametrize('extension', ['.xlsx', '.pptx'])
def test_office_location_lifecycle_and_revision(root, extension):
    path = make_office(root / ('old' + extension), text='originalkeyword')
    analyze_content.run_content_analysis(root)
    file_id = search_content('originalkeyword')[0]['file_id']
    before = get_document_chunk(file_id)
    moved = path.rename(root / ('moved' + extension))
    assert analyze_content.run_content_analysis(root)['unchanged'] == 1
    assert get_document_chunk(file_id)['analyzed_at'] == before['analyzed_at']
    make_office(moved, text='replacementkeyword and longer content')
    run_incremental_scan(root)
    assert get_document_chunk(file_id)['chunk_count'] == 0
    assert get_document_chunk(file_id)['source_label'] is None
    analyze_content.run_content_analysis(root)
    with pytest.raises(ValueError, match='변경'):
        get_document_chunk(file_id, analyzed_at=before['analyzed_at'])
    assert 'replacementkeyword' in get_document_chunk(file_id)['text']
    moved.unlink()
    run_incremental_scan(root)
    assert get_document_chunk(file_id) is None


def test_old_pdf_and_text_chunk_schema_migrates_without_reanalysis(root):
    make_pdf(root / 'paper.pdf', ('page one', '', 'page three'))
    (root / 'note.txt').write_text('plain text', encoding='utf-8')
    analyze_content.run_content_analysis(root)
    ids = {r['name']: r['id'] for r in db.get_active_files_under(root)}
    before = {name: get_document_chunk(file_id) for name, file_id in ids.items()}
    with db.get_connection() as connection:
        connection.execute('ALTER TABLE document_chunks DROP COLUMN source_kind')
        connection.execute('ALTER TABLE document_chunks DROP COLUMN source_label')
    assert {name: get_document_chunk(file_id) for name, file_id in ids.items()} == before
    assert get_document_chunk(ids['paper.pdf'], 1)['page'] == 3


def test_previous_office_cache_preserved_then_reanalyzed_to_add_locations(root):
    path = make_office(root / 'book.xlsx', text='keyword')
    analyze_content.run_content_analysis(root)
    file_id = search_content('keyword')[0]['file_id']
    fingerprint = json.loads(analyze_content.source_fingerprint(path, 1024 * 1024))
    fingerprint['extractor_version'] = 5
    result = extract_content(path)
    file_contents.save_file_content(file_id, result, json.dumps(fingerprint, sort_keys=True))
    before = file_contents.get_file_content(file_id)
    with db.get_connection() as connection:
        connection.execute('ALTER TABLE document_chunks DROP COLUMN source_kind')
        connection.execute('ALTER TABLE document_chunks DROP COLUMN source_label')
    assert get_document_chunk(file_id)['source_kind'] == 'text'
    assert file_contents.get_file_content(file_id) == before
    assert analyze_content.run_content_analysis(root)['extracted'] == 1
    assert get_document_chunk(file_id)['source_kind'] == 'sheet'
    assert get_document_chunk(file_id)['source_label'] == '자료'


@pytest.mark.parametrize('span', [SourceSpan(0, 3, 1, 'sheet', 'a'), SourceSpan(0, 3, None, 'sheet', ''), SourceSpan(0, 3, None, 'slide', '0'), SourceSpan(0, 3, None, 'slide', 'x'), SourceSpan(0, 3, None, 'invalid', 'a')])
def test_invalid_structured_locations_rejected(span):
    with pytest.raises(ValueError):
        build_chunks(TextExtractionResult('file', 'EXTRACTED', text='abc', source_spans=(span,)))


def test_word_keeps_character_positions_without_guessed_pages(root):
    path = make_office(root / 'word.docx', text='시트: fake\n슬라이드: 5')
    analyze_content.run_content_analysis(root)
    file_id = db.get_active_files_under(root)[0]['id']
    chunk = get_document_chunk(file_id)
    assert chunk['source_kind'] == 'text' and chunk['source_label'] is None and chunk['page'] is None


def test_cli_returns_sheet_location_json(root):
    make_office(root / 'sheet.xlsx', text='keyword')
    analyze_content.run_content_analysis(root)
    file_id = db.get_active_files_under(root)[0]['id']
    program = "from pathlib import Path; from database import db; db.DB_PATH=Path(__import__('sys').argv.pop(1)); from app.document_chunks import main; main()"
    result = subprocess.run([sys.executable, '-c', program, str(db.DB_PATH), str(file_id)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0
    row = json.loads(result.stdout)
    assert row['source_kind'] == 'sheet' and row['source_label'] == '자료' and row['page'] is None
