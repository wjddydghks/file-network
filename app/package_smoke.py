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
    import pymupdf
    from openpyxl import Workbook

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
        (root / 'package-note.txt').write_bytes('한글 packagekeyword sharedpackagemarker'.encode('utf-8'))
        workbook = Workbook()
        workbook.active.title = '검증 자료'
        workbook.active['A1'] = 'packageofficekeyword'
        workbook.create_sheet('다음 시트')['A1'] = 'second sheet'
        workbook.save(root / 'package-sheets.xlsx')
        workbook.close()
        (root / 'package-ignore').mkdir()
        (root / 'package-ignore' / 'hidden.txt').write_text('excludedpackagekeyword', encoding='utf-8')
        with pymupdf.open() as document:
            for text in ('', 'packagepdfkeyword page two sharedpackagemarker', '', 'page four'):
                page = document.new_page()
                if text:
                    page.insert_text((72, 72), text)
            document.save(root / 'package-pages.pdf')
        data_path = root / 'app-state' / 'file_network.db'
        if not frozen:
            data_path.parent.mkdir()
        with patch.object(db, 'DB_PATH', data_path):
            view = MainWindow(settings_path=base / 'settings.json')
            try:
                view.folder.setText(str(root))
                view.limit.setValue(7)
                view.excluded_dirs.setText('package-ignore')
                view.show()
                view.start_analysis()
                wait_for(lambda: view.worker is None)
                if view._outcome[0] != 'completed' or view._outcome[1]['extracted'] != 3:
                    raise RuntimeError(f'패키지 GUI 분석 실패: {view._outcome}')
                if len(db.get_active_files_under(root)) != 3:
                    raise RuntimeError('패키지 제외 폴더 또는 앱 DB가 스캔되었습니다.')
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
                view.related_button.click()
                wait_for(lambda: view.worker is None)
                if view._view_mode != 'related' or len(view._results) != 1 or 'sharedpackagemarker' not in view._results[0]['shared_keywords']:
                    raise RuntimeError('패키지 관련 문서 키워드 근거 검증 실패')
                view.graph_button.click()
                app.processEvents()
                if view.graph_dialog is None or len(view.graph_dialog.nodes) != 2 or len(view.graph_dialog.edges) != 1:
                    raise RuntimeError('패키지 관계 그래프 검증 실패')
                view.graph_dialog.selector.setCurrentIndex(1)
                view.graph_dialog.recenter_button.click()
                wait_for(lambda: view.worker is None)
                if view.graph_dialog is None or view.graph_dialog.source['name'] != 'package-pages.pdf' or not view.graph_dialog.back_button.isEnabled():
                    raise RuntimeError('패키지 그래프 중심 변경 검증 실패')
                view.graph_dialog.back_button.click()
                if view.graph_dialog is None or view.graph_dialog.source['name'] != 'package-note.txt':
                    raise RuntimeError('패키지 이전 그래프 복원 검증 실패')
                view.graph_dialog.close()
                app.processEvents()
                view.query.setText('packagepdfkeyword')
                view.start_search()
                wait_for(lambda: view.worker is None)
                if len(view._results) != 1:
                    raise RuntimeError('패키지 PDF 검색 실패')
                view.citation_button.click()
                wait_for(lambda: view.worker is None)
                dialog = view.content_dialog
                if dialog is None:
                    raise RuntimeError('패키지 PDF 미리보기 실패')
                dialog.mode.setCurrentIndex(dialog.mode.findData('chunks'))
                wait_for(lambda: dialog.worker is None)
                if dialog._result.get('page') != 2 or 'packagepdfkeyword' not in dialog.body.toPlainText():
                    raise RuntimeError('패키지 PDF 인용 페이지 검증 실패')
                dialog.next.click()
                wait_for(lambda: dialog.worker is None)
                if dialog._result.get('page') != 4 or dialog.body.toPlainText() != 'page four':
                    raise RuntimeError('패키지 PDF 인용 탐색 실패')
                dialog.close()
                view.query.setText('packageofficekeyword')
                view.start_search()
                wait_for(lambda: view.worker is None)
                if len(view._results) != 1:
                    raise RuntimeError('패키지 Excel 검색 실패')
                view.start_content_preview()
                wait_for(lambda: view.worker is None)
                dialog = view.content_dialog
                if dialog is None:
                    raise RuntimeError('패키지 Excel 미리보기 실패')
                dialog.mode.setCurrentIndex(dialog.mode.findData('chunks'))
                wait_for(lambda: dialog.worker is None)
                if dialog._result.get('source_kind') != 'sheet' or dialog._result.get('source_label') != '검증 자료':
                    raise RuntimeError('패키지 Excel 인용 위치 검증 실패')
                dialog.next.click()
                wait_for(lambda: dialog.worker is None)
                if dialog._result.get('source_label') != '다음 시트' or 'second sheet' not in dialog.body.toPlainText():
                    raise RuntimeError('패키지 Excel 인용 탐색 실패')
                dialog.close()
                view.close()
            finally:
                view.close()
                wait_for(lambda: view.worker is None)
                wait_for(lambda: view.content_dialog is None or view.content_dialog.worker is None)
                view.close()
                view.deleteLater()
                app.processEvents()
            restored = MainWindow(settings_path=base / 'settings.json')
            try:
                if restored.limit.value() != 7 or restored.folder.text() != str(root) or restored.query.text() or restored.excluded_dirs.text() != 'package-ignore':
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
        'gui': {'analysis': True, 'search': True, 'preview': True, 'citations': True, 'office_citations': True, 'matching_citation': True, 'related_documents': True, 'relation_graph': True, 'graph_navigation': True, 'exclusions': True, 'settings_restore': True, 'writable_data_directory': True, 'internal_db_exclusion': True},
        'benchmark': benchmark,
    }
