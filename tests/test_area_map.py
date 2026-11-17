from pathlib import Path

import pytest

from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.area_layout import layout_areas
from core.operations import OperationCancelled
from database import db
from database.area_map import FILE_PAGE_SIZE, GROUP_PAGE_SIZE, get_area_map


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    folder = tmp_path / 'project'
    folder.mkdir()
    return folder


def test_empty_area_does_not_create_database(root):
    snapshot = get_area_map(root)
    assert snapshot['total_files'] == 0 and not snapshot['groups']
    assert not db.DB_PATH.exists()


def test_hierarchy_types_states_scope_boundary_and_bytes(root):
    nested = root / 'planning' / 'notes'
    nested.mkdir(parents=True)
    (nested / 'a.txt').write_text('budget project', encoding='utf-8')
    (root / 'b.txt').write_text('timeline project', encoding='utf-8')
    (root / 'image.bin').write_bytes(b'\xff')
    sibling = root.with_name('project-extra')
    sibling.mkdir()
    (sibling / 'outside.txt').write_text('outside', encoding='utf-8')
    run_content_analysis(root)
    run_content_analysis(sibling)
    data = get_area_map(root)
    assert data['total_files'] == data['selected_files'] == 3
    assert data['states'] == {'EXTRACTED': 2, 'SKIPPED': 1}
    assert sum(g['count'] for g in data['groups']) == 3
    assert data['total_bytes'] == sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    folder = next(g for g in data['groups'] if g['kind'] == 'folder')
    child = get_area_map(root, focus=folder['key'])
    assert child['total_files'] == 1
    assert child['groups'][0]['label'] == 'notes'
    direct = get_area_map(root, selected_type='.txt')
    assert direct['total_files'] == 3 and direct['selected_files'] == 1
    assert direct['files'][0]['name'] == 'b.txt'


def test_pending_changed_and_deleted_in_structure(root):
    (root / 'a.txt').write_text('old', encoding='utf-8')
    (root / 'gone.txt').write_text('old', encoding='utf-8')
    run_content_analysis(root)
    (root / 'a.txt').write_text('changed content', encoding='utf-8')
    (root / 'new.txt').write_text('new', encoding='utf-8')
    (root / 'gone.txt').unlink()
    run_incremental_scan(root)
    result = get_area_map(root)
    assert result['total_files'] == 2
    assert result['states'] == {'STALE': 1, 'PENDING': 1}


def test_interest_body_name_literal_unicode_and_unmatched_context(root):
    (root / 'body.txt').write_text('budget rollout 프로젝트', encoding='utf-8')
    (root / 'budget rollout.bin').write_bytes(b'\xff')
    (root / 'other.txt').write_text('different topic', encoding='utf-8')
    run_content_analysis(root)
    result = get_area_map(root, interest='budget rollout')
    assert result['total_files'] == 3 and result['matched_files'] == result['selected_files'] == 2
    assert {r['match_source'] for r in result['files']} == {'content', 'name'}
    assert get_area_map(root, interest='프로젝트')['matched_files'] == 1
    assert get_area_map(root, interest='" OR *')['matched_files'] == 0
    assert sum(g['count'] for g in result['groups']) == 3  # Nonmatches still contribute area.
    with db.get_connection() as conn:
        conn.execute("SELECT 1 FROM file_content_search LIMIT 1")  # Index initialized locally.


def test_large_file_and_group_pages_keep_exact_totals(root):
    for i in range(FILE_PAGE_SIZE + 7):
        (root / f'{i:03}.txt').write_text('keyword', encoding='utf-8')
    for i in range(GROUP_PAGE_SIZE + 3):
        sub = root / f'folder{i:03}'
        sub.mkdir()
        (sub / 'a.txt').write_text('keyword', encoding='utf-8')
    run_incremental_scan(root)
    first = get_area_map(root)
    second = get_area_map(root, file_offset=FILE_PAGE_SIZE)
    groups2 = get_area_map(root, group_offset=GROUP_PAGE_SIZE)
    total = FILE_PAGE_SIZE + 7 + GROUP_PAGE_SIZE + 3
    assert first['total_files'] == first['selected_files'] == total
    assert len(first['files']) == FILE_PAGE_SIZE
    assert len(first['files']) + len(second['files']) == total
    assert not {r['file_id'] for r in first['files']} & {r['file_id'] for r in second['files']}
    assert first['group_count'] == GROUP_PAGE_SIZE + 4
    assert len(first['groups']) == GROUP_PAGE_SIZE
    assert len(first['groups']) + len(groups2['groups']) == first['group_count']
    assert sum(g['count'] for g in first['groups'] + groups2['groups']) == total


@pytest.mark.parametrize('changes', [
    {'focus': '..'}, {'interest': 'x' * 4097}, {'interest': 'a\x00b'},
    {'file_offset': -1}, {'file_offset': True}, {'group_offset': -1}, {'selected_type': 4},
])
def test_invalid_request_before_database_write(root, changes):
    if changes.get('focus') == '..':
        changes['focus'] = root.parent
    with pytest.raises(ValueError):
        get_area_map(root, **changes)
    assert not db.DB_PATH.exists()


def test_cancelled_map_never_creates_database(root):
    with pytest.raises(OperationCancelled):
        get_area_map(root, is_cancelled=lambda: True)
    assert not db.DB_PATH.exists()


@pytest.mark.parametrize('weights', [[1], [1, 1, 2], [1000, 1, 1, 1], list(range(1, 41))])
def test_area_proportions_inside_bounds_and_no_overlap(weights):
    rectangles = layout_areas(weights)
    assert len(rectangles) == len(weights)
    area = 1000 * 560
    for i, (x, y, width, height) in enumerate(rectangles):
        assert x >= 0 and y >= 0 and width > 0 and height > 0
        assert x + width <= 1000.000001 and y + height <= 560.000001
        assert width * height == pytest.approx(area * weights[i] / sum(weights))
        for x2, y2, w2, h2 in rectangles[i + 1:]:
            overlap = max(0, min(x + width, x2 + w2) - max(x, x2)) * max(0, min(y + height, y2 + h2) - max(y, y2))
            assert overlap == pytest.approx(0, abs=1e-6)
