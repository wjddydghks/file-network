from threading import Event

import pytest

from app.analyze_content import run_content_analysis
from services import openai_questions as service
from tests.test_area_map_gui import area_view, wait_map
from tests.test_gui import application, window, wait_until
from tests.test_ai_themes import theme_data, theme_response


@pytest.fixture
def theme_view(application, area_view, monkeypatch):
    view, root, area = area_view
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('FILE_NETWORK_AI_ENABLED', raising=False)
    monkeypatch.setattr(service, '_request', lambda *a: pytest.fail('Unexpected external request'))
    area.theme_button.click()
    dialog = area.theme_dialog
    assert dialog is not None
    yield view, root, area, dialog
    if view.area_dialog is not None and area.theme_dialog is not None:
        area.theme_dialog.close()
        wait_until(application, lambda: area.theme_dialog is None)


def prepare(application, dialog):
    dialog.add.click()
    dialog.candidates.setCurrentIndex(1)
    dialog.add.click()
    dialog.question.setPlainText('프로젝트 일정과 비용의 관계')
    dialog.prepare.click()
    wait_until(application, lambda: dialog.worker is None)
    assert dialog._plan is not None, dialog.status.text()


def approve(dialog):
    dialog.enabled.setChecked(True)
    dialog.key.setText('test-theme-key')
    dialog.approve.setChecked(True)
    assert dialog.send.isEnabled()


def test_theme_prepare_is_local_and_requires_explicit_consent(application, theme_view):
    _, root, area, dialog = theme_view
    prepare(application, dialog)
    assert not dialog.enabled.isChecked() and not dialog.approve.isChecked()
    assert not dialog.send.isEnabled()
    assert 'excerpt_theme_map' in dialog.payload.toPlainText()
    assert 'D1' in dialog.payload.toPlainText() and 'D2' in dialog.payload.toPlainText()
    assert str(root) not in dialog.payload.toPlainText()
    dialog.close()
    wait_until(application, lambda: area.theme_dialog is None)
    assert area.snapshot['total_files'] == 3


def test_theme_map_reason_quotes_and_versioned_local_citation(application, theme_view, monkeypatch):
    _, root, _, dialog = theme_view
    prepare(application, dialog)
    plan = dialog._plan
    data = theme_data(plan)
    calls = []
    monkeypatch.setattr(service, '_request', lambda *a: calls.append(a) or theme_response(data))
    approve(dialog)
    dialog.send.click()
    dialog.send.click()
    wait_until(application, lambda: dialog.worker is None)
    assert len(calls) == 1
    assert dialog.tabs.currentIndex() == dialog.graph_tab
    assert not dialog.form_toggle.isChecked() and not dialog.question.isVisible()
    assert set(dialog.theme_graph.sources) == {'S1','S2'}
    assert 'AI 추정' in dialog.theme_graph.note.text()
    relation_index = next(i for i,e in enumerate(dialog.theme_graph.entries) if e['title'].startswith('AI 추정 ·'))
    dialog.theme_graph.selector.setCurrentIndex(relation_index)
    text = dialog.theme_graph.details.toPlainText()
    assert data['relations'][0]['explanation'] in text
    for item in data['relations'][0]['evidence']:
        assert item['quote'] in text
    dialog.theme_graph.open_citation.click()
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.reference.toPlainText() == plan.sources[0].text
    (root / 'note.txt').write_text('new budget content', encoding='utf-8')
    run_content_analysis(root)
    dialog.open_graph_citation('S1')
    wait_until(application, lambda: dialog.worker is None)
    assert not dialog.reference.toPlainText() and '변경' in dialog.status.text()
    assert len(calls) == 1


def test_new_request_error_does_not_reuse_old_graph(application, theme_view, monkeypatch):
    _, _, _, dialog = theme_view
    prepare(application, dialog)
    first = dialog._plan
    monkeypatch.setattr(service, '_request', lambda *a: theme_response(theme_data(first)))
    approve(dialog)
    dialog.send.click()
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.theme_result is not None
    assert not dialog.send.isEnabled()
    dialog.form_toggle.setChecked(True)
    dialog.question.setPlainText('다른 관심사')
    assert dialog._plan is None
    assert dialog.theme_result['interest'] == first.question
    dialog.prepare.click()
    wait_until(application, lambda: dialog.worker is None)
    approve(dialog)
    monkeypatch.setattr(service, '_request', lambda *a: theme_response({'topics': [], 'relations': [], 'unclassified': []}))
    dialog.send.click()
    wait_until(application, lambda: dialog.worker is None)
    assert dialog.theme_result is None and not dialog.theme_graph.entries
    assert '아직 검증된 AI 결과가 없습니다' in dialog.theme_graph.note.text()
    assert '검증' in dialog.status.text()


@pytest.mark.parametrize('target', ['theme','area','main'])
def test_nested_close_waits_for_paid_request_thread(application, theme_view, monkeypatch, target):
    view, _, area, dialog = theme_view
    prepare(application, dialog)
    plan = dialog._plan
    entered, release = Event(), Event()
    calls = []
    def request(*args):
        entered.set();calls.append(args)
        assert release.wait(10)
        return theme_response(theme_data(plan))
    monkeypatch.setattr(service, '_request', request)
    approve(dialog)
    dialog.send.click()
    try:
        wait_until(application, entered.is_set)
        {'theme': dialog, 'area': area, 'main': view}[target].close()
        assert dialog.worker is not None and dialog._closing
    finally:
        release.set()
    wait_until(application, lambda: view.area_dialog is None or area.theme_dialog is None)
    assert len(calls) == 1
    if target in ('area','main'):
        wait_until(application, lambda: view.area_dialog is None)
    if target == 'main':
        wait_until(application, lambda: not view.isVisible())


def test_unclassified_sources_are_visible_without_invented_relations(application, theme_view, monkeypatch):
    _, _, _, dialog = theme_view
    prepare(application, dialog)
    monkeypatch.setattr(service, '_request', lambda *a: theme_response({'topics': [], 'relations': [], 'unclassified': ['S1','S2']}))
    approve(dialog)
    dialog.send.click()
    wait_until(application, lambda: dialog.worker is None)
    assert set(dialog.theme_graph.sources) == {'S1','S2'}
    assert dialog.theme_result['relations'] == []
    assert '미분류' in dialog.answer.toPlainText()
    assert '주제 0개 / 관계 0개' in dialog.theme_graph.note.text()
