import sqlite3
from pathlib import Path

import pytest

from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.operations import OperationCancelled
from database import db
from database.workspace_status import get_workspace_status


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    folder = tmp_path / 'work'
    folder.mkdir()
    return folder


def test_missing_database_is_not_created(root):
    result = get_workspace_status(root)
    assert result['files'] == 0 and result['scan'] is None and result['folders'] == []
    assert not Path(db.DB_PATH).exists()


def test_parent_scan_child_scope_and_boundary(root):
    child = root / 'child'
    child.mkdir()
    (child / 'note.txt').write_text('Searchable project', encoding='utf-8')
    (root / 'other.txt').write_text('Other project', encoding='utf-8')
    sibling = root.with_name('work-extra')
    sibling.mkdir()
    (sibling / 'private.txt').write_text('Other scope', encoding='utf-8')
    run_content_analysis(root)
    run_content_analysis(sibling)
    result = get_workspace_status(child)
    assert result['files'] == result['extracted'] == 1
    assert result['scan']['root_path'] == str(root)
    assert len(result['folders']) == 2
    assert get_workspace_status(root)['files'] == 2
    assert get_workspace_status()['files'] == 3
    assert get_workspace_status(child / 'empty')['files'] == 0


def test_scan_complete_does_not_mean_body_extracted(root):
    (root / 'note.txt').write_text('Content', encoding='utf-8')
    run_incremental_scan(root)
    result = get_workspace_status(root)
    assert result['scan']['scan_complete']
    assert result['pending'] == 1 and result['extracted'] == 0
    assert result['last_saved_at'] is None


def test_skipped_stale_failed_deleted_counts(root):
    for name in ['ready.txt', 'stale.txt', 'failed.txt', 'gone.txt', 'unsupported.bin']:
        (root / name).write_text('Project', encoding='utf-8')
    run_content_analysis(root)
    (root / 'stale.txt').write_text('Changed project longer', encoding='utf-8')
    (root / 'gone.txt').unlink()
    run_incremental_scan(root)
    with db.get_connection() as conn:
        conn.execute("UPDATE file_contents SET status='FAILED',text=NULL WHERE file_id=(SELECT id FROM files WHERE name='failed.txt')")
    result = get_workspace_status(root)
    assert [result[k] for k in ['files','extracted','pending','stale','failed','skipped']] == [4,1,0,1,1,1]
    assert result['last_saved_at']


def test_latest_incomplete_scan_and_history_dedup(root):
    (root / 'note.txt').write_text('Content', encoding='utf-8')
    run_content_analysis(root)
    run_incremental_scan(root)
    with db.get_connection() as conn:
        conn.execute("UPDATE scan_sessions SET status='CANCELLED',scan_complete=0 WHERE id=(SELECT id FROM scan_sessions ORDER BY started_at DESC LIMIT 1)")
    result = get_workspace_status(root)
    assert result['scan']['status'] == 'CANCELLED'
    assert result['extracted'] == 1 and len(result['folders']) == 1


def test_empty_scanned_folder_and_missing_source_are_distinct(root):
    run_content_analysis(root)
    result = get_workspace_status(root)
    assert result['scan'] is not None and result['files'] == 0
    root.rmdir()
    assert get_workspace_status(root)['root_available'] is False


def test_status_read_is_readonly_and_does_not_change_schema(root):
    (root / 'note.txt').write_text('Content', encoding='utf-8')
    run_content_analysis(root)
    before = Path(db.DB_PATH).read_bytes()
    result = get_workspace_status(root)
    assert result['extracted'] == 1
    assert Path(db.DB_PATH).read_bytes() == before
    assert get_workspace_status(root)['folders'][0]['root_path'] == str(root)


def test_cancel_before_database_access(root):
    with pytest.raises(OperationCancelled):
        get_workspace_status(root, is_cancelled=lambda: True)
    assert not Path(db.DB_PATH).exists()
