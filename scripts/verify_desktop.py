"""Python 환경 경로 없이 실제 빌드 결과를 실행하고 자체 검증 보고서를 확인한다."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    executable = args.executable.resolve()
    report_path = args.report.resolve()
    if not executable.is_file():
        raise RuntimeError('실행 파일이 없습니다.')
    if report_path.exists():
        raise RuntimeError('이전 보고서를 재사용하지 않도록 새 보고서 경로를 지정하세요.')
    env = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
        env.pop(key, None)
    if os.name == 'nt':
        windows = Path(os.environ['SystemRoot'])
        env['FILE_NETWORK_SELF_TEST_PLATFORM'] = 'windows'
        env['PATH'] = os.pathsep.join(str(path) for path in (windows / 'System32', windows, windows / 'System32' / 'Wbem'))
    else:
        env['PATH'] = '/usr/bin:/bin'
    with TemporaryDirectory(prefix='file-network-external-cwd-') as directory:
        result = subprocess.run([str(executable), '--self-test-report', str(report_path)], cwd=directory, env=env, timeout=180, capture_output=True, text=True, errors='replace')
    if result.returncode:
        print(result.stdout)
        print(result.stderr)
    if not report_path.exists():
        raise RuntimeError(f'패키지 보고서가 없습니다 (exit={result.returncode}).')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if result.returncode != 0 or not report.get('success'):
        raise RuntimeError('배포본 자체 검증 실패')
    if not report.get('frozen') or not report.get('database_outside_bundle'):
        raise RuntimeError('독립 실행 배포본/DB 경로 검증 실패')
    if not all(report['gui'].values()) or report['benchmark']['indexed_files'] != 18:
        raise RuntimeError('패키지 기능 검증 실패')
    if os.name == 'nt' and (report.get('gui_platform') != 'windows' or not list(executable.parent.rglob('qwindows.dll'))):
        raise RuntimeError('Windows 기본 GUI 플랫폼 검증 실패')
    if list(executable.parent.rglob('file_network.db')):
        raise RuntimeError('배포 폴더에 DB가 포함되었거나 생성되었습니다.')


if __name__ == '__main__':
    main()
