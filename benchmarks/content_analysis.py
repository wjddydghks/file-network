"""임시 파일/DB로 분석 성능을 측정한다: python -m benchmarks.content_analysis."""
import argparse
from contextlib import redirect_stdout
import io
import json
import platform
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from app.analyze_content import run_content_analysis
from database import db
from database.content_search import search_content


def run_benchmark(file_count=1000):
    if type(file_count) is not int or not 1 <= file_count <= 100_000:
        raise ValueError('파일 수는 1~100000 사이의 정수여야 합니다.')
    output = {
        'environment': {'python': platform.python_version(), 'platform': platform.platform(), 'sqlite': sqlite3.sqlite_version},
        'files': file_count,
        'note': 'Small UTF-8 text files in a temporary directory; OS caches are not cleared. Not a mixed-document or Windows-specific performance guarantee.',
    }
    original_connection = db.get_connection
    connection_count = 0

    def connect():
        nonlocal connection_count
        connection_count += 1
        return original_connection()

    with TemporaryDirectory(prefix='file-network-benchmark-') as directory:
        base = Path(directory)
        root = base / 'scan'
        started = perf_counter()
        for index in range(file_count):
            folder = root / f'folder-{index // 100:04}'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f'note-{index:06}.txt').write_bytes(f'benchmarkneedle local content {index}\n'.encode('utf-8'))
        output['create_seconds'] = round(perf_counter() - started, 6)
        with patch.object(db, 'DB_PATH', base / 'benchmark.db'), patch.object(db, 'get_connection', connect):
            for phase in ('initial', 'cached'):
                connection_count = 0
                started = perf_counter()
                with redirect_stdout(io.StringIO()):
                    counts = run_content_analysis(root)
                output[phase] = {'seconds': round(perf_counter() - started, 6), 'connections': connection_count, 'counts': counts}
                expected = 'extracted' if phase == 'initial' else 'unchanged'
                if counts[expected] != file_count or sum(counts.values()) != file_count:
                    raise RuntimeError(f'{phase} 분석 결과가 예상과 다릅니다: {counts}')
            started = perf_counter()
            matches = search_content('benchmarkneedle', root_path=root, limit=min(file_count, 20))
            if len(matches) != min(file_count, 20):
                raise RuntimeError('검색 결과 수가 예상과 다릅니다.')
            output['first_search'] = {'seconds': round(perf_counter() - started, 6), 'matches': len(matches)}
    return output


def main():
    parser = argparse.ArgumentParser(description='Isolated File Network analysis benchmark (temporary files and DB)')
    parser.add_argument('--files', type=int, default=1000, help='실제 생성할 텍스트 파일 수 (1~100000, 기본 1000)')
    args = parser.parse_args()
    if not 1 <= args.files <= 100_000:
        parser.error('파일 수는 1~100000 사이여야 합니다.')
    print(json.dumps(run_benchmark(args.files), ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
