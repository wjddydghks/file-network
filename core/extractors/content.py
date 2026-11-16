from pathlib import Path

from core.extractors.pdf import extract_pdf
from core.extractors.text import DEFAULT_MAX_BYTES, extract_text


def extract_content(file_path, max_bytes=DEFAULT_MAX_BYTES):
    """확장자에 따라 로컬 추출기로 전달하고 공통 결과 형식을 반환한다."""
    if Path(file_path).suffix.lower() == ".pdf":
        return extract_pdf(file_path, max_bytes=max_bytes)
    return extract_text(file_path, max_bytes=max_bytes)
