"""기존 관련 문서 스냅샷을 그리는 로컬 Qt 그래프. DB/네트워크 접근 없음."""
from html import escape
from math import cos, pi, sin

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)


MAX_GRAPH_NEIGHBORS = 20


class DocumentNode(QGraphicsRectItem):
    def __init__(self, row, is_source, activate):
        super().__init__(-85, -30, 170, 60)
        self.file_id = row['file_id']
        self._activate = activate
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setBrush(QColor('#dbeafe' if is_source else '#f8fafc'))
        self.setPen(QPen(QColor('#2563eb' if is_source else '#64748b'), 2))
        self.setZValue(1)
        title = QGraphicsSimpleTextItem(self)
        title.setBrush(QColor('#0f172a'))
        text = QFontMetrics(title.font()).elidedText(row['name'].replace('\n', ' ').replace('\r', ' '), Qt.TextElideMode.ElideMiddle, 152)
        title.setText(text)
        title.setPos(-76, -20)
        subtitle = QGraphicsSimpleTextItem('기준 문서' if is_source else '관련 후보', self)
        subtitle.setBrush(QColor('#475569'))
        subtitle.setPos(-76, 3)
        self.setToolTip(escape(row['name']) + '<br>' + escape(row['path']))

    def mouseDoubleClickEvent(self, event):
        self._activate(self.file_id)
        event.accept()


class GraphCanvas(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor('#ffffff'))

    def zoom(self, factor):
        current = self.transform().m11()
        target = min(3.0, max(0.15, current * factor))
        self.scale(target / current, target / current)

    def wheelEvent(self, event):
        if event.angleDelta().y():
            self.zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
            event.accept()
        else:
            super().wheelEvent(event)

    def fit_graph(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom(1.0)


class RelatedGraphDialog(QDialog):
    fileActivated = Signal(object, str)

    def __init__(self, snapshot, parent=None):
        super().__init__(parent)
        self.setWindowTitle('관련 문서 그래프 — 공유 키워드 근거')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1050, 800)
        self._shown = False
        self.source = snapshot['source']
        candidates = snapshot['results'][:MAX_GRAPH_NEIGHBORS]
        self.rows = {row['file_id']: row for row in [self.source, *candidates]}
        self._keywords = [item['term'] for item in snapshot['keywords']]
        self.scene = QGraphicsScene(self)
        self.nodes = {}
        self.edges = []
        self.canvas = GraphCanvas(self.scene, self)
        self.selector = QComboBox()
        self.selector.setAccessibleName('그래프 문서 선택')
        for row in [self.source, *candidates]:
            is_source = row['file_id'] == self.source['file_id']
            self.selector.addItem(('기준: ' if is_source else '') + row['name'], row['file_id'])
            node = DocumentNode(row, is_source, self._activate_node)
            self.scene.addItem(node)
            self.nodes[row['file_id']] = node
        radius = max(270, len(candidates) * 34)
        for index, row in enumerate(candidates):
            angle = -pi / 2 + 2 * pi * index / len(candidates)
            x, y = radius * cos(angle), radius * sin(angle)
            self.nodes[row['file_id']].setPos(x, y)
            edge = self.scene.addLine(0, 0, x, y, QPen(QColor('#94a3b8'), 2))
            edge.setZValue(-1)
            edge.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            edge.setData(0, row['file_id'])
            edge.setToolTip('공유 키워드: ' + escape(', '.join(row['shared_keywords'])))
            self.edges.append(edge)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-35, -35, 35, 35))
        note = QLabel(f"기준 문서와 상위 후보 {len(candidates)}/{len(snapshot['results'])}개를 표시합니다. 선은 공유 키워드 근거이며 의미 유사성·실제 관계를 확정하지 않습니다. 후보끼리의 관계는 그리지 않습니다.")
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.PlainText)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('문서 선택'))
        controls.addWidget(self.selector, 1)
        self.zoom_in = QPushButton('확대 +')
        self.zoom_out = QPushButton('축소 −')
        self.fit = QPushButton('화면 맞춤')
        self.zoom_in.clicked.connect(lambda: self.canvas.zoom(1.2))
        self.zoom_out.clicked.connect(lambda: self.canvas.zoom(1 / 1.2))
        self.fit.clicked.connect(self.canvas.fit_graph)
        for button in (self.zoom_in, self.zoom_out, self.fit):
            button.setAutoDefault(False)
            controls.addWidget(button)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(190)
        self.open_body = QPushButton('선택 문서 본문 보기')
        self.open_body.setAutoDefault(False)
        self.open_body.clicked.connect(self._open_selected)
        close = QPushButton('닫기')
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        actions = QHBoxLayout()
        actions.addWidget(QLabel('문서 클릭: 근거 확인 · 두 번 클릭: 본문 보기 · 빈 공간 드래그: 이동'))
        actions.addStretch(1)
        actions.addWidget(self.open_body)
        actions.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(controls)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.details)
        layout.addLayout(actions)
        self.scene.selectionChanged.connect(self._selection_changed)
        self.selector.currentIndexChanged.connect(lambda: self.select_file(self.selector.currentData()))
        self.select_file(self.source['file_id'])

    def select_file(self, file_id):
        if file_id not in self.nodes:
            return
        self.scene.clearSelection()
        self.nodes[file_id].setSelected(True)

    def _selection_changed(self):
        selected = [item for item in self.scene.selectedItems() if isinstance(item, DocumentNode)]
        edges = [item for item in self.scene.selectedItems() if item.data(0) in self.rows]
        if not selected and not edges:
            return
        file_id = selected[0].file_id if selected else edges[0].data(0)
        row = self.rows[file_id]
        index = self.selector.findData(row['file_id'])
        self.selector.blockSignals(True)
        self.selector.setCurrentIndex(index)
        self.selector.blockSignals(False)
        is_source = row['file_id'] == self.source['file_id']
        keywords = self._keywords if is_source else row['shared_keywords']
        label = '선정 키워드' if is_source else '공유 키워드'
        self.details.setPlainText(f"{row['name']}\n{row['path']}\n분석 시각: {row['analyzed_at']}\n{label}: {', '.join(keywords) or '없음'}\n\n{row.get('snippet', '')}\n\n마지막 분석 결과입니다. 원본이 변경되었을 수 있습니다.")

    def _activate_node(self, file_id):
        self.select_file(file_id)
        self._open_selected()

    def _open_selected(self):
        row = self.rows[self.selector.currentData()]
        self.fileActivated.emit(row['file_id'], row['analyzed_at'])
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown:
            self.canvas.fit_graph()
            self._shown = True
