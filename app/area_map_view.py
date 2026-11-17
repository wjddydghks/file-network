"""전체 영역의 저장 구조를 탐색하는 로컬 지도. 의미·업무 관계를 추정하지 않는다."""
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.analysis_details import STATUS_LABELS
from app.graph_view import GraphCanvas
from core.area_layout import layout_areas
from database.area_map import FILE_PAGE_SIZE, GROUP_PAGE_SIZE, get_area_map
from database.document_chunks import get_document_chunk


def text_label(text=''):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class AreaTile(QGraphicsRectItem):
    def __init__(self, group, index, rect, interest, activate):
        x, y, width, height = rect
        super().__init__(x + 2, y + 2, max(0, width - 4), max(0, height - 4))
        self._activate, self.index = activate, index
        self.setData(0, index)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        muted = bool(interest) and group['matched'] == 0
        self.setBrush(QColor('#f0f2f7' if muted else '#e6edff' if group['kind'] == 'folder' else '#def3eb'))
        self.setPen(QPen(QColor('#cbd4e4' if muted else '#6079ce'), 1.5))
        self.setToolTip(escape(f"{group['label']}\n저장 {group['count']:,} · 키워드 일치 {group['matched']:,}\n{group['key']}"))
        if width >= 120 and height >= 85:
            title = QGraphicsSimpleTextItem(self)
            font = QFont(); font.setPixelSize(24); font.setBold(True)
            title.setFont(font)
            title.setBrush(QColor('#263b68'))
            title.setText(QFontMetrics(title.font()).elidedText(group['label'], Qt.TextElideMode.ElideMiddle, max(1, int(width - 32))))
            title.setPos(x + 16, y + 16)
            count = QGraphicsSimpleTextItem(f"{group['matched']:,} / {group['count']:,}" if interest else f"{group['count']:,}개 파일", self)
            count_font = QFont(); count_font.setPixelSize(20)
            count.setFont(count_font)
            count.setPos(x + 16, y + 52)
            count.setBrush(QColor('#61718e'))

    def mouseDoubleClickEvent(self, event):
        self._activate(self.index)
        event.accept()


class AreaMapDialog(QDialog):
    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.setWindowTitle('영역 지도 · 로컬 구조와 키워드')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1250, 850)
        self.setMinimumSize(960, 680)
        self.root = str(Path(root).expanduser().resolve())
        self.worker = None
        self._closing = False
        self.snapshot = None
        self.theme_dialog = None
        self._preview = None
        self._outcome = ('cancelled', None)
        self.back = QPushButton('상위 범위')
        self.back.clicked.connect(self.go_back)
        self.home = QPushButton('처음 영역')
        self.home.clicked.connect(lambda: self.load_map(focus=self.root, selected_type=None, group_offset=0, file_offset=0))
        self.path = QLineEdit(self.root)
        self.path.setReadOnly(True)
        self.refresh = QPushButton('지도 새로고침')
        self.refresh.clicked.connect(lambda: self.load_map())
        self.interest = QLineEdit()
        self.interest.setPlaceholderText('관심 키워드 · 이름 또는 본문에서 모든 단어 일치 (의미 검색 아님)')
        self.interest.returnPressed.connect(self.apply_interest)
        self.apply = QPushButton('관심 영역 강조')
        self.apply.setObjectName('primary')
        self.apply.clicked.connect(self.apply_interest)
        self.theme_button = QPushButton('AI 주제·관계 분석')
        self.theme_button.clicked.connect(self.open_theme_analysis)
        self.summary = text_label('선택 영역의 저장 구조를 읽고 있습니다.')
        self.scene = QGraphicsScene(self)
        self.canvas = GraphCanvas(self.scene, self)
        self.canvas.setMinimumSize(380, 230)
        self.selector = QComboBox()
        self.selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(15)
        self.selector.currentIndexChanged.connect(self.select_group)
        self.open_group_button = QPushButton('선택 묶음 펼치기')
        self.open_group_button.clicked.connect(self.open_group)
        self.group_previous = QPushButton('이전 묶음')
        self.group_next = QPushButton('다음 묶음')
        self.group_previous.clicked.connect(lambda: self.page_groups(-1))
        self.group_next.clicked.connect(lambda: self.page_groups(1))
        self.fit = QPushButton('지도 맞춤')
        self.fit.clicked.connect(self.canvas.fit_graph)
        self.group_detail = text_label()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['문서', '분석 상태', '일치 위치'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 175)
        self.table.itemSelectionChanged.connect(self.file_selection_changed)
        self.table.itemDoubleClicked.connect(lambda *_: self.load_preview())
        self.file_previous = QPushButton('이전 파일')
        self.file_next = QPushButton('다음 파일')
        self.file_previous.clicked.connect(lambda: self.page_files(-1))
        self.file_next.clicked.connect(lambda: self.page_files(1))
        self.file_count = text_label()
        self.preview_button = QPushButton('선택 문서 인용 보기 · 로컬')
        self.preview_button.clicked.connect(lambda: self.load_preview())
        self.chunk_previous = QPushButton('이전 인용')
        self.chunk_next = QPushButton('다음 인용')
        self.chunk_previous.clicked.connect(lambda: self.load_preview(self._preview['segment'] - 1))
        self.chunk_next.clicked.connect(lambda: self.load_preview(self._preview['segment'] + 1))
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setPlaceholderText('문서를 선택하고 인용을 열면 실제 저장 근거를 확인할 수 있습니다.')
        self.location = text_label()
        self.status = text_label()
        self.close_button = QPushButton('닫기')
        self.close_button.clicked.connect(self.close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        navigation = QHBoxLayout()
        for widget in (self.back, self.home, self.path, self.refresh):
            navigation.addWidget(widget, 1 if widget is self.path else 0)
        layout.addLayout(navigation)
        row = QHBoxLayout()
        row.addWidget(self.interest, 1)
        row.addWidget(self.apply)
        row.addWidget(self.theme_button)
        layout.addLayout(row)
        layout.addWidget(self.summary)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(self.selector, 1)
        row.addWidget(self.open_group_button)
        left_layout.addLayout(row)
        left_layout.addWidget(self.canvas, 1)
        left_layout.addWidget(self.group_detail)
        row = QHBoxLayout()
        for widget in (self.group_previous, self.group_next, self.fit):
            row.addWidget(widget)
        left_layout.addLayout(row)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.file_count)
        right_layout.addWidget(self.table, 2)
        row = QHBoxLayout()
        row.addWidget(self.file_previous)
        row.addWidget(self.file_next)
        right_layout.addLayout(row)
        right_layout.addWidget(self.preview_button)
        row = QHBoxLayout()
        row.addWidget(self.chunk_previous)
        row.addWidget(self.chunk_next)
        right_layout.addLayout(row)
        right_layout.addWidget(self.location)
        right_layout.addWidget(self.body, 1)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([650, 480])
        layout.addWidget(split, 1)
        layout.addWidget(text_label('면적 = 이 페이지의 저장 파일 수 · 폴더는 하위 전체 포함 · 형식 묶음은 현재 폴더 바로 아래 파일만 포함'))
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.scene.selectionChanged.connect(self._scene_selected)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.update_buttons()
        QTimer.singleShot(0, self.load_map)

    def _start(self, kind, operation):
        from app.gui import TaskThread
        self._kind, self._outcome = kind, ('cancelled', None)
        self.worker = TaskThread(operation, self)
        self.worker.completed.connect(lambda data: setattr(self, '_outcome', ('completed', data)))
        self.worker.failed.connect(lambda error: setattr(self, '_outcome', ('failed', error)))
        self.worker.finished.connect(self._finished)
        self.update_buttons()
        self.worker.start()

    def load_map(self, **changes):
        if self.worker is not None or self._closing:
            return
        old = self.snapshot or {}
        options = {key: old.get(key, default) for key, default in [
            ('focus', self.root), ('selected_type', None), ('group_offset', 0), ('file_offset', 0)]}
        options.update(changes)
        options['interest'] = self.interest.text()
        self.status.setText('선택 영역을 집계하는 중… 외부 전송 없음.')
        self._start('map', lambda cancelled, progress: get_area_map(self.root, is_cancelled=cancelled, **options))

    @Slot()
    def apply_interest(self):
        self.load_map(group_offset=0, file_offset=0)

    @Slot()
    def go_back(self):
        if self.snapshot is None:
            return
        focus = self.snapshot['focus']
        if self.snapshot['selected_type'] is None and focus != self.root:
            focus = str(Path(focus).parent)
        self.load_map(focus=focus, selected_type=None, group_offset=0, file_offset=0)

    def _activate_tile(self, index):
        self.selector.setCurrentIndex(index)
        self.open_group()

    @Slot()
    def open_group(self):
        group = self.selector.currentData()
        if group is None:
            return
        self.load_map(focus=group['key'] if group['kind'] == 'folder' else self.snapshot['focus'],
                      selected_type=group['key'] if group['kind'] == 'type' else None,
                      group_offset=0, file_offset=0)

    def page_groups(self, delta):
        self.load_map(group_offset=max(0, self.snapshot['group_offset'] + delta * GROUP_PAGE_SIZE))

    def page_files(self, delta):
        self.load_map(file_offset=max(0, self.snapshot['file_offset'] + delta * FILE_PAGE_SIZE))

    @Slot()
    def _scene_selected(self):
        selected = self.scene.selectedItems()
        if selected:
            self.selector.setCurrentIndex(selected[0].data(0))

    @Slot()
    def select_group(self):
        group = self.selector.currentData()
        if group:
            relationship = '실제 하위 폴더 포함 관계' if group['kind'] == 'folder' else '현재 폴더 바로 아래의 동일 확장자 묶음'
            self.group_detail.setText(f"{group['label']} · {relationship}\n저장 {group['count']:,} · 키워드 일치 {group['matched']:,} · 본문 검색 가능 {group['extracted']:,}")
        self.update_buttons()

    @Slot()
    def file_selection_changed(self):
        self._preview = None
        self.body.clear()
        self.location.clear()
        self.update_buttons()

    def selected_file(self):
        row = self.table.currentRow()
        files = self.snapshot['files'] if self.snapshot else []
        return files[row] if 0 <= row < len(files) else None

    def load_preview(self, segment=0):
        row = self.selected_file()
        if self.worker is not None or not row or row['status'] != 'EXTRACTED':
            return
        self.body.clear()
        self.location.clear()
        self.status.setText('분석 버전이 일치하는 로컬 인용을 읽고 있습니다.')
        self._start('preview', lambda cancelled, progress: get_document_chunk(row['file_id'], segment, row['analyzed_at']))

    def update_buttons(self):
        busy, data = self.worker is not None, self.snapshot
        for widget in (self.home, self.refresh, self.interest, self.apply, self.table, self.selector, self.fit):
            widget.setEnabled(not busy)
        self.theme_button.setEnabled(not busy and data is not None and sum(r['status'] == 'EXTRACTED' for r in data['files']) >= 2)
        self.back.setEnabled(not busy and data is not None and (data['focus'] != self.root or data['selected_type'] is not None))
        self.open_group_button.setEnabled(not busy and self.selector.currentIndex() >= 0)
        self.group_previous.setEnabled(not busy and data is not None and data['group_offset'] > 0)
        self.group_next.setEnabled(not busy and data is not None and data['group_offset'] + GROUP_PAGE_SIZE < data['group_count'])
        self.file_previous.setEnabled(not busy and data is not None and data['file_offset'] > 0)
        self.file_next.setEnabled(not busy and data is not None and data['file_offset'] + FILE_PAGE_SIZE < data['selected_files'])
        selected = self.selected_file()
        self.preview_button.setEnabled(not busy and selected is not None and selected['status'] == 'EXTRACTED')
        self.chunk_previous.setEnabled(not busy and self._preview is not None and self._preview['segment'] > 0)
        self.chunk_next.setEnabled(not busy and self._preview is not None and self._preview['segment'] + 1 < self._preview['chunk_count'])

    @Slot()
    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        if self._closing:
            self.close()
            return
        outcome, result = self._outcome
        if outcome != 'completed':
            if self._kind == 'preview':
                self._preview = None
            self.status.setText(result if outcome == 'failed' else '조회가 취소되었습니다.')
        elif self._kind == 'map':
            self.render_snapshot(result)
        elif result and result['status'] == 'EXTRACTED' and result['chunk_count']:
            self._preview = result
            self.body.setPlainText(result['text'])
            location = f"PDF 페이지 {result['page']}" if result['page'] else {'sheet': 'Excel 시트', 'slide': 'PowerPoint 슬라이드'}.get(result['source_kind'], '텍스트 구간') + (' ' + str(result['source_label']) if result['source_label'] else '')
            self.location.setText(f"인용 {result['segment'] + 1}/{result['chunk_count']} · {location}")
            self.status.setText('마지막 분석 시점의 저장 인용입니다. 원본 변경은 다시 분석해야 확인됩니다.')
        else:
            self._preview = None
            self.status.setText('저장 인용이 없거나 분석 결과가 변경되었습니다. 지도를 새로고침하세요.')
        self.update_buttons()

    def render_snapshot(self, data):
        self.snapshot = data
        self._preview = None
        self.body.clear()
        self.location.clear()
        self.path.setText(data['focus'] + (f" · 직접 파일 형식: {data['selected_type'] or '확장자 없음'}" if data['selected_type'] is not None else ''))
        query = data['interest'] or '없음'
        self.summary.setText(f"이 범위 저장 파일 {data['total_files']:,} · 본문 검색 가능 {data['states'].get('EXTRACTED', 0):,} · 관심 키워드 일치 {data['matched_files']:,}\n적용 키워드: {query} · AI 의미 분석 아님 · 저장 시점 기준")
        self.scene.clear()
        self.selector.blockSignals(True)
        self.selector.clear()
        groups = data['groups']
        for index, (group, rect) in enumerate(zip(groups, layout_areas([g['count'] for g in groups]))):
            self.selector.addItem(f"{group['label']} · {group['count']:,}개", group)
            self.scene.addItem(AreaTile(group, index, rect, data['interest'], self._activate_tile))
        self.selector.blockSignals(False)
        if data['interest']:
            for index, group in enumerate(groups):
                if group['matched']:
                    self.selector.setCurrentIndex(index)
                    break
        self.scene.setSceneRect(0, 0, 1000, 560)
        if not groups:
            self.scene.addSimpleText('저장된 묶음이 없습니다. 폴더 분석 또는 이전 페이지를 확인하세요.')
        self.canvas.fit_graph()
        self.group_detail.clear()
        self.select_group()
        self.table.setRowCount(len(data['files']))
        for index, row in enumerate(data['files']):
            values = (row['name'], STATUS_LABELS.get(row['status'], row['status']), {'content': '본문', 'name': '파일명', None: '—'}[row['match_source']])
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(row['path'])
                self.table.setItem(index, column, item)
        if data['files']:
            self.table.selectRow(0)
        count, total = len(data['files']), data['selected_files']
        self.file_count.setText(f"문서 {data['file_offset'] + 1 if count else 0}–{data['file_offset'] + count if count else 0} / {total:,}")
        self.status.setText(f"묶음 {data['group_offset'] + 1 if groups else 0}–{data['group_offset'] + len(groups) if groups else 0} / {data['group_count']:,} · 파일 목록은 {FILE_PAGE_SIZE}개씩 표시합니다. 외부 전송 없음.")

    @Slot()
    def open_theme_analysis(self):
        if self.worker is not None or not self.snapshot:
            return
        if self.theme_dialog is not None:
            self.theme_dialog.raise_()
            return
        candidates = [r for r in self.snapshot['files'] if r['status'] == 'EXTRACTED']
        if len(candidates) < 2:
            self.status.setText('현재 파일 페이지에 본문 분석이 완료된 문서가 최소 2개 필요합니다.')
            return
        from app.theme_dialog import ThemeAnalysisDialog
        self.theme_dialog = ThemeAnalysisDialog(candidates, self.root, self.snapshot['interest'], self)
        self.theme_dialog.finished.connect(self._clear_theme_dialog)
        self.theme_dialog.show()

    @Slot()
    def _clear_theme_dialog(self):
        self.theme_dialog = None
        if self._closing:
            self.close()

    @property
    def has_active_work(self):
        return self.worker is not None or (self.theme_dialog is not None and self.theme_dialog.worker is not None)

    def closeEvent(self, event):
        self._closing = True
        if self.worker is not None:
            self.worker.cancel_event.set()
            self.status.setText('조회 종료 후 닫습니다.')
            event.ignore()
        elif self.theme_dialog is not None and self.theme_dialog.worker is not None:
            self.theme_dialog.close()
            event.ignore()
        else:
            if self.theme_dialog is not None:
                self.theme_dialog.close()
            super().closeEvent(event)

    def reject(self):
        if self.has_active_work or self.theme_dialog is not None:
            self.close()
        else:
            self._closing = True
            if self.theme_dialog is not None:
                self.theme_dialog.close()
            super().reject()
