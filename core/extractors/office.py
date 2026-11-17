"""DOCX·XLSX·PPTX의 로컬 본문 추출. 문서 코드와 외부 링크를 실행하지 않는다."""
from io import BytesIO
from pathlib import Path
import stat
from zipfile import BadZipFile, ZipFile

from core.operations import check_cancelled
from core.extractors.text import DEFAULT_MAX_BYTES, SourceSpan, TextExtractionResult


SUPPORTED_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
DEFAULT_MAX_UNPACKED_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_MEMBERS = 10_000
DEFAULT_MAX_TEXT_CHARS = 1_000_000
DEFAULT_MAX_CELLS = 100_000
DEFAULT_MAX_SLIDES = 200
MAX_NESTING = 32
OLE_HEADER = bytes.fromhex("d0cf11e0a1b11ae1")


class _LimitReached(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _join_limited(parts, max_chars, separator="\n", is_cancelled=None):
    collected = []
    length = 0
    check_cancelled(is_cancelled)
    for part in parts:
        check_cancelled(is_cancelled)
        if not part.strip():
            continue
        length += len(part) + (len(separator) if collected else 0)
        if length > max_chars:
            raise _LimitReached("TEXT_LIMIT_EXCEEDED")
        collected.append(part)
    check_cancelled(is_cancelled)
    return separator.join(collected)


def _docx_parts(container, max_chars, depth=0, is_cancelled=None):
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    if depth > MAX_NESTING:
        raise _LimitReached("STRUCTURE_LIMIT_EXCEEDED")
    check_cancelled(is_cancelled)
    for block in container.iter_inner_content():
        check_cancelled(is_cancelled)
        if isinstance(block, Paragraph):
            yield block.text
        elif isinstance(block, Table):
            for row in block.rows:
                check_cancelled(is_cancelled)
                yield _join_limited(
                    (_join_limited(_docx_parts(cell, max_chars, depth + 1, is_cancelled), max_chars, is_cancelled=is_cancelled) for cell in row.cells),
                    max_chars, separator="\t", is_cancelled=is_cancelled,
                )


def _read_docx(content, max_chars, is_cancelled=None):
    from docx import Document

    check_cancelled(is_cancelled)
    document = Document(BytesIO(content))
    return _join_limited(
        _docx_parts(document, max_chars, is_cancelled=is_cancelled),
        max_chars, is_cancelled=is_cancelled,
    )


def _join_sections(sections, max_chars, kind, is_cancelled=None):
    collected = []
    spans = []
    length = 0
    check_cancelled(is_cancelled)
    for label, text in sections:
        check_cancelled(is_cancelled)
        if not text:
            continue
        start = length + (1 if collected else 0)
        length = start + len(text)
        if length > max_chars:
            raise _LimitReached("TEXT_LIMIT_EXCEEDED")
        collected.append(text)
        spans.append(SourceSpan(start, length, None, kind=kind, label=label))
    return "\n".join(collected), tuple(spans)


def _read_xlsx(content, max_chars, max_cells, is_cancelled=None):
    from openpyxl import load_workbook

    check_cancelled(is_cancelled)
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    try:
        check_cancelled(is_cancelled)
        def sections():
            visited = 0
            declared = 0
            for sheet in workbook.worksheets:
                check_cancelled(is_cancelled)
                declared += (sheet.max_row or 0) * (sheet.max_column or 0)
                if declared > max_cells:
                    raise _LimitReached("CELL_LIMIT_EXCEEDED")
                sheet.reset_dimensions()
                def rows():
                    nonlocal visited
                    for row in sheet.iter_rows(values_only=True):
                        check_cancelled(is_cancelled)
                        visited += max(len(row), 1)
                        if visited > max_cells:
                            raise _LimitReached("CELL_LIMIT_EXCEEDED")
                        yield _join_limited(
                            (str(value) if value is not None else "" for value in row),
                            max_chars, separator="\t", is_cancelled=is_cancelled,
                        )
                body = _join_limited(rows(), max_chars, is_cancelled=is_cancelled)
                if body:
                    yield sheet.title, f"시트: {sheet.title}\n{body}"
        return _join_sections(sections(), max_chars, 'sheet', is_cancelled=is_cancelled)
    finally:
        workbook.close()


def _pptx_shape_parts(shapes, max_chars, depth=0, is_cancelled=None):
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    if depth > MAX_NESTING:
        raise _LimitReached("STRUCTURE_LIMIT_EXCEEDED")
    check_cancelled(is_cancelled)
    for shape in shapes:
        check_cancelled(is_cancelled)
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _pptx_shape_parts(shape.shapes, max_chars, depth + 1, is_cancelled)
        elif shape.has_text_frame:
            for paragraph in shape.text_frame.paragraphs:
                check_cancelled(is_cancelled)
                yield paragraph.text
        elif shape.has_table:
            for row in shape.table.rows:
                check_cancelled(is_cancelled)
                yield _join_limited(
                    (cell.text for cell in row.cells if not cell.is_spanned),
                    max_chars, separator="\t", is_cancelled=is_cancelled,
                )


def _read_pptx(content, max_chars, max_slides, is_cancelled=None):
    from pptx import Presentation

    check_cancelled(is_cancelled)
    presentation = Presentation(BytesIO(content))
    check_cancelled(is_cancelled)
    if len(presentation.slides) > max_slides:
        raise _LimitReached("SLIDE_LIMIT_EXCEEDED")

    def sections():
        for number, slide in enumerate(presentation.slides, 1):
            check_cancelled(is_cancelled)
            body = _join_limited(
                _pptx_shape_parts(slide.shapes, max_chars, is_cancelled=is_cancelled),
                max_chars, is_cancelled=is_cancelled,
            )
            if body:
                yield str(number), f"슬라이드: {number}\n{body}"
    return _join_sections(sections(), max_chars, 'slide', is_cancelled=is_cancelled)


def extract_office(
    file_path, max_bytes=DEFAULT_MAX_BYTES,
    max_unpacked_bytes=DEFAULT_MAX_UNPACKED_BYTES, max_members=DEFAULT_MAX_MEMBERS,
    max_text_chars=DEFAULT_MAX_TEXT_CHARS,
    max_cells=DEFAULT_MAX_CELLS, max_slides=DEFAULT_MAX_SLIDES, is_cancelled=None,
):
    for name, value in (
        ("max_bytes", max_bytes), ("max_unpacked_bytes", max_unpacked_bytes),
        ("max_members", max_members), ("max_text_chars", max_text_chars),
        ("max_cells", max_cells), ("max_slides", max_slides),
    ):
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name}는 양의 정수여야 합니다.")
    check_cancelled(is_cancelled)
    path = Path(file_path).expanduser()
    result_path = str(path)
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        return TextExtractionResult(result_path, "SKIPPED", reason="UNSUPPORTED_TYPE")
    try:
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            return TextExtractionResult(result_path, "SKIPPED", reason="NOT_REGULAR_FILE")
        with path.open("rb") as source:
            content = source.read(max_bytes + 1)
            check_cancelled(is_cancelled)
    except OSError as error:
        return TextExtractionResult(result_path, "FAILED", reason="READ_ERROR", error=str(error))
    if len(content) > max_bytes:
        return TextExtractionResult(result_path, "SKIPPED", reason="TOO_LARGE")
    if content.startswith(OLE_HEADER):
        return TextExtractionResult(result_path, "SKIPPED", reason="ENCRYPTED_OR_LEGACY_OFFICE")
    try:
        with ZipFile(BytesIO(content)) as archive:
            check_cancelled(is_cancelled)
            members = archive.infolist()
            if len(members) > max_members:
                raise _LimitReached("ARCHIVE_MEMBER_LIMIT_EXCEEDED")
            if sum(member.file_size for member in members) > max_unpacked_bytes:
                raise _LimitReached("ARCHIVE_SIZE_LIMIT_EXCEEDED")
            if any(member.flag_bits & 1 for member in members):
                return TextExtractionResult(result_path, "SKIPPED", reason="ENCRYPTED_OFFICE")
            required = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}[extension]
            if required not in archive.namelist():
                raise BadZipFile("문서 형식에 필요한 Office 구성 요소가 없습니다.")
        check_cancelled(is_cancelled)
        options = {"is_cancelled": is_cancelled} if is_cancelled is not None else {}
        spans = ()
        if extension == ".docx":
            text = _read_docx(content, max_text_chars, **options)
        elif extension == ".xlsx":
            text, spans = _read_xlsx(content, max_text_chars, max_cells, **options)
        else:
            text, spans = _read_pptx(content, max_text_chars, max_slides, **options)
        check_cancelled(is_cancelled)
        if not text:
            return TextExtractionResult(result_path, "SKIPPED", reason="NO_TEXT")
        return TextExtractionResult(result_path, "EXTRACTED", text=text, source_spans=spans)
    except _LimitReached as error:
        return TextExtractionResult(result_path, "SKIPPED", reason=error.reason)
    except ImportError as error:
        return TextExtractionResult(result_path, "FAILED", reason="DEPENDENCY_MISSING", error=str(error))
    except MemoryError:
        raise
    except Exception as error:
        # 세 파서의 서로 다른 손상 문서 예외를 파일별로 격리한다. 취소는 전파한다.
        return TextExtractionResult(result_path, "FAILED", reason="INVALID_OFFICE", error=str(error))
