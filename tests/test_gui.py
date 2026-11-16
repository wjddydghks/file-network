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
    (root / "unsupported.docx").write_bytes(b"docx")
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
    assert [Path(path) for path in opened] == [root / "unsupported.docx"]
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
