from dataclasses import dataclass
from pathlib import Path
import stat

from core.scanner.scanner import classify_file


DEFAULT_MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class TextExtractionResult:
    path: str
    status: str
    text: str | None = None
    reason: str | None = None
    error: str | None = None


def extract_text(file_path, max_bytes=DEFAULT_MAX_BYTES):
    """TEXT/CODE 파일을 제한된 크기로 읽고 UTF-8 내용을 반환한다."""
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes는 양의 정수여야 합니다.")

    path = Path(file_path).expanduser()
    result_path = str(path)

    if classify_file(path.suffix) not in {"TEXT", "CODE"}:
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

    if b"\x00" in content:
        return TextExtractionResult(result_path, "SKIPPED", reason="BINARY_CONTENT")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        return TextExtractionResult(result_path, "FAILED", reason="INVALID_ENCODING", error=str(error))

    return TextExtractionResult(result_path, "EXTRACTED", text=text)
