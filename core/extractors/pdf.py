from pathlib import Path
import stat

from core.extractors.text import DEFAULT_MAX_BYTES, TextExtractionResult


DEFAULT_MAX_PAGES = 200
DEFAULT_MAX_TEXT_CHARS = 1_000_000
PAGE_SEPARATOR = "\n\f\n"


def extract_pdf(
    file_path, max_bytes=DEFAULT_MAX_BYTES,
    max_pages=DEFAULT_MAX_PAGES, max_text_chars=DEFAULT_MAX_TEXT_CHARS,
):
    """PDF 텍스트 레이어를 로컬에서 추출한다. OCR·암호 해제는 수행하지 않는다."""
    for name, value in (("max_bytes", max_bytes), ("max_pages", max_pages), ("max_text_chars", max_text_chars)):
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name}는 양의 정수여야 합니다.")

    path = Path(file_path).expanduser()
    result_path = str(path)
    if path.suffix.lower() != ".pdf":
        return TextExtractionResult(result_path, "SKIPPED", reason="UNSUPPORTED_TYPE")

    try:
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            return TextExtractionResult(result_path, "SKIPPED", reason="NOT_REGULAR_FILE")
        with path.open("rb") as source:
            content = source.read(max_bytes + 1)
    except OSError as error:
        return TextExtractionResult(result_path, "FAILED", reason="READ_ERROR", error=str(error))
    if len(content) > max_bytes:
        return TextExtractionResult(result_path, "SKIPPED", reason="TOO_LARGE")

    try:
        import pymupdf
    except ImportError as error:
        return TextExtractionResult(result_path, "FAILED", reason="DEPENDENCY_MISSING", error=str(error))

    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if not document.is_pdf:
                return TextExtractionResult(result_path, "FAILED", reason="INVALID_PDF")
            if document.needs_pass:
                return TextExtractionResult(result_path, "SKIPPED", reason="ENCRYPTED_PDF")
            if document.page_count > max_pages:
                return TextExtractionResult(result_path, "SKIPPED", reason="PAGE_LIMIT_EXCEEDED")

            pages = []
            character_count = 0
            requires_ocr = False
            for page in document:
                text = page.get_text("text", sort=True).rstrip("\r\n")
                if not text.strip() and page.get_image_info():
                    requires_ocr = True
                character_count += len(text) + (len(PAGE_SEPARATOR) if pages else 0)
                if character_count > max_text_chars:
                    return TextExtractionResult(result_path, "SKIPPED", reason="TEXT_LIMIT_EXCEEDED")
                pages.append(text)

            text = PAGE_SEPARATOR.join(pages)
            if not text.strip():
                reason = "OCR_REQUIRED" if requires_ocr else "NO_TEXT"
                return TextExtractionResult(result_path, "SKIPPED", reason=reason)
            return TextExtractionResult(
                result_path, "EXTRACTED", text=text,
                reason="PARTIAL_OCR_REQUIRED" if requires_ocr else None,
            )
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError, ValueError) as error:
        return TextExtractionResult(result_path, "FAILED", reason="INVALID_PDF", error=str(error))
