"""사용자 데이터에 접근하지 않는 실제 배포 실행 파일의 자체 검증."""
import os
from pathlib import Path
import platform
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch


def run_smoke():
    os.environ['QT_QPA_PLATFORM'] = os.environ.get('FILE_NETWORK_SELF_TEST_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from app.gui import MainWindow
    from app.storage import default_database_path, is_frozen
    from benchmarks.content_analysis import run_benchmark
    from database import db

    frozen = is_frozen()
    outside_bundle = not frozen or not default_database_path().resolve().is_relative_to(Path(sys.executable).resolve().parent)
    if not outside_bundle:
        raise RuntimeError('배포 DB 경로가 실행 파일 폴더 안에 있습니다.')
    benchmark = run_benchmark(18, dataset='mixed')
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('File Network')
    app.setQuitOnLastWindowClosed(False)

    def wait_for(condition):
        deadline = monotonic() + 20
        while not condition():
            app.processEvents()
            if monotonic() >= deadline:
                raise TimeoutError('패키지 GUI 작업이 완료되지 않았습니다.')
            sleep(0.005)
        app.processEvents()

    with TemporaryDirectory(prefix='file-network-package-smoke-') as directory:
        base = Path(directory).resolve()
        root = base / 'scan'
        root.mkdir()
        (root / 'package-note.txt').write_bytes('한글 packagekeyword'.encode('utf-8'))
        data_path = base / 'user-data' / 'file_network.db'
        if not frozen:
            data_path.parent.mkdir()
        with patch.object(db, 'DB_PATH', data_path):
            view = MainWindow(settings_path=base / 'settings.json')
            try:
                view.folder.setText(str(root))
                view.limit.setValue(7)
                view.show()
                view.start_analysis()
                wait_for(lambda: view.worker is None)
                if view._outcome[0] != 'completed' or view._outcome[1]['extracted'] != 1:
                    raise RuntimeError(f'패키지 GUI 분석 실패: {view._outcome}')
                view.query.setText('packagekeyword')
                view.start_search()
                wait_for(lambda: view.worker is None)
                if len(view._results) != 1 or 'packagekeyword' not in view.preview.toPlainText():
                    raise RuntimeError('패키지 GUI 검색 실패')
                view.start_content_preview()
                wait_for(lambda: view.worker is None)
                if view.content_dialog is None or 'packagekeyword' not in view.content_dialog.body.toPlainText():
                    raise RuntimeError('패키지 본문 미리보기 실패')
                view.content_dialog.close()
                view.close()
            finally:
                view.close()
                wait_for(lambda: view.worker is None)
                view.deleteLater()
                app.processEvents()
            restored = MainWindow(settings_path=base / 'settings.json')
            try:
                if restored.limit.value() != 7 or restored.folder.text() != str(root) or restored.query.text():
                    raise RuntimeError('패키지 설정 복원 실패')
                if restored.worker is not None:
                    raise RuntimeError('시작 시 자동 작업이 실행되었습니다.')
            finally:
                restored.close()
                restored.deleteLater()
                app.processEvents()
            if not data_path.is_file():
                raise RuntimeError('사용자 데이터 폴더 DB 생성 실패')
    return {
        'success': True, 'frozen': frozen, 'platform': platform.platform(),
        'database_outside_bundle': outside_bundle, 'gui_platform': app.platformName(),
        'gui': {'analysis': True, 'search': True, 'preview': True, 'settings_restore': True, 'writable_data_directory': True},
        'benchmark': benchmark,
    }
