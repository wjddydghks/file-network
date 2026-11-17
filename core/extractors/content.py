from pathlib import Path

from core.extractors.office import SUPPORTED_EXTENSIONS, extract_office
from core.extractors.pdf import extract_pdf
from core.extractors.text import DEFAULT_MAX_BYTES, extract_text, validate_text_encoding
from core.operations import check_cancelled


def extract_content(file_path, max_bytes=DEFAULT_MAX_BYTES, text_encoding="auto", is_cancelled=None):
    """확장자에 따라 로컬 추출기로 전달하고 공통 결과 형식을 반환한다."""
    validate_text_encoding(text_encoding)
    check_cancelled(is_cancelled)
    options = {'is_cancelled': is_cancelled} if is_cancelled is not None else {}
    if Path(file_path).suffix.lower() == ".pdf":
        result = extract_pdf(file_path, max_bytes=max_bytes, **options)
    elif Path(file_path).suffix.lower() in SUPPORTED_EXTENSIONS:
        result = extract_office(file_path, max_bytes=max_bytes, **options)
    else:
        result = extract_text(file_path, max_bytes=max_bytes, encoding=text_encoding, **options)
    check_cancelled(is_cancelled)
    return result
