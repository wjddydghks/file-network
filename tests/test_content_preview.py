import pytest

from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.extractors.text import TextExtractionResult
from database import db, file_contents
from database.file_search import search_filename


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    folder = tmp_path / 'scan'
    folder.mkdir()
    return folder


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_preview_reads_cached_unicode_text_without_source(root, newline):
    path = root / '한글.txt'
    text = newline.join(['앞부분', '한글 본문 😀', '뒷부분'])
    path.write_bytes(text.encode('utf-8'))
    run_content_analysis(root)
    file_id = search_filename('한글')[0]['file_id']
    path.unlink()  # No rescan: preview explicitly represents the last analysis.
    result = file_contents.get_content_preview(file_id)
    assert result['text'] == text
    assert result['name'] == path.name
    assert result['path'] == str(path)
    assert result['total_chars'] == len(text)
    assert result['analyzed_at']
    assert not result['truncated']


@pytest.mark.parametrize('extra', [0, 1])
def test_preview_bounds_returned_text_by_characters(root, extra):
    path = root / 'large.txt'
    path.write_text('x', encoding='utf-8')
    run_incremental_scan(root)
    file_id = search_filename('large')[0]['file_id']
    text = '한😀' * ((file_contents.MAX_PREVIEW_CHARS + extra) // 2) + ('끝' if extra else '')
    file_contents.initialize_file_contents()
    file_contents.save_file_content(file_id, TextExtractionResult(str(path), 'EXTRACTED', text=text), 'fingerprint')
    result = file_contents.get_content_preview(file_id)
    assert result['text'] == text[:file_contents.MAX_PREVIEW_CHARS]
    assert result['total_chars'] == len(text)
    assert result['truncated'] == bool(extra)
    assert file_contents.get_file_content(file_id)['text'] == text


@pytest.mark.parametrize('status', ['SKIPPED', 'FAILED', 'STALE'])
def test_non_extracted_states_never_return_leftover_text(root, status):
    path = root / 'file.txt'
    path.write_text('original', encoding='utf-8')
    run_content_analysis(root)
    file_id = search_filename('file')[0]['file_id']
    file_contents.save_file_content(
        file_id, TextExtractionResult(str(path), status, text='obsolete text', reason='TEST_REASON'), 'test',
    )
    result = file_contents.get_content_preview(file_id)
    assert result['status'] == status
    assert result['reason'] == 'TEST_REASON'
    assert result['text'] == ''
    assert result['total_chars'] == 0
    assert not result['truncated']


def test_pending_and_missing_ids(root):
    (root / 'pending.png').write_bytes(b'\xff')
    run_incremental_scan(root)
    file_id = search_filename('pending')[0]['file_id']
    result = file_contents.get_content_preview(file_id)
    assert result['status'] == 'PENDING'
    assert result['text'] == ''
    assert result['analyzed_at'] is None
    assert file_contents.get_content_preview(file_id + 1) is None


def test_change_move_and_delete_use_current_scan_state(root):
    path = root / 'before.txt'
    path.write_text('original', encoding='utf-8')
    run_content_analysis(root)
    file_id = search_filename('before')[0]['file_id']
    moved = root / 'after.txt'
    path.rename(moved)
    run_incremental_scan(root)
    assert file_contents.get_content_preview(file_id)['path'] == str(moved)
    assert file_contents.get_content_preview(file_id)['text'] == 'original'
    moved.write_text('changed body', encoding='utf-8')
    run_incremental_scan(root)
    assert file_contents.get_content_preview(file_id)['status'] == 'STALE'
    assert file_contents.get_content_preview(file_id)['text'] == ''
    run_content_analysis(root)
    assert file_contents.get_content_preview(file_id)['text'] == 'changed body'
    moved.unlink()
    run_incremental_scan(root)
    assert file_contents.get_content_preview(file_id) is None


@pytest.mark.parametrize('file_id', [None, True, 0, -1, '1', 1.0, 2**63])
def test_invalid_ids_do_not_create_database(root, file_id):
    with pytest.raises(ValueError):
        file_contents.get_content_preview(file_id)
    assert not db.DB_PATH.exists()
