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
    view = gui.MainWindow(settings_path=tmp_path / 'settings.json')
    view.folder.setText(str(root))
    view.show()
    application.processEvents()
    yield view, root
    view.close()
    wait_until(application, lambda: view.worker is None and view.scope_worker is None)
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


@pytest.mark.parametrize('selected_encoding', ['auto', 'cp949'])
def test_gui_windows_text_encoding_analysis_search_and_preview(application, window, selected_encoding):
    import codecs
    view, root = window
    text = '한글 keyword'
    original = text.encode('cp949') if selected_encoding == 'cp949' else codecs.BOM_UTF16_LE + text.encode('utf-16-le')
    path = root / '한글.txt'
    path.write_bytes(original)
    if selected_encoding == 'cp949':
        view.start_analysis()
        wait_until(application, lambda: view.worker is None)
        assert '실패 1' in view.status.text()
    view.text_encoding.setCurrentIndex(view.text_encoding.findData(selected_encoding))
    view.start_analysis()
    assert not view.text_encoding.isEnabled()
    wait_until(application, lambda: view.worker is None)
    assert '추출 1' in view.status.text()
    assert view.text_encoding.isEnabled()
    view.query.setText('한글')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.table.rowCount() == 1
    assert '[한글]' in view.preview.toPlainText()
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    assert view.content_dialog.body.toPlainText() == text
    view.content_dialog.close()
    assert path.read_bytes() == original


def test_gui_settings_autosave_restore_without_query_or_automatic_work(application, window):
    from app.gui_settings import load_settings
    from database import db
    view, root = window
    view.max_size.setValue(25)
    view.text_encoding.setCurrentIndex(view.text_encoding.findData('cp949'))
    view.search_target.setCurrentIndex(view.search_target.findData('all'))
    view.all_folders.setChecked(True)
    view.limit.setValue(42)
    view.analysis_filter.setCurrentIndex(view.analysis_filter.findData('FAILED'))
    view.query.setText('private search term')
    wait_until(application, lambda: view.settings_path.exists())
    saved, notice = load_settings(view.settings_path)
    assert notice is None
    assert saved.folder == str(root) and saved.result_limit == 42
    assert 'private search term' not in view.settings_path.read_text(encoding='utf-8')
    view.close()
    restored = gui.MainWindow(settings_path=view.settings_path)
    try:
        restored.show()
        application.processEvents()
        assert restored._current_settings() == saved
        assert '통합 검색어' in restored.query.placeholderText()
        assert restored.query.text() == ''
        assert restored.worker is None and restored.table.rowCount() == 0
        assert not db.DB_PATH.exists()
    finally:
        restored.close()
        wait_until(application, lambda: restored.scope_worker is None)
        restored.close()
        restored.deleteLater()
        application.processEvents()


@pytest.mark.parametrize('payload', [b'{broken', b'{"version": 999}', b'{"folder":"\\ud800"}'])
def test_gui_corrupt_settings_startup_and_close_preserve_original(application, tmp_path, monkeypatch, payload):
    path = tmp_path / 'settings.json'
    path.write_bytes(payload)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'must-not-create.db')
    view = gui.MainWindow(settings_path=path)
    try:
        view.show()
        application.processEvents()
        assert view.settings_status.text()
        assert view.settings_status.isVisible()
        assert view.worker is None and view.folder.text() == ''
        view.close()
        assert path.read_bytes() == payload
        assert not db.DB_PATH.exists()
    finally:
        view.close()
        wait_until(application, lambda: view.scope_worker is None)
        view.close()
        view.deleteLater()
        application.processEvents()


def test_gui_reset_settings_keeps_original_file_and_analysis_database(application, window):
    from app.gui_settings import GuiSettings, load_settings
    from database.content_search import search_content
    view, root = window
    path = root / 'note.txt'
    path.write_bytes(b'hello keyword')
    run_content_analysis(root)
    view.limit.setValue(77)
    view.search_target.setCurrentIndex(view.search_target.findData('all'))
    view.options_toggle.setChecked(True)
    QTest.mouseClick(view.reset_settings_button, Qt.MouseButton.LeftButton)
    assert view._current_settings() == GuiSettings()
    assert load_settings(view.settings_path)[0] == GuiSettings()
    assert path.read_bytes() == b'hello keyword'
    assert len(search_content('keyword')) == 1
    assert '기본값' in view.status.text()


def test_gui_settings_save_error_does_not_block_analysis_and_can_retry(application, window, monkeypatch):
    from app.gui_settings import GuiSettings, load_settings
    view, root = window
    (root / 'note.txt').write_bytes(b'hello')
    original_save = gui.save_settings
    original_save(view.settings_path, GuiSettings(folder='before'))
    original_bytes = view.settings_path.read_bytes()
    def denied(*args):
        raise PermissionError('read only preferences')
    monkeypatch.setattr(gui, 'save_settings', denied)
    view.limit.setValue(200)
    wait_until(application, lambda: '저장 실패' in view.settings_status.text())
    assert view.settings_path.read_bytes() == original_bytes
    assert view.analyze.isEnabled()
    view.start_analysis()
    wait_until(application, lambda: view.worker is None)
    assert '추출 1' in view.status.text()
    monkeypatch.setattr(gui, 'save_settings', original_save)
    view._save_settings()
    assert load_settings(view.settings_path)[0].result_limit == 200
    assert view.settings_status.text() == ''


def test_gui_settings_saved_after_close_waits_for_analysis_cancellation(application, window, monkeypatch):
    from app.gui_settings import load_settings
    view, _ = window
    started = Event()
    view.limit.setValue(37)
    view.text_encoding.setCurrentIndex(view.text_encoding.findData('cp949'))
    monkeypatch.setattr(gui, 'run_content_analysis', slow_analysis(started))
    view.start_analysis()
    wait_until(application, started.is_set)
    assert not view.reset_settings_button.isEnabled()
    view.close()
    wait_until(application, lambda: view.worker is None and not view.isVisible())
    settings, _ = load_settings(view.settings_path)
    assert settings.result_limit == 37 and settings.text_encoding == 'cp949'


def test_gui_default_settings_location_uses_standard_config_directory(application, tmp_path, monkeypatch):
    from app.gui_settings import GuiSettings, save_settings
    target = tmp_path / 'standard-config' / 'file-network' / 'gui-settings.json'
    save_settings(target, GuiSettings(folder='remembered folder'))
    monkeypatch.setattr(gui.QStandardPaths, 'writableLocation', lambda location: str(target.parent.parent))
    view = gui.MainWindow()
    try:
        assert view.settings_path == target
        assert view.folder.text() == 'remembered folder'
    finally:
        view.close()
        wait_until(application, lambda: view.scope_worker is None)
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
    def operation(root, max_bytes, is_cancelled, progress_callback, text_encoding="auto"):
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


def test_gui_citations_keep_pdf_page_numbers_and_switch_back(application, window):
    from tests.test_pdf_extraction import make_pdf
    view, root = window
    make_pdf(root / 'note.pdf', ('', 'citationkeyword second page', '', 'fourth page'))
    run_content_analysis(root)
    view.query.setText('citationkeyword')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    dialog = view.content_dialog
    dialog.mode.setCurrentIndex(dialog.mode.findData('chunks'))
    wait_until(application, lambda: dialog.worker is None)
    assert 'PDF 2페이지' in dialog.summary.text()
    assert '인용 1/2' in dialog.summary.text()
    assert dialog.body.toPlainText() == 'citationkeyword second page'
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert not dialog.mode.isEnabled()
    wait_until(application, lambda: dialog.worker is None)
    assert 'PDF 4페이지' in dialog.summary.text()
    assert dialog.body.toPlainText() == 'fourth page'
    dialog.mode.setCurrentIndex(dialog.mode.findData('text'))
    wait_until(application, lambda: dialog.worker is None)
    assert 'fourth page' in dialog.body.toPlainText()
    assert '원본 페이지가 아닙니다' in dialog.note.text()
    dialog.close()
    application.processEvents()


def test_gui_citation_changed_revision_clears_body_and_refreshes(application, window):
    view, root = window
    path = root / 'note.txt'
    path.write_text('citationkeyword original', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('citationkeyword')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    dialog = view.content_dialog
    path.write_text('replacement content longer', encoding='utf-8')
    run_content_analysis(root)
    dialog.mode.setCurrentIndex(dialog.mode.findData('chunks'))
    wait_until(application, lambda: dialog.worker is None)
    assert not dialog.body.toPlainText()
    assert '분석 결과가 변경' in dialog.status.text()
    assert dialog.refresh.isEnabled() and dialog.mode.isEnabled()
    QTest.mouseClick(dialog.refresh, Qt.MouseButton.LeftButton)
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.body.toPlainText() == 'replacement content longer'
    assert '추출 본문 위치' in dialog.summary.text()
    assert 'PDF' not in dialog.summary.text()
    dialog.close()
    application.processEvents()


def test_gui_citation_legacy_data_requires_reanalysis(application, window):
    view, root = window
    (root / 'note.txt').write_text('citationkeyword old', encoding='utf-8')
    run_content_analysis(root)
    with db.get_connection() as connection:
        connection.execute('DELETE FROM document_chunks')
    view.query.setText('citationkeyword')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    dialog = view.content_dialog
    dialog.mode.setCurrentIndex(dialog.mode.findData('chunks'))
    wait_until(application, lambda: dialog.worker is None)
    assert not dialog.body.toPlainText()
    assert '다시 분석' in dialog.summary.text()
    assert not dialog.next.isEnabled()
    dialog.mode.setCurrentIndex(dialog.mode.findData('text'))
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.body.toPlainText() == 'citationkeyword old'
    dialog.close()
    application.processEvents()


def test_gui_excluded_folders_analysis_validation_and_reset(application, window):
    view, root = window
    (root / 'skip').mkdir()
    (root / 'skip' / 'hidden.txt').write_text('hiddenkeyword', encoding='utf-8')
    (root / 'kept.txt').write_text('keptkeyword', encoding='utf-8')
    view.excluded_dirs.setText('../invalid')
    view.start_analysis()
    assert view.worker is None
    assert '폴더명' in view.status.text()
    assert not db.DB_PATH.exists()
    view.excluded_dirs.setText(' skip, node_modules ')
    view.start_analysis()
    assert not view.excluded_dirs.isEnabled()
    wait_until(application, lambda: view.worker is None)
    assert view._outcome[0] == 'completed'
    assert view._outcome[1]['extracted'] == 1
    assert [row['name'] for row in db.get_active_files_under(root)] == ['kept.txt']
    view._save_settings(force=True)
    restored = gui.MainWindow(settings_path=view.settings_path)
    assert restored.excluded_dirs.text() == ' skip, node_modules '
    restored.close()
    wait_until(application, lambda: restored.scope_worker is None)
    restored.close()
    restored.deleteLater()
    view.reset_settings()
    assert view.excluded_dirs.text() == ''


@pytest.mark.parametrize('extension,location', [('.xlsx', "Excel 시트: <b>한글'&>"), ('.pptx', 'PowerPoint 슬라이드: 2')])
def test_gui_office_citation_original_locations_plain_text(application, window, extension, location):
    from tests.test_office_citations import make_sections
    view, root = window
    make_sections(root / ('note' + extension), [('빈 시작', ''), ("<b>한글'&>", 'officecitationkeyword'), ('빈 중간', ''), ('마지막', 'last body')])
    run_content_analysis(root)
    view.query.setText('officecitationkeyword')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    dialog = view.content_dialog
    dialog.mode.setCurrentIndex(dialog.mode.findData('chunks'))
    wait_until(application, lambda: dialog.worker is None)
    assert location in dialog.summary.text()
    assert dialog.summary.textFormat() == Qt.TextFormat.PlainText
    assert 'officecitationkeyword' in dialog.body.toPlainText()
    dialog.next.click()
    wait_until(application, lambda: dialog.worker is None)
    assert 'last body' in dialog.body.toPlainText()
    assert ('Excel 시트: 마지막' if extension == '.xlsx' else 'PowerPoint 슬라이드: 4') in dialog.summary.text()
    dialog.mode.setCurrentIndex(dialog.mode.findData('text'))
    wait_until(application, lambda: dialog.worker is None)
    assert 'officecitationkeyword' in dialog.body.toPlainText() and 'last body' in dialog.body.toPlainText()
    dialog.close()
    application.processEvents()


def test_gui_matching_citation_uses_completed_search_and_actual_page(application, window):
    from tests.test_pdf_extraction import make_pdf
    view, root = window
    make_pdf(root / 'note.pdf', ('first unrelated', '', 'needlekeyword here'))
    run_content_analysis(root)
    view.query.setText('needlekeyword')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.citation_button.isEnabled()
    view.query.setText('unsubmitted edit')
    before = list(view._results)
    QTest.mouseClick(view.citation_button, Qt.MouseButton.LeftButton)
    assert not view.citation_button.isEnabled()
    wait_until(application, lambda: view.worker is None)
    dialog = view.content_dialog
    assert dialog is not None and dialog.mode.currentData() == 'chunks'
    assert 'PDF 3페이지' in dialog.summary.text()
    assert 'needlekeyword' in dialog.body.toPlainText()
    assert view._results == before and view.table.currentRow() == 0
    dialog.close()
    application.processEvents()
    view.search_target.setCurrentIndex(view.search_target.findData('name'))
    view.query.setText('note')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert not view.citation_button.isEnabled()
    view.start_details()
    wait_until(application, lambda: view.worker is None)
    assert not view.citation_button.isEnabled()


def test_gui_no_matching_citation_preserves_results_and_full_body(application, window):
    view, root = window
    text = 'alpha ' + 'x ' * 1100 + 'beta'
    (root / 'note.txt').write_text(text, encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('alpha beta')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    before = list(view._results)
    view.start_matching_citation()
    wait_until(application, lambda: view.worker is None)
    assert view.content_dialog is None
    assert '모든 검색어를 포함하는 인용이 없습니다' in view.status.text()
    assert view._results == before and view.content_button.isEnabled()
    view.start_content_preview()
    wait_until(application, lambda: view.worker is None)
    assert view.content_dialog.body.toPlainText() == text
    view.content_dialog.close()
    application.processEvents()


def test_gui_unified_name_only_result_disables_matching_citation(application, window):
    view, root = window
    (root / 'needlekeyword.png').write_bytes(b'\xff')
    view.start_analysis()
    wait_until(application, lambda: view.worker is None)
    view.search_target.setCurrentIndex(view.search_target.findData('all'))
    view.query.setText('needlekeyword')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view._results[0]['match_source'] == 'name'
    assert not view.citation_button.isEnabled()
    assert view.content_button.isEnabled()


def test_gui_matching_citation_cancel_keeps_results(application, window, monkeypatch):
    view, root = window
    (root / 'note.txt').write_text('needle', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('needle')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    before = list(view._results)
    started = Event()
    def waiting(file_id, query, is_cancelled):
        started.set()
        while not is_cancelled():
            time.sleep(0.005)
        check_cancelled(is_cancelled)
    monkeypatch.setattr(gui, 'find_matching_citation', waiting)
    view.start_matching_citation()
    wait_until(application, started.is_set)
    view.cancel_task()
    wait_until(application, lambda: view.worker is None)
    assert view._outcome[0] == 'cancelled'
    assert view._results == before and view.table.currentRow() == 0
    assert view.citation_button.isEnabled() and view.content_button.isEnabled()


def test_gui_related_documents_keywords_preview_and_citation(application, window):
    view, root = window
    (root / 'source.txt').write_text('galaxy telescope astronomy', encoding='utf-8')
    (root / 'other.txt').write_text('galaxy telescope science', encoding='utf-8')
    run_content_analysis(root)
    view.search_target.setCurrentIndex(view.search_target.findData('all'))
    view.query.setText('astronomy')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view.related_button.isEnabled()
    view.related_button.click()
    assert not view.related_button.isEnabled()
    wait_until(application, lambda: view.worker is None)
    assert view._view_mode == 'related' and len(view._results) == 1
    assert view._results[0]['name'] == 'other.txt'
    assert view.table.horizontalHeaderItem(1).text() == '공유 키워드'
    assert 'galaxy' in view.preview.toPlainText()
    assert '공유 키워드' in view.preview.toPlainText()
    assert 'source.txt' in view.status.text()
    assert view.citation_button.isEnabled() and view.content_button.isEnabled()
    view.start_matching_citation()
    wait_until(application, lambda: view.worker is None)
    assert view.content_dialog is not None
    assert 'galaxy' in view.content_dialog.body.toPlainText()
    view.content_dialog.close()
    application.processEvents()
    view.start_related_documents()
    wait_until(application, lambda: view.worker is None)
    assert view._results[0]['name'] == 'source.txt'


def test_gui_related_error_preserves_filename_results(application, window):
    view, root = window
    (root / 'image.png').write_bytes(b'\xff')
    view.start_analysis()
    wait_until(application, lambda: view.worker is None)
    view.search_target.setCurrentIndex(view.search_target.findData('name'))
    view.query.setText('image')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    before = list(view._results)
    view.start_related_documents()
    wait_until(application, lambda: view.worker is None)
    assert view._outcome[0] == 'failed'
    assert '분석이 완료된' in view.status.text()
    assert view._results == before and view.table.currentRow() == 0
    assert view.related_button.isEnabled()


def test_gui_related_cancellation_preserves_original_results(application, window, monkeypatch):
    view, root = window
    (root / 'source.txt').write_text('galaxy', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('galaxy')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    before = list(view._results)
    started = Event()
    def waiting(file_id, limit, root_path, is_cancelled):
        started.set()
        while not is_cancelled():
            time.sleep(0.005)
        check_cancelled(is_cancelled)
    monkeypatch.setattr(gui, 'find_related_documents', waiting)
    view.start_related_documents()
    wait_until(application, started.is_set)
    view.cancel_task()
    wait_until(application, lambda: view.worker is None)
    assert view._outcome[0] == 'cancelled' and view._results == before
    assert view.related_button.isEnabled() and view.content_button.isEnabled()


def test_gui_graph_opens_candidate_and_source_snapshots(application, window):
    view, root = window
    (root / 'source.txt').write_text('galaxy telescope astronomy', encoding='utf-8')
    (root / 'other.txt').write_text('galaxy telescope science', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('astronomy')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert not view.graph_button.isEnabled()
    view.start_related_documents()
    wait_until(application, lambda: view.worker is None)
    before = list(view._results)
    assert view.graph_button.isEnabled()
    view.graph_button.click()
    graph = view.graph_dialog
    assert graph is not None and len(graph.nodes) == 2
    view.open_related_graph()
    assert view.graph_dialog is graph
    graph.selector.setCurrentIndex(1)
    graph.open_body.click()
    wait_until(application, lambda: view.worker is None)
    assert view.graph_dialog is None
    assert view.content_dialog.body.toPlainText() == 'galaxy telescope science'
    assert view._results == before
    view.content_dialog.close()
    application.processEvents()
    view.open_related_graph()
    view.graph_dialog.open_body.click()  # Root is not in the candidate table.
    wait_until(application, lambda: view.worker is None)
    assert view.content_dialog.body.toPlainText() == 'galaxy telescope astronomy'
    view.content_dialog.close()
    application.processEvents()
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert not view.graph_button.isEnabled() and view._related_snapshot is None


def test_gui_graph_rejects_changed_analysis_revision(application, window):
    view, root = window
    (root / 'source.txt').write_text('galaxy astronomy', encoding='utf-8')
    candidate = root / 'other.txt'
    candidate.write_text('galaxy science', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('astronomy')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_related_documents()
    wait_until(application, lambda: view.worker is None)
    view.open_related_graph()
    graph = view.graph_dialog
    graph.selector.setCurrentIndex(1)
    candidate.write_text('replacement document longer', encoding='utf-8')
    run_content_analysis(root)
    graph.open_body.click()
    wait_until(application, lambda: view.worker is None)
    assert view.content_dialog is None and view.graph_dialog is None
    assert view._outcome[0] == 'failed' and '분석 결과가 변경' in view.status.text()
    assert view.table.rowCount() == 1


def test_gui_empty_graph_and_parent_close_are_safe(application, window):
    view, root = window
    (root / 'source.txt').write_text('uniquekeyword', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('uniquekeyword')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_related_documents()
    wait_until(application, lambda: view.worker is None)
    assert view._results == [] and view.graph_button.isEnabled()
    view.open_related_graph()
    assert len(view.graph_dialog.nodes) == 1 and not view.graph_dialog.edges
    view.close()
    application.processEvents()
    assert view.graph_dialog is None and not view.isVisible()


@pytest.mark.parametrize('close_window', [False, True])
def test_gui_cancels_real_pdf_between_pages(application, window, monkeypatch, close_window):
    from database import file_contents
    from tests.test_pdf_extraction import make_pdf
    view, root = window
    make_pdf(root / 'note.pdf', ('first', 'second', 'third'))
    started, resume = Event(), Event()
    calls = []
    actual = pymupdf.Page.get_text
    def page_text(page, *args, **kwargs):
        result = actual(page, *args, **kwargs)
        calls.append(page.number)
        if len(calls) == 1:
            started.set()
            assert resume.wait(5), 'GUI did not release the current parser call'
        return result
    monkeypatch.setattr(pymupdf.Page, 'get_text', page_text)
    view.start_analysis()
    try:
        wait_until(application, started.is_set)
        if close_window:
            view.close()
        else:
            view.cancel_task()
    finally:
        resume.set()
    wait_until(application, lambda: view.worker is None)
    assert view._outcome[0] == 'cancelled' and calls == [0]
    identity = db.get_active_files_under(root)[0]['id']
    assert file_contents.get_file_content(identity) is None
    if close_window:
        assert not view.isVisible()
    else:
        view.start_analysis()
        wait_until(application, lambda: view.worker is None)
        assert view._outcome[0] == 'completed' and view._outcome[1]['extracted'] == 1
        assert 'third' in file_contents.get_file_content(identity)['text']


@pytest.mark.parametrize('invalid,expected', [('x' * 4097, '4096'), (' '.join(['word'] * 101), '100')])
def test_gui_invalid_search_keeps_related_results_and_recovers(application, window, invalid, expected):
    view, root = window
    (root / 'source.txt').write_text('galaxy astronomy', encoding='utf-8')
    (root / 'other.txt').write_text('galaxy science', encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('astronomy')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_related_documents()
    wait_until(application, lambda: view.worker is None)
    before = list(view._results)
    snapshot = view._related_snapshot
    preview = view.preview.toPlainText()
    view.query.setText(invalid)
    view.start_search()
    assert view.worker is None
    assert expected in view.status.text()
    assert view._results == before and view._related_snapshot is snapshot
    assert view.table.currentRow() == 0 and view.preview.toPlainText() == preview
    assert view.graph_button.isEnabled() and view.citation_button.isEnabled()
    view.query.setText('science')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert view._results[0]['name'] == 'other.txt'
    assert view._related_snapshot is None and view._view_mode == 'search'
