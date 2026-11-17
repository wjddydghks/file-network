from dataclasses import asdict
import json

import pytest

from app.gui_settings import GuiSettings, MAX_SETTINGS_BYTES, load_settings, save_settings


def test_roundtrip_unicode_and_only_preference_fields(tmp_path):
    path = tmp_path / 'config' / 'gui-settings.json'
    expected = GuiSettings(folder='C:\\한글 문서', max_size_mib=25, text_encoding='cp949', search_target='all', all_folders=True, result_limit=50, analysis_filter='FAILED')
    save_settings(path, expected)
    result, notice = load_settings(path)
    assert result == expected and notice is None
    assert json.loads(path.read_text(encoding='utf-8')) == asdict(expected)
    assert set(json.loads(path.read_text(encoding='utf-8'))) == set(asdict(GuiSettings()))
    assert not list(path.parent.glob('*.tmp'))


def test_missing_settings_does_not_create_directory(tmp_path):
    path = tmp_path / 'missing' / 'gui-settings.json'
    assert load_settings(path) == (GuiSettings(), None)
    assert not path.parent.exists()


@pytest.mark.parametrize('payload', [b'{broken', b'\xff', b'[]', b'null', b'{"version": 2}', b'{"version": true}', b'[' * 1200 + b']' * 1200, b' ' * (MAX_SETTINGS_BYTES + 1)])
def test_invalid_settings_use_defaults_and_preserve_file(tmp_path, payload):
    path = tmp_path / 'settings.json'
    path.write_bytes(payload)
    settings, notice = load_settings(path)
    assert settings == GuiSettings()
    assert notice
    assert path.read_bytes() == payload


@pytest.mark.parametrize('field,value', [
    ('folder', 123), ('folder', 'bad\x00path'), ('folder', '\ud800'), ('folder', 'x' * 32768),
    ('max_size_mib', True), ('max_size_mib', 0), ('max_size_mib', 101),
    ('text_encoding', 'unknown'), ('search_target', ['all']),
    ('all_folders', 'false'), ('result_limit', 1001), ('result_limit', 1.5),
    ('analysis_filter', 'FUTURE'),
])
def test_invalid_fields_fall_back_individually(tmp_path, field, value):
    path = tmp_path / 'settings.json'
    data = {'folder': 'valid folder', 'result_limit': 42, field: value}
    path.write_text(json.dumps(data), encoding='utf-8')
    settings, notice = load_settings(path)
    assert notice
    assert getattr(settings, field) == getattr(GuiSettings(), field)
    if field != 'folder':
        assert settings.folder == 'valid folder'
    if field != 'result_limit':
        assert settings.result_limit == 42


def test_unknown_fields_are_not_loaded_or_saved(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'folder': 'local folder', 'query': 'private query', 'text': 'private body', 'api_key': 'private key'}), encoding='utf-8')
    settings, notice = load_settings(path)
    assert notice is None and settings.folder == 'local folder'
    save_settings(path, settings)
    assert 'private' not in path.read_text(encoding='utf-8')


def test_replace_failure_preserves_existing_file_and_removes_temporary(tmp_path, monkeypatch):
    from app import gui_settings
    path = tmp_path / 'settings.json'
    save_settings(path, GuiSettings(folder='before'))
    original = path.read_bytes()
    def fail(*args):
        raise PermissionError('read only')
    monkeypatch.setattr(gui_settings.os, 'replace', fail)
    with pytest.raises(PermissionError):
        save_settings(path, GuiSettings(folder='after'))
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ['settings.json']


def test_read_error_and_invalid_save_do_not_crash_or_overwrite(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / 'settings.json'
    save_settings(path, GuiSettings(folder='before'))
    original = path.read_bytes()
    with pytest.raises(ValueError):
        save_settings(path, GuiSettings(result_limit=True))
    assert path.read_bytes() == original
    def denied(*args, **kwargs):
        raise PermissionError('denied')
    monkeypatch.setattr(Path, 'open', denied)
    settings, notice = load_settings(path)
    assert settings == GuiSettings() and 'denied' in notice


def test_utf8_bom_settings_from_windows_editor_are_supported(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_bytes(b'\xef\xbb\xbf' + json.dumps({'folder': '한글 문서'}, ensure_ascii=False).encode('utf-8'))
    settings, notice = load_settings(path)
    assert settings.folder == '한글 문서' and notice is None


def test_folder_length_matches_qt_utf16_limit_without_losing_other_fields(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'folder': 'x' * 16000 + '😀' * 9000, 'result_limit': 42}, ensure_ascii=False), encoding='utf-8')
    settings, notice = load_settings(path)
    assert notice and settings.folder == '' and settings.result_limit == 42
