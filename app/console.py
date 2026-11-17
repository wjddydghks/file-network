"""CLI 출력이 Windows 파이프의 기본 문자셋에 의존하지 않도록 한다."""
import sys


def configure_console():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
