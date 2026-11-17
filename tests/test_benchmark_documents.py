import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from benchmarks import content_analysis as benchmark
from database import db


def test_mixed_benchmark_checks_all_formats_changes_and_temp_cleanup(tmp_path, monkeypatch):
    original_db = tmp_path / 'existing.db'
    original_db.write_bytes(b'untouched user database sentinel')
    monkeypatch.setattr(db, 'DB_PATH', original_db)
    original_temporary_directory = benchmark.TemporaryDirectory
    created = []
    @contextmanager
    def temporary_directory(*args, **kwargs):
        with original_temporary_directory(*args, **kwargs) as directory:
            base = Path(directory)
            created.append(base)
            (base / 'alias').mkdir()
            yield str(base / 'alias' / '..')
    monkeypatch.setattr(benchmark, 'TemporaryDirectory', temporary_directory)
    report = benchmark.run_benchmark(18, dataset='mixed')
    assert report['dataset'] == 'mixed'
    assert report['format_counts'] == dict.fromkeys(benchmark.MIXED_KINDS, 3)
    assert report['format_matches'] == dict.fromkeys(benchmark.MIXED_KINDS, 3)
    assert report['initial']['counts']['extracted'] == 18
    assert report['cached']['counts']['unchanged'] == 18
    assert report['first_search']['matches'] == 18
    assert report['indexed_files'] == 18
    assert report['changed']['counts'] == {'extracted': 6, 'unchanged': 11, 'failed': 0, 'skipped': 0}
    assert report['post_change_cached']['counts']['unchanged'] == 17
    assert report['lifecycle'] == {'modified': 6, 'moved': 1, 'deleted': 1, 'updated_matches': 6, 'active_files': 17, 'moved_id_preserved': True, 'deleted_content_cleared': True, 'remaining_original_matches': 11}
    assert 0 < report['max_source_bytes'] < 1024*1024
    assert report['source_bytes'] > report['max_source_bytes']
    assert db.DB_PATH == original_db
    assert original_db.read_bytes() == b'untouched user database sentinel'
    assert created and all(not path.exists() for path in created)
    assert all(str(path) not in json.dumps(report) for path in created)


def test_benchmark_cli_keeps_default_text_profile_and_writes_utf8_json(tmp_path, monkeypatch, capsys):
    report_path = tmp_path / '보고서' / '결과.json'
    monkeypatch.setattr('sys.argv', ['benchmark', '--files', '3', '--output', str(report_path)])
    benchmark.main()
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert json.loads(capsys.readouterr().out) == report
    assert report['dataset'] == 'text'
    assert report['format_counts'] == {'txt_utf8': 3}
    assert report['initial']['counts']['extracted'] == 3
    assert report['cached']['counts']['unchanged'] == 3
    assert 'lifecycle' not in report


@pytest.mark.parametrize('count,dataset', [(0, 'text'), (True, 'mixed'), (100001, 'text'), (17, 'mixed'), (10001, 'mixed'), (18, 'unknown')])
def test_invalid_benchmark_options_do_not_create_files(monkeypatch, count, dataset):
    monkeypatch.setattr(benchmark, 'TemporaryDirectory', lambda **kwargs: pytest.fail('invalid options created files'))
    with pytest.raises(ValueError):
        benchmark.run_benchmark(count, dataset=dataset)


def test_failed_benchmark_restores_database_and_cleans_temporary_data(tmp_path, monkeypatch):
    user_db = tmp_path / 'user.db'
    monkeypatch.setattr(db, 'DB_PATH', user_db)
    original_temporary_directory = benchmark.TemporaryDirectory
    created = []
    def temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        created.append(Path(directory.name))
        return directory
    monkeypatch.setattr(benchmark, 'TemporaryDirectory', temporary_directory)
    monkeypatch.setattr(benchmark, 'run_content_analysis', lambda root: {'extracted': 0, 'unchanged': 0, 'skipped': 0, 'failed': 1})
    with pytest.raises(RuntimeError, match='예상과 다릅니다'):
        benchmark.run_benchmark(1)
    assert db.DB_PATH == user_db
    assert not user_db.exists()
    assert all(not path.exists() for path in created)
