import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
pytest.importorskip('PySide6', reason='GUI tests require requirements-gui.txt')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.graph_view import MAX_GRAPH_NEIGHBORS, RelatedGraphDialog


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def snapshot(count=3):
    source = {'file_id': 2**40, 'name': "<b>기준 & 문서.txt", 'path': '/local/source.txt', 'analyzed_at': 'revision-source'}
    rows = [{'file_id': i + 1, 'name': f'문서-{i}.txt', 'path': f'/local/{i}.txt', 'analyzed_at': f'revision-{i}', 'shared_keywords': ['galaxy', '한글'], 'snippet': '[galaxy] 한글 body'} for i in range(count)]
    return {'source': source, 'keywords': [{'term': 'galaxy'}, {'term': '한글'}], 'results': rows}


@pytest.mark.parametrize('count', [0, 1, 3, 20, 100])
def test_graph_is_bounded_star_with_only_evidence_edges(application, count):
    data = snapshot(count)
    graph = RelatedGraphDialog(data)
    graph.show()
    application.processEvents()
    visible = min(count, MAX_GRAPH_NEIGHBORS)
    assert len(graph.nodes) == visible + 1
    assert len(graph.edges) == visible
    assert graph.selector.count() == visible + 1
    source = graph.nodes[data['source']['file_id']]
    assert source.pos().isNull()
    for edge, row in zip(graph.edges, data['results']):
        assert edge.line().p1().isNull()
        assert edge.line().p2() == graph.nodes[row['file_id']].pos()
        assert 'galaxy' in edge.toolTip() and '한글' in edge.toolTip()
    assert '선정 키워드' in graph.details.toPlainText()
    assert data['source']['name'] in graph.details.toPlainText()
    assert 0.15 <= graph.canvas.transform().m11() <= 3.01
    graph.close()
    application.processEvents()


def test_node_edge_and_keyboard_selector_show_plain_text_evidence(application):
    data = snapshot()
    data['results'][1]['name'] = '길다' * 200 + '\n<b>filename'
    graph = RelatedGraphDialog(data)
    graph.show()
    application.processEvents()
    graph.selector.setCurrentIndex(2)
    assert graph.nodes[2].isSelected()
    assert '공유 키워드: galaxy, 한글' in graph.details.toPlainText()
    assert data['results'][1]['name'] in graph.details.toPlainText()
    graph.scene.clearSelection()
    graph.edges[0].setSelected(True)
    assert graph.selector.currentData() == 1
    assert '/local/0.txt' in graph.details.toPlainText()
    graph.select_file(data['source']['file_id'])
    emitted = []
    graph.fileActivated.connect(lambda file_id, revision: emitted.append((file_id, revision)))
    QTest.mouseClick(graph.open_body, Qt.MouseButton.LeftButton)
    assert emitted == [(2**40, 'revision-source')]
    application.processEvents()


def test_zoom_controls_clamp_and_fit_remains_available(application):
    graph = RelatedGraphDialog(snapshot(20))
    graph.show()
    application.processEvents()
    initial = graph.canvas.transform().m11()
    graph.zoom_in.click()
    assert graph.canvas.transform().m11() > initial
    for _ in range(60):
        graph.canvas.zoom(1.2)
    assert abs(graph.canvas.transform().m11() - 3.0) < 0.001
    for _ in range(60):
        graph.canvas.zoom(1 / 1.2)
    assert abs(graph.canvas.transform().m11() - 0.15) < 0.001
    graph.fit.click()
    assert abs(graph.canvas.transform().m11() - initial) < 0.05
    graph.close()
    application.processEvents()


def test_double_click_node_emits_its_revision(application):
    data = snapshot(1)
    graph = RelatedGraphDialog(data)
    graph.show()
    application.processEvents()
    emitted = []
    graph.fileActivated.connect(lambda file_id, revision: emitted.append((file_id, revision)))
    point = graph.canvas.mapFromScene(graph.nodes[1].pos())
    QTest.mouseDClick(graph.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert emitted == [(1, 'revision-0')]
    application.processEvents()
