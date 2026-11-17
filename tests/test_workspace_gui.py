from threading import Event

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app import gui
from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.operations import check_cancelled
from database import db
from tests.test_gui import application, window, wait_until


def settled(application, view):
    wait_until(application, lambda: view.scope_worker is None and not view._scope_timer.isActive())


def test_folder_status_pending_body_ready_and_history(application, window):
    view, root = window
    settled(application, view)
    assert view.scope_badge.text() == '미분석 범위'
    assert not db.DB_PATH.exists()
    (root / 'project.txt').write_text('Project plan', encoding='utf-8')
    run_incremental_scan(root)
    view.refresh_scope.click()
    settled(application, view)
    assert view.scope_metrics['files'].text() == '1'
    assert view.scope_metrics['extracted'].text() == '0'
    assert view.scope_badge.text() == '분석 확인 필요'
    assert view.folder_history.count() == 1
    view.start_analysis()
    assert view.scope_badge.text() == '분석 중'
    wait_until(application, lambda: view.worker is None)
    settled(application, view)
    assert view.scope_badge.text() == '본문 검색 가능'
    assert view.scope_metrics['extracted'].text() == '1'
    assert '본문 저장' in view.scope_detail.text()
    assert '변경은 다시 분석' in view.scope_detail.text()


def test_scope_child_all_and_saved_folder_selection(application, window):
    view, root = window
    child = root / 'child'
    child.mkdir()
    (child / 'one.txt').write_text('One project', encoding='utf-8')
    (root / 'two.txt').write_text('Two project', encoding='utf-8')
    run_content_analysis(root)
    view.folder.setText(str(child))
    settled(application, view)
    assert view.scope_metrics['files'].text() == '1'
    assert '상위 폴더' in view.scope_detail.text()
    view.all_folders.setChecked(True)
    settled(application, view)
    assert view.scope_metrics['files'].text() == '2'
    assert '모든 저장 폴더' in view.scope_title.text()
    view._select_history_folder(view.folder_history.item(0))
    settled(application, view)
    assert view.folder.text() == str(root)
    assert not view.all_folders.isChecked()
    assert view.scope_metrics['files'].text() == '2'


def test_incomplete_scan_does_not_hide_searchable_saved_data(application, window):
    view, root = window
    (root / 'one.txt').write_text('One project', encoding='utf-8')
    run_content_analysis(root)
    with db.get_connection() as conn:
        conn.execute("UPDATE scan_sessions SET status='CANCELLED',scan_complete=0")
    view.refresh_workspace_status()
    settled(application, view)
    assert view.scope_badge.text() == '미완료 스캔 기록'
    assert view.scope_metrics['extracted'].text() == '1'


def test_late_status_cannot_replace_new_path(application, window, monkeypatch):
    view, root = window
    settled(application, view)
    other = root / 'other'
    other.mkdir()
    entered, release = Event(), Event()
    original = gui.get_workspace_status
    def delayed(path, is_cancelled=None):
        if path == str(root):
            entered.set()
            assert release.wait(10)
        return original(path, is_cancelled)
    monkeypatch.setattr(gui, 'get_workspace_status', delayed)
    view.refresh_workspace_status()
    try:
        wait_until(application, entered.is_set)
        view.folder.setText(str(other))
        QTest.qWait(300)
        application.processEvents()
    finally:
        release.set()
    settled(application, view)
    assert view._scope_request[0] == str(other)
    assert view.scope_badge.text() == '미분석 범위'
    assert view.scope_metrics['files'].text() == '0'


def test_window_close_waits_for_summary_thread(application, window, monkeypatch):
    view, root = window
    settled(application, view)
    entered, release = Event(), Event()
    def delayed(path, is_cancelled=None):
        entered.set()
        assert release.wait(10)
        check_cancelled(is_cancelled)
    monkeypatch.setattr(gui, 'get_workspace_status', delayed)
    view.refresh_workspace_status()
    try:
        wait_until(application, entered.is_set)
        view.close()
        assert view.scope_worker is not None and view._closing
    finally:
        release.set()
    wait_until(application, lambda: view.scope_worker is None and not view.isVisible())
