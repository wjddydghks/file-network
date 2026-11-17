from pathlib import Path
from threading import Event

import pymupdf
import pytest
from docx import Document
from docx.text.paragraph import Paragraph
from openpyxl import Workbook
from openpyxl.worksheet._read_only import ReadOnlyWorksheet
from pptx import Presentation
from pptx.text.text import _Paragraph as SlideParagraph
from pptx.util import Inches

from app import analyze_content
from core.extractors.content import extract_content
from core.extractors import office
from core.extractors.text import TextExtractionResult
from core.operations import OperationCancelled
from database import db, file_contents
from tests.test_pdf_extraction import make_pdf


@pytest.mark.parametrize('extension', ['.txt', '.pdf', '.docx', '.xlsx', '.pptx'])
def test_precancel_never_opens_the_source(tmp_path, monkeypatch, extension):
    def forbidden(*args, **kwargs):
        raise AssertionError('Cancelled extraction must not open files')
    monkeypatch.setattr(Path, 'open', forbidden)
    with pytest.raises(OperationCancelled):
        extract_content(tmp_path / ('file' + extension), is_cancelled=lambda: True)


def test_pdf_stops_after_current_page_and_closes_document(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / 'note.pdf', ('first', 'second', 'third'))
    stopped = Event()
    calls = []
    documents = []
    actual_open, actual_text = pymupdf.open, pymupdf.Page.get_text
    def opened(*args, **kwargs):
        document = actual_open(*args, **kwargs)
        documents.append(document)
        return document
    def text(page, *args, **kwargs):
        calls.append(page.number)
        result = actual_text(page, *args, **kwargs)
        stopped.set()
        return result
    monkeypatch.setattr(pymupdf, 'open', opened)
    monkeypatch.setattr(pymupdf.Page, 'get_text', text)
    with pytest.raises(OperationCancelled):
        extract_content(path, is_cancelled=stopped.is_set)
    assert calls == [0] and documents[0].is_closed
    path.unlink()  # No live source handle, including on Windows.


def test_docx_stops_between_paragraphs_without_failed_result(tmp_path, monkeypatch):
    path = tmp_path / 'note.docx'
    document = Document()
    for text in ('first', 'second', 'third'):
        document.add_paragraph(text)
    document.save(path)
    stopped = Event()
    calls = []
    original = Paragraph.text
    def text(paragraph):
        result = original.fget(paragraph)
        calls.append(result)
        stopped.set()
        return result
    monkeypatch.setattr(Paragraph, 'text', property(text, original.fset))
    with pytest.raises(OperationCancelled):
        extract_content(path, is_cancelled=stopped.is_set)
    assert calls == ['first']
    path.unlink()


def test_xlsx_stops_between_rows_and_closes_workbook(tmp_path, monkeypatch):
    path = tmp_path / 'note.xlsx'
    workbook = Workbook()
    for text in ('first', 'second', 'third'):
        workbook.active.append([text])
    workbook.save(path)
    workbook.close()
    import openpyxl
    original_load, original_rows = openpyxl.load_workbook, ReadOnlyWorksheet.iter_rows
    stopped = Event()
    calls, closed = [], []
    def load(*args, **kwargs):
        book = original_load(*args, **kwargs)
        original_close = book.close
        def close():
            closed.append(True)
            original_close()
        book.close = close
        return book
    def rows(sheet, *args, **kwargs):
        for row in original_rows(sheet, *args, **kwargs):
            calls.append(row)
            stopped.set()
            yield row
    monkeypatch.setattr(openpyxl, 'load_workbook', load)
    monkeypatch.setattr(ReadOnlyWorksheet, 'iter_rows', rows)
    with pytest.raises(OperationCancelled):
        extract_content(path, is_cancelled=stopped.is_set)
    assert calls == [('first',)] and closed == [True]
    path.unlink()


def test_pptx_stops_between_paragraphs_and_slides(tmp_path, monkeypatch):
    path = tmp_path / 'note.pptx'
    presentation = Presentation()
    for text in ('first', 'second', 'third'):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = text
    presentation.save(path)
    stopped = Event()
    calls = []
    original = SlideParagraph.text
    def text(paragraph):
        result = original.fget(paragraph)
        calls.append(result)
        stopped.set()
        return result
    monkeypatch.setattr(SlideParagraph, 'text', property(text, original.fset))
    with pytest.raises(OperationCancelled):
        extract_content(path, is_cancelled=stopped.is_set)
    assert calls == ['first']
    path.unlink()


def test_cancelled_document_is_not_saved_and_next_run_retries(tmp_path, monkeypatch):
    root = tmp_path / 'scan'
    root.mkdir()
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    make_pdf(root / 'note.pdf', ('first', 'second', 'third'))
    stopped = Event()
    actual = pymupdf.Page.get_text
    def text(page, *args, **kwargs):
        result = actual(page, *args, **kwargs)
        stopped.set()
        return result
    monkeypatch.setattr(pymupdf.Page, 'get_text', text)
    with pytest.raises(OperationCancelled):
        analyze_content.run_content_analysis(root, is_cancelled=stopped.is_set)
    identity = db.get_active_files_under(root)[0]['id']
    assert file_contents.get_file_content(identity) is None
    with db.get_connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM document_chunks').fetchone()[0] == 0
    monkeypatch.setattr(pymupdf.Page, 'get_text', actual)
    assert analyze_content.run_content_analysis(root)['extracted'] == 1
    assert 'third' in file_contents.get_file_content(identity)['text']


def test_cancel_after_extractor_returns_prevents_database_write(tmp_path, monkeypatch):
    root = tmp_path / 'scan'
    root.mkdir()
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    (root / 'note.txt').write_text('document', encoding='utf-8')
    stopped = Event()
    def extract(path, **options):
        stopped.set()
        return TextExtractionResult(str(path), 'EXTRACTED', text='must not save')
    monkeypatch.setattr(analyze_content, 'extract_content', extract)
    with pytest.raises(OperationCancelled):
        analyze_content.run_content_analysis(root, is_cancelled=stopped.is_set)
    identity = db.get_active_files_under(root)[0]['id']
    assert file_contents.get_file_content(identity) is None


def test_output_joins_stop_consuming_generator_after_cancel():
    stopped = Event()
    visited = []
    def parts():
        for word in ('first', 'second', 'third'):
            visited.append(word)
            stopped.set()
            yield word
    with pytest.raises(OperationCancelled):
        office._join_limited(parts(), 100, is_cancelled=stopped.is_set)
    assert visited == ['first']


@pytest.mark.parametrize('extension,reason', [('.txt', 'TOO_LARGE'), ('.pdf', 'PAGE_LIMIT_EXCEEDED'), ('.docx', 'TEXT_LIMIT_EXCEEDED'), ('.xlsx', 'CELL_LIMIT_EXCEEDED'), ('.pptx', 'SLIDE_LIMIT_EXCEEDED')])
def test_default_limits_skip_large_individual_documents(tmp_path, extension, reason):
    path = tmp_path / ('large' + extension)
    if extension == '.txt':
        path.write_bytes(b'x' * (1024 * 1024 + 1))
    elif extension == '.pdf':
        make_pdf(path, ('',) * 201)
    elif extension == '.docx':
        document = Document()
        document.add_paragraph('x' * 1_000_001)
        document.save(path)
    elif extension == '.xlsx':
        workbook = Workbook()
        workbook.active['A100001'] = 'last row'
        workbook.save(path)
        workbook.close()
    else:
        presentation = Presentation()
        for _ in range(201):
            presentation.slides.add_slide(presentation.slide_layouts[6])
        presentation.save(path)
    result = extract_content(path)
    assert result.status == 'SKIPPED' and result.reason == reason
    assert result.text is None and not result.source_spans
