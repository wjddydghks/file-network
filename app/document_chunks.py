"""저장된 본문의 인용 단위를 JSON으로 조회한다."""
import argparse
import json

from app.extract_text import positive_integer
from database.document_chunks import get_document_chunk


def main():
    parser = argparse.ArgumentParser(description="File Network - Document Citations")
    parser.add_argument("file_id", type=positive_integer, help="검색 결과의 file_id")
    parser.add_argument("--chunk", type=positive_integer, default=1, help="1부터 시작하는 인용 번호")
    parser.add_argument("--revision", help="이전 조회의 analyzed_at (다른 분석 버전이면 거부)")
    args = parser.parse_args()
    try:
        result = get_document_chunk(args.file_id, segment=args.chunk - 1, analyzed_at=args.revision)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result))
    if result is None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
