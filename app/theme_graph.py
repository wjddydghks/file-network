"""검증된 AI 응답의 주제·인용 관계를 표시한다. DB/네트워크 접근 없음."""
from html import escape
from math import ceil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from app.graph_view import GraphCanvas
from core.ai_themes import RELATION_LABELS


class ThemeGraph(QWidget):
    citationRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None
        self.entries = []
        self.scene = QGraphicsScene(self)
        self.canvas = GraphCanvas(self.scene, self)
        self.selector = QComboBox()
        self.selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(20)
        self.selector.currentIndexChanged.connect(self.show_entry)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.open_citation = QPushButton('선택 항목의 첫 근거 인용 열기')
        self.open_citation.clicked.connect(self.request_citation)
        self.fit = QPushButton('지도 맞춤')
        self.fit.clicked.connect(self.canvas.fit_graph)
        self.note = QLabel('선택 인용의 AI 추정 지도입니다. 관계를 선택하면 이유와 실제 인용을 확인합니다.')
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.note)
        controls = QHBoxLayout()
        controls.addWidget(self.selector, 1)
        controls.addWidget(self.fit)
        layout.addLayout(controls)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.canvas)
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.addWidget(self.details, 1)
        panel_layout.addWidget(self.open_citation)
        split.addWidget(panel)
        split.setSizes([780, 330])
        layout.addWidget(split, 1)
        self.scene.selectionChanged.connect(self._scene_selected)
        self.open_citation.setEnabled(False)

    def clear(self):
        self.scene.blockSignals(True)
        self.scene.clear()
        self.scene.blockSignals(False)
        self.selector.clear()
        self.details.clear()
        self.result = None
        self.entries = []
        self.note.setText('아직 검증된 AI 결과가 없습니다. 전송 미리보기와 승인 후 분석하세요.')
        self.sources = {}
        self.open_citation.setEnabled(False)

    def _entry(self, title, explanation, evidence):
        index = len(self.entries)
        self.entries.append({'title': title, 'explanation': explanation, 'evidence': evidence})
        self.selector.addItem(title, index)
        return index

    def _node(self, title, subtitle, x, y, width, index, color):
        item = QGraphicsRectItem(x, y, width, 60)
        item.setBrush(QColor(color))
        item.setPen(QPen(QColor('#7386b6'), 1.5))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        item.setData(0, index)
        item.setZValue(1)
        item.setToolTip(escape(title))
        font = QFont(); font.setPixelSize(20); font.setBold(True)
        label = QGraphicsSimpleTextItem(item)
        label.setFont(font)
        label.setText(QFontMetrics(font).elidedText(title.replace('\n', ' '), Qt.TextElideMode.ElideMiddle, int(width - 20)))
        label.setPos(x + 10, y + 8)
        font2 = QFont(); font2.setPixelSize(16)
        sub = QGraphicsSimpleTextItem(subtitle, item)
        sub.setFont(font2)
        sub.setPos(x + 10, y + 35)
        sub.setBrush(QColor('#61718e'))
        self.scene.addItem(item)
        return item

    def render_result(self, result):
        self.clear()
        self.result = result
        self.sources = {s['source_id']: s for s in result['sources']}
        self.selector.blockSignals(True)
        positions, rectangles = {}, {}
        groups = list(result['topics'])
        if result['unclassified']:
            groups.append({'label': '미분류 · 근거 부족', 'summary': 'AI가 주제로 분류하지 않은 인용입니다.',
                           'members': result['unclassified'], 'evidence': []})
        y = 0
        for row_start in range(0, len(groups), 3):
            row_groups = groups[row_start:row_start + 3]
            row_height = max(ceil(len(g['members']) / 2) * 76 + 115 for g in row_groups)
            for col, topic in enumerate(row_groups):
                x = col * 390
                background = self.scene.addRect(x - 10, y - 10, 370, row_height - 12,
                    QPen(QColor('#e0e7f4')), QColor('#f3f6fc'))
                background.setZValue(-2)
                title = 'AI 추정 주제 · ' + topic['label']
                index = self._entry(title, topic['summary'], topic['evidence'])
                self._node(topic['label'], f"{len(topic['members'])}개 선택 인용", x, y, 350, index, '#e5ecff')
                for i, source_id in enumerate(topic['members']):
                    source = self.sources[source_id]
                    title = Path(source['path']).name
                    index = self._entry(f"인용 {source_id} · {title}", '분석에 사용한 승인 인용입니다.',
                        [{'source_id': source_id, 'quote': source['text'][:300]}])
                    nx, ny = x + (i % 2) * 180, y + 85 + (i // 2) * 76
                    node = self._node(title, f"{source_id} · 인용 {source['segment'] + 1}", nx, ny, 170, index, '#ffffff')
                    rectangles[source_id] = node.rect()
                    positions[source_id] = (nx + 85, ny + 30)
            y += row_height + 30
        for relation in result['relations']:
            title = f"AI 추정 · {RELATION_LABELS[relation['kind']]} · {relation['source']} ↔ {relation['target']}"
            index = self._entry(title, relation['explanation'], relation['evidence'])
            x1, y1 = positions[relation['source']]
            x2, y2 = positions[relation['target']]
            best_path, best_hits = None, float('inf')
            for offset in (80, -80, 120, -120, 180, -180, 260, -260, 400, -400, 640, -640):
                path = QPainterPath()
                path.moveTo(x1, y1)
                if abs(x2 - x1) >= abs(y2 - y1):
                    path.cubicTo(x1, y1 + offset, x2, y2 + offset, x2, y2)
                else:
                    path.cubicTo(x1 + offset, y1, x2 + offset, y2, x2, y2)
                hits = sum(path.intersects(rect.adjusted(-4, -4, 4, 4)) for sid, rect in rectangles.items()
                           if sid not in (relation['source'], relation['target']))
                if hits < best_hits:
                    best_path, best_hits = path, hits
                if not hits:
                    break
            line = self.scene.addPath(best_path, QPen(QColor('#d28534'), 3))
            line.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            line.setData(0, index)
            line.setToolTip(escape(title + '\n' + relation['explanation']))
            line.setZValue(0)
        self.selector.blockSignals(False)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-25, -25, 25, 25))
        self.note.setText(f"AI 추정 · {len({s['file_id'] for s in result['sources']})}개 문서 / {len(result['sources'])}개 선택 인용 · 주제 {len(result['topics'])}개 / 관계 {len(result['relations'])}개\n관심사: {result['interest']} · 문서 전체나 영역 전체를 분석한 결과가 아닙니다.")
        self.show_entry()
        self.canvas.fit_graph()

    def _scene_selected(self):
        selected = self.scene.selectedItems()
        if selected:
            self.selector.setCurrentIndex(selected[0].data(0))

    def show_entry(self):
        index = self.selector.currentIndex()
        self.open_citation.setEnabled(False)
        if not self.result or not 0 <= index < len(self.entries):
            return
        entry = self.entries[index]
        parts = [entry['title'], entry['explanation'], 'AI 추정입니다. 인용의 존재 검증은 관계의 사실성을 보장하지 않습니다.']
        for evidence in entry['evidence']:
            source = self.sources[evidence['source_id']]
            parts.append(f"{evidence['source_id']} · {source['path']} · 인용 {source['segment'] + 1}\n{evidence['quote']}")
        self.details.setPlainText('\n\n'.join(parts))
        self.open_citation.setEnabled(bool(entry['evidence']))

    def request_citation(self):
        index = self.selector.currentIndex()
        if 0 <= index < len(self.entries) and self.entries[index]['evidence']:
            self.citationRequested.emit(self.entries[index]['evidence'][0]['source_id'])
