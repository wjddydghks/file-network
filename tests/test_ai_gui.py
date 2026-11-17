import json
import os
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.analyze_content import run_content_analysis
from services import openai_questions as service
from tests.test_gui import application, window, wait_until
from tests.test_ai_questions import response


@pytest.fixture
def ai_view(application, window, monkeypatch):
    view, root = window
    (root / 'alpha.txt').write_text('Alpha project Friday timeline ' * 160, encoding='utf-8')
    (root / 'beta.txt').write_text('Alpha project Monday timeline', encoding='utf-8')
    run_content_analysis(root)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('FILE_NETWORK_AI_ENABLED', raising=False)
    monkeypatch.delenv('FILE_NETWORK_OPENAI_MODEL', raising=False)
    def forbidden(*args):
        pytest.fail('Unexpected external call')
    monkeypatch.setattr(service, '_request', forbidden)
    view.query.setText('Alpha')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    for index, result in enumerate(view._results):
        if result['name'] == 'alpha.txt':
            view.table.selectRow(index)
            break
    view.ai_button.click()
    dialog = view.ai_dialog
    assert dialog is not None
    yield view, root, dialog
    if view.ai_dialog is not None:
        view.ai_dialog.close()
        wait_until(application, lambda: view.ai_dialog is None)


def prepare(application, dialog):
    dialog.add.click()
    dialog.question.setPlainText('When is the project timeline?')
    dialog.prepare.click()
    wait_until(application, lambda: dialog.worker is None)
    assert dialog._plan is not None, dialog.status.text()


def approve(dialog):
    dialog.enabled.setChecked(True)
    dialog.key.setText('test-key-not-real')
    dialog.approve.setChecked(True)
    assert dialog.send.isEnabled()


def test_gui_preview_no_key_no_call_and_search_preserved(application, ai_view):
    view, root, dialog = ai_view
    results = list(view._results)
    prepare(application, dialog)
    assert not dialog.enabled.isChecked() and not dialog.send.isEnabled()
    assert not dialog.approve.isChecked()
    plan = dialog._plan
    assert plan.sources[0].text in dialog.preview.toPlainText()
    payload = json.loads(dialog.payload.toPlainText())
    assert payload == plan.payload() and payload['store'] is False
    assert str(root) not in dialog.payload.toPlainText()
    assert dialog.key.echoMode() == dialog.key.EchoMode.Password
    dialog.close()
    wait_until(application, lambda: view.ai_dialog is None)
    assert view._results == results
    assert view.table.currentRow() >= 0
    view._save_settings()
    assert 'test-key' not in view.settings_path.read_text(encoding='utf-8')


@pytest.mark.parametrize('change', ['question', 'model', 'selection'])
def test_changes_invalidate_review_and_approval(application, ai_view, change):
    _, _, dialog = ai_view
    prepare(application, dialog)
    approve(dialog)
    if change == 'question':
        dialog.question.setPlainText('Different question')
    elif change == 'model':
        dialog.model.setText('gpt-4.1')
    else:
        dialog.chunk.setValue(2)
        dialog.add.click()
    assert dialog._plan is None and not dialog.send.isEnabled()
    assert not dialog.approve.isChecked() and not dialog.payload.toPlainText()


@pytest.mark.parametrize('change', ['key', 'enabled'])
def test_key_or_mode_change_revokes_approval(application, ai_view, change):
    _, _, dialog = ai_view
    prepare(application, dialog)
    approve(dialog)
    if change == 'key':
        dialog.key.setText('different-test-key')
    else:
        dialog.enabled.setChecked(False)
    assert dialog._plan is not None
    assert not dialog.approve.isChecked() and not dialog.send.isEnabled()


def test_source_limits_duplicate_and_remove(application, ai_view):
    _, _, dialog = ai_view
    dialog.add.click()
    dialog.add.click()
    assert dialog.sources.count() == 1
    for chunk in (2, 3, 4, 5):
        dialog.chunk.setValue(chunk)
        dialog.add.click()
    assert dialog.sources.count() == 4
    dialog.sources.setCurrentRow(1)
    dialog.remove.click()
    assert dialog.sources.count() == 3
    assert len(dialog._selections) == 3


@pytest.mark.parametrize('mode', ['sensitive', 'missing_chunk', 'missing_key', 'stale'])
def test_gui_fail_closed(application, ai_view, mode):
    _, root, dialog = ai_view
    dialog.add.click()
    dialog.question.setPlainText('user@example.com' if mode == 'sensitive' else 'When?')
    if mode == 'missing_chunk':
        dialog.sources.setCurrentRow(0)
        dialog.remove.click()
        dialog.chunk.setValue(9999)
        dialog.add.click()
    dialog.prepare.click()
    wait_until(application, lambda: dialog.worker is None)
    if mode in ('sensitive', 'missing_chunk'):
        assert dialog._plan is None
        assert not dialog.send.isEnabled()
        return
    assert dialog._plan is not None
    if mode == 'stale':
        (root / 'alpha.txt').write_text('Changed timeline', encoding='utf-8')
        run_content_analysis(root)
        dialog.key.setText('test-key-not-real')
    dialog.enabled.setChecked(True)
    dialog.approve.setChecked(True)
    dialog.send.click()
    wait_until(application, lambda: dialog.worker is None)
    assert dialog._answer is None and dialog._plan is None
    assert not dialog.send.isEnabled()
    assert '변경' in dialog.status.text() if mode == 'stale' else 'OPENAI_API_KEY' in dialog.status.text()


def test_answer_local_citation_and_changed_revision(application, ai_view, monkeypatch):
    view, root, dialog = ai_view
    prepare(application, dialog)
    source_text = dialog._plan.sources[0].text
    calls = []
    monkeypatch.setattr(service, '_request', lambda *args: calls.append(args) or response())
    approve(dialog)
    dialog.send.click()
    dialog.send.click()  # Duplicate click while worker is active has no effect.
    wait_until(application, lambda: dialog.worker is None)
    assert len(calls) == 1
    assert dialog._answer is not None and dialog._plan is None
    assert dialog.tabs.currentIndex() == 2
    assert '다음 주 금요일' in dialog.answer.toPlainText()
    assert 'When is the project timeline?' in dialog.answer.toPlainText()
    assert dialog.citations.count() == 1 and not dialog.send.isEnabled()
    dialog.open_citation.click()
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.reference.toPlainText() == source_text
    (root / 'alpha.txt').write_text('Changed timeline', encoding='utf-8')
    run_content_analysis(root)
    dialog.open_citation.click()
    wait_until(application, lambda: dialog.worker is None)
    assert not dialog.reference.toPlainText()
    assert '변경' in dialog.status.text()
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['provider', 'unknown_source'])
def test_gui_api_error_requires_fresh_review(application, ai_view, monkeypatch, failure):
    _, _, dialog = ai_view
    prepare(application, dialog)
    calls = []
    def request(*args):
        calls.append(args)
        if failure == 'provider':
            raise service.AIServiceError('호출 실패')
        return response(citations=['S99'])
    monkeypatch.setattr(service, '_request', request)
    approve(dialog)
    dialog.send.click()
    wait_until(application, lambda: dialog.worker is None)
    assert len(calls) == 1 and dialog._answer is None
    assert not dialog.send.isEnabled() and not dialog.approve.isChecked()
    dialog.send_request()
    assert len(calls) == 1


@pytest.mark.parametrize('close_parent', [False, True])
def test_close_waits_for_api_thread(application, ai_view, monkeypatch, close_parent):
    view, _, dialog = ai_view
    prepare(application, dialog)
    entered, release = Event(), Event()
    calls = []
    def request(*args):
        calls.append(args)
        entered.set()
        assert release.wait(10)
        return response()
    monkeypatch.setattr(service, '_request', request)
    approve(dialog)
    dialog.send.click()
    try:
        wait_until(application, entered.is_set)
        if close_parent:
            view.close()
        else:
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        application.processEvents()
        assert dialog.worker is not None
        assert dialog._closing
        assert view.ai_dialog is dialog
    finally:
        release.set()
    wait_until(application, lambda: view.ai_dialog is None)
    assert len(calls) == 1
    if close_parent:
        assert not view.isVisible()


def test_escape_clears_key_without_external_call(application, ai_view):
    view, _, dialog = ai_view
    dialog.key.setText('never-save-this-key')
    dialog.question.setPlainText('unsent question')
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    wait_until(application, lambda: view.ai_dialog is None)
    view._save_settings()
    assert 'never-save-this-key' not in view.settings_path.read_text(encoding='utf-8')


def test_multi_document_comparison_uses_only_selected_excerpts(application, ai_view, monkeypatch):
    _, root, dialog = ai_view
    dialog.add.click()
    first_id = dialog.candidates.currentData()
    for index in range(dialog.candidates.count()):
        if dialog.candidates.itemData(index) != first_id:
            dialog.candidates.setCurrentIndex(index)
            break
    dialog.add.click()
    dialog.question.setPlainText('두 인용의 일정을 비교해줘.')
    dialog.prepare.click()
    wait_until(application, lambda: dialog.worker is None)
    assert len(dialog._plan.sources) == 2
    assert 'Friday' in dialog.preview.toPlainText() and 'Monday' in dialog.preview.toPlainText()
    calls = []
    monkeypatch.setattr(service, '_request', lambda *args: calls.append(args) or response(citations=['S1', 'S2']))
    approve(dialog)
    dialog.send.click()
    wait_until(application, lambda: dialog.worker is None)
    assert len(calls) == 1 and dialog.citations.count() == 2
    payload = calls[0][0]
    assert str(root) not in json.dumps(payload)
    for index, word in enumerate(['Friday', 'Monday']):
        dialog.citations.setCurrentIndex(index)
        dialog.open_citation.click()
        wait_until(application, lambda: dialog.worker is None)
        assert word in dialog.reference.toPlainText()
