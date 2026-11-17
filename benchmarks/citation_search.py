"""합성 긴 문서의 앞/끝/미일치 인용 조회를 임시 DB에서 검증한다."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from app.analyze_content import run_content_analysis
from core.citations import MAX_CHUNK_CHARS
from database import db
from database.document_chunks import find_matching_citation


def run_benchmark(chunk_count=2000):
    if type(chunk_count) is not int or not 1 <= chunk_count <= 10000:
        raise ValueError('인용 묶음 수는 1~10000 사이의 정수여야 합니다.')
    block = ('word ' * MAX_CHUNK_CHARS)[:MAX_CHUNK_CHARS]
    head = ('headneedle ' + block)[:MAX_CHUNK_CHARS]
    text = head + block * (chunk_count - 1) + ' tailneedle'
    output = {
        'environment': {'python': platform.python_version(), 'platform': platform.platform()},
        'characters': len(text), 'citation_chunks': chunk_count + 1,
        'note': 'Synthetic ASCII text in a temporary directory; single-run timings with OS caches retained, not a real-PC performance or memory guarantee.',
    }
    with TemporaryDirectory(prefix='file-network-citation-benchmark-') as directory:
        base = Path(directory).resolve()
        root = base / 'scan'
        root.mkdir()
        (root / 'long.txt').write_text(text, encoding='utf-8')
        with patch.object(db, 'DB_PATH', base / 'test.db'):
            with redirect_stdout(io.StringIO()):
                counts = run_content_analysis(root, max_bytes=len(text.encode('utf-8')))
            if counts['extracted'] != 1:
                raise RuntimeError('합성 긴 문서 분석 실패')
            file_id = db.get_active_files_under(root)[0]['id']
            for phase, query, expected in [('first', 'headneedle', 0), ('last', 'tailneedle', chunk_count), ('missing', 'absentneedle', None)]:
                started = perf_counter()
                result = find_matching_citation(file_id, query)
                seconds = perf_counter() - started
                segment = result['segment'] if result is not None else None
                if segment != expected:
                    raise RuntimeError(f'{phase} 인용 위치 검증 실패')
                output[phase] = {'seconds': round(seconds, 6), 'segment': segment}
            output['database_bytes'] = Path(db.DB_PATH).stat().st_size
    return output


def main():
    parser = argparse.ArgumentParser(description='Isolated long-document citation benchmark')
    parser.add_argument('--chunks', type=int, default=2000, help='본문의 2000자 구간 수 (1~10000, 기본 2000)')
    parser.add_argument('--output', type=Path, help='집계 JSON 저장 위치 (원문·임시 경로 없음)')
    args = parser.parse_args()
    try:
        result = run_benchmark(args.chunks)
    except ValueError as error:
        parser.error(str(error))
    payload = json.dumps(result, ensure_ascii=True, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + '\n', encoding='utf-8')
    print(payload)


if __name__ == '__main__':
    main()
