"""폴더 이름·정확한 파일 경로 기반 제외. 원본 경로를 추가로 탐색하지 않는다."""
import os
from pathlib import Path


def validate_excluded_dirs(names=()):
    if not isinstance(names, (tuple, list)) or len(names) > 100:
        raise ValueError("제외 폴더는 최대 100개 이름의 목록이어야 합니다.")
    for name in names:
        if (not isinstance(name, str) or not name or len(name) > 255
                or name in ('.', '..') or name != name.strip()
                or any(c in name for c in '/\\\x00,*?[]:')
                or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in name)):
            raise ValueError("제외 항목에는 경로·와일드카드가 아닌 폴더명만 입력하세요.")
    return tuple(dict.fromkeys(names))


def parse_excluded_dirs(text):
    if not isinstance(text, str) or len(text) > 26000:
        raise ValueError("제외 폴더 입력이 올바르지 않습니다.")
    return validate_excluded_dirs([part.strip() for part in text.split(',') if part.strip()])


class ExcludedDirectories:
    def __init__(self, root, names, files=()):
        self.root = Path(root)
        self.names = {os.path.normcase(name) for name in names}
        self.files = {os.path.normcase(os.path.abspath(path)) for path in files}

    def contains_file(self, path):
        """파일 경로를 먼저 확인하고 폴더명은 명시한 루트 아래에서만 비교한다."""
        if self.files and os.path.normcase(os.path.abspath(path)) in self.files:
            return True
        if not self.names:
            return False
        try:
            parents = Path(path).relative_to(self.root).parts[:-1]
        except ValueError:
            return False
        return any(os.path.normcase(part) in self.names for part in parents)
