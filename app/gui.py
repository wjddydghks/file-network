"""File Network의 로컬 분석·검색용 최소 데스크톱 화면."""
import sys
from pathlib import Path
from threading import Event

try:
    from PySide6.QtCore import Qt, QStandardPaths, QThread, QTimer, QUrl, Signal, Slot
    from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QTextCursor, QTextDocument
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
        QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
        QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    )
except ImportError as error:
    raise SystemExit(
        f"GUI를 불러올 수 없습니다: {error}\n"
        "python -m pip install -r requirements-gui.txt를 실행하고, "
        "Linux에서는 README의 Qt 시스템 라이브러리 안내도 확인하세요."
    ) from error

from app.analyze_content import run_content_analysis
from app.analysis_details import STATUS_LABELS, format_analysis_detail, reason_label
from app.gui_settings import GuiSettings, load_settings, save_settings
from core.operations import check_cancelled
from core.scanner.exclusions import parse_excluded_dirs
from database.content_search import search_content
from database.file_search import search_filename
from database.unified_search import search_all
from database.file_contents import MAX_PREVIEW_CHARS, get_content_preview, list_analysis_results
from database.document_chunks import find_matching_citation, get_document_chunk
from database.related_documents import MAX_RELATED_RESULTS, find_related_documents

MATCH_SOURCE_LABELS = {"content": "본문", "name": "파일명"}


def default_settings_path():
    directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
    return (Path(directory) if directory else Path.home() / '.config') / 'file-network' / 'gui-settings.json'


class ContentPreviewDialog(QDialog):
    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("저장된 본문 미리보기")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(850, 650)
        self.worker = None
        self._closing = False
        self._result = result
        self._available = True
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(150)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.mode = QComboBox()
        self.mode.addItem("전체 본문 구간", "text")
        self.mode.addItem("인용 단위 · 원본 위치", "chunks")
        self.mode.setCurrentIndex(1 if result.get('chunk_mode') else 0)
        self.mode.currentIndexChanged.connect(lambda: self.load_segment(0))
        self.previous = QPushButton("이전 구간")
        self.previous.clicked.connect(lambda: self.load_segment(self._result['segment'] - 1))
        self.next = QPushButton("다음 구간")
        self.next.clicked.connect(lambda: self.load_segment(self._result['segment'] + 1))
        self.refresh = QPushButton("처음부터 새로 읽기")
        self.refresh.clicked.connect(lambda: self.load_segment(0, refresh=True))
        navigation = QHBoxLayout()
        for widget in (self.mode, self.previous, self.next, self.refresh):
            navigation.addWidget(widget)
        self.find_query = QLineEdit()
        self.find_query.setPlaceholderText("현재 표시 구간에서 찾기")
        self.find_query.returnPressed.connect(self.find_next)
        self.find_previous = QPushButton("이전 일치")
        self.find_previous.clicked.connect(lambda: self.find_next(backward=True))
        self.find_forward = QPushButton("다음 일치")
        self.find_forward.clicked.connect(self.find_next)
        for button in (self.previous, self.next, self.refresh, self.find_previous, self.find_forward):
            button.setAutoDefault(False)
        find_row = QHBoxLayout()
        for widget in (self.find_query, self.find_previous, self.find_forward):
            find_row.addWidget(widget)
        self.find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.find_shortcut.activated.connect(self.find_query.setFocus)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setPlaceholderText("저장된 본문이 없습니다. 위의 분석 상태와 사유를 확인하세요.")
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        close = QPushButton("닫기")
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        layout = QVBoxLayout(self)
        for widget in (self.note, self.details, self.summary):
            layout.addWidget(widget)
        layout.addLayout(navigation)
        layout.addLayout(find_row)
        layout.addWidget(self.body, 1)
        layout.addWidget(self.status)
        layout.addWidget(close)
        self._render(result)

    def _render(self, result):
        self._result = result
        self._available = True
        self.details.setPlainText(format_analysis_detail(result))
        self.body.setPlainText(result['text'])
        total = result['total_chars']
        if result.get('chunk_mode'):
            self.note.setText("마지막 분석 시점의 인용입니다. PDF 페이지는 1부터 세는 실제 페이지 순서이며 인쇄된 쪽 번호와 다를 수 있습니다. 다른 형식에는 페이지를 추정하지 않습니다.")
            if result['chunk_count']:
                location = f"PDF {result['page']:,}페이지" if result['page'] is not None else "추출 본문 위치"
                if result.get('source_kind') == 'sheet':
                    location = f"Excel 시트: {result['source_label']}"
                elif result.get('source_kind') == 'slide':
                    location = f"PowerPoint 슬라이드: {result['source_label']}"
                self.summary.setText(f"인용 {result['segment'] + 1:,}/{result['chunk_count']:,} — {location}, {result['start_char'] + 1:,}–{result['end_char']:,}자 / 전체 {total:,}자")
            else:
                self.summary.setText("저장된 인용이 없습니다. 이전 버전에서 분석한 파일은 폴더를 다시 분석하세요.")
        else:
            self.note.setText("마지막 분석 시점의 텍스트입니다. 최신 원본과 다를 수 있습니다. 구간은 글자 수 기준이며 원본 페이지가 아닙니다.")
            start = result['segment'] * MAX_PREVIEW_CHARS + 1 if total else 0
            end = start + len(result['text']) - 1 if total else 0
            self.summary.setText(f"저장된 본문 {total:,}자 — 구간 {result['segment'] + 1:,}/{result['segment_count']:,} ({start:,}–{end:,}자)")
        self.status.setText("현재 구간 안에서 찾습니다. 구간 경계를 가로지르는 단어는 찾지 않습니다.")
        self._set_busy(False)

    def _set_busy(self, busy):
        self.previous.setEnabled(not busy and self._available and self._result['segment'] > 0)
        self.next.setEnabled(not busy and self._available and self._result['segment'] + 1 < self._result['segment_count'])
        self.refresh.setEnabled(not busy)
        self.mode.setEnabled(not busy)
        for widget in (self.find_query, self.find_previous, self.find_forward, self.body):
            widget.setEnabled(not busy and self._available)

    def load_segment(self, segment, refresh=False):
        if self.worker is not None:
            return
        file_id = self._result['file_id']
        revision = None if refresh else self._result['analyzed_at']
        self._set_busy(True)
        self.status.setText("저장된 본문 구간 조회 중…")
        self._outcome = ("cancelled", None)
        loader = get_document_chunk if self.mode.currentData() == "chunks" else get_content_preview
        self.worker = TaskThread(lambda cancelled, progress: loader(file_id, segment=segment, analyzed_at=revision), self)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    @Slot(object)
    def _completed(self, result):
        self._outcome = ("completed", result)

    @Slot(str)
    def _failed(self, message):
        self._outcome = ("failed", message)

    @Slot()
    def _finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        if self._closing:
            self.close()
            return
        outcome, result = self._outcome
        if outcome == "completed" and result is not None:
            self._render(result)
        else:
            self._available = False
            self.body.clear()
            self.summary.clear()
            self.status.setText(result if outcome == "failed" else "저장된 본문을 찾을 수 없습니다. 처음부터 새로 읽거나 스캔·분석을 실행하세요.")
            self._set_busy(False)

    @Slot()
    def find_next(self, backward=False):
        if self.worker is not None or not self._available:
            return
        query = self.find_query.text()
        if not query:
            self.status.setText("찾을 단어를 입력하세요.")
            return
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        found = self.body.find(query, flags)
        if not found:
            cursor = self.body.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
            self.body.setTextCursor(cursor)
            found = self.body.find(query, flags)
        self.status.setText("현재 구간에서 일치하는 단어를 선택했습니다." if found else "현재 구간에 일치하는 단어가 없습니다.")

    def closeEvent(self, event):
        if self.worker is not None:
            self._closing = True
            self.worker.cancel_event.set()
            self.status.setText("구간 조회가 끝나면 닫습니다.")
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        # Escape도 실행 중인 구간 조회의 종료를 기다린다.
        if self.worker is not None:
            self.close()
        else:
            super().reject()


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
    def __init__(self, settings_path=None):
        super().__init__()
        self.setWindowTitle("File Network — 로컬 파일 검색")
        self.resize(1000, 720)
        self.worker = None
        self._closing = False
        self._results = []
        self._results_query = ''
        self._pending_search_query = ''
        self._kind = None
        self._outcome = None
        self._view_mode = "search"
        self._search_target = "content"
        self.content_dialog = None
        self.settings_path = Path(settings_path) if settings_path is not None else default_settings_path()
        self._saved_settings, settings_notice = load_settings(self.settings_path)

        self.folder = QLineEdit()
        self.folder.setPlaceholderText("분석하거나 검색할 폴더를 선택하세요")
        self.browse = QPushButton("폴더 선택")
        self.browse.clicked.connect(self.select_folder)
        self.max_size = QSpinBox()
        self.max_size.setRange(1, 100)
        self.max_size.setValue(10)
        self.max_size.setSuffix(" MiB")
        self.max_size.setToolTip("파일 하나당 최대 분석 크기")
        self.text_encoding = QComboBox()
        self.text_encoding.addItem("자동 (BOM·UTF-8)", "auto")
        self.text_encoding.addItem("UTF-8", "utf-8")
        self.text_encoding.addItem("CP949 (한글 Windows)", "cp949")
        self.text_encoding.setToolTip("폴더의 텍스트·코드에 적용합니다. CP949는 직접 선택하며 PDF/Office에는 적용하지 않습니다.")
        self.analyze = QPushButton("스캔·분석")
        self.analyze.clicked.connect(self.start_analysis)
        self.cancel = QPushButton("취소")
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_task)
        folder_row = QHBoxLayout()
        for widget in (self.folder, self.browse, QLabel("파일 크기 제한"), self.max_size, self.text_encoding, self.analyze, self.cancel):
            folder_row.addWidget(widget)
        self.excluded_dirs = QLineEdit()
        self.excluded_dirs.setMaxLength(26000)
        self.excluded_dirs.setPlaceholderText("예: .git, node_modules, .venv (비워 두면 제외 없음)")
        self.excluded_dirs.setToolTip("쉼표로 구분한 폴더명 전체를 모든 깊이에서 제외합니다. 선택한 루트 자체는 제외하지 않습니다. 기존 DB·검색 결과는 유지되며 삭제하지 않습니다.")
        exclusions_row = QHBoxLayout()
        exclusions_row.addWidget(QLabel("분석 제외 폴더"))
        exclusions_row.addWidget(self.excluded_dirs)

        self.query = QLineEdit()
        self.query.setPlaceholderText("본문 검색어 — 여러 단어는 모두 포함하는 문서 검색")
        self.query.returnPressed.connect(self.start_search)
        self.search_target = QComboBox()
        self.search_target.addItem("본문", "content")
        self.search_target.addItem("파일명", "name")
        self.search_target.addItem("통합 (본문·파일명)", "all")
        self.search_target.setToolTip("통합 검색은 본문 일치를 우선하고 파일명 일치를 추가합니다. 같은 파일은 한 번 표시합니다.")
        self.search_target.currentIndexChanged.connect(self._update_search_hint)
        self.all_folders = QCheckBox("모든 분석 폴더")
        self.all_folders.setToolTip("검색과 분석 내역을 저장된 전체 폴더 범위에서 조회")
        self.limit = QSpinBox()
        self.limit.setRange(1, 1000)
        self.limit.setValue(100)
        self.search = QPushButton("검색")
        self.search.clicked.connect(self.start_search)
        search_row = QHBoxLayout()
        for widget in (self.search_target, self.query, self.all_folders, QLabel("최대 결과"), self.limit, self.search):
            search_row.addWidget(widget)

        self.analysis_filter = QComboBox()
        self.analysis_filter.addItem("전체 상태", None)
        for status, label in STATUS_LABELS.items():
            self.analysis_filter.addItem(label, status)
        self.details_button = QPushButton("분석 내역 보기")
        self.details_button.clicked.connect(self.start_details)
        self.reset_settings_button = QPushButton("설정 초기화")
        self.reset_settings_button.setToolTip("저장된 화면 설정만 기본값으로 되돌립니다.")
        self.reset_settings_button.clicked.connect(self.reset_settings)
        details_row = QHBoxLayout()
        details_row.addWidget(QLabel("파일별 분석 상태"))
        details_row.addWidget(self.analysis_filter)
        details_row.addWidget(self.details_button)
        details_row.addStretch(1)
        details_row.addWidget(self.reset_settings_button)

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
        self.preview.setPlaceholderText("검색 결과를 선택하면 일치 본문 또는 파일명을 표시합니다.")
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
        self.content_button = QPushButton("저장된 본문 보기")
        self.content_button.setEnabled(False)
        self.content_button.clicked.connect(self.start_content_preview)
        self.citation_button = QPushButton("일치 인용 보기")
        self.citation_button.setEnabled(False)
        self.citation_button.setToolTip("완료한 본문 검색의 모든 검색어가 포함된 첫 인용을 엽니다. 검색어가 여러 인용에 나뉘면 일치 인용이 없을 수 있습니다.")
        self.citation_button.clicked.connect(self.start_matching_citation)
        self.related_button = QPushButton("관련 문서 보기")
        self.related_button.setEnabled(False)
        self.related_button.setToolTip("저장된 본문의 공유 키워드로 후보를 찾습니다. 의미 유사성이나 실제 관계를 확정하는 결과는 아닙니다.")
        self.related_button.clicked.connect(self.start_related_documents)
        open_row = QHBoxLayout()
        open_row.addWidget(self.selected_path, 1)
        open_row.addWidget(self.content_button)
        open_row.addWidget(self.citation_button)
        open_row.addWidget(self.related_button)
        open_row.addWidget(self.open_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status = QLabel("폴더를 선택해 분석한 뒤 본문을 검색하세요. 이미 분석한 폴더는 바로 검색할 수 있습니다.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.settings_status = QLabel(self)
        self.settings_status.setTextFormat(Qt.TextFormat.PlainText)
        self.settings_status.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addLayout(folder_row)
        layout.addLayout(exclusions_row)
        layout.addLayout(search_row)
        layout.addLayout(details_row)
        layout.addWidget(splitter, 1)
        layout.addLayout(open_row)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.settings_status)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self._apply_settings(self._saved_settings)
        self._settings_notice(settings_notice)
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(300)
        self._settings_timer.timeout.connect(self._save_settings)
        self.folder.editingFinished.connect(self._schedule_settings)
        self.excluded_dirs.editingFinished.connect(self._schedule_settings)
        self.browse.clicked.connect(self._schedule_settings)
        for combo in (self.text_encoding, self.search_target, self.analysis_filter):
            combo.currentIndexChanged.connect(self._schedule_settings)
        for spin in (self.max_size, self.limit):
            spin.valueChanged.connect(self._schedule_settings)
        self.all_folders.toggled.connect(self._schedule_settings)

    def _settings_notice(self, message):
        self.settings_status.setText(message or '')
        self.settings_status.setVisible(bool(message))

    def _apply_settings(self, settings):
        self.folder.setText(settings.folder)
        self.excluded_dirs.setText(settings.excluded_dirs)
        self.max_size.setValue(settings.max_size_mib)
        self.text_encoding.setCurrentIndex(self.text_encoding.findData(settings.text_encoding))
        self.search_target.setCurrentIndex(self.search_target.findData(settings.search_target))
        self.all_folders.setChecked(settings.all_folders)
        self.limit.setValue(settings.result_limit)
        self.analysis_filter.setCurrentIndex(self.analysis_filter.findData(settings.analysis_filter))

    def _current_settings(self):
        return GuiSettings(
            folder=self.folder.text(), max_size_mib=self.max_size.value(),
            text_encoding=self.text_encoding.currentData(), search_target=self.search_target.currentData(),
            all_folders=self.all_folders.isChecked(), result_limit=self.limit.value(),
            analysis_filter=self.analysis_filter.currentData(),
            excluded_dirs=self.excluded_dirs.text(),
        )

    @Slot()
    def _schedule_settings(self):
        self._settings_timer.start()

    @Slot()
    def _save_settings(self, force=False):
        settings = self._current_settings()
        if not force and settings == self._saved_settings:
            return
        try:
            save_settings(self.settings_path, settings)
        except (OSError, ValueError) as error:
            self._settings_notice(f'설정 저장 실패 — 현재 화면은 계속 사용할 수 있습니다: {error}')
        else:
            self._saved_settings = settings
            self._settings_notice(None)

    @Slot()
    def reset_settings(self):
        if self.worker is not None:
            return
        self._apply_settings(GuiSettings())
        self._settings_timer.stop()
        self._save_settings(force=True)
        self.status.setText("화면 설정을 기본값으로 되돌렸습니다.")

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
            excluded_dirs = parse_excluded_dirs(self.excluded_dirs.text())
        except (ValueError, OSError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        max_bytes = self.max_size.value() * 1024 * 1024
        text_encoding = self.text_encoding.currentData()
        options = {'excluded_dirs': excluded_dirs} if excluded_dirs else {}
        self._start_task("analysis", lambda cancelled, progress: run_content_analysis(
            root, max_bytes=max_bytes, is_cancelled=cancelled, progress_callback=progress,
            text_encoding=text_encoding,
            **options,
        ))

    @Slot()
    def _update_search_hint(self):
        hints = {
            "content": "본문 검색어 — 여러 단어는 모두 포함하는 문서 검색",
            "name": "파일명 검색어 — 여러 단어를 모두 포함하는 이름 검색",
            "all": "통합 검색어 — 본문 또는 파일명에 모든 단어를 포함하는 파일 검색",
        }
        self.query.setPlaceholderText(hints[self.search_target.currentData()])

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
        self._search_target = self.search_target.currentData()
        self._pending_search_query = query
        search = {"content": search_content, "name": search_filename, "all": search_all}[self._search_target]
        self._start_task("search", lambda cancelled, progress: search(query, limit=limit, root_path=root))

    @Slot()
    def start_details(self):
        if self.worker is not None:
            return
        try:
            root = None if self.all_folders.isChecked() else self._root()
        except (ValueError, OSError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        status = self.analysis_filter.currentData()
        limit = self.limit.value()
        self._start_task("details", lambda cancelled, progress: list_analysis_results(root, status=status, limit=limit))

    def _start_task(self, kind, operation):
        self._kind = kind
        self._outcome = ("cancelled", None)
        if kind not in ("preview", "citation", "related"):
            self._results = []
            self.table.setRowCount(0)
            self.update_preview()
        self._set_busy(True)
        self.status.setText({"analysis": "스캔·분석 중…", "search": "검색 중…", "details": "분석 내역 조회 중…", "preview": "저장된 본문 조회 중…", "citation": "일치 인용 조회 중…", "related": "공유 키워드로 관련 문서 조회 중…"}[kind])
        self.progress.setRange(0, 0)
        self.worker = TaskThread(operation, self)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _set_busy(self, busy):
        for widget in (self.folder, self.excluded_dirs, self.browse, self.max_size, self.text_encoding, self.analyze, self.query, self.search_target, self.all_folders, self.limit, self.search, self.table, self.analysis_filter, self.details_button, self.reset_settings_button):
            widget.setEnabled(not busy)
        self.open_button.setEnabled(not busy and self.table.currentRow() >= 0)
        self.content_button.setEnabled(not busy and self.table.currentRow() >= 0)
        self.citation_button.setEnabled(not busy and self._can_open_citation())
        self.related_button.setEnabled(not busy and self.table.currentRow() >= 0)
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
                f"건너뜀 {result['skipped']:,}, 실패 {result['failed']:,}개. 분석 내역에서 파일별 사유를 확인하세요."
            )
        elif self._kind in ("preview", "citation"):
            if result is None:
                self.status.setText("모든 검색어를 포함하는 인용이 없습니다. 검색어가 구간에 나뉘었거나 인용이 없을 수 있습니다. 저장된 본문 보기 또는 재분석으로 확인하세요." if self._kind == 'citation' else "저장된 파일을 찾을 수 없습니다. 검색 또는 분석 내역을 새로 조회하세요.")
            elif not self._closing:
                self.content_dialog = ContentPreviewDialog(result, self)
                self.content_dialog.finished.connect(self._clear_content_dialog)
                self.content_dialog.show()
                self.status.setText("검색어가 모두 포함된 첫 인용을 표시합니다. 이전·다음 구간은 검색 일치 여부와 무관한 일반 인용 탐색입니다." if self._kind == 'citation' else "마지막 분석 시점의 저장된 본문을 표시합니다.")
        else:
            related = self._kind == 'related'
            related_info = result if related else None
            if related:
                result = related_info['results']
            self._results = result
            self._view_mode = self._kind
            if self._kind == 'search':
                self._results_query = self._pending_search_query
            details = self._view_mode == "details"
            unified = self._view_mode == 'search' and self._search_target == "all"
            match_label = "일치 파일명" if self._search_target == "name" else "일치 본문"
            if related:
                headers = ["파일명", "공유 키워드", "경로", "일치 본문"]
            elif details:
                headers = ["파일명", "상태", "사유", "경로"]
            elif unified:
                headers = ["파일명", "일치 위치", "경로", "일치 내용"]
            else:
                headers = ["파일명", "경로", match_label]
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.table.setRowCount(len(result))
            for row_index, row in enumerate(result):
                if related:
                    values = (row['name'], ', '.join(row['shared_keywords']), row['path'], row['snippet'])
                elif details:
                    values = (row["name"], STATUS_LABELS.get(row["status"], row["status"]), reason_label(row), row["path"])
                elif unified:
                    values = (row["name"], MATCH_SOURCE_LABELS[row["match_source"]], row["path"], row["snippet"])
                else:
                    values = (row["name"], row["path"], row["snippet"])
                for column, value in enumerate(values):
                    self.table.setItem(row_index, column, QTableWidgetItem(value or ""))
            if related:
                terms = ', '.join(item['term'] for item in related_info['keywords']) or '없음'
                self.status.setText(f"기준: {related_info['source']['name']} — 관련 후보 {len(result):,}개 (최대 {MAX_RELATED_RESULTS}). 선정 키워드: {terms}. 공유 키워드 근거이며 실제 관계를 확정하지 않습니다.")
            elif details:
                self.status.setText(f"분석 내역 {len(result):,}개 — 최대 {self.limit.value():,}개 표시. 마지막으로 확인한 상태입니다.")
            else:
                self.status.setText(f"검색 결과 {len(result):,}개" if result else "검색 결과가 없습니다. 폴더와 검색어, 분석 여부를 확인하세요.")
            if result:
                self.table.selectRow(0)
            self.update_preview()
        if self._closing:
            self.close()

    @Slot()
    def update_preview(self):
        index = self.table.currentRow()
        if 0 <= index < len(self._results):
            result = self._results[index]
            text = format_analysis_detail(result) if self._view_mode == "details" else result["snippet"] or ""
            if self._view_mode != "details" and result.get("match_source"):
                text = f"일치 위치: {MATCH_SOURCE_LABELS[result['match_source']]}\n\n{text}"
            if self._view_mode == 'related':
                text = f"공유 키워드: {', '.join(result['shared_keywords'])}\n\n{text}"
            self.preview.setPlainText(text)
            self.selected_path.setText(result["path"])
            self.open_button.setEnabled(self.worker is None)
            self.content_button.setEnabled(self.worker is None)
            self.citation_button.setEnabled(self.worker is None and self._can_open_citation())
            self.related_button.setEnabled(self.worker is None)
        else:
            self.preview.clear()
            self.selected_path.setText("선택된 파일 없음")
            self.open_button.setEnabled(False)
            self.content_button.setEnabled(False)
            self.citation_button.setEnabled(False)
            self.related_button.setEnabled(False)

    def _can_open_citation(self):
        index = self.table.currentRow()
        if 0 <= index < len(self._results) and self._view_mode == 'related':
            return bool(self._results[index]['shared_keywords'])
        return (0 <= index < len(self._results) and self._view_mode == 'search'
                and self._search_target != 'name' and bool(self._results_query)
                and self._results[index].get('match_source') != 'name')

    @Slot()
    def start_matching_citation(self):
        if self.worker is not None or not self._can_open_citation():
            return
        file_id = self._results[self.table.currentRow()]['file_id']
        query = ' '.join(self._results[self.table.currentRow()]['shared_keywords']) if self._view_mode == 'related' else self._results_query
        self._start_task('citation', lambda cancelled, progress: find_matching_citation(file_id, query, is_cancelled=cancelled))

    @Slot()
    def start_related_documents(self):
        index = self.table.currentRow()
        if self.worker is not None or not 0 <= index < len(self._results):
            return
        try:
            root = None if self.all_folders.isChecked() else self._root()
        except (ValueError, OSError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        file_id = self._results[index]['file_id']
        limit = min(self.limit.value(), MAX_RELATED_RESULTS)
        self._start_task('related', lambda cancelled, progress: find_related_documents(file_id, limit=limit, root_path=root, is_cancelled=cancelled))

    @Slot()
    def _clear_content_dialog(self):
        self.content_dialog = None
        if self._closing and self.worker is None:
            self.close()

    @Slot()
    def start_content_preview(self):
        index = self.table.currentRow()
        if self.worker is not None or not 0 <= index < len(self._results):
            return
        file_id = self._results[index]['file_id']
        self._start_task("preview", lambda cancelled, progress: get_content_preview(file_id))

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
        elif self.content_dialog is not None and self.content_dialog.worker is not None:
            self._closing = True
            self.content_dialog.close()
            event.ignore()
        else:
            self._settings_timer.stop()
            self._save_settings()
            event.accept()


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("File Network")
    window = MainWindow()
    window.show()
    raise SystemExit(application.exec())


if __name__ == "__main__":
    main()
