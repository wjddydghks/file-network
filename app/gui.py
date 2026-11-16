"""File Network의 로컬 분석·검색용 최소 데스크톱 화면."""
import sys
from pathlib import Path
from threading import Event

try:
    from PySide6.QtCore import Qt, QThread, QUrl, Signal, Slot
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
        QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
        QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    )
except ImportError as error:
    raise SystemExit("GUI 의존성을 설치하세요: python -m pip install -r requirements-gui.txt") from error

from app.analyze_content import run_content_analysis
from core.operations import check_cancelled
from database.content_search import search_content


class TaskThread(QThread):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(dict)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = Event()

    def run(self):
        try:
            check_cancelled(self.cancel_event.is_set)
            result = self.operation(self.cancel_event.is_set, self.progress.emit)
            check_cancelled(self.cancel_event.is_set)
        except KeyboardInterrupt:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error) or "작업을 완료하지 못했습니다.")
        else:
            self.completed.emit(result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("File Network — 로컬 파일 검색")
        self.resize(1000, 720)
        self.worker = None
        self._closing = False
        self._results = []
        self._kind = None
        self._outcome = None

        self.folder = QLineEdit()
        self.folder.setPlaceholderText("분석하거나 검색할 폴더를 선택하세요")
        self.browse = QPushButton("폴더 선택")
        self.browse.clicked.connect(self.select_folder)
        self.max_size = QSpinBox()
        self.max_size.setRange(1, 100)
        self.max_size.setValue(10)
        self.max_size.setSuffix(" MiB")
        self.max_size.setToolTip("파일 하나당 최대 분석 크기")
        self.analyze = QPushButton("스캔·분석")
        self.analyze.clicked.connect(self.start_analysis)
        self.cancel = QPushButton("취소")
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_task)
        folder_row = QHBoxLayout()
        for widget in (self.folder, self.browse, QLabel("파일 크기 제한"), self.max_size, self.analyze, self.cancel):
            folder_row.addWidget(widget)

        self.query = QLineEdit()
        self.query.setPlaceholderText("본문 검색어 — 여러 단어는 모두 포함하는 문서 검색")
        self.query.returnPressed.connect(self.start_search)
        self.all_folders = QCheckBox("모든 분석 폴더 검색")
        self.limit = QSpinBox()
        self.limit.setRange(1, 1000)
        self.limit.setValue(100)
        self.search = QPushButton("검색")
        self.search.clicked.connect(self.start_search)
        search_row = QHBoxLayout()
        for widget in (self.query, self.all_folders, QLabel("최대 결과"), self.limit, self.search):
            search_row.addWidget(widget)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["파일명", "경로", "일치 본문"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.update_preview)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected())
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("검색 결과를 선택하면 일치한 본문을 표시합니다.")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.preview)
        splitter.setSizes([400, 160])

        self.selected_path = QLabel("선택된 파일 없음")
        self.selected_path.setTextFormat(Qt.TextFormat.PlainText)
        self.selected_path.setWordWrap(True)
        self.open_button = QPushButton("원본 파일 열기")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected)
        open_row = QHBoxLayout()
        open_row.addWidget(self.selected_path, 1)
        open_row.addWidget(self.open_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status = QLabel("폴더를 선택해 분석한 뒤 본문을 검색하세요. 이미 분석한 폴더는 바로 검색할 수 있습니다.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addLayout(folder_row)
        layout.addLayout(search_row)
        layout.addWidget(splitter, 1)
        layout.addLayout(open_row)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

    @Slot()
    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "분석할 폴더 선택", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _root(self):
        if not self.folder.text().strip():
            raise ValueError("폴더를 먼저 선택하세요.")
        root = Path(self.folder.text().strip()).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("존재하는 폴더를 선택하세요.")
        return str(root)

    @Slot()
    def start_analysis(self):
        if self.worker is not None:
            return
        try:
            root = self._root()
        except (ValueError, OSError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        max_bytes = self.max_size.value() * 1024 * 1024
        self._start_task("analysis", lambda cancelled, progress: run_content_analysis(
            root, max_bytes=max_bytes, is_cancelled=cancelled, progress_callback=progress,
        ))

    @Slot()
    def start_search(self):
        if self.worker is not None:
            return
        query = self.query.text().strip()
        if not query:
            self.status.setText("검색어를 입력하세요.")
            return
        try:
            root = None if self.all_folders.isChecked() else self._root()
        except (ValueError, OSError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        limit = self.limit.value()
        self._start_task("search", lambda cancelled, progress: search_content(query, limit=limit, root_path=root))

    def _start_task(self, kind, operation):
        self._kind = kind
        self._outcome = ("cancelled", None)
        self._results = []
        self.table.setRowCount(0)
        self.update_preview()
        self._set_busy(True)
        self.status.setText("스캔·분석 중…" if kind == "analysis" else "검색 중…")
        self.progress.setRange(0, 0)
        self.worker = TaskThread(operation, self)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _set_busy(self, busy):
        for widget in (self.folder, self.browse, self.max_size, self.analyze, self.query, self.all_folders, self.limit, self.search, self.table):
            widget.setEnabled(not busy)
        self.open_button.setEnabled(not busy and self.table.currentRow() >= 0)
        self.cancel.setEnabled(busy)

    @Slot()
    def cancel_task(self):
        if self.worker is not None:
            self.worker.cancel_event.set()
            self.cancel.setEnabled(False)
            self.status.setText("취소 요청됨 — 현재 처리가 끝나는 대로 중단합니다.")

    @Slot(dict)
    def _progress(self, progress):
        if self.worker is None or self.worker.cancel_event.is_set():
            return
        processed = progress["processed"]
        if progress["phase"] == "scan":
            self.status.setText(f"파일 스캔 중 — {processed:,}개 확인")
        else:
            total = progress["total"]
            self.progress.setRange(0, max(total, 1))
            self.progress.setValue(processed)
            self.status.setText(f"본문 분석 중 — {processed:,} / {total:,}개")

    @Slot(object)
    def _completed(self, result):
        self._outcome = ("completed", result)

    @Slot(str)
    def _failed(self, error):
        self._outcome = ("failed", error)

    @Slot()
    def _cancelled(self):
        self._outcome = ("cancelled", None)

    @Slot()
    def _finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        outcome, result = self._outcome
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if outcome == "completed" else 0)
        self._set_busy(False)
        if outcome == "failed":
            self.status.setText(f"작업 실패: {result}")
        elif outcome == "cancelled":
            self.status.setText("작업이 취소되었습니다. 이미 저장된 결과는 유지됩니다.")
        elif self._kind == "analysis":
            self.status.setText(
                f"분석 완료 — 추출 {result['extracted']:,}, 기존 결과 재사용 {result['unchanged']:,}, "
                f"건너뜀 {result['skipped']:,}, 실패 {result['failed']:,}개. 본문 검색을 사용할 수 있습니다."
            )
        else:
            self._results = result
            self.table.setRowCount(len(result))
            for row_index, row in enumerate(result):
                for column, key in enumerate(("name", "path", "snippet")):
                    self.table.setItem(row_index, column, QTableWidgetItem(row[key] or ""))
            self.status.setText(f"검색 결과 {len(result):,}개" if result else "검색 결과가 없습니다. 폴더와 검색어, 분석 여부를 확인하세요.")
            if result:
                self.table.selectRow(0)
        if self._closing:
            self.close()

    @Slot()
    def update_preview(self):
        index = self.table.currentRow()
        if 0 <= index < len(self._results):
            result = self._results[index]
            self.preview.setPlainText(result["snippet"] or "")
            self.selected_path.setText(result["path"])
            self.open_button.setEnabled(self.worker is None)
        else:
            self.preview.clear()
            self.selected_path.setText("선택된 파일 없음")
            self.open_button.setEnabled(False)

    @Slot()
    def open_selected(self):
        index = self.table.currentRow()
        if self.worker is not None or not 0 <= index < len(self._results):
            return
        path = Path(self._results[index]["path"])
        try:
            exists = path.is_file()
        except OSError as error:
            self.status.setText(f"파일 상태를 확인할 수 없습니다: {error}")
            return
        if not exists:
            self.status.setText("파일을 찾을 수 없습니다. 폴더를 다시 스캔·분석하세요.")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status.setText("파일을 열 수 없습니다. OS 기본 앱 연결을 확인하세요.")

    def closeEvent(self, event):
        if self.worker is not None:
            self._closing = True
            self.cancel_task()
            event.ignore()
        else:
            event.accept()


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("File Network")
    window = MainWindow()
    window.show()
    raise SystemExit(application.exec())


if __name__ == "__main__":
    main()
