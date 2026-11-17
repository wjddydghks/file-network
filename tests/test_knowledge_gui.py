from threading import Event
from pathlib import Path

import pytest

from app import knowledge_dialog
from app.analyze_content import run_content_analysis
from services import openai_questions as transport
from tests.test_gui import application,window,wait_until
from tests.test_knowledge_graph import Provider


@pytest.fixture
def graph_window(application,window,monkeypatch):
    view,root=window
    (root/'plan.txt').write_text('프로젝트 일정과 예산 계획입니다.',encoding='utf-8')
    (root/'budget.txt').write_text('프로젝트 예산과 일정 검토입니다.',encoding='utf-8')
    (root/'blocked.txt').write_text('연락 user@example.com',encoding='utf-8')
    (root/'file.bin').write_bytes(b'\xff')
    monkeypatch.setattr(transport,'_request',lambda *a:pytest.fail('Unexpected external call'))
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    view.knowledge_button.click()
    dialog=view.knowledge_dialog
    wait_until(application,lambda:dialog.plan is not None and dialog.worker is None)
    yield view,root,dialog
    if view.knowledge_dialog is not None:
        view.knowledge_dialog.close()
        wait_until(application,lambda:view.knowledge_dialog is None)


def approve(dialog):
    dialog.key.setText('fake-gui-key');dialog.enabled.setChecked(True);dialog.approve.setChecked(True)
    assert dialog.run.isEnabled()


def test_only_core_actions_and_auto_whole_area_preparation(application,graph_window):
    view,root,dialog=graph_window
    assert not view.ai_button.isVisible() and not view.area_button.isVisible()
    assert not view.related_button.isVisible() and not view.graph_button.isVisible()
    assert not view.citation_button.isVisible()
    assert view.knowledge_button.isVisible()
    assert len(dialog.plan.documents)==4 and len(dialog.plan.eligible)==2
    assert not dialog.run.isEnabled() and not dialog.enabled.isChecked()
    assert '새 전송 2개' in dialog.plan_summary.text()
    assert '외부 호출 없음' in dialog.status.text()
    assert not hasattr(dialog,'question') and not hasattr(dialog,'chunk')
    assert dialog.plan.calls==1


def test_node_edge_file_list_body_original_and_cache_reopen(application,graph_window,monkeypatch):
    view,root,dialog=graph_window
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    approve(dialog);dialog.run.click()
    wait_until(application,lambda:dialog.worker is None)
    assert len(provider.calls)==1 and dialog.state['complete']
    assert dialog.tabs.currentIndex()==0
    dialog.graph.selector.setCurrentIndex(dialog.graph.selector.findData('C1'))
    assert dialog.table.rowCount()==1
    assert dialog.graph.edges
    edge=dialog.graph.edges[0]
    dialog.graph.selector.setCurrentIndex(dialog.graph.selector.findData(edge['id']))
    assert dialog.table.rowCount()==len(edge['files'])
    assert set(d.file_id for d in dialog.page_documents)==set(edge['files'])
    assert '세부 주제' in dialog.node_title.text()
    assert dialog.reason.toPlainText()
    doc=dialog.selected_document();dialog.preview.click()
    wait_until(application,lambda:dialog.worker is None)
    assert '프로젝트' in dialog.body.toPlainText()
    opened=[];monkeypatch.setattr(knowledge_dialog.QDesktopServices,'openUrl',lambda url:opened.append(url.toLocalFile()) or True)
    dialog.original.click();assert [Path(path) for path in opened]==[Path(doc.path)]
    dialog.close();wait_until(application,lambda:view.knowledge_dialog is None)
    view.knowledge_button.click();second=view.knowledge_dialog
    wait_until(application,lambda:second.plan is not None and second.worker is None)
    assert second.plan.calls==0 and second.state['complete']
    assert len(provider.calls)==1
    assert not second.key.text() and not second.enabled.isChecked()
    second.close();wait_until(application,lambda:view.knowledge_dialog is None)


def test_file_change_on_prepare_invalidates_cache_and_uses_new_scope(application,graph_window,monkeypatch):
    _,root,dialog=graph_window
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    approve(dialog);dialog.run.click();wait_until(application,lambda:dialog.worker is None)
    (root/'new.txt').write_text('신규 프로젝트 예산 일정',encoding='utf-8')
    (root/'plan.txt').write_text('변경된 프로젝트 예산',encoding='utf-8')
    dialog.prepare.click();wait_until(application,lambda:dialog.plan is not None and dialog.worker is None)
    assert len(dialog.plan.documents)==5 and dialog.plan.calls>0
    assert dialog.state is None
    assert all(n['id'].startswith('status:') for n in dialog.graph.nodes)
    assert len(provider.calls)==1
    assert not dialog.approve.isChecked()


def test_single_global_call_completes_all_files_without_summary_or_classification_calls(application,graph_window,monkeypatch):
    _,_,dialog=graph_window
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    assert dialog.max_calls.value()==1
    approve(dialog);dialog.run.click();wait_until(application,lambda:dialog.worker is None)
    assert dialog.state['complete'] and len(provider.calls)==1
    assert len(dialog.state['assignments'])==2


@pytest.mark.parametrize('change',['interest','model','budget','key'])
def test_config_and_budget_revoke_approval(application,graph_window,change):
    _,_,dialog=graph_window
    approve(dialog)
    if change=='interest':dialog.interest.setText('일정')
    elif change=='model':dialog.model.setText('gpt-4.1')
    elif change=='budget':dialog.max_calls.setValue(2)
    else:dialog.key.setText('different-fake-key')
    assert not dialog.approve.isChecked() and not dialog.run.isEnabled()


@pytest.mark.parametrize('target',['dialog','main'])
def test_close_waits_for_automatic_analysis_and_keeps_checkpoint(application,graph_window,monkeypatch,target):
    view,_,dialog=graph_window
    entered,release=Event(),Event();provider=Provider()
    def slow(*args):
        entered.set();assert release.wait(10);return provider(*args)
    monkeypatch.setattr(transport,'_request',slow)
    approve(dialog);dialog.run.click()
    try:
        wait_until(application,entered.is_set)
        (dialog if target=='dialog' else view).close()
        assert dialog.worker is not None and dialog._closing
    finally:release.set()
    wait_until(application,lambda:view.knowledge_dialog is None)
    if target=='main':wait_until(application,lambda:not view.isVisible())
    assert len(provider.calls)==1


def test_preview_changed_version_gives_current_workflow_recovery(application,graph_window,monkeypatch):
    _,root,dialog=graph_window
    monkeypatch.setattr(transport,'_request',Provider())
    approve(dialog);dialog.run.click();wait_until(application,lambda:dialog.worker is None)
    dialog.graph.selector.setCurrentIndex(dialog.graph.selector.findData('C1'))
    doc=dialog.selected_document()
    (root/doc.name).write_text('변경된 프로젝트 내용',encoding='utf-8')
    run_content_analysis(root)
    dialog.preview.click();wait_until(application,lambda:dialog.worker is None)
    assert not dialog.body.toPlainText() and '영역 분석 준비' in dialog.status.text()


def test_interest_drilldown_and_statuses_stay_outside_graph(application,graph_window,monkeypatch):
    _,_,dialog=graph_window
    monkeypatch.setattr(transport,'_request',Provider())
    approve(dialog);dialog.run.click();wait_until(application,lambda:dialog.worker is None)
    graph=dialog.graph
    assert [n['id'] for n in graph.visible_nodes]==['I1']
    assert '전송 차단 1개' in graph.status_counts.text()
    assert all(not n['id'].startswith('status:') for n in graph.visible_nodes)
    graph.selector.setCurrentIndex(graph.selector.findData('I1'))
    assert dialog.table.rowCount()==2
    assert {n['id'] for n in graph.visible_nodes}=={'I1','C1','C2'}
    import gc
    gc.collect();application.processEvents()
    texts=[item.toPlainText() for item in graph.scene.items() if hasattr(item,'toPlainText')]
    assert {'프로젝트 관리','일정','예산','2개 파일'}.issubset(texts)
    assert '프로젝트 관리' in graph.breadcrumb.text()
    assert '프로젝트 관리 →' in dialog.reason.toPlainText()
    hierarchy=next(e for e in graph.edges if e['kind']=='hierarchy' and e['target']=='C2')
    graph.selector.setCurrentIndex(graph.selector.findData(hierarchy['id']))
    assert set(d.file_id for d in dialog.page_documents)==set(hierarchy['files'])
    assert '세부 주제' in dialog.node_title.text()
    graph.back.click()
    assert graph.selector.currentData()=='all'
    assert [n['id'] for n in graph.visible_nodes]==['I1']
    graph.selector.setCurrentIndex(graph.selector.findData('status:blocked'))
    assert dialog.table.rowCount()==1 and dialog.selected_document().blocked
    assert [n['id'] for n in graph.visible_nodes]==['I1']


def test_mouse_click_drills_down_without_deleting_active_event_item(application,graph_window,monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    _,_,dialog=graph_window
    monkeypatch.setattr(transport,'_request',Provider())
    approve(dialog);dialog.run.click();wait_until(application,lambda:dialog.worker is None)
    graph=dialog.graph
    root=next(item for item in graph.scene.items() if item.data(0)=='I1')
    point=graph.canvas.mapFromScene(root.sceneBoundingRect().center())
    QTest.mouseClick(graph.canvas.viewport(),Qt.MouseButton.LeftButton,pos=point)
    wait_until(application,lambda:graph.focus=='I1')
    assert {n['id'] for n in graph.visible_nodes}=={'I1','C1','C2'}
    assert dialog.table.rowCount()==2


def test_many_topics_use_multiple_columns_and_keep_text_readable(application):
    from app.knowledge_graph_view import KnowledgeGraphView
    from core.knowledge_graph import Document,KnowledgePlan,empty_state
    docs=tuple(Document(f'F{i}',i,f'/local/{i}',str(i),'EXTRACTED','rev','주제 본문','') for i in range(12))
    plan=KnowledgePlan('/local','','gpt-4.1-mini',docs);state=empty_state(plan)
    state['interests']=[{'id':'I1','label':'사용자 및 알림 도구 개발','description':'사용자 기능 관련 주제'}]
    state['topics']=[{'id':f'C{i+1}','parent':'I1','label':f'프로젝트 알림 처리 및 사용자 인터페이스 주제 {i+1}',
                      'description':'세부 주제 설명','sources':[d.alias]} for i,d in enumerate(docs)]
    state['assignments']={d.alias:[{'category':f'C{i+1}','reason':'분류 경로','quote':'주제 본문'}] for i,d in enumerate(docs)}
    view=KnowledgeGraphView();view.resize(1000,760);view.show();application.processEvents()
    try:
        view.render_graph(plan,state)
        view.selector.setCurrentIndex(view.selector.findData('I1'));application.processEvents()
        cards=[item for item in view.scene.items() if isinstance(item.data(0),str) and item.data(0).startswith('C')]
        assert len(cards)==12
        assert len({round(card.pos().x()) for card in cards})==3
        assert len({round(card.pos().y()) for card in cards})==4
        assert view.canvas.transform().m11()>=0.9
        assert all(item.font().pixelSize()*view.canvas.transform().m11()>=12.6 for item in view.text_items if item.font().bold())
        view.selector.setCurrentIndex(view.selector.findData('C12'));application.processEvents()
        last=next(card for card in cards if card.data(0)=='C12')
        visible=view.canvas.mapToScene(view.canvas.viewport().rect()).boundingRect()
        assert visible.contains(last.sceneBoundingRect())
        view.zoom_in.click();assert view.canvas.transform().m11()>0.9
        view.scale_label.click();assert view.canvas.transform().m11()==1.0
        view.fit.click();assert view.canvas.transform().m11()>=0.9
    finally:
        view.close();view.deleteLater();application.processEvents()
