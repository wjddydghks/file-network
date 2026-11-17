"""GUI 실행과 배포본의 비대화식 자체 검증 진입점."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback


def _ensure_streams():
    # Windows GUI 실행 파일에서는 stdout/stderr가 None일 수 있다.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
        elif hasattr(getattr(sys, name), 'reconfigure'):
            getattr(sys, name).reconfigure(encoding='utf-8', errors='backslashreplace')


def main():
    _ensure_streams()
    parser = argparse.ArgumentParser(description='File Network desktop')
    parser.add_argument('--self-test-report', type=Path, help='합성 데이터로 패키지를 검증하고 JSON 보고서 저장')
    args = parser.parse_args()
    if args.self_test_report is not None:
        try:
            from app.package_smoke import run_smoke
            report = run_smoke()
        except Exception:
            report = {'success': False, 'error': traceback.format_exc()}
        args.self_test_report.parent.mkdir(parents=True, exist_ok=True)
        args.self_test_report.write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
        return 0 if report['success'] else 1
    try:
        from app.gui import main as launch
        launch()
        return 0
    except SystemExit as error:
        if error.code is None or isinstance(error.code, int):
            return error.code or 0
        message = str(error.code)
    except Exception:
        message = traceback.format_exc()
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'File Network — 시작 오류', 0x10)
    else:
        print(message, file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
