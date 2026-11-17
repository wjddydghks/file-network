import os
import time
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6", reason="GUI tests require requirements-gui.txt")
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import pymupdf

from app import gui
from app.analyze_content import run_content_analysis
from core.operations import check_cancelled
from database import db


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def wait_until(application, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        application.processEvents()
        QTest.qWait(5)
        assert time.monotonic() < deadline, "GUI operation timed out"
    application.processEvents()


@pytest.fixture
def window(application, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    view = gui.MainWindow()
    view.folder.setText(str(root))
    view.show()
    application.processEvents()
    yield view, root
    view.close()
    wait_until(application, lambda: view.worker is None)
    view.close()
    view.deleteLater()
    application.processEvents()


def test_gui_content_segments_navigation_find_refresh_and_changed_revision(application, window):
    from database import file_contents
    from core.extractors.text import TextExtractionResult
    view, root = window
    path = root / 'note.txt'
    text = ('prefix line\n' * 10_000)[:file_contents.MAX_PREVIEW_CHARS] + 'Alpha needle beta NEEDLE'
    path.write_bytes(text.encode('utf-8'))
    run_content_analysis(root)
    view.query.setText('needle')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    dialog = view.content_dialog
    assert not dialog.previous.isEnabled() and dialog.next.isEnabled()
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert dialog.worker is not None
    assert not dialog.next.isEnabled()
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.body.toPlainText() == 'Alpha needle beta NEEDLE'
    assert '구간 2/2' in dialog.summary.text()
    assert dialog.previous.isEnabled() and not dialog.next.isEnabled()
    from PySide6.QtGui import QKeySequence
    assert dialog.find_shortcut.key() == QKeySequence(QKeySequence.StandardKey.Find)
    dialog.find_shortcut.activated.emit()
    application.processEvents()
    assert dialog.find_query.hasFocus()
    dialog.find_query.setText('needle')
    QTest.keyClick(dialog.find_query, Qt.Key.Key_Return)
    first = dialog.body.textCursor().selectionStart()
    assert dialog.body.textCursor().selectedText() == 'needle'
    dialog.find_next()
    second = dialog.body.textCursor().selectionStart()
    assert second > first
    dialog.find_next()
    assert dialog.body.textCursor().selectionStart() == first
    dialog.find_next(backward=True)
    assert dialog.body.textCursor().selectionStart() == second
    dialog.find_query.setText('missing')
    dialog.find_next()
    assert '없습니다' in dialog.status.text()
    dialog.find_query.clear()
    dialog.find_next()
    assert '입력하세요' in dialog.status.text()
    QTest.mouseClick(dialog.previous, Qt.MouseButton.LeftButton)
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.body.toPlainText() == text[:file_contents.MAX_PREVIEW_CHARS]
    # A separately reanalyzed document must not be mixed with this snapshot.
    file_id = view._results[0]['file_id']
    file_contents.save_file_content(file_id, TextExtractionResult(str(path), 'EXTRACTED', text='new analysis'), 'new')
    dialog.load_segment(1)
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.body.toPlainText() == ''
    assert '변경' in dialog.status.text()
    assert not dialog.next.isEnabled()
    QTest.mouseClick(dialog.refresh, Qt.MouseButton.LeftButton)
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.body.toPlainText() == 'new analysis'
    assert '구간 1/1' in dialog.summary.text()
    dialog.close()
    assert view.content_dialog is None
    assert view.table.rowCount() == 1


@pytest.mark.parametrize('close_method', ['close', 'escape', 'parent'])
def test_gui_segment_worker_close_escape_and_parent_wait_safely(application, window, monkeypatch, close_method):
    view, root = window
    (root / 'note.txt').write_bytes(b'needle')
    run_content_analysis(root)
    view.query.setText('needle')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    dialog = view.content_dialog
    started = Event()
    release = Event()

    def slow_preview(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return None

    monkeypatch.setattr(gui, 'get_content_preview', slow_preview)
    dialog.load_segment(0, refresh=True)
    try:
        wait_until(application, started.is_set)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        wait_until(application, lambda: bool(heartbeat))
        if close_method == 'escape':
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        elif close_method == 'parent':
            view.close()
        else:
            dialog.close()
        assert dialog.worker is not None
        assert dialog._closing
    finally:
        release.set()
    wait_until(application, lambda: view.content_dialog is None)
    if close_method == 'parent':
        assert not view.isVisible()


def test_gui_segment_deleted_and_query_error_clear_text_and_allow_refresh(application, window, monkeypatch):
    from app.incremental_scan import run_incremental_scan
    view, root = window
    path = root / 'note.txt'
    path.write_bytes(b'needle')
    run_content_analysis(root)
    view.query.setText('needle')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    dialog = view.content_dialog
    original = gui.get_content_preview

    def fail(*args, **kwargs):
        raise RuntimeError('구간 조회 오류')

    monkeypatch.setattr(gui, 'get_content_preview', fail)
    dialog.load_segment(0, refresh=True)
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.body.toPlainText() == ''
    assert '구간 조회 오류' in dialog.status.text()
    assert dialog.refresh.isEnabled()
    monkeypatch.setattr(gui, 'get_content_preview', original)
    dialog.load_segment(0, refresh=True)
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.body.toPlainText() == 'needle'
    path.unlink()
    run_incremental_scan(root)
    dialog.load_segment(0, refresh=True)
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.body.toPlainText() == ''
    assert '찾을 수 없습니다' in dialog.status.text()
    assert not dialog.find_forward.isEnabled()
    dialog.close()


def test_gui_stored_content_preview_preserves_search_and_handles_changed_file(application, window):
    from app.incremental_scan import run_incremental_scan

    view, root = window
    path = root / 'note.txt'
    text = 'start of document\n' + 'context ' * 100 + 'needle\nend of document'
    path.write_text(text, encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('needle')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    snippet = view.preview.toPlainText()
    view.start_content_preview()
    assert not view.content_button.isEnabled()
    wait_until(application, lambda: view.worker is None)
    dialog = view.content_dialog
    assert dialog.isVisible()
    assert dialog.body.isReadOnly()
    assert dialog.body.toPlainText() == text
    assert '분석 완료' in dialog.details.toPlainText()
    assert view.table.rowCount() == 1
    assert view.table.currentRow() == 0
    assert view.preview.toPlainText() == snippet
    dialog.close()
    path.write_text('changed document with longer body', encoding='utf-8')
    run_incremental_scan(root)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    assert view.content_dialog.body.toPlainText() == ''
    assert '재분석 필요' in view.content_dialog.details.toPlainText()
    view.content_dialog.close()
    path.unlink()
    run_incremental_scan(root)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    assert '찾을 수 없습니다' in view.status.text()
    assert view.content_button.isEnabled()


def test_gui_preview_from_pending_details_and_truncation(application, window):
    from app.incremental_scan import run_incremental_scan
    from core.extractors.text import TextExtractionResult
    from database import file_contents

    view, root = window
    path = root / 'pending.txt'
    path.write_text('x', encoding='utf-8')
    run_incremental_scan(root)
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    assert '미분석' in view.content_dialog.details.toPlainText()
    assert view.content_dialog.body.toPlainText() == ''
    view.content_dialog.close()
    text = '가' * (file_contents.MAX_PREVIEW_CHARS + 1)
    file_id = view._results[0]['file_id']
    file_contents.save_file_content(file_id, TextExtractionResult(str(path), 'EXTRACTED', text=text), 'test')
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    assert len(view.content_dialog.body.toPlainText()) == file_contents.MAX_PREVIEW_CHARS
    assert '100,001' in view.content_dialog.summary.text()
    assert '구간 1/2' in view.content_dialog.summary.text()
    assert view.table.columnCount() == 4
    view.content_dialog.close()


def test_gui_content_preview_error_restores_results_and_controls(application, window, monkeypatch):
    view, root = window
    (root / 'note.txt').write_text('needle', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('needle')
    view.start_search()
    wait_until(application, lambda: view.worker is None)

    def fail(file_id):
        raise RuntimeError('본문 조회 오류')

    monkeypatch.setattr(gui, 'get_content_preview', fail)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    assert '본문 조회 오류' in view.status.text()
    assert view.table.rowCount() == 1
    assert view.content_button.isEnabled()
    assert view.search.isEnabled()
    assert view.content_dialog is None


@pytest.mark.parametrize('close_window', [False, True])
def test_gui_content_preview_cancel_and_close_wait_for_worker(application, window, monkeypatch, close_window):
    view, root = window
    (root / 'note.txt').write_text('needle', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('needle')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    started = Event()
    release = Event()

    def slow_preview(file_id):
        started.set()
        assert release.wait(5)
        return None

    monkeypatch.setattr(gui, 'get_content_preview', slow_preview)
    view.start_content_preview()
    wait_until(application, started.is_set)
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    wait_until(application, lambda: bool(heartbeat))
    if close_window:
        view.close()
    else:
        view.cancel_task()
    release.set()
    wait_until(application, lambda: view.worker is None)
    assert view.content_dialog is None
    if close_window:
        assert not view.isVisible()
    else:
        assert '취소' in view.status.text()
        assert view.table.rowCount() == 1
        assert view.content_button.isEnabled()


def test_gui_unified_search_sources_dedup_preview_and_target_switch(application, window):
    view, root = window
    (root / 'needle.txt').write_text('needle body', encoding='utf-8')
    (root / 'needle.png').write_bytes(b'\xff')
    run_content_analysis(root)
    view.query.setText('needle')
    view.search_target.setCurrentIndex(view.search_target.findData('all'))
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.columnCount() == 4
    assert view.table.rowCount() == 2
    assert view.table.horizontalHeaderItem(1).text() == '일치 위치'
    assert view.table.item(0, 1).text() == '본문'
    assert view.table.item(1, 1).text() == '파일명'
    assert '일치 위치: 본문' in view.preview.toPlainText()
    assert '[needle]' in view.preview.toPlainText()
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    assert view.content_dialog.body.toPlainText() == 'needle body'
    view.content_dialog.close()
    view.table.selectRow(1)
    assert '일치 위치: 파일명' in view.preview.toPlainText()
    assert 'needle.png' in view.preview.toPlainText()
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    assert view.table.horizontalHeaderItem(1).text() == '상태'
    assert '일치 위치:' not in view.preview.toPlainText()
    view.search_target.setCurrentIndex(view.search_target.findData('name'))
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.columnCount() == 3
    assert view.table.rowCount() == 2
    assert '일치 위치:' not in view.preview.toPlainText()
    view.search_target.setCurrentIndex(view.search_target.findData('content'))
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1


def test_gui_unified_search_scope_limit_and_error_recovery(application, window, monkeypatch):
    view, root = window
    sibling = root.parent / 'outside'
    sibling.mkdir()
    for folder in (root, sibling):
        (folder / 'needle.png').write_bytes(b'\xff')
        (folder / 'document.txt').write_text('needle body', encoding='utf-8')
        run_content_analysis(folder)
    view.query.setText('needle')
    view.search_target.setCurrentIndex(view.search_target.findData('all'))
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 2
    view.all_folders.setChecked(True)
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 4
    view.limit.setValue(1)
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1
    assert view._results[0]['match_source'] == 'content'

    def fail(*args, **kwargs):
        raise RuntimeError('통합 검색 오류')

    monkeypatch.setattr(gui, 'search_all', fail)
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert '통합 검색 오류' in view.status.text()
    assert view.search_target.isEnabled()
    assert not view.content_button.isEnabled()


def test_gui_real_text_and_pdf_analysis_search_preview_and_open(application, window, monkeypatch):
    view, root = window
    (root / "note.txt").write_text("Hello needle", encoding="utf-8")
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "PDF needle")
        (root / "document.pdf").write_bytes(document.tobytes())
    QTest.mouseClick(view.analyze, Qt.MouseButton.LeftButton)
    assert view.worker is not None
    assert not view.analyze.isEnabled()
    wait_until(application, lambda: view.worker is None)
    assert "추출 2" in view.status.text()
    assert view.analyze.isEnabled()

    view.query.setText("needle")
    QTest.mouseClick(view.search, Qt.MouseButton.LeftButton)
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 2
    assert "needle" in view.preview.toPlainText()
    assert view.open_button.isEnabled()
    opened = []
    monkeypatch.setattr(gui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    QTest.mouseClick(view.open_button, Qt.MouseButton.LeftButton)
    assert [Path(path) for path in opened] == [Path(view._results[0]["path"])]
    os.unlink(opened[0])
    QTest.mouseClick(view.open_button, Qt.MouseButton.LeftButton)
    assert "파일을 찾을 수 없습니다" in view.status.text()
    assert len(opened) == 1


def test_gui_search_scope_and_no_results(application, window):
    view, root = window
    sibling = root.parent / "outside"
    sibling.mkdir()
    for folder in (root, sibling):
        (folder / "sample.txt").write_text("keyword", encoding="utf-8")
        run_content_analysis(folder)
    view.query.setText("keyword")
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1
    view.all_folders.setChecked(True)
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 2
    view.query.setText("missing")
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 0
    assert not view.open_button.isEnabled()
    assert "검색 결과가 없습니다" in view.status.text()


def test_gui_validation_does_not_start_work(window):
    view, root = window
    view.folder.clear()
    view.start_analysis()
    assert view.worker is None
    assert "폴더" in view.status.text()
    view.folder.setText(str(root / "missing"))
    view.start_analysis()
    assert view.worker is None
    view.query.clear()
    view.start_search()
    assert view.worker is None
    assert "검색어" in view.status.text()


def test_gui_worker_failure_restores_controls(application, window, monkeypatch):
    view, _ = window

    def fail(*args, **kwargs):
        raise RuntimeError("검색 오류")

    monkeypatch.setattr(gui, "search_content", fail)
    view.query.setText("keyword")
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert "검색 오류" in view.status.text()
    assert view.search.isEnabled()
    assert not view.cancel.isEnabled()
    assert view.progress.value() == 0


def slow_analysis(started):
    def operation(root, max_bytes, is_cancelled, progress_callback):
        started.set()
        progress_callback({"phase": "scan", "processed": 10})
        while not is_cancelled():
            Event().wait(0.005)
        check_cancelled(is_cancelled)
    return operation


def test_gui_remains_responsive_and_can_cancel(application, window, monkeypatch):
    view, _ = window
    started = Event()
    monkeypatch.setattr(gui, "run_content_analysis", slow_analysis(started))
    view.start_analysis()
    wait_until(application, started.is_set)
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    wait_until(application, lambda: bool(heartbeat))
    assert view.worker is not None
    QTest.mouseClick(view.cancel, Qt.MouseButton.LeftButton)
    wait_until(application, lambda: view.worker is None)
    assert "취소" in view.status.text()
    assert view.progress.value() == 0
    assert view.analyze.isEnabled()
    assert not view.cancel.isEnabled()


def test_closing_gui_waits_for_worker_without_destroying_running_thread(application, window, monkeypatch):
    view, _ = window
    started = Event()
    monkeypatch.setattr(gui, "run_content_analysis", slow_analysis(started))
    view.start_analysis()
    wait_until(application, started.is_set)
    view.close()
    wait_until(application, lambda: view.worker is None and not view.isVisible())
    assert view._closing


def test_folder_picker_updates_folder_and_preserves_it_when_cancelled(window, monkeypatch):
    view, root = window
    view.folder.clear()
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *args: str(root))
    QTest.mouseClick(view.browse, Qt.MouseButton.LeftButton)
    assert view.folder.text() == str(root)
    monkeypatch.setattr(gui.QFileDialog, "getExistingDirectory", lambda *args: "")
    QTest.mouseClick(view.browse, Qt.MouseButton.LeftButton)
    assert view.folder.text() == str(root)


def test_gui_analysis_details_filter_reasons_open_and_switch_to_search(application, window, monkeypatch):
    view, root = window
    (root / "good.txt").write_text("Hello keyword", encoding="utf-8")
    (root / "broken.txt").write_bytes(b"\xff")
    (root / "unsupported.doc").write_bytes(b"docx")
    view.start_analysis()
    wait_until(application, lambda: view.worker is None)
    assert "실패 1" in view.status.text()
    QTest.mouseClick(view.details_button, Qt.MouseButton.LeftButton)
    assert not view.details_button.isEnabled()
    wait_until(application, lambda: view.worker is None)
    assert view.table.columnCount() == 4
    assert view.table.rowCount() == 3
    assert "UTF-8" in view.preview.toPlainText()
    assert "오류 상세" in view.preview.toPlainText()
    view.analysis_filter.setCurrentIndex(view.analysis_filter.findData("SKIPPED"))
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1
    assert "지원하지 않는 형식" in view.table.item(0, 2).text()
    assert "원본 파일은 열 수 있습니다" in view.preview.toPlainText()
    opened = []
    monkeypatch.setattr(gui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    QTest.mouseClick(view.open_button, Qt.MouseButton.LeftButton)
    assert [Path(path) for path in opened] == [root / "unsupported.doc"]
    view.query.setText("Hello")
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.columnCount() == 3
    assert view.table.rowCount() == 1
    assert "[Hello]" in view.preview.toPlainText()
    assert "상태:" not in view.preview.toPlainText()


def test_gui_details_show_pending_files_scope_and_limit(application, window):
    from app.incremental_scan import run_incremental_scan

    view, root = window
    sibling = root.parent / "outside"
    sibling.mkdir()
    for folder in (root, sibling):
        (folder / "pending.txt").write_text("hello", encoding="utf-8")
        run_incremental_scan(folder)
    view.analysis_filter.setCurrentIndex(view.analysis_filter.findData("PENDING"))
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1
    assert "미분석" in view.preview.toPlainText()
    view.all_folders.setChecked(True)
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 2
    view.limit.setValue(1)
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1
    assert "최대 1개" in view.status.text()


def test_gui_filename_search_unextracted_files_and_switch_to_content(application, window, monkeypatch):
    from app.incremental_scan import run_incremental_scan

    view, root = window
    (root / 'needle image.png').write_bytes(b'\xff')
    (root / 'text.txt').write_text('needle body', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('needle')
    view.search_target.setCurrentIndex(view.search_target.findData('name'))
    assert '파일명' in view.query.placeholderText()
    view.start_search()
    assert not view.search_target.isEnabled()
    wait_until(application, lambda: view.worker is None)
    assert view.search_target.isEnabled()
    assert view.table.rowCount() == 1
    assert view.table.horizontalHeaderItem(2).text() == '일치 파일명'
    assert view.preview.toPlainText() == 'needle image.png'
    opened = []
    monkeypatch.setattr(gui.QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    view.open_selected()
    assert [Path(path) for path in opened] == [root / 'needle image.png']
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    assert view.table.columnCount() == 4
    view.search_target.setCurrentIndex(view.search_target.findData('content'))
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1
    assert view.table.horizontalHeaderItem(2).text() == '일치 본문'
    assert '[needle]' in view.preview.toPlainText()
    # Metadata-only files can also be found without any content analysis.
    (root / 'pending needle.zip').write_bytes(b'\xff')
    run_incremental_scan(root)
    view.search_target.setCurrentIndex(view.search_target.findData('name'))
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 2


def test_gui_filename_scope_limit_and_error_recovery(application, window, monkeypatch):
    from app.incremental_scan import run_incremental_scan

    view, root = window
    sibling = root.parent / 'outside'
    sibling.mkdir()
    for folder in (root, sibling):
        (folder / 'keyword.png').write_bytes(b'\xff')
        run_incremental_scan(folder)
    view.search_target.setCurrentIndex(view.search_target.findData('name'))
    view.query.setText('keyword')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1
    view.all_folders.setChecked(True)
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 2
    view.limit.setValue(1)
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1

    def fail(*args, **kwargs):
        raise RuntimeError('파일명 검색 오류')

    monkeypatch.setattr(gui, 'search_filename', fail)
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert '파일명 검색 오류' in view.status.text()
    assert view.search_target.isEnabled()
    assert view.search.isEnabled()


def test_gui_details_error_restores_controls(application, window, monkeypatch):
    view, _ = window

    def fail(*args, **kwargs):
        raise RuntimeError("분석 내역 조회 실패")

    monkeypatch.setattr(gui, "list_analysis_results", fail)
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    assert "분석 내역 조회 실패" in view.status.text()
    assert view.details_button.isEnabled()
    assert view.analysis_filter.isEnabled()
    assert not view.cancel.isEnabled()


def test_gui_office_analysis_search_and_invalid_document_details(application, window):
    from tests.office_fixtures import make_office

    view, root = window
    for extension in (".docx", ".xlsx", ".pptx"):
        make_office(root / ("sample" + extension), "Officekeyword 한글")
    (root / "broken.docx").write_bytes(b"broken")
    view.start_analysis()
    wait_until(application, lambda: view.worker is None)
    assert "추출 3" in view.status.text()
    assert "실패 1" in view.status.text()
    view.query.setText("Officekeyword")
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 3
    assert "Officekeyword" in view.preview.toPlainText()
    view.analysis_filter.setCurrentIndex(view.analysis_filter.findData("FAILED"))
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1
    assert "Office 문서 읽기·해석 실패" in view.preview.toPlainText()
