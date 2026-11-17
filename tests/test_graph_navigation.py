import os
from threading import Event
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6', reason='GUI tests require requirements-gui.txt')

from app import gui
from app.analyze_content import run_content_analysis
from core.operations import check_cancelled
from database import db
from tests.test_gui import application, window, wait_until


def prepare(application, window):
    view, root = window
    for name, text in [('a.txt', 'galaxy astronomy'), ('b.txt', 'galaxy telescope'), ('c.txt', 'telescope physics')]:
        (root / name).write_text(text, encoding='utf-8')
    run_content_analysis(root)
    view.query.setText('astronomy')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    view.start_related_documents()
    wait_until(application, lambda: view.worker is None)
    view.open_related_graph()
    return view, root


def choose(graph, name):
    identity = next(row['file_id'] for row in graph.rows.values() if row['name'] == name)
    graph.select_file(identity)
    assert graph.recenter_button.isEnabled()
    graph.recenter_button.click()


def test_recenter_chain_and_back_restore_without_database_query(application, window, monkeypatch):
    view, root = prepare(application, window)
    initial = view._related_snapshot
    assert not view.graph_dialog.back_button.isEnabled()
    assert not view.graph_dialog.recenter_button.isEnabled()
    choose(view.graph_dialog, 'b.txt')
    wait_until(application, lambda: view.worker is None)
    assert view.graph_dialog.source['name'] == 'b.txt'
    assert len(view._graph_history) == 1
    assert {r['name'] for r in view._results} == {'a.txt', 'c.txt'}
    choose(view.graph_dialog, 'c.txt')
    wait_until(application, lambda: view.worker is None)
    assert view.graph_dialog.source['name'] == 'c.txt' and len(view._graph_history) == 2
    def forbidden(*args, **kwargs):
        raise AssertionError('Back navigation must not query the database')
    monkeypatch.setattr(gui, 'find_related_documents', forbidden)
    monkeypatch.setattr(db, 'get_connection', forbidden)
    view.graph_dialog.back_button.click()
    assert view.graph_dialog.source['name'] == 'b.txt' and len(view._graph_history) == 1
    view.graph_dialog.back_button.click()
    assert view.graph_dialog.source['name'] == 'a.txt'
    assert view._related_snapshot == initial and view._results == initial['results']
    assert not view._graph_history and not view.graph_dialog.back_button.isEnabled()
    view.graph_dialog.close()


def test_recenter_inherits_original_scope_and_limit(application, window, monkeypatch):
    view, root = prepare(application, window)
    scope = view._related_scope
    view.graph_dialog.close()
    elsewhere = root.parent / 'elsewhere'
    elsewhere.mkdir()
    view.folder.setText(str(elsewhere))
    view.all_folders.setChecked(True)
    view.limit.setValue(1)
    actual = gui.find_related_documents
    calls = []
    def tracked(file_id, limit, root_path, is_cancelled):
        calls.append((root_path, limit))
        return actual(file_id, limit=limit, root_path=root_path, is_cancelled=is_cancelled)
    monkeypatch.setattr(gui, 'find_related_documents', tracked)
    view.open_related_graph()
    choose(view.graph_dialog, 'b.txt')
    wait_until(application, lambda: view.worker is None)
    assert calls == [scope] and view._related_scope == scope
    view.graph_dialog.back_button.click()
    assert view._related_scope == scope
    view.graph_dialog.close()


@pytest.mark.parametrize('cancel', [False, True])
def test_recenter_error_or_cancel_preserves_graph_and_history(application, window, monkeypatch, cancel):
    view, root = prepare(application, window)
    original = view._related_snapshot
    started = Event()
    def failed(file_id, limit, root_path, is_cancelled):
        started.set()
        if not cancel:
            raise ValueError('document changed')
        while not is_cancelled():
            time.sleep(0.005)
        check_cancelled(is_cancelled)
    monkeypatch.setattr(gui, 'find_related_documents', failed)
    choose(view.graph_dialog, 'b.txt')
    wait_until(application, started.is_set)
    if cancel:
        view.cancel_task()
    wait_until(application, lambda: view.worker is None)
    assert view._related_snapshot == original and view._results == original['results']
    assert not view._graph_history and view._pending_graph_parent is None
    assert view.graph_dialog.source['name'] == 'a.txt'
    assert view.graph_dialog.notice.isVisible()
    assert ('취소' if cancel else 'document changed') in view.graph_dialog.notice.text()
    view.graph_dialog.close()


def test_close_during_recenter_never_reopens_graph(application, window, monkeypatch):
    view, root = prepare(application, window)
    started = Event()
    def waiting(file_id, limit, root_path, is_cancelled):
        started.set()
        while not is_cancelled():
            time.sleep(0.005)
        check_cancelled(is_cancelled)
    monkeypatch.setattr(gui, 'find_related_documents', waiting)
    choose(view.graph_dialog, 'b.txt')
    wait_until(application, started.is_set)
    view.close()
    wait_until(application, lambda: view.worker is None)
    assert not view.isVisible() and view.graph_dialog is None


def test_history_bound_and_new_search_reset(application, window):
    view, root = prepare(application, window)
    for index in range(gui.MAX_GRAPH_HISTORY + 2):
        choose(view.graph_dialog, 'b.txt' if index % 2 == 0 else 'a.txt')
        wait_until(application, lambda: view.worker is None)
    assert len(view._graph_history) == gui.MAX_GRAPH_HISTORY
    view.graph_dialog.close()
    view.query.setText('galaxy')
    view.start_search()
    wait_until(application, lambda: view.worker is None)
    assert not view._graph_history and view._related_snapshot is None
    assert not view.graph_button.isEnabled()
