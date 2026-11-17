from pathlib import Path
import json
import subprocess
import sys

import pytest

from app import storage
from database import db


def test_source_database_path_preserves_existing_project_location(monkeypatch):
    monkeypatch.setattr(storage.sys, 'frozen', False, raising=False)
    assert storage.default_database_path() == Path(storage.__file__).resolve().parents[1] / 'database' / 'file_network.db'


@pytest.mark.parametrize('platform', ['win32', 'darwin', 'linux'])
def test_packaged_database_path_is_user_data_and_not_created_on_import(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(storage.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(storage.sys, 'platform', platform)
    monkeypatch.setattr(storage.Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))
    expected = {
        'win32': tmp_path / 'local' / 'file-network',
        'darwin': tmp_path / 'home' / 'Library' / 'Application Support' / 'file-network',
        'linux': tmp_path / 'xdg' / 'file-network',
    }[platform]
    assert storage.default_database_path() == expected / 'file_network.db'
    assert not expected.exists()


@pytest.mark.parametrize('platform,variable', [('win32', 'LOCALAPPDATA'), ('linux', 'XDG_DATA_HOME')])
def test_relative_data_environment_does_not_put_db_in_current_directory(tmp_path, monkeypatch, platform, variable):
    monkeypatch.setattr(storage.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(storage.sys, 'platform', platform)
    monkeypatch.setattr(storage.Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setenv(variable, 'relative-directory')
    assert storage.default_database_path().is_absolute()
    assert storage.default_database_path().is_relative_to(tmp_path / 'home')


def test_packaged_connection_creates_missing_user_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'new' / 'user-data' / 'file_network.db')
    with db.get_connection() as conn:
        assert conn.execute('SELECT 1').fetchone()[0] == 1
    assert db.DB_PATH.is_file()


def test_desktop_self_test_runs_real_gui_and_synthetic_documents_in_subprocess(tmp_path):
    pytest.importorskip('PySide6')
    report_path = tmp_path / 'smoke.json'
    result = subprocess.run([sys.executable, '-m', 'app.desktop', '--self-test-report', str(report_path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, encoding='utf-8', errors='replace', timeout=90)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert result.returncode == 0, report
    assert report['success'] and not report['frozen']
    assert all(report['gui'].values())
    assert report['benchmark']['indexed_files'] == 18
    assert report['benchmark']['lifecycle']['deleted_content_cleared']
