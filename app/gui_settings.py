"""GUI 환경설정의 제한된 JSON 읽기·검증·원자적 저장."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from core.extractors.text import TEXT_ENCODINGS
from database.file_contents import ANALYSIS_STATUSES
from core.scanner.exclusions import parse_excluded_dirs


MAX_SETTINGS_BYTES = 64 * 1024


@dataclass(frozen=True)
class GuiSettings:
    version: int = 1
    folder: str = ''
    max_size_mib: int = 10
    text_encoding: str = 'auto'
    search_target: str = 'all'
    all_folders: bool = False
    result_limit: int = 100
    analysis_filter: str | None = None
    excluded_dirs: str = ''


def _valid_exclusions(value):
    try:
        parse_excluded_dirs(value)
        return True
    except ValueError:
        return False


def _valid_folder(value):
    if not isinstance(value, str) or len(value) > 32767 or '\x00' in value:
        return False
    try:
        return len(value.encode('utf-16-le')) // 2 <= 32767
    except UnicodeEncodeError:
        return False


def _parse_settings(data):
    if not isinstance(data, dict):
        raise ValueError('설정은 JSON 객체여야 합니다.')
    version = data.get('version', 1)
    if type(version) is not int or version != 1:
        raise ValueError('지원하지 않는 설정 버전입니다.')
    defaults = asdict(GuiSettings())
    validators = {
        'folder': _valid_folder,
        'excluded_dirs': _valid_exclusions,
        'max_size_mib': lambda v: type(v) is int and 1 <= v <= 100,
        'text_encoding': lambda v: isinstance(v, str) and v in TEXT_ENCODINGS,
        'search_target': lambda v: isinstance(v, str) and v in ('content', 'name', 'all'),
        'all_folders': lambda v: type(v) is bool,
        'result_limit': lambda v: type(v) is int and 1 <= v <= 1000,
        'analysis_filter': lambda v: v is None or (isinstance(v, str) and v in ANALYSIS_STATUSES),
    }
    invalid = False
    for key, validate in validators.items():
        if key in data:
            if validate(data[key]):
                defaults[key] = data[key]
            else:
                invalid = True
    return GuiSettings(**defaults), invalid


def load_settings(path):
    try:
        with Path(path).open('rb') as source:
            payload = source.read(MAX_SETTINGS_BYTES + 1)
        if len(payload) > MAX_SETTINGS_BYTES:
            raise ValueError('설정 파일이 너무 큽니다.')
        settings, invalid = _parse_settings(json.loads(payload.decode('utf-8-sig')))
    except FileNotFoundError:
        return GuiSettings(), None
    except (OSError, ValueError, RecursionError) as error:
        return GuiSettings(), f'설정을 읽지 못해 기본값을 사용합니다: {error}'
    notice = '일부 설정 값이 올바르지 않아 해당 항목을 기본값으로 복원했습니다.' if invalid else None
    return settings, notice


def save_settings(path, settings):
    data = asdict(settings)
    validated, invalid = _parse_settings(data)
    if invalid or validated != settings:
        raise ValueError('올바른 설정 값만 저장할 수 있습니다.')
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if len(payload) > MAX_SETTINGS_BYTES:
        raise ValueError('설정 파일이 너무 큽니다.')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='wb', dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp', delete=False) as target:
            temporary = Path(target.name)
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
