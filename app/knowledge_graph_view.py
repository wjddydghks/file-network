"""관심사 노드/공통 파일 연결을 눌러 파일 목록을 탐색하는 지식 그래프."""
from itertools import combinations
from math import cos, sin, pi, sqrt
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import (QComboBox, QGraphicsEllipseItem, QGraphicsItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QHBoxLayout, QPushButton, QVBoxLayout, QWidget)

from app.graph_view import GraphCanvas
from core.knowledge_graph import graph_nodes


class KnowledgeGraphView(QWidget):
    filesRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.canvas = GraphCanvas(self.scene, self)
        self.selector = QComboBox()
        self.selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(18)
        self.selector.currentIndexChanged.connect(self._selected_combo)
        self.fit = QPushButton('그래프 맞춤')
        self.fit.clicked.connect(self.canvas.fit_graph)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        row = QHBoxLayout()
        row.addWidget(self.selector, 1);row.addWidget(self.fit)
        layout.addLayout(row);layout.addWidget(self.canvas,1)
        self.scene.selectionChanged.connect(self._selected_scene)
        self.file_ids = {}
        self.labels = {}
        self.descriptions = {}
        self.nodes = []
        self.edges = []

    def render_graph(self, plan, state):
        self.scene.blockSignals(True);self.selector.blockSignals(True)
        self.scene.clear();self.selector.clear()
        self.nodes = graph_nodes(plan, state)
        self.edges = []
        self.file_ids = {'all': [d.file_id for d in plan.documents]}
        self.labels = {'all': '영역 전체 파일'}
        self.descriptions = {'all': '선택한 영역의 저장 파일입니다. 미분석·차단·미분류 파일도 포함합니다.'}
        self.selector.addItem(f'전체 파일 · {len(plan.documents):,}개', 'all')
        for node in self.nodes:
            key = node['id']
            self.file_ids[key] = node['files']
            self.labels[key] = node['label']
            self.descriptions[key] = node['description']
            self.selector.addItem(f"{node['label']} · {len(node['files']):,}개", key)
        categories = [n for n in self.nodes if not n['id'].startswith('status:')]
        statuses = [n for n in self.nodes if n['id'].startswith('status:')]
        by_file = {}
        for node in categories:
            for file_id in node['files']:
                by_file.setdefault(file_id, []).append(node['id'])
        common = {}
        for file_id, category_ids in by_file.items():
            for pair in combinations(sorted(category_ids), 2):
                common.setdefault(pair, []).append(file_id)
        positions = {}
        radius = max(210, len(categories) * 29) if len(categories)>1 else 0
        for index, node in enumerate(categories):
            angle = -pi/2 + 2*pi*index/max(1,len(categories))
            positions[node['id']] = (radius*cos(angle), radius*sin(angle))
        palette = ['#dfe8ff','#e0f4ec','#fff0d8','#eee5ff','#fce3eb','#dff1f7']
        for (a,b), files in sorted(common.items(), key=lambda p: -len(p[1])):
            key = f'edge:{a}:{b}'
            self.file_ids[key] = files
            self.labels[key] = f'{self.labels[a]} ↔ {self.labels[b]}'
            self.descriptions[key] = '두 관심사에 함께 AI 분류된 공통 파일입니다. 인과관계나 문서의 선후 관계를 뜻하지 않습니다.'
            self.selector.addItem(f'{self.labels[key]} · 공통 {len(files):,}개',key)
            x1,y1 = positions[a];x2,y2 = positions[b]
            item = self.scene.addLine(x1,y1,x2,y2,QPen(QColor('#a7b7d2'), min(7,1.5+sqrt(len(files)))))
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable);item.setData(0,key)
            item.setToolTip(escape(f'{self.labels[key]} · 공통 파일 {len(files)}개'))
            self.edges.append({'id':key,'source':a,'target':b,'files':files})
        for index, node in enumerate(categories):
            self._node(node, *positions[node['id']], palette[index%len(palette)])
        for index,node in enumerate(statuses):
            self._node(node,(index-(len(statuses)-1)/2)*180,radius+220,'#edf0f5')
        if not self.nodes:
            self.scene.addSimpleText('저장된 파일이 없습니다. 선택한 폴더를 먼저 분석하세요.')
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-45,-45,45,45))
        self.scene.blockSignals(False);self.selector.blockSignals(False)
        self.canvas.fit_graph()
        self._selected_combo()

    def _node(self, node, x, y, color):
        diameter = min(166,120+sqrt(len(node['files']))*3)
        item = QGraphicsEllipseItem(x-diameter/2,y-diameter/2,diameter,diameter)
        item.setBrush(QColor(color));item.setPen(QPen(QColor('#7388af'),2))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item.setData(0,node['id']);item.setZValue(2)
        item.setToolTip(escape(node['label']+'\n'+node['description']))
        font = QFont();font.setPixelSize(21);font.setBold(True)
        title = QGraphicsSimpleTextItem(item);title.setFont(font)
        title.setText(QFontMetrics(font).elidedText(node['label'],Qt.TextElideMode.ElideMiddle,int(diameter-14)))
        title.setPos(x-title.boundingRect().width()/2,y-23)
        count = QGraphicsSimpleTextItem(f"{len(node['files']):,}개 파일",item)
        font2 = QFont();font2.setPixelSize(18);count.setFont(font2)
        count.setPos(x-count.boundingRect().width()/2,y+10)
        self.scene.addItem(item)

    def _selected_combo(self):
        key = self.selector.currentData()
        if key is not None:
            self.filesRequested.emit(key)

    def _selected_scene(self):
        selected = self.scene.selectedItems()
        if selected:
            index = self.selector.findData(selected[0].data(0))
            if index>=0:
                self.selector.setCurrentIndex(index)
