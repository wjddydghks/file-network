from PyInstaller.utils.hooks import collect_data_files, copy_metadata


datas = [('DESKTOP_README.txt', '.')]
for package in ('docx', 'pptx', 'openpyxl', 'pymupdf'):
    datas += collect_data_files(package)
for distribution in ('PyMuPDF', 'python-docx', 'python-pptx', 'openpyxl', 'PySide6-Essentials', 'shiboken6', 'Pillow', 'lxml', 'defusedxml', 'XlsxWriter', 'et_xmlfile', 'typing_extensions', 'PyInstaller'):
    datas += copy_metadata(distribution)

a = Analysis(
    ['desktop.py'], pathex=[SPECPATH], binaries=[], datas=datas,
    hiddenimports=[], hookspath=[], runtime_hooks=[], excludes=[],
    noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='FileNetwork',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='FileNetwork')
