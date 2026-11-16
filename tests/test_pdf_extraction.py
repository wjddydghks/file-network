import builtins
import json
from pathlib import Path
import subprocess
import sys

import pymupdf
import pytest

from app import analyze_content
from app.incremental_scan import run_incremental_scan
from core.extractors.content import extract_content
from core.extractors.pdf import PAGE_SEPARATOR, extract_pdf
from core.extractors.text import TextExtractionResult
from database import db, file_contents
from database.content_search import search_content


def make_pdf(path, pages=("Hello PDF",), encrypted=False):
    with pymupdf.open() as document:
        for text in pages:
            page = document.new_page()
            if text:
                page.insert_text((72, 72), text, fontname="korea" if not text.isascii() else "helv")
        options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "owner_pw": "owner", "user_pw": "secret"} if encrypted else {}
        path.write_bytes(document.tobytes(deflate=True, **options))
    return path


def add_image_page(document):
    page = document.new_page()
    image = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 10, 10), 0)
    image.clear_with(0)
    page.insert_image(pymupdf.Rect(72, 72, 100, 100), pixmap=image)


def test_pdf_extracts_ordered_pages_and_preserves_original(tmp_path):
    path = make_pdf(tmp_path / "sample.PDF", ("First page", "Second page"))
    original = path.read_bytes()
    result = extract_content(path)
    assert result.status == "EXTRACTED"
    assert result.reason is None
    assert result.text == f"First page{PAGE_SEPARATOR}Second page"
    assert path.read_bytes() == original


def test_pdf_supports_korean_text(tmp_path):
    path = make_pdf(tmp_path / "한글.pdf", ("한글 프로젝트",))
    result = extract_pdf(path, max_bytes=10 * 1024 * 1024)
    assert result.status == "EXTRACTED"
    assert "한글" in result.text
    assert "프로젝트" in result.text


def test_pdf_input_limit_is_inclusive(tmp_path):
    path = make_pdf(tmp_path / "sample.pdf")
    size = path.stat().st_size
    assert extract_pdf(path, max_bytes=size).status == "EXTRACTED"
    assert extract_pdf(path, max_bytes=size - 1).reason == "TOO_LARGE"


def test_pdf_page_and_output_limits(tmp_path):
    path = make_pdf(tmp_path / "sample.pdf", ("first", "second", "third"))
    assert extract_pdf(path, max_pages=2).reason == "PAGE_LIMIT_EXCEEDED"
    result = extract_pdf(path, max_text_chars=5)
    assert result.reason == "TEXT_LIMIT_EXCEEDED"
    assert result.text is None


@pytest.mark.parametrize("argument", ["max_bytes", "max_pages", "max_text_chars"])
def test_pdf_rejects_invalid_limits(tmp_path, argument):
    with pytest.raises(ValueError):
        extract_pdf(tmp_path / "sample.pdf", **{argument: 0})


def test_encrypted_pdf_is_not_opened_without_password(tmp_path):
    path = make_pdf(tmp_path / "secret.pdf", encrypted=True)
    result = extract_pdf(path)
    assert result.status == "SKIPPED"
    assert result.reason == "ENCRYPTED_PDF"
    assert result.text is None


@pytest.mark.parametrize("content", [b"", b"not a PDF", b"%PDF-1.7\ninvalid content"])
def test_invalid_pdf_returns_failure(tmp_path, content):
    path = tmp_path / "broken.pdf"
    path.write_bytes(content)
    result = extract_pdf(path)
    assert result.status == "FAILED"
    assert result.reason == "INVALID_PDF"
    assert result.text is None


def test_empty_pdf_is_distinguished_from_image_only_pdf(tmp_path):
    blank = make_pdf(tmp_path / "blank.pdf", ("",))
    assert extract_pdf(blank).reason == "NO_TEXT"
    path = tmp_path / "image.pdf"
    with pymupdf.open() as document:
        add_image_page(document)
        path.write_bytes(document.tobytes(deflate=True))
    result = extract_pdf(path)
    assert result.status == "SKIPPED"
    assert result.reason == "OCR_REQUIRED"
    assert result.text is None


def test_mixed_pdf_reports_partial_ocr_requirement(tmp_path):
    path = tmp_path / "mixed.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Searchable text")
        add_image_page(document)
        path.write_bytes(document.tobytes(deflate=True))
    result = extract_pdf(path)
    assert result.status == "EXTRACTED"
    assert "Searchable text" in result.text
    assert result.reason == "PARTIAL_OCR_REQUIRED"


def test_pdf_read_errors_and_non_regular_paths(tmp_path, monkeypatch):
    assert extract_pdf(tmp_path / "missing.pdf").reason == "READ_ERROR"
    directory = tmp_path / "directory.pdf"
    directory.mkdir()
    assert extract_pdf(directory).reason == "NOT_REGULAR_FILE"
    path = make_pdf(tmp_path / "denied.pdf")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", denied)
    assert extract_pdf(path).reason == "READ_ERROR"


def test_pdf_missing_dependency_has_explicit_failure(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "sample.pdf")
    original_import = builtins.__import__

    def without_pymupdf(name, *args, **kwargs):
        if name == "pymupdf":
            raise ImportError("missing PyMuPDF")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pymupdf)
    result = extract_pdf(path)
    assert result.status == "FAILED"
    assert result.reason == "DEPENDENCY_MISSING"


@pytest.mark.parametrize("valid", [True, False])
def test_pdf_cli_outputs_json(tmp_path, valid):
    path = make_pdf(tmp_path / "sample.pdf", ("Hello PDF",))
    if not valid:
        path.write_bytes(b"broken PDF")
    result = subprocess.run(
        [sys.executable, "-m", "app.extract_text", str(path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == (0 if valid else 1)
    output = json.loads(result.stdout)
    assert output["status"] == ("EXTRACTED" if valid else "FAILED")
    if valid:
        assert "Hello PDF" in output["text"]
    else:
        assert output["reason"] == "INVALID_PDF"


def test_pdf_analysis_cache_search_and_invalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    path = make_pdf(root / "sample.pdf", ("Original keyword",))
    original = path.read_bytes()
    assert analyze_content.run_content_analysis(root)["extracted"] == 1
    assert path.read_bytes() == original
    assert len(search_content("Original")) == 1
    assert analyze_content.run_content_analysis(root)["unchanged"] == 1
    make_pdf(path, ("Replacement document content",))
    run_incremental_scan(root)
    assert search_content("Original") == []
    assert analyze_content.run_content_analysis(root)["extracted"] == 1
    assert len(search_content("Replacement")) == 1
    path.unlink()
    run_incremental_scan(root)
    assert search_content("Replacement") == []


def test_previously_unsupported_pdf_cache_is_reanalyzed(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    path = make_pdf(root / "sample.pdf")
    run_incremental_scan(root)
    file_contents.initialize_file_contents()
    file_id = db.get_active_files_under(root)[0]["id"]
    previous_fingerprint = json.loads(analyze_content.source_fingerprint(path, 1024 * 1024))
    previous_fingerprint["extractor_version"] = 1
    file_contents.save_file_content(
        file_id, TextExtractionResult(str(path), "SKIPPED", reason="UNSUPPORTED_TYPE"),
        json.dumps(previous_fingerprint, sort_keys=True),
    )
    assert analyze_content.run_content_analysis(root)["extracted"] == 1
    assert file_contents.get_file_content(file_id)["status"] == "EXTRACTED"


def test_pdf_read_is_bounded_and_stream_is_closed(tmp_path, monkeypatch):
    import io

    path = make_pdf(tmp_path / "sample.pdf")

    class BoundedStream(io.BytesIO):
        def read(self, size=-1):
            assert size == 5
            return super().read(size)

    source = BoundedStream(b"123456789")
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: source)
    assert extract_pdf(path, max_bytes=4).reason == "TOO_LARGE"
    assert source.closed
