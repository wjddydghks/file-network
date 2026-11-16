from dataclasses import dataclass
import codecs
from pathlib import Path
import stat

from core.scanner.scanner import classify_file


DEFAULT_MAX_BYTES = 1024 * 1024
TEXT_ENCODINGS = ("auto", "utf-8", "cp949")


def validate_text_encoding(encoding):
    if encoding not in TEXT_ENCODINGS:
        raise ValueError("텍스트 인코딩은 auto, utf-8, cp949 중에서 선택하세요.")


@dataclass(frozen=True)
class TextExtractionResult:
    path: str
    status: str
    text: str | None = None
    reason: str | None = None
    error: str | None = None


def extract_text(file_path, max_bytes=DEFAULT_MAX_BYTES, encoding="auto"):
    """TEXT/CODE를 엄격하게 디코딩하며 auto는 BOM 또는 UTF-8만 사용한다."""
    validate_text_encoding(encoding)
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

    codec = "cp949" if encoding == "cp949" else "utf-8-sig"
    if encoding == "auto":
        # UTF-32 LE의 BOM은 UTF-16 LE로 시작하므로 긴 BOM부터 확인한다.
        for bom, bom_codec in (
            (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
            (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
        ):
            if content.startswith(bom):
                codec = bom_codec
                break

    if codec in {"utf-8-sig", "cp949"} and b"\x00" in content:
        return TextExtractionResult(result_path, "SKIPPED", reason="BINARY_CONTENT")

    try:
        text = content.decode(codec)
    except UnicodeDecodeError as error:
        return TextExtractionResult(result_path, "FAILED", reason="INVALID_ENCODING", error=str(error))

    if "\x00" in text:
        return TextExtractionResult(result_path, "SKIPPED", reason="BINARY_CONTENT")

    return TextExtractionResult(result_path, "EXTRACTED", text=text)
