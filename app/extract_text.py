import argparse
from dataclasses import asdict
import json

from core.extractors.content import extract_content
from core.extractors.text import DEFAULT_MAX_BYTES


def positive_integer(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("양의 정수를 입력하세요.") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("양의 정수를 입력하세요.")
    return number


def main():
    parser = argparse.ArgumentParser(description="File Network - File Content Extractor")
    parser.add_argument("path", help="내용을 추출할 텍스트·소스 코드·PDF·Office 파일")
    parser.add_argument(
        "--max-bytes", type=positive_integer, default=DEFAULT_MAX_BYTES,
        help="최대 파일 크기 (바이트, 기본 1048576)",
    )
    args = parser.parse_args()
    result = extract_content(args.path, max_bytes=args.max_bytes)
    # JSON 이스케이프로 콘솔 인코딩과 무관하게 한글·이모지를 보존한다.
    print(json.dumps(asdict(result)))
    if result.status == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
