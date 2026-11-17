from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app import area_map_view
from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.operations import check_cancelled
from tests.test_gui import application, window, wait_until


@pytest.fixture
def area_view(application, window):
    view, root = window
    (root / 'note.txt').write_text('budget project ' * 200 + 'end marker', encoding='utf-8')
    (root / 'budget.bin').write_bytes(b'\xff')
    child = root / 'planning'
    child.mkdir()
    (child / 'schedule.txt').write_text('schedule project', encoding='utf-8')
    run_content_analysis(root)
    view.open_area_map()
    dialog = view.area_dialog
    wait_until(application, lambda: dialog.snapshot is not None and dialog.worker is None)
    yield view, root, dialog
    if view.area_dialog is not None:
        view.area_dialog.close()
        wait_until(application, lambda: view.area_dialog is None)


def wait_map(application, dialog):
    wait_until(application, lambda: dialog.worker is None)


def select_group(dialog, kind):
    for index in range(dialog.selector.count()):
        if dialog.selector.itemData(index)['kind'] == kind:
            dialog.selector.setCurrentIndex(index)
            return
    pytest.fail('Missing group')


def select_file(dialog, name):
    for index, row in enumerate(dialog.snapshot['files']):
        if row['name'] == name:
            dialog.table.selectRow(index)
            return
    pytest.fail('Missing file')


def test_whole_area_drill_back_type_and_preserve_main_results(application, area_view):
    view, root, dialog = area_view
    assert dialog.snapshot['total_files'] == 3
    assert dialog.selector.count() == 3
    assert 'AI 의미 분석 아님' in dialog.summary.text()
    assert not dialog.back.isEnabled()
    select_group(dialog, 'folder')
    dialog.open_group_button.click()
    wait_map(application, dialog)
    assert dialog.snapshot['focus'] == str(root / 'planning')
    assert dialog.snapshot['total_files'] == 1 and dialog.back.isEnabled()
    dialog.back.click()
    wait_map(application, dialog)
    assert dialog.snapshot['focus'] == str(root)
    select_group(dialog, 'type')
    dialog.open_group_button.click()
    wait_map(application, dialog)
    assert dialog.snapshot['selected_type'] is not None
    assert dialog.snapshot['selected_files'] == 1
    dialog.home.click()
    wait_map(application, dialog)
    assert dialog.snapshot['selected_type'] is None
    dialog.close()
    wait_until(application, lambda: view.area_dialog is None)
    assert view.worker is None


def test_interest_highlights_context_and_failed_input_keeps_snapshot(application, area_view):
    _, _, dialog = area_view
    dialog.interest.setText('budget')
    dialog.apply.click()
    wait_map(application, dialog)
    assert dialog.snapshot['total_files'] == 3 and dialog.snapshot['matched_files'] == 2
    assert len(dialog.scene.items()) >= dialog.selector.count()
    assert dialog.selector.count() == 3  # Nonmatching area remains visible.
    assert {row['match_source'] for row in dialog.snapshot['files']} == {'name', 'content'}
    snapshot = dialog.snapshot
    dialog.interest.setText('x' * 4097)
    dialog.apply.click()
    wait_map(application, dialog)
    assert dialog.snapshot is snapshot
    assert '4096' in dialog.status.text()


def test_citation_navigation_and_reanalysis_guard(application, area_view):
    _, root, dialog = area_view
    select_file(dialog, 'note.txt')
    dialog.preview_button.click()
    wait_map(application, dialog)
    assert dialog._preview['segment'] == 0
    assert dialog.body.toPlainText().startswith('budget project')
    assert dialog.chunk_next.isEnabled()
    dialog.chunk_next.click()
    wait_map(application, dialog)
    assert 'end marker' in dialog.body.toPlainText()
    (root / 'note.txt').write_text('new budget', encoding='utf-8')
    run_content_analysis(root)
    dialog.preview_button.click()
    wait_map(application, dialog)
    assert not dialog.body.toPlainText() and dialog._preview is None
    assert '변경' in dialog.status.text()
    assert not dialog.chunk_next.isEnabled()
    dialog.refresh.click()
    wait_map(application, dialog)
    select_file(dialog, 'note.txt')
    dialog.preview_button.click()
    wait_map(application, dialog)
    assert dialog.body.toPlainText() == 'new budget'


def test_skipped_file_has_no_false_body_preview(application, area_view):
    _, _, dialog = area_view
    select_file(dialog, 'budget.bin')
    assert not dialog.preview_button.isEnabled()
    assert not dialog.body.toPlainText()


def test_map_file_and_group_pages(application, area_view):
    _, root, dialog = area_view
    for i in range(83):
        (root / f'added{i:03}.txt').write_text('extra', encoding='utf-8')
    for i in range(41):
        child = root / f'folder{i:03}'
        child.mkdir()
        (child / 'a.txt').write_text('extra', encoding='utf-8')
    run_incremental_scan(root)
    dialog.refresh.click()
    wait_map(application, dialog)
    assert dialog.file_next.isEnabled() and dialog.group_next.isEnabled()
    first_ids = {r['file_id'] for r in dialog.snapshot['files']}
    total = dialog.snapshot['total_files']
    dialog.file_next.click()
    wait_map(application, dialog)
    assert not first_ids & {r['file_id'] for r in dialog.snapshot['files']}
    assert dialog.snapshot['total_files'] == total
    dialog.group_next.click()
    wait_map(application, dialog)
    assert dialog.snapshot['group_offset'] == 40
    assert dialog.group_previous.isEnabled()


@pytest.mark.parametrize('parent_close', [False, True])
def test_close_waits_for_area_thread(application, area_view, monkeypatch, parent_close):
    view, _, dialog = area_view
    entered, release = Event(), Event()
    def slow(*args, is_cancelled=None, **kwargs):
        entered.set()
        assert release.wait(10)
        check_cancelled(is_cancelled)
    monkeypatch.setattr(area_map_view, 'get_area_map', slow)
    dialog.refresh.click()
    try:
        wait_until(application, entered.is_set)
        if parent_close:
            view.close()
        else:
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.worker is not None and dialog._closing
    finally:
        release.set()
    wait_until(application, lambda: view.area_dialog is None)
    if parent_close:
        wait_until(application, lambda: not view.isVisible())


def test_map_uses_selected_path_even_when_search_is_global(application, window):
    view, root = window
    (root / 'a.txt').write_text('one', encoding='utf-8')
    sibling = root.parent / 'other'
    sibling.mkdir()
    (sibling / 'b.txt').write_text('two', encoding='utf-8')
    run_content_analysis(root)
    run_content_analysis(sibling)
    view.all_folders.setChecked(True)
    view.open_area_map()
    dialog = view.area_dialog
    wait_until(application, lambda: dialog.snapshot is not None and dialog.worker is None)
    assert dialog.snapshot['root'] == str(root)
    assert dialog.snapshot['total_files'] == 1
    dialog.close()
    wait_until(application, lambda: view.area_dialog is None)
