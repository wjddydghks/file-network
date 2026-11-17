"""저장된 추출 본문의 오프셋을 유지하는 인용 단위 분할."""

MAX_CHUNK_CHARS = 2_000


def build_chunks(result):
    """0-based, end-exclusive 문자 범위. 위치는 추출기 메타데이터만 사용한다."""
    if result.status != "EXTRACTED" or not result.text:
        return []
    text = result.text
    spans = result.source_spans
    if spans:
        previous_end = 0
        previous_page = 0
        for span in spans:
            if (type(span.start) is not int or type(span.end) is not int
                    or not previous_end <= span.start <= span.end <= len(text)
                    or span.kind not in ('page', 'sheet', 'slide')
                    or span.kind != spans[0].kind):
                raise ValueError("올바르지 않은 원본 위치 범위입니다.")
            if span.kind == 'page':
                if type(span.page) is not int or span.page <= previous_page or span.label is not None:
                    raise ValueError("올바르지 않은 원본 페이지 범위입니다.")
                previous_page = span.page
            elif (span.page is not None or not isinstance(span.label, str)
                  or not span.label or len(span.label) > 255
                  or (span.kind == 'slide' and (not span.label.isascii()
                      or not span.label.isdecimal() or int(span.label) <= 0))):
                raise ValueError("올바르지 않은 Office 위치입니다.")
            previous_end = span.end
        ranges = [(s.start, s.end, s.page, s.kind, s.label) for s in spans]
    else:
        ranges = [(0, len(text), None, 'text', None)]
    chunks = []
    for start, end, page, kind, label in ranges:
        for offset in range(start, end, MAX_CHUNK_CHARS):
            chunks.append((len(chunks), offset, min(offset + MAX_CHUNK_CHARS, end), page, kind, label))
    return chunks
