import json
import sqlite3

import pytest

from app import search_content as cli
from app.analyze_content import run_content_analysis
from app.incremental_scan import run_incremental_scan
from core.extractors.text import TextExtractionResult
from database import db, file_contents
from database.content_search import initialize_content_search, search_content


@pytest.fixture
def search_root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    root = tmp_path / "scan"
    root.mkdir()
    return root


def test_indexes_existing_text_and_supports_unicode_and_multiple_terms(search_root):
    (search_root / "first.txt").write_text("한글 프로젝트 Python testing", encoding="utf-8")
    (search_root / "second.txt").write_text("Python tutorial", encoding="utf-8")
    run_content_analysis(search_root)
    assert len(search_content("PYTHON")) == 2
    matches = search_content("한글 Python")
    assert [row["name"] for row in matches] == ["first.txt"]
    assert "[한글]" in matches[0]["snippet"]
    assert isinstance(matches[0]["score"], float)
    initialize_content_search()
    assert len(search_content("Python")) == 2


def test_indexes_new_and_reanalyzed_text_and_removes_stale_terms(search_root):
    initialize_content_search()
    path = search_root / "sample.txt"
    path.write_text("original keyword", encoding="utf-8")
    run_content_analysis(search_root)
    assert len(search_content("original")) == 1
    path.write_text("replacement content longer", encoding="utf-8")
    run_incremental_scan(search_root)
    assert search_content("original") == []
    assert search_content("replacement") == []
    run_content_analysis(search_root)
    assert len(search_content("replacement")) == 1
    assert search_content("original") == []
    path.unlink()
    run_incremental_scan(search_root)
    assert search_content("replacement") == []


def test_move_uses_current_path_and_preserves_id(search_root):
    path = search_root / "before.txt"
    path.write_text("searchable keyword", encoding="utf-8")
    run_content_analysis(search_root)
    original = search_content("keyword")[0]
    destination = search_root / "after.txt"
    path.rename(destination)
    run_incremental_scan(search_root)
    moved = search_content("keyword")[0]
    assert moved["file_id"] == original["file_id"]
    assert moved["path"] == str(destination)
    assert moved["name"] == "after.txt"


@pytest.mark.parametrize("status", ["STALE", "SKIPPED", "FAILED"])
def test_non_extracted_results_are_excluded(search_root, status):
    (search_root / "sample.txt").write_text("keyword", encoding="utf-8")
    run_content_analysis(search_root)
    file_id = search_content("keyword")[0]["file_id"]
    file_contents.save_file_content(
        file_id, TextExtractionResult("sample.txt", status, text="keyword"), "test"
    )
    assert search_content("keyword") == []


def test_physical_record_deletion_updates_index(search_root):
    (search_root / "sample.txt").write_text("keyword", encoding="utf-8")
    run_content_analysis(search_root)
    file_id = search_content("keyword")[0]["file_id"]
    with db.get_connection() as connection:
        connection.execute("DELETE FROM files WHERE id = ?", (file_id,))
    assert search_content("keyword") == []
    with db.get_connection() as connection:
        connection.execute("INSERT INTO file_content_search(file_content_search, rank) VALUES ('integrity-check', 1)")


def test_scope_and_result_limit_preserve_directory_boundaries(search_root):
    sibling = search_root.parent / "scan-other"
    sibling.mkdir()
    for root in (search_root, sibling):
        for name in ("a.txt", "b.txt"):
            (root / name).write_text("keyword", encoding="utf-8")
        run_content_analysis(root)
    assert len(search_content("keyword")) == 4
    matches = search_content("keyword", root_path=search_root)
    assert len(matches) == 2
    assert all(row["path"].startswith(str(search_root) + db.sep) for row in matches)
    assert len(search_content("keyword", limit=1)) == 1
    assert [row["path"] for row in matches] == sorted(row["path"] for row in matches)


def test_orders_by_relevance(search_root):
    (search_root / "focused.txt").write_text("needle needle needle", encoding="utf-8")
    (search_root / "other.txt").write_text("needle " + "other " * 100, encoding="utf-8")
    run_content_analysis(search_root)
    results = search_content("needle")
    assert results[0]["name"] == "focused.txt"
    assert results[0]["score"] <= results[1]["score"]


@pytest.mark.parametrize("query", ['"', '*', '(', 'a:b', "needle OR missing", "needle NOT missing", "'; DROP TABLE files; --"])
def test_query_syntax_is_treated_as_literal_input(search_root, query):
    (search_root / "sample.txt").write_text("needle only", encoding="utf-8")
    run_content_analysis(search_root)
    assert search_content(query) == []
    assert db.get_file_count() == 1


@pytest.mark.parametrize("query, limit", [("", 20), ("   ", 20), ("needle", 0), ("needle", 1001)])
def test_invalid_query_and_limit_do_not_create_db(search_root, query, limit):
    with pytest.raises(ValueError):
        search_content(query, limit=limit)
    assert not db.DB_PATH.exists()


def test_cli_outputs_json_with_current_paths(search_root, monkeypatch, capsys):
    (search_root / "한글.txt").write_text("한글 keyword", encoding="utf-8")
    run_content_analysis(search_root)
    capsys.readouterr()
    monkeypatch.setattr("sys.argv", ["search_content", "한글", "--root", str(search_root), "--limit", "1"])
    cli.main()
    results = json.loads(capsys.readouterr().out)
    assert results[0]["name"] == "한글.txt"
    monkeypatch.setattr("sys.argv", ["search_content", "missing"])
    cli.main()
    assert json.loads(capsys.readouterr().out) == []


def test_cli_reports_invalid_input_and_database_errors(search_root, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["search_content", "keyword", "--limit", "1001"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    capsys.readouterr()

    def database_error(*args, **kwargs):
        raise sqlite3.OperationalError("unavailable")

    monkeypatch.setattr(cli, "search_content", database_error)
    monkeypatch.setattr("sys.argv", ["search_content", "keyword"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert "Traceback" not in capsys.readouterr().err


def test_deleted_file_restoration_is_searchable_after_reanalysis(search_root):
    import os

    path = search_root / "sample.txt"
    path.write_text("before", encoding="utf-8")
    original_stat = path.stat()
    run_content_analysis(search_root)
    original_id = search_content("before")[0]["file_id"]
    path.unlink()
    run_incremental_scan(search_root)
    assert search_content("before") == []
    path.write_text("after!", encoding="utf-8")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    run_content_analysis(search_root)
    assert search_content("before") == []
    assert search_content("after")[0]["file_id"] == original_id


def test_empty_database_returns_no_results(search_root):
    assert search_content("keyword") == []
    assert db.get_file_count() == 0


@pytest.mark.parametrize(
    "stage, exception, message",
    [("unavailable", RuntimeError, "FTS5"), ("rebuild", sqlite3.OperationalError, "rebuild failure")],
)
def test_failed_initialization_is_reported_and_atomic(search_root, monkeypatch, stage, exception, message):
    original_connection = db.get_connection

    class WithoutFTS5:
        def __init__(self):
            self.connection = original_connection()

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql, *args):
            if stage == "unavailable" and "CREATE VIRTUAL TABLE" in sql:
                raise sqlite3.OperationalError("no such module: fts5")
            if stage == "rebuild" and "VALUES ('rebuild')" in sql:
                raise sqlite3.OperationalError("simulated rebuild failure")
            return self.connection.execute(sql, *args)

    with monkeypatch.context() as patch:
        patch.setattr(db, "get_connection", WithoutFTS5)
        with pytest.raises(exception, match=message):
            initialize_content_search()
    with db.get_connection() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'file_content_search'"
        ).fetchone() is None
    assert search_content("keyword") == []
