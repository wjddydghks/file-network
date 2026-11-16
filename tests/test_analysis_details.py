import pytest

from app.analysis_details import format_analysis_detail, reason_label
from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from database import db
from database.file_contents import list_analysis_results


@pytest.fixture
def report_root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "nested" / "good.txt").write_text("hello", encoding="utf-8")
    (root / "bad.txt").write_bytes(b"\xff")
    (root / "unsupported.docx").write_bytes(b"docx")
    (root / "large.txt").write_text("123456789", encoding="utf-8")
    (root / "stale.txt").write_text("old", encoding="utf-8")
    (root / "deleted.txt").write_text("delete", encoding="utf-8")
    run_content_analysis(root, max_bytes=8)
    (root / "pending.txt").write_text("new", encoding="utf-8")
    (root / "stale.txt").write_text("modified content", encoding="utf-8")
    (root / "deleted.txt").unlink()
    run_incremental_scan(root)
    sibling = tmp_path / "scan-other"
    sibling.mkdir()
    (sibling / "outside.txt").write_text("outside", encoding="utf-8")
    run_incremental_scan(sibling)
    return root


def test_analysis_report_has_all_states_without_body_or_deleted_files(report_root, monkeypatch):
    from pathlib import Path

    def forbidden_read(*args, **kwargs):
        pytest.fail("분석 내역 조회가 원본 파일을 열어서는 안 됩니다.")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", forbidden_read)
        rows = list_analysis_results(report_root)
    by_name = {row["name"]: row for row in rows}
    assert {name: row["status"] for name, row in by_name.items()} == {
        "good.txt": "EXTRACTED", "bad.txt": "FAILED",
        "unsupported.docx": "SKIPPED", "large.txt": "SKIPPED",
        "stale.txt": "STALE", "pending.txt": "PENDING",
    }
    assert rows[0]["status"] == "FAILED"
    assert by_name["bad.txt"]["reason"] == "INVALID_ENCODING"
    assert by_name["bad.txt"]["error"]
    assert by_name["pending.txt"]["analyzed_at"] is None
    assert all("text" not in row and "source_fingerprint" not in row for row in rows)
    assert len(list_analysis_results()) == 7
    assert "deleted.txt" not in by_name and "outside.txt" not in by_name


@pytest.mark.parametrize("status", ["EXTRACTED", "SKIPPED", "FAILED", "STALE", "PENDING"])
def test_analysis_report_filters_and_limits(report_root, status):
    rows = list_analysis_results(report_root, status=status)
    assert rows
    assert all(row["status"] == status for row in rows)
    assert [row["path"] for row in rows] == sorted(row["path"] for row in rows)
    assert list_analysis_results(report_root, status=status, limit=1) == rows[:1]


@pytest.mark.parametrize("options", [{"status": "invalid"}, {"limit": 0}, {"limit": 1001}])
def test_invalid_report_options_do_not_create_database(tmp_path, monkeypatch, options):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with pytest.raises(ValueError):
        list_analysis_results(tmp_path, **options)
    assert not db.DB_PATH.exists()


def test_empty_analysis_report(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    assert list_analysis_results(tmp_path) == []


def test_analysis_reason_details_explain_failures_and_future_codes():
    row = {"name": "sample.pdf", "path": "/sample.pdf", "status": "EXTRACTED", "reason": "PARTIAL_OCR_REQUIRED"}
    assert "이미지" in reason_label(row)
    assert "검색되지 않습니다" in format_analysis_detail(row)
    row.update(status="FAILED", reason="READ_ERROR", error="permission denied")
    detail = format_analysis_detail(row)
    assert "권한" in detail
    assert "permission denied" in detail
    row.update(status="PENDING", reason=None, error=None)
    assert "아직" in reason_label(row)
    assert "스캔·분석" in format_analysis_detail(row)
    row.update(status="FUTURE_STATUS", reason="FUTURE_REASON")
    assert "FUTURE_REASON" in format_analysis_detail(row)
