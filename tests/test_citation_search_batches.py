import hashlib

import pytest

from app.analyze_content import run_content_analysis
from benchmarks.citation_search import run_benchmark
from core.citations import MAX_CHUNK_CHARS
from database import db
from database.document_chunks import CITATION_SEARCH_BATCH_SIZE, find_matching_citation


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    folder = (tmp_path / 'scan').resolve()
    folder.mkdir()
    return folder


def store(root, blocks):
    assert all(len(block) == MAX_CHUNK_CHARS for block in blocks)
    (root / 'long.txt').write_text(''.join(blocks), encoding='utf-8')
    run_content_analysis(root, max_bytes=(root / 'long.txt').stat().st_size)
    return db.get_active_files_under(root)[0]['id']


def instrument(monkeypatch):
    actual = db.get_connection
    counts = {'inserted': 0, 'live': 0, 'peak': 0, 'cleared': 0}
    def trace(sql):
        if sql.startswith('INSERT INTO citation_matches(rowid,'):
            counts['inserted'] += 1
            counts['live'] += 1
            counts['peak'] = max(counts['peak'], counts['live'])
        elif sql.startswith('INSERT INTO citation_matches(citation_matches)'):
            counts['cleared'] += 1
            counts['live'] = 0
    def connect():
        connection = actual()
        connection.set_trace_callback(trace)
        return connection
    monkeypatch.setattr(db, 'get_connection', connect)
    return counts


def test_first_match_stops_before_later_batches(root, monkeypatch):
    blocks = [('needle ' + 'x ' * MAX_CHUNK_CHARS)[:MAX_CHUNK_CHARS]] + ['x ' * (MAX_CHUNK_CHARS // 2)] * (CITATION_SEARCH_BATCH_SIZE * 2)
    identity = store(root, blocks)
    counts = instrument(monkeypatch)
    result = find_matching_citation(identity, 'needle')
    assert result['segment'] == 0
    assert 0 < counts['inserted'] <= CITATION_SEARCH_BATCH_SIZE < len(blocks)


@pytest.mark.parametrize('target', [CITATION_SEARCH_BATCH_SIZE - 1, CITATION_SEARCH_BATCH_SIZE, CITATION_SEARCH_BATCH_SIZE * 2])
def test_batch_boundary_and_last_match_keep_unicode_offsets(root, monkeypatch, target):
    blocks = ['가😀 ' * (MAX_CHUNK_CHARS // 3) + '가😀'] * (CITATION_SEARCH_BATCH_SIZE * 2 + 1)
    blocks[target] = ('CAFÉ needle ' + blocks[target])[:MAX_CHUNK_CHARS]
    identity = store(root, blocks)
    before = hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
    counts = instrument(monkeypatch)
    result = find_matching_citation(identity, 'cafe NEEDLE')
    assert result['segment'] == target
    assert result['text'] == blocks[target]
    assert result['start_char'] == target * MAX_CHUNK_CHARS
    assert counts['peak'] <= CITATION_SEARCH_BATCH_SIZE
    assert counts['cleared'] == target // CITATION_SEARCH_BATCH_SIZE
    assert hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest() == before


def test_no_match_discards_previous_temporary_index_batches(root, monkeypatch):
    identity = store(root, ['word ' * (MAX_CHUNK_CHARS // 5)] * (CITATION_SEARCH_BATCH_SIZE * 2 + 1))
    counts = instrument(monkeypatch)
    assert find_matching_citation(identity, 'missing') is None
    assert counts['inserted'] == CITATION_SEARCH_BATCH_SIZE * 2 + 1
    assert counts['peak'] <= CITATION_SEARCH_BATCH_SIZE and counts['live'] == 0


def test_long_document_benchmark_verifies_first_last_and_missing():
    report = run_benchmark(4)
    assert report['first']['segment'] == 0 and report['last']['segment'] == 4
    assert report['missing']['segment'] is None and report['citation_chunks'] == 5
    assert report['database_bytes'] > 0


@pytest.mark.parametrize('count', [True, 0, 10001, '4'])
def test_benchmark_rejects_invalid_sizes(count):
    with pytest.raises(ValueError):
        run_benchmark(count)


def test_cancel_after_batch_clear_preserves_document_and_can_retry(root, monkeypatch):
    from core.operations import OperationCancelled
    identity = store(root, ['word ' * (MAX_CHUNK_CHARS // 5)] * (CITATION_SEARCH_BATCH_SIZE * 2 + 1))
    before = hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
    counts = instrument(monkeypatch)
    with pytest.raises(OperationCancelled):
        find_matching_citation(identity, 'missing', is_cancelled=lambda: counts['cleared'] >= 1)
    assert counts['cleared'] >= 1
    assert hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest() == before
    assert find_matching_citation(identity, 'word')['segment'] == 0


def test_legacy_nul_text_matches_existing_sqlite_preview_rules(root):
    from core.extractors.text import TextExtractionResult
    from database import file_contents
    identity = store(root, ['word ' * (MAX_CHUNK_CHARS // 5)])
    file_contents.save_file_content(identity, TextExtractionResult('legacy', 'EXTRACTED', text='visible\x00hiddenkeyword'), 'legacy')
    assert find_matching_citation(identity, 'hiddenkeyword') is None
    assert find_matching_citation(identity, 'visible')['text'] == 'visible'
