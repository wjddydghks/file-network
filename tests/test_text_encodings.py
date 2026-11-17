import codecs
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app import analyze_content
from core.extractors.content import extract_content
from core.extractors.text import extract_text
from database import db, file_contents
from database.content_search import search_content


BOM_ENCODINGS = [
    (codecs.BOM_UTF16_LE, 'utf-16-le'), (codecs.BOM_UTF16_BE, 'utf-16-be'),
    (codecs.BOM_UTF32_LE, 'utf-32-le'), (codecs.BOM_UTF32_BE, 'utf-32-be'),
]


@pytest.mark.parametrize('bom,codec', BOM_ENCODINGS)
@pytest.mark.parametrize('text', ['', '안녕하세요 😀\r\nkeyword\n끝'])
def test_auto_uses_explicit_bom_and_preserves_original(tmp_path, bom, codec, text):
    path = tmp_path / '한글.TXT'
    original = bom + text.encode(codec)
    path.write_bytes(original)
    result = extract_content(path)
    assert result.status == 'EXTRACTED'
    assert result.text == text
    assert path.read_bytes() == original
    assert extract_text(path, max_bytes=len(original)).text == text
    assert extract_text(path, max_bytes=len(original)-1).reason == 'TOO_LARGE'


def test_cp949_is_explicit_and_utf8_is_not_guessed(tmp_path):
    path = tmp_path / 'legacy.txt'
    original = '한글 보고서\r\nkeyword'.encode('cp949')
    path.write_bytes(original)
    assert extract_text(path).reason == 'INVALID_ENCODING'
    assert extract_text(path, encoding='utf-8').reason == 'INVALID_ENCODING'
    result = extract_content(path, text_encoding='cp949')
    assert result.status == 'EXTRACTED'
    assert result.text == original.decode('cp949')
    assert path.read_bytes() == original
    path.write_bytes(b'\x81')
    assert extract_text(path, encoding='cp949').reason == 'INVALID_ENCODING'


@pytest.mark.parametrize('bom,codec', BOM_ENCODINGS)
def test_decoded_nul_is_binary_and_malformed_bom_content_fails(tmp_path, bom, codec):
    path = tmp_path / 'sample.txt'
    path.write_bytes(bom + 'hello\x00world'.encode(codec))
    assert extract_text(path).reason == 'BINARY_CONTENT'
    path.write_bytes(bom + b'\x00')
    result = extract_text(path)
    assert result.status == 'FAILED'
    assert result.reason == 'INVALID_ENCODING'
    assert result.error
    path.write_bytes('hello'.encode(codec))
    assert extract_text(path).reason == 'BINARY_CONTENT'  # No BOM: no UTF-16/32 guessing.
    path.write_bytes(bom + '한글'.encode(codec))
    assert extract_text(path, encoding='utf-8').status != 'EXTRACTED'


@pytest.mark.parametrize('encoding', ['auto', 'utf-8', 'cp949'])
def test_binary_nul_is_never_text(tmp_path, encoding):
    path = tmp_path / 'binary.txt'
    path.write_bytes(b'\xff\x00binary')
    assert extract_text(path, encoding=encoding).reason == 'BINARY_CONTENT'


@pytest.mark.parametrize('encoding', [None, 'latin1', 'utf-16', [], 123])
def test_invalid_encoding_does_not_open_file_or_create_db(tmp_path, monkeypatch, encoding):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    with pytest.raises(ValueError):
        extract_text(tmp_path / 'missing.txt', encoding=encoding)
    with pytest.raises(ValueError):
        extract_content(tmp_path / 'missing.pdf', text_encoding=encoding)
    with pytest.raises(ValueError):
        analyze_content.run_content_analysis(tmp_path, text_encoding=encoding)
    assert not db.DB_PATH.exists()


def test_batch_encoding_cache_search_and_nontext_cache(tmp_path, monkeypatch):
    from tests.office_fixtures import make_office

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    root = tmp_path / 'scan'
    root.mkdir()
    (root / 'legacy.txt').write_bytes('한글 keyword'.encode('cp949'))
    make_office(root / 'doc.docx', 'Officekeyword')
    assert analyze_content.run_content_analysis(root)['failed'] == 1
    assert search_content('한글') == []
    counts = analyze_content.run_content_analysis(root, text_encoding='cp949')
    assert counts == {'extracted': 1, 'failed': 0, 'skipped': 0, 'unchanged': 1}
    row = search_content('한글')[0]
    assert json.loads(file_contents.get_file_content(row['file_id'])['source_fingerprint'])['text_encoding'] == 'cp949'
    assert analyze_content.run_content_analysis(root, text_encoding='cp949')['unchanged'] == 2
    counts = analyze_content.run_content_analysis(root, text_encoding='utf-8')
    assert counts['failed'] == 1 and counts['unchanged'] == 1
    assert search_content('한글') == []
    assert search_content('Officekeyword')


@pytest.mark.parametrize('bom,codec', BOM_ENCODINGS)
def test_bom_batch_extraction_and_previous_skipped_cache_reanalysis(tmp_path, monkeypatch, bom, codec):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    root = tmp_path / 'scan'
    root.mkdir()
    path = root / 'document.txt'
    path.write_bytes(bom + '한글 keyword'.encode(codec))
    old_version = analyze_content.EXTRACTOR_VERSION
    assert analyze_content.run_content_analysis(root)['extracted'] == 1
    row = search_content('한글')[0]
    content = file_contents.get_file_content(row['file_id'])
    fingerprint = json.loads(content['source_fingerprint'])
    fingerprint['extractor_version'] = old_version - 1
    from core.extractors.text import TextExtractionResult
    file_contents.save_file_content(row['file_id'], TextExtractionResult(str(path), 'SKIPPED', reason='BINARY_CONTENT'), json.dumps(fingerprint, sort_keys=True))
    assert analyze_content.run_content_analysis(root)['extracted'] == 1
    assert search_content('keyword')
    assert analyze_content.run_content_analysis(root)['unchanged'] == 1


@pytest.mark.parametrize('option,original,expected', [
    ('cp949', '한글 keyword'.encode('cp949'), '한글 keyword'),
    ('auto', codecs.BOM_UTF16_LE + '한글 😀'.encode('utf-16-le'), '한글 😀'),
])
def test_extraction_cli_encodings(tmp_path, option, original, expected):
    path = tmp_path / 'sample.txt'
    path.write_bytes(original)
    r = subprocess.run([sys.executable, '-m', 'app.extract_text', str(path), '--encoding', option], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)['text'] == expected


@pytest.mark.parametrize('suffix', ['.pdf', '.docx', '.xlsx', '.pptx'])
def test_encoding_option_does_not_change_pdf_or_office_extraction(tmp_path, suffix):
    from tests.office_fixtures import make_office
    path = tmp_path / ('document' + suffix)
    if suffix == '.pdf':
        import pymupdf
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), 'PDF keyword')
            doc.save(path)
    else:
        make_office(path, 'Office keyword 한글')
    assert extract_content(path, text_encoding='cp949') == extract_content(path)
    assert analyze_content.source_fingerprint(path, 1024*1024, 'cp949') == analyze_content.source_fingerprint(path, 1024*1024, 'auto')


def test_batch_cli_encoding_selection(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    root = tmp_path / 'scan'
    root.mkdir()
    (root / 'legacy.txt').write_bytes('한글 keyword'.encode('cp949'))
    monkeypatch.setattr('sys.argv', ['analyze_content', str(root), '--encoding', 'cp949'])
    analyze_content.main()
    counts = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert counts['extracted'] == 1
    assert search_content('한글')
    monkeypatch.setattr('sys.argv', ['analyze_content', str(root), '--encoding', 'unknown'])
    with pytest.raises(SystemExit) as error:
        analyze_content.main()
    assert error.value.code == 2
