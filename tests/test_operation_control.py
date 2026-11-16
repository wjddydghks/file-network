from threading import Event

import pytest

from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.operations import OperationCancelled
from core.scanner.scanner import scan_directory
from database import db
from database.scan_sessions import get_latest_scan_session


def test_scanner_can_cancel_before_producing_a_batch(tmp_path):
    (tmp_path / "sample.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(OperationCancelled):
        list(scan_directory(tmp_path, is_cancelled=lambda: True))


def test_scan_cancellation_callback_records_session(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    with pytest.raises(OperationCancelled):
        run_incremental_scan(root, is_cancelled=lambda: True)
    assert get_latest_scan_session()["status"] == "CANCELLED"
    assert get_latest_scan_session()["scan_complete"] == 0


def test_analysis_reports_progress_and_resumes_after_callback_cancellation(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    for name in ("first.txt", "second.txt"):
        (root / name).write_text("hello", encoding="utf-8")
    cancel = Event()
    reports = []

    def report(progress):
        reports.append(progress)
        if progress["phase"] == "analysis" and progress["processed"] == 1:
            cancel.set()

    with pytest.raises(OperationCancelled):
        run_content_analysis(root, is_cancelled=cancel.is_set, progress_callback=report)
    assert any(progress["phase"] == "scan" for progress in reports)
    assert reports[-1] == {"phase": "analysis", "processed": 1, "total": 2}
    with db.get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM file_contents").fetchone()[0] == 1
    assert get_latest_scan_session()["status"] == "COMPLETED"
    reports.clear()
    counts = run_content_analysis(root, progress_callback=reports.append)
    assert counts["unchanged"] == 1
    assert counts["extracted"] == 1
    assert reports[-1] == {"phase": "analysis", "processed": 2, "total": 2}


def test_cancellation_after_scan_progress_prevents_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    missing = root / "missing.txt"
    missing.write_text("keep", encoding="utf-8")
    run_incremental_scan(root)
    missing.unlink()
    (root / "new.txt").write_text("new", encoding="utf-8")
    cancel = Event()
    with pytest.raises(OperationCancelled):
        run_incremental_scan(root, is_cancelled=cancel.is_set, progress_callback=lambda _: cancel.set())
    assert get_latest_scan_session()["status"] == "CANCELLED"
    assert get_latest_scan_session()["deleted_count"] == 0
    assert db.get_active_file_count() == 1


def test_cancel_request_at_final_analysis_progress_is_honored(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    (root / "sample.txt").write_text("hello", encoding="utf-8")
    cancel = Event()

    def report(progress):
        if progress["phase"] == "analysis" and progress["processed"] == 1:
            cancel.set()

    with pytest.raises(OperationCancelled):
        run_content_analysis(root, is_cancelled=cancel.is_set, progress_callback=report)
    with db.get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM file_contents").fetchone()[0] == 1
