"""임시 파일/DB로 분석 성능을 측정한다: python -m benchmarks.content_analysis."""
import argparse
from collections import Counter
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
from benchmarks.documents import MIXED_KINDS, SUFFIXES, format_token, write_document
from database import db, file_contents
from database.content_search import search_content


def _count_matches(query):
    # 임시 DB 전체의 검색 가능한 결과 수를 검증한다. API 미리보기는 별도로 검사한다.
    with db.get_connection() as connection:
        return connection.execute("""
            SELECT COUNT(*) FROM file_content_search
            JOIN file_contents ON file_contents.file_id = file_content_search.rowid
            JOIN files ON files.id = file_contents.file_id
            WHERE file_content_search MATCH ? AND file_contents.status = 'EXTRACTED'
              AND files.scan_status != 'DELETED'
        """, (query,)).fetchone()[0]


def run_benchmark(file_count=1000, dataset="text"):
    if type(file_count) is not int or not 1 <= file_count <= 100_000:
        raise ValueError('파일 수는 1~100000 사이의 정수여야 합니다.')
    if dataset not in ('text', 'mixed'):
        raise ValueError('데이터셋은 text 또는 mixed여야 합니다.')
    if dataset == 'mixed' and not 18 <= file_count <= 10_000:
        raise ValueError('혼합 파일 수는 18~10000 사이여야 합니다.')
    kinds = ('txt_utf8',) if dataset == 'text' else MIXED_KINDS
    output = {
        'environment': {'python': platform.python_version(), 'platform': platform.platform(), 'sqlite': sqlite3.sqlite_version},
        'files': file_count,
        'dataset': dataset,
        'note': 'Synthetic small documents in a temporary directory; OS caches are not cleared. Times depend on this runner, not a guarantee for real PCs or large/complex documents.',
    }
    original_connection = db.get_connection
    connection_count = 0

    def connect():
        nonlocal connection_count
        connection_count += 1
        return original_connection()

    with TemporaryDirectory(prefix='file-network-benchmark-') as directory:
        base = Path(directory).resolve()
        root = base / 'scan'
        documents = []
        started = perf_counter()
        for index in range(file_count):
            folder = root / f'folder-{index // 100:04}'
            folder.mkdir(parents=True, exist_ok=True)
            kind = kinds[index % len(kinds)]
            path = folder / (f'note-{index:06}' + SUFFIXES[kind])
            if dataset == 'text':
                path.write_bytes(f'benchmarkneedle local content {index}\n'.encode('utf-8'))
            else:
                write_document(path, kind, index)
            documents.append((path, kind, index))
        output['create_seconds'] = round(perf_counter() - started, 6)
        output['format_counts'] = dict(Counter(kind for _, kind, _ in documents))
        sizes = [path.stat().st_size for path, _, _ in documents]
        output['source_bytes'] = sum(sizes)
        output['max_source_bytes'] = max(sizes)
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
            output['indexed_files'] = _count_matches('benchmarkneedle')
            if output['indexed_files'] != file_count:
                raise RuntimeError('전체 파일의 본문 색인 검증 실패')
            if dataset == 'mixed':
                format_matches = {}
                for kind, count in output['format_counts'].items():
                    rows = search_content('benchmarkneedle ' + format_token(kind), root_path=root, limit=min(count, 20))
                    if len(rows) != min(count, 20):
                        raise RuntimeError(f'{kind} 검색 검증 실패')
                    format_matches[kind] = len(rows)
                output['format_matches'] = format_matches
                before = {row['path']: row['id'] for row in db.get_active_files_under(root)}
                modified = documents[:len(kinds)]
                for path, kind, index in modified:
                    write_document(path, kind, index, keyword='benchmarkupdated')
                move_path, _, _ = documents[len(kinds)]
                moved = move_path.with_name('renamed-' + move_path.name)
                move_path.rename(moved)
                delete_path, _, _ = documents[len(kinds) + 2]
                delete_path.unlink()
                connection_count = 0
                started = perf_counter()
                with redirect_stdout(io.StringIO()):
                    counts = run_content_analysis(root)
                if counts != {'extracted': len(kinds), 'unchanged': file_count - len(kinds) - 1, 'failed': 0, 'skipped': 0}:
                    raise RuntimeError(f'수정/이동/삭제 분석 검증 실패: {counts}')
                output['changed'] = {'seconds': round(perf_counter() - started, 6), 'connections': connection_count, 'counts': counts}
                after = {row['path']: row['id'] for row in db.get_active_files_under(root)}
                updated = search_content('benchmarkupdated', root_path=root, limit=20)
                if {row['file_id'] for row in updated} != {before[str(path)] for path, _, _ in modified}:
                    raise RuntimeError('수정 본문의 검색 반영 실패')
                if _count_matches('benchmarkneedle') != file_count - len(kinds) - 1:
                    raise RuntimeError('수정/삭제 후 기존 검색어의 결과 수가 잘못되었습니다.')
                if search_content('benchmarkneedle benchmarkupdated', root_path=root):
                    raise RuntimeError('수정 전 검색어가 본문 인덱스에 남았습니다.')
                if after.get(str(moved)) != before[str(move_path)] or str(move_path) in after:
                    raise RuntimeError('이동 파일 ID 보존 실패')
                deleted = file_contents.get_file_content(before[str(delete_path)])
                if len(after) != file_count - 1 or str(delete_path) in after or deleted['status'] != 'STALE' or deleted['text'] is not None:
                    raise RuntimeError('삭제 파일/본문 무효화 실패')
                output['lifecycle'] = {'modified': len(kinds), 'moved': 1, 'deleted': 1, 'updated_matches': len(updated), 'active_files': len(after), 'moved_id_preserved': True, 'deleted_content_cleared': True, 'remaining_original_matches': file_count - len(kinds) - 1}
                connection_count = 0
                started = perf_counter()
                with redirect_stdout(io.StringIO()):
                    final_counts = run_content_analysis(root)
                if final_counts != {'extracted': 0, 'skipped': 0, 'failed': 0, 'unchanged': file_count - 1}:
                    raise RuntimeError(f'변경 후 캐시 재사용 검증 실패: {final_counts}')
                output['post_change_cached'] = {'seconds': round(perf_counter() - started, 6), 'connections': connection_count, 'counts': final_counts}

            with db.get_connection() as connection:
                output['stored_text_chars'] = connection.execute(
                    'SELECT COALESCE(SUM(length(text)),0) FROM file_contents'
                ).fetchone()[0]
                output['citation_chunks'] = connection.execute('SELECT COUNT(*) FROM document_chunks').fetchone()[0]
            output['database_bytes'] = Path(db.DB_PATH).stat().st_size

    return output


def main():
    parser = argparse.ArgumentParser(description='Isolated File Network analysis benchmark (temporary files and DB)')
    parser.add_argument('--files', type=int, default=1000, help='실제 생성할 파일 수 (text 1~100000, mixed 18~10000, 기본 1000)')
    parser.add_argument('--dataset', choices=('text', 'mixed'), default='text', help='text: UTF-8만, mixed: 텍스트/UTF-16/PDF/DOCX/XLSX/PPTX (18~10000개)')
    parser.add_argument('--output', type=Path, help='집계 JSON 보고서 경로 (UTF-8, 원문/임시 경로 없음)')
    args = parser.parse_args()
    if not 1 <= args.files <= 100_000:
        parser.error('파일 수는 1~100000 사이여야 합니다.')
    if args.dataset == 'mixed' and not 18 <= args.files <= 10_000:
        parser.error('혼합 파일 수는 18~10000 사이여야 합니다.')
    payload = json.dumps(run_benchmark(args.files, dataset=args.dataset), ensure_ascii=True, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + '\n', encoding='utf-8')
    print(payload)


if __name__ == '__main__':
    main()
