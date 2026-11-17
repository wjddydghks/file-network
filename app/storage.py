"""소스 실행의 기존 DB와 배포 실행의 사용자 데이터 경로를 구분한다."""
import os
from pathlib import Path
import sys


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def user_data_directory():
    if sys.platform == 'win32':
        candidate = Path(os.environ.get('LOCALAPPDATA', ''))
        base = candidate if candidate.is_absolute() else Path.home() / 'AppData' / 'Local'
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        candidate = Path(os.environ.get('XDG_DATA_HOME', ''))
        base = candidate if candidate.is_absolute() else Path.home() / '.local' / 'share'
    return base / 'file-network'


def default_database_path():
    if is_frozen():
        return user_data_directory() / 'file_network.db'
    return Path(__file__).resolve().parents[1] / 'database' / 'file_network.db'
