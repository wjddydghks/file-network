import io
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core.extractors.text import extract_text


@pytest.mark.parametrize("suffix", [".txt", ".md", ".py", ".JS"])
@pytest.mark.parametrize("content", ["", "안녕하세요 😀\r\nprint('hello')\n"])
def test_extracts_text_without_modifying_original(tmp_path, suffix, content):
    path = tmp_path / ("sample" + suffix)
    original = content.encode("utf-8")
    path.write_bytes(original)
    result = extract_text(path)
    assert result.status == "EXTRACTED"
    assert result.text == content
    assert result.reason is None
    assert path.read_bytes() == original


def test_utf8_bom_is_removed(tmp_path):
    path = tmp_path / "bom.txt"
    path.write_bytes(b"\xef\xbb\xbf" + "한글".encode("utf-8"))
    assert extract_text(path).text == "한글"


@pytest.mark.parametrize(
    "content, limit, status, reason",
    [
        (b"1234", 4, "EXTRACTED", None),
        (b"12345", 4, "SKIPPED", "TOO_LARGE"),
        ("한글".encode("utf-8"), 5, "SKIPPED", "TOO_LARGE"),
        (b"a\x00b", 4, "SKIPPED", "BINARY_CONTENT"),
        (b"\xff", 4, "FAILED", "INVALID_ENCODING"),
    ],
)
def test_limits_and_content_detection(tmp_path, content, limit, status, reason):
    path = tmp_path / "sample.txt"
    path.write_bytes(content)
    result = extract_text(path, max_bytes=limit)
    assert result.status == status
    assert result.reason == reason
    if status != "EXTRACTED":
        assert result.text is None


def test_read_is_bounded_and_stream_closed(tmp_path, monkeypatch):
    path = tmp_path / "sample.txt"
    path.write_bytes(b"unused")

    class LimitedStream(io.BytesIO):
        def read(self, size=-1):
            assert size == 5
            return super().read(size)

    source = LimitedStream(b"123456789")
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: source)
    assert extract_text(path, max_bytes=4).reason == "TOO_LARGE"
    assert source.closed


def test_unsupported_format_is_not_opened(tmp_path, monkeypatch):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"example")

    def unexpected_open(*args, **kwargs):
        pytest.fail("지원하지 않는 파일을 열었습니다.")

    monkeypatch.setattr(Path, "open", unexpected_open)
    assert extract_text(path).reason == "UNSUPPORTED_TYPE"


@pytest.mark.parametrize("kind", ["directory", "special"])
def test_non_regular_files_are_not_opened(tmp_path, monkeypatch, kind):
    path = tmp_path / "sample.txt"
    if kind == "directory":
        path.mkdir()
    else:
        monkeypatch.setattr(Path, "stat", lambda *args, **kwargs: SimpleNamespace(st_mode=stat.S_IFIFO))

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("일반 파일이 아닌 경로를 열었습니다."))
        assert extract_text(path).reason == "NOT_REGULAR_FILE"


def test_missing_file_returns_read_error(tmp_path):
    result = extract_text(tmp_path / "missing.txt")
    assert result.status == "FAILED"
    assert result.reason == "READ_ERROR"
    assert result.error


def test_permission_error_returns_failure(tmp_path, monkeypatch):
    path = tmp_path / "sample.txt"
    path.write_text("hello", encoding="utf-8")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", denied)
    result = extract_text(path)
    assert result.status == "FAILED"
    assert result.reason == "READ_ERROR"


@pytest.mark.parametrize("limit", [0, -1, 1.5])
def test_invalid_size_limit_is_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        extract_text(tmp_path / "sample.txt", max_bytes=limit)


@pytest.mark.parametrize(
    "suffix, content, status, code",
    [
        (".txt", b"hello", "EXTRACTED", 0),
        (".py", "한글 😀".encode("utf-8"), "EXTRACTED", 0),
        (".doc", b"legacy", "SKIPPED", 0),
        (".txt", b"\xff", "FAILED", 1),
    ],
)
def test_cli_emits_json_and_exit_status(tmp_path, suffix, content, status, code):
    path = tmp_path / ("sample" + suffix)
    path.write_bytes(content)
    result = subprocess.run(
        [sys.executable, "-m", "app.extract_text", str(path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == code
    output = json.loads(result.stdout)
    assert output["status"] == status
    if status == "EXTRACTED":
        assert output["text"] == content.decode("utf-8")
    assert "Traceback" not in result.stderr


def test_cli_rejects_invalid_limit(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "app.extract_text", str(tmp_path / "sample.txt"), "--max-bytes", "0"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""
