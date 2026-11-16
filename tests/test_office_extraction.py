import builtins
from datetime import date
from io import BytesIO
import json
from pathlib import Path
import struct
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches
import pytest

from app import analyze_content
from app.incremental_scan import run_incremental_scan
from core.extractors import office
from core.extractors.content import extract_content
from core.extractors.office import extract_office
from core.extractors.text import TextExtractionResult
from database import db, file_contents
from database.content_search import search_content
from tests.office_fixtures import make_office


EXTENSIONS = (".docx", ".xlsx", ".pptx")


@pytest.mark.parametrize("extension", EXTENSIONS)
def test_office_extracts_unicode_and_preserves_original(tmp_path, extension):
    path = make_office(tmp_path / ("문서" + extension.upper()))
    original = path.read_bytes()
    result = extract_content(path)
    assert result.status == "EXTRACTED"
    assert "Office keyword 한글" in result.text
    assert path.read_bytes() == original


def test_docx_preserves_paragraph_table_and_nested_table_order(tmp_path):
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_paragraph("앞부분")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "표내용"
    nested = table.cell(0, 1).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "중첩표"
    document.add_paragraph("뒷부분")
    document.save(path)
    result = extract_office(path)
    assert result.status == "EXTRACTED"
    assert result.text.index("앞부분") < result.text.index("표내용") < result.text.index("중첩표") < result.text.index("뒷부분")


def test_xlsx_includes_sheets_values_dates_and_unevaluated_formulas(tmp_path):
    path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "기본"
    sheet.append(["한글", 42, date(2026, 11, 17)])
    sheet.append(["=SUM(B1,1)", "='https://example.invalid/[book.xlsx]Sheet1'!A1"])
    workbook.create_sheet("보조")["A1"] = "second"
    workbook.save(path)
    workbook.close()
    result = extract_office(path)
    assert result.status == "EXTRACTED"
    for value in ("기본", "한글", "42", "2026-11-17", "=SUM(B1,1)", "https://example.invalid/", "보조", "second"):
        assert value in result.text
    assert result.text.index("기본") < result.text.index("보조")


def test_pptx_includes_slides_tables_and_grouped_text_but_not_notes(tmp_path):
    path = tmp_path / "sample.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = "첫 슬라이드"
    table = slide.shapes.add_table(1, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "표내용"
    group = slide.shapes.add_group_shape()
    group.shapes.add_textbox(Inches(1), Inches(4), Inches(4), Inches(1)).text = "그룹텍스트"
    slide.notes_slide.notes_text_frame.text = "노트전용문구"
    presentation.slides.add_slide(presentation.slide_layouts[6]).shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = "두번째"
    presentation.save(path)
    result = extract_office(path)
    assert result.status == "EXTRACTED"
    for value in ("슬라이드: 1", "첫 슬라이드", "표내용", "그룹텍스트", "슬라이드: 2", "두번째"):
        assert value in result.text
    assert "노트전용문구" not in result.text


@pytest.mark.parametrize("extension", EXTENSIONS)
def test_office_empty_and_invalid_documents_have_clear_reasons(tmp_path, extension):
    path = make_office(tmp_path / ("sample" + extension), blank=True)
    assert extract_office(path).reason == "NO_TEXT"
    path.write_bytes(b"not an Office document")
    result = extract_office(path)
    assert result.status == "FAILED"
    assert result.reason == "INVALID_OFFICE"
    assert result.text is None


@pytest.mark.parametrize("extension", EXTENSIONS)
def test_office_input_and_output_limits(tmp_path, extension):
    path = make_office(tmp_path / ("sample" + extension))
    size = path.stat().st_size
    assert extract_office(path, max_bytes=size).status == "EXTRACTED"
    assert extract_office(path, max_bytes=size - 1).reason == "TOO_LARGE"
    assert extract_office(path, max_text_chars=2).reason == "TEXT_LIMIT_EXCEEDED"
    assert extract_office(path, max_unpacked_bytes=1).reason == "ARCHIVE_SIZE_LIMIT_EXCEEDED"
    assert extract_office(path, max_members=1).reason == "ARCHIVE_MEMBER_LIMIT_EXCEEDED"


def test_xlsx_cell_and_pptx_slide_limits(tmp_path):
    path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    workbook.active["B2"] = "value"
    workbook.save(path)
    workbook.close()
    assert extract_office(path, max_cells=1).reason == "CELL_LIMIT_EXCEEDED"
    path = tmp_path / "sample.pptx"
    presentation = Presentation()
    for _ in range(2):
        presentation.slides.add_slide(presentation.slide_layouts[6])
    presentation.save(path)
    assert extract_office(path, max_slides=1).reason == "SLIDE_LIMIT_EXCEEDED"


def test_docx_nesting_limit(tmp_path, monkeypatch):
    path = tmp_path / "nested.docx"
    document = Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.add_table(rows=1, cols=1).cell(0, 0).text = "nested"
    document.save(path)
    monkeypatch.setattr(office, "MAX_NESTING", 1)
    assert extract_office(path).reason == "STRUCTURE_LIMIT_EXCEEDED"


def test_office_archive_guards_and_container_mismatch(tmp_path):
    path = tmp_path / "archive.docx"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "A" * 10000)
    assert extract_office(path, max_unpacked_bytes=100).reason == "ARCHIVE_SIZE_LIMIT_EXCEEDED"
    with ZipFile(path, "w") as archive:
        archive.writestr("wrong.xml", "data")
    assert extract_office(path).reason == "INVALID_OFFICE"
    path.write_bytes(office.OLE_HEADER + b"encrypted or legacy container")
    assert extract_office(path).reason == "ENCRYPTED_OR_LEGACY_OFFICE"


def test_encrypted_zip_marker_is_not_parsed(tmp_path):
    path = tmp_path / "encrypted.docx"
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "data")
    content = bytearray(path.read_bytes())
    local = content.index(b"PK\x03\x04")
    central = content.index(b"PK\x01\x02")
    struct.pack_into("<H", content, local + 6, 1)
    struct.pack_into("<H", content, central + 8, 1)
    path.write_bytes(content)
    assert extract_office(path).reason == "ENCRYPTED_OFFICE"


def test_office_reads_are_bounded_and_streams_close(tmp_path, monkeypatch):
    path = make_office(tmp_path / "sample.docx")

    class BoundedStream(BytesIO):
        def read(self, size=-1):
            assert size == 5
            return super().read(size)

    stream = BoundedStream(b"123456789")
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: stream)
    assert extract_office(path, max_bytes=4).reason == "TOO_LARGE"
    assert stream.closed


def test_office_read_errors_and_unsupported_paths(tmp_path, monkeypatch):
    assert extract_office(tmp_path / "missing.docx").reason == "READ_ERROR"
    path = tmp_path / "directory.xlsx"
    path.mkdir()
    assert extract_office(path).reason == "NOT_REGULAR_FILE"
    assert extract_office(tmp_path / "legacy.doc").reason == "UNSUPPORTED_TYPE"
    path = make_office(tmp_path / "sample.docx")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", denied)
    assert extract_office(path).reason == "READ_ERROR"


@pytest.mark.parametrize("extension, package", [(".docx", "docx"), (".xlsx", "openpyxl"), (".pptx", "pptx")])
def test_missing_office_dependency_is_reported(tmp_path, monkeypatch, extension, package):
    path = make_office(tmp_path / ("sample" + extension))
    original = builtins.__import__

    def without_package(name, *args, **kwargs):
        if name == package:
            raise ImportError("missing dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_package)
    assert extract_office(path).reason == "DEPENDENCY_MISSING"


@pytest.mark.parametrize("option", ["max_bytes", "max_members", "max_unpacked_bytes", "max_text_chars", "max_cells", "max_slides"])
def test_office_invalid_limits(tmp_path, option):
    with pytest.raises(ValueError):
        extract_office(tmp_path / "sample.docx", **{option: 0})


@pytest.mark.parametrize("extension", EXTENSIONS)
def test_office_cli_json_output(tmp_path, extension):
    path = make_office(tmp_path / ("sample" + extension))
    result = subprocess.run(
        [sys.executable, "-m", "app.extract_text", str(path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["status"] == "EXTRACTED"
    assert "한글" in output["text"]


def test_office_batch_cache_search_failure_isolation_and_invalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    for extension in EXTENSIONS:
        make_office(root / ("sample" + extension))
    (root / "broken.docx").write_bytes(b"broken")
    (root / "legacy.doc").write_bytes(b"legacy")
    counts = analyze_content.run_content_analysis(root)
    assert counts == {"extracted": 3, "skipped": 1, "failed": 1, "unchanged": 0}
    assert len(search_content("keyword")) == 3
    counts = analyze_content.run_content_analysis(root)
    assert counts["unchanged"] == 4 and counts["failed"] == 1
    path = make_office(root / "sample.docx", "Replacement longer document")
    run_incremental_scan(root)
    assert len(search_content("keyword")) == 2
    assert analyze_content.run_content_analysis(root)["extracted"] == 1
    assert len(search_content("Replacement")) == 1
    path.unlink()
    run_incremental_scan(root)
    assert search_content("Replacement") == []


def test_previously_unsupported_office_results_are_reanalyzed(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    for extension in EXTENSIONS:
        make_office(root / ("sample" + extension))
    run_incremental_scan(root)
    file_contents.initialize_file_contents()
    for file in db.get_active_files_under(root):
        path = Path(file["path"])
        fingerprint = json.loads(analyze_content.source_fingerprint(path, 1024 * 1024))
        fingerprint["extractor_version"] = 2
        file_contents.save_file_content(
            file["id"], TextExtractionResult(str(path), "SKIPPED", reason="UNSUPPORTED_TYPE"),
            json.dumps(fingerprint, sort_keys=True),
        )
    assert analyze_content.run_content_analysis(root)["extracted"] == 3


def test_xlsx_underspecified_dimensions_do_not_hide_cells(tmp_path):
    import re

    path = tmp_path / "dimensions.xlsx"
    workbook = Workbook()
    workbook.active["B2"] = "visiblekeyword"
    workbook.save(path)
    workbook.close()
    original = path.read_bytes()
    target = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(target, "w") as output:
        for member in source.infolist():
            content = source.read(member.filename)
            if member.filename == "xl/worksheets/sheet1.xml":
                content = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:A1"', content)
            output.writestr(member, content)
    path.write_bytes(target.getvalue())
    result = extract_office(path)
    assert result.status == "EXTRACTED"
    assert "visiblekeyword" in result.text
