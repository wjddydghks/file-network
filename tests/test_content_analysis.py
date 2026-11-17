import json
import sqlite3

import pytest

from app import analyze_content
from app.incremental_scan import run_incremental_scan
from core.extractors.text import TextExtractionResult
from database import db, file_contents


@pytest.fixture
def content_root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    return root


def content_rows():
    with db.get_connection() as connection:
        return [dict(row) for row in connection.execute("""
            SELECT files.id, files.name, file_contents.*
            FROM files JOIN file_contents ON files.id = file_contents.file_id
            ORDER BY files.name
        """)]


def test_batch_results_are_stored_and_only_failures_retried(content_root, monkeypatch):
    root = content_root
    inputs = {
        "hello.txt": "한글".encode("utf-8"),
        "code.py": b"pass",
        "paper.doc": b"docx",
        "binary.txt": b"a\x00b",
        "large.txt": b"123456789",
        "invalid.txt": b"\xff",
    }
    for name, data in inputs.items():
        (root / name).write_bytes(data)
    counts = analyze_content.run_content_analysis(root, max_bytes=8)
    assert counts == {"extracted": 2, "skipped": 3, "failed": 1, "unchanged": 0}
    rows = {row["name"]: row for row in content_rows()}
    assert rows["hello.txt"]["text"] == "한글"
    assert rows["paper.doc"]["reason"] == "UNSUPPORTED_TYPE"
    assert rows["binary.txt"]["reason"] == "BINARY_CONTENT"
    assert rows["large.txt"]["reason"] == "TOO_LARGE"
    assert rows["invalid.txt"]["reason"] == "INVALID_ENCODING"
    assert all(row["analyzed_at"] for row in rows.values())

    calls = []
    original = analyze_content.extract_content

    def tracked(path, max_bytes, **options):
        calls.append(path.name)
        return original(path, max_bytes=max_bytes, **options)

    monkeypatch.setattr(analyze_content, "extract_content", tracked)
    counts = analyze_content.run_content_analysis(root, max_bytes=8)
    assert counts == {"extracted": 0, "skipped": 0, "failed": 1, "unchanged": 5}
    assert calls == ["invalid.txt"]
    for name, data in inputs.items():
        assert (root / name).read_bytes() == data


def test_changes_and_deletions_invalidate_old_text(content_root):
    changed = content_root / "changed.txt"
    deleted = content_root / "deleted.txt"
    changed.write_text("before", encoding="utf-8")
    deleted.write_text("delete", encoding="utf-8")
    analyze_content.run_content_analysis(content_root)

    changed.write_text("after modification", encoding="utf-8")
    deleted.unlink()
    run_incremental_scan(content_root)
    rows = {row["name"]: row for row in content_rows()}
    assert rows["changed.txt"]["status"] == "STALE"
    assert rows["changed.txt"]["reason"] == "FILE_CHANGED"
    assert rows["deleted.txt"]["reason"] == "FILE_DELETED"
    assert all(row["text"] is None for row in rows.values())

    assert analyze_content.run_content_analysis(content_root)["extracted"] == 1
    rows = {row["name"]: row for row in content_rows()}
    assert rows["changed.txt"]["text"] == "after modification"
    assert rows["deleted.txt"]["status"] == "STALE"


def test_move_preserves_id_and_cached_text(content_root):
    path = content_root / "before.txt"
    path.write_text("hello", encoding="utf-8")
    analyze_content.run_content_analysis(content_root)
    original = content_rows()[0]
    path.rename(content_root / "after.txt")
    counts = analyze_content.run_content_analysis(content_root)
    moved = content_rows()[0]
    assert counts["unchanged"] == 1
    assert counts["extracted"] == 0
    assert moved["id"] == original["id"]
    assert moved["text"] == original["text"]
    assert moved["analyzed_at"] == original["analyzed_at"]


def test_extension_change_invalidates_cache(content_root):
    path = content_root / "before.txt"
    path.write_text("hello", encoding="utf-8")
    analyze_content.run_content_analysis(content_root)
    original_id = content_rows()[0]["id"]
    path.rename(content_root / "after.doc")
    counts = analyze_content.run_content_analysis(content_root)
    row = content_rows()[0]
    assert counts["skipped"] == 1
    assert row["id"] == original_id
    assert row["text"] is None
    assert row["reason"] == "UNSUPPORTED_TYPE"


def test_limit_and_extractor_version_changes_invalidate_cache(content_root, monkeypatch):
    (content_root / "sample.txt").write_text("hello", encoding="utf-8")
    assert analyze_content.run_content_analysis(content_root, max_bytes=4)["skipped"] == 1
    assert analyze_content.run_content_analysis(content_root, max_bytes=8)["extracted"] == 1
    assert analyze_content.run_content_analysis(content_root, max_bytes=8)["unchanged"] == 1
    monkeypatch.setattr(analyze_content, "EXTRACTOR_VERSION", analyze_content.EXTRACTOR_VERSION + 1)
    assert analyze_content.run_content_analysis(content_root, max_bytes=8)["extracted"] == 1


@pytest.mark.parametrize("change", ["modify", "remove"])
def test_file_changed_during_extraction_is_not_saved(content_root, monkeypatch, change):
    path = content_root / "sample.txt"
    path.write_text("before", encoding="utf-8")
    original = analyze_content.extract_content

    def changing_extract(path, max_bytes, **options):
        result = original(path, max_bytes=max_bytes, **options)
        if change == "modify":
            path.write_text("changed after reading", encoding="utf-8")
        else:
            path.unlink()
        return result

    monkeypatch.setattr(analyze_content, "extract_content", changing_extract)
    assert analyze_content.run_content_analysis(content_root)["failed"] == 1
    row = content_rows()[0]
    assert row["status"] == "FAILED"
    assert row["text"] is None
    assert row["reason"] == ("FILE_CHANGED_DURING_ANALYSIS" if change == "modify" else "READ_ERROR")
    if change == "modify":
        monkeypatch.setattr(analyze_content, "extract_content", original)
        assert analyze_content.run_content_analysis(content_root)["extracted"] == 1
        assert content_rows()[0]["text"] == "changed after reading"


def test_initialization_preserves_files_and_sessions_and_enforces_fk(content_root):
    (content_root / "sample.txt").write_text("hello", encoding="utf-8")
    run_incremental_scan(content_root)
    with db.get_connection() as connection:
        original_session = dict(connection.execute("SELECT * FROM scan_sessions").fetchone())
    file_contents.initialize_file_contents()
    file_contents.initialize_file_contents()
    assert db.get_file_count() == 1
    with db.get_connection() as connection:
        assert dict(connection.execute("SELECT * FROM scan_sessions").fetchone()) == original_session
    with pytest.raises(sqlite3.IntegrityError):
        file_contents.save_file_content(999, TextExtractionResult("missing", "EXTRACTED", text="x"), "signature")


def test_only_target_directory_is_analyzed(content_root):
    sibling = content_root.parent / "scan-other"
    sibling.mkdir()
    (sibling / "outside.txt").write_text("outside", encoding="utf-8")
    (content_root / "inside.txt").write_text("inside", encoding="utf-8")
    run_incremental_scan(sibling)
    assert analyze_content.run_content_analysis(content_root)["extracted"] == 1
    assert [row["name"] for row in content_rows()] == ["inside.txt"]


@pytest.mark.parametrize("valid", [True, False])
def test_cli_reports_counts_and_exit_status(content_root, monkeypatch, capsys, valid):
    (content_root / "sample.txt").write_bytes(b"hello" if valid else b"\xff")
    monkeypatch.setattr("sys.argv", ["analyze_content", str(content_root)])
    if valid:
        analyze_content.main()
    else:
        with pytest.raises(SystemExit) as exit_info:
            analyze_content.main()
        assert exit_info.value.code == 1
    counts = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert counts["extracted"] == int(valid)
    assert counts["failed"] == int(not valid)


def test_invalid_limit_does_not_create_db(content_root):
    with pytest.raises(ValueError):
        analyze_content.run_content_analysis(content_root, max_bytes=0)
    assert not db.DB_PATH.exists()


def test_deleted_file_restoration_reanalyzes_same_metadata(content_root):
    import os

    path = content_root / "sample.txt"
    path.write_text("before", encoding="utf-8")
    original_stat = path.stat()
    analyze_content.run_content_analysis(content_root)
    original_id = content_rows()[0]["id"]
    path.unlink()
    assert analyze_content.run_content_analysis(content_root)["extracted"] == 0
    assert content_rows()[0]["text"] is None
    path.write_text("after!", encoding="utf-8")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert analyze_content.run_content_analysis(content_root)["extracted"] == 1
    assert content_rows()[0]["id"] == original_id
    assert content_rows()[0]["text"] == "after!"


def test_failed_reanalysis_clears_previous_body(content_root):
    path = content_root / "sample.txt"
    path.write_text("before", encoding="utf-8")
    analyze_content.run_content_analysis(content_root)
    path.write_bytes(b"\xff")
    assert analyze_content.run_content_analysis(content_root)["failed"] == 1
    row = content_rows()[0]
    assert row["status"] == "FAILED"
    assert row["text"] is None
    assert row["reason"] == "INVALID_ENCODING"
    path.write_text("fixed", encoding="utf-8")
    assert analyze_content.run_content_analysis(content_root)["extracted"] == 1
    assert content_rows()[0]["text"] == "fixed"


def test_batch_cancellation_keeps_completed_results_for_restart(content_root, monkeypatch, capsys):
    from database.scan_sessions import get_latest_scan_session

    for name in ("first.txt", "second.txt"):
        (content_root / name).write_text("hello", encoding="utf-8")
    original = analyze_content.extract_content
    calls = 0

    def interrupt_second(path, max_bytes, **options):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(path, max_bytes=max_bytes, **options)

    monkeypatch.setattr(analyze_content, "extract_content", interrupt_second)
    monkeypatch.setattr("sys.argv", ["analyze_content", str(content_root)])
    with pytest.raises(SystemExit) as exit_info:
        analyze_content.main()
    assert exit_info.value.code == 130
    assert "취소" in capsys.readouterr().out
    assert len(content_rows()) == 1
    assert get_latest_scan_session()["status"] == "COMPLETED"
    monkeypatch.setattr(analyze_content, "extract_content", original)
    counts = analyze_content.run_content_analysis(content_root)
    assert counts["unchanged"] == 1
    assert counts["extracted"] == 1
    assert len(content_rows()) == 2
