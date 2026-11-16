from pathlib import Path

from core.extractors.office import SUPPORTED_EXTENSIONS, extract_office
from core.extractors.pdf import extract_pdf
from core.extractors.text import DEFAULT_MAX_BYTES, extract_text, validate_text_encoding


def extract_content(file_path, max_bytes=DEFAULT_MAX_BYTES, text_encoding="auto"):
    """확장자에 따라 로컬 추출기로 전달하고 공통 결과 형식을 반환한다."""
    validate_text_encoding(text_encoding)
    if Path(file_path).suffix.lower() == ".pdf":
        return extract_pdf(file_path, max_bytes=max_bytes)
    if Path(file_path).suffix.lower() in SUPPORTED_EXTENSIONS:
        return extract_office(file_path, max_bytes=max_bytes)
    return extract_text(file_path, max_bytes=max_bytes, encoding=text_encoding)
