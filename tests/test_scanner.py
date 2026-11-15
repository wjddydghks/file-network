
from core.scanner.scanner import scan_directory


def test_scan_files_in_nested_directories(tmp_path):
    """하위 디렉터리까지 파일을 탐색하는지 검증한다."""

    # 테스트용 디렉터리 생성
    (tmp_path / "first.txt").write_text(
        "File Network Test",
        encoding="utf-8"
    )

    nested_dir = tmp_path / "project"
    nested_dir.mkdir()

    (nested_dir / "scanner.py").write_text(
        "print('scanner')",
        encoding="utf-8"
    )

    # 스캔 실행
    batches = list(scan_directory(str(tmp_path), batch_size=2))

    # 배치로 반환된 파일 목록을 하나로 합친다.
    files = [file for batch in batches for file in batch]

    # 파일 개수 검증
    assert len(files) == 2

    # 파일명 검증
    names = {file["name"] for file in files}

    assert "first.txt" in names
    assert "scanner.py" in names

    # 경로 검증
    paths = {file["path"] for file in files}

    assert str((tmp_path / "first.txt").resolve()) in paths
    assert str((nested_dir / "scanner.py").resolve()) in paths


def test_scan_file_metadata(tmp_path):
    """파일 메타데이터가 정상적으로 수집되는지 검증한다."""

    test_file = tmp_path / "metadata.txt"
    content = "Hello File Network"

    test_file.write_text(content, encoding="utf-8")

    batches = list(scan_directory(str(tmp_path)))

    files = [file for batch in batches for file in batch]

    assert len(files) == 1

    file = files[0]

    assert file["name"] == "metadata.txt"
    assert file["extension"] == ".txt"
    assert file["size"] == test_file.stat().st_size
    assert file["file_type"] == "TEXT"
    assert file["path"] == str(test_file.resolve())

    assert file["modified_at"] is not None
    assert file["scanned_at"] is not None


def test_scan_empty_directory(tmp_path):
    """빈 디렉터리에서는 파일이 발견되지 않는지 검증한다."""

    batches = list(scan_directory(str(tmp_path)))

    files = [file for batch in batches for file in batch]

    assert len(files) == 0


def test_scan_invalid_directory():
    """존재하지 않는 경로를 전달하면 오류가 발생하는지 검증한다."""

    import pytest

    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        list(scan_directory("/this/path/does/not/exist"))



def test_scan_refreshes_missing_directory_entry_identity(tmp_path, monkeypatch):
    """Windows처럼 DirEntry 식별자가 0이면 전체 stat 조회로 보완한다."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from core.scanner import scanner

    path = tmp_path / "sample.txt"
    path.write_text("hello", encoding="utf-8")
    actual_stat = path.stat()
    original_scandir = scanner.os.scandir

    class MissingIdentityStat:
        st_ino = 0
        st_dev = 0

        def __init__(self, stat):
            self.stat = stat

        def __getattr__(self, name):
            return getattr(self.stat, name)

    @contextmanager
    def scandir_without_identity(directory):
        with original_scandir(directory) as entries:
            yield [SimpleNamespace(
                name=entry.name,
                path=entry.path,
                is_symlink=entry.is_symlink,
                is_dir=entry.is_dir,
                is_file=entry.is_file,
                stat=lambda follow_symlinks=False: MissingIdentityStat(actual_stat),
            ) for entry in entries]

    monkeypatch.setattr(scanner.os, "scandir", scandir_without_identity)
    files = [file for batch in scan_directory(tmp_path) for file in batch]
    assert files[0]["inode"] == actual_stat.st_ino
    assert files[0]["device_id"] == actual_stat.st_dev
