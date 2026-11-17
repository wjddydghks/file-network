"""큰 관심사 → 세부 주제 → 관련 파일을 탐색하는 지식 그래프."""
from itertools import combinations
from math import sqrt
from html import escape

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import (QComboBox, QGraphicsEllipseItem, QGraphicsItem, QGraphicsScene,
    QGraphicsTextItem, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

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
        self.back = QPushButton('전체 관심사')
        self.back.clicked.connect(self._back)
        self.breadcrumb = QLabel('전체 관심사')
        self.breadcrumb.setTextFormat(Qt.TextFormat.PlainText)
        self.breadcrumb.setWordWrap(True)
        self.status_counts = QLabel()
        self.status_counts.setWordWrap(True)
        self.status_counts.setTextFormat(Qt.TextFormat.PlainText)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        row = QHBoxLayout()
        row.addWidget(self.back);row.addWidget(self.selector, 1);row.addWidget(self.fit)
        layout.addLayout(row);layout.addWidget(self.breadcrumb);layout.addWidget(self.status_counts)
        layout.addWidget(self.canvas,1)
        self.scene.selectionChanged.connect(self._selected_scene)
        self.file_ids = {}
        self.labels = {}
        self.descriptions = {}
        self.nodes = []
        self.edges = []
        self.visible_nodes = []
        self.focus = ''

    def render_graph(self, plan, state):
        self.selector.blockSignals(True)
        self.selector.clear()
        self.nodes = graph_nodes(plan, state)
        self.edges = []
        self.file_ids = {'all': [d.file_id for d in plan.documents]}
        self.labels = {'all': '영역 전체 파일'}
        self.descriptions = {'all': '선택한 영역의 저장 파일입니다. 관심사를 누르면 세부 주제와 관련 파일을 볼 수 있습니다.'}
        self.selector.addItem(f'전체 파일 · {len(plan.documents):,}개', 'all')
        interests = [n for n in self.nodes if n['id'].startswith('I')]
        topics = [n for n in self.nodes if n['id'].startswith('C')]
        statuses = [n for n in self.nodes if n['id'].startswith('status:')]
        ordered = []
        for root in interests:
            ordered.append(root)
            ordered.extend(n for n in topics if n.get('parent') == root['id'])
        ordered.extend(statuses)
        for node in ordered:
            key = node['id']
            self.file_ids[key] = node['files']
            self.labels[key] = node['label']
            self.descriptions[key] = node['description']
            prefix = '    ↳ ' if key.startswith('C') else '상태 · ' if key.startswith('status:') else ''
            self.selector.addItem(f"{prefix}{node['label']} · {len(node['files']):,}개", key)
        for collection in (topics, interests):
            by_file = {}
            for node in collection:
                for file_id in node['files']:
                    by_file.setdefault(file_id, []).append(node['id'])
            common = {}
            for file_id, category_ids in by_file.items():
                for pair in combinations(sorted(category_ids), 2):
                    common.setdefault(pair, []).append(file_id)
            for (a,b), files in sorted(common.items(), key=lambda p: -len(p[1])):
                self._edge(f'edge:{a}:{b}', a, b, files, 'shared',
                           '두 주제에 함께 AI 분류된 공통 파일입니다. 인과관계나 문서의 선후 관계를 뜻하지 않습니다.')
        for topic in topics:
            parent = topic['parent']
            self._edge(f"hierarchy:{parent}:{topic['id']}",parent,topic['id'],topic['files'],'hierarchy',
                       '큰 관심사에 속한 세부 주제입니다. 이 연결을 누르면 해당 세부 주제의 파일을 보여줍니다.')
        self.status_counts.setText('처리 현황 · '+' / '.join(f"{n['label']} {len(n['files']):,}개" for n in statuses) if statuses else '처리 현황 · 모든 전송 가능 파일 분류 완료')
        self.selector.blockSignals(False)
        self.focus = ''
        self._draw('')
        self._selected_combo()

    def _edge(self, key, a, b, files, kind, description):
        self.file_ids[key] = files
        self.labels[key] = f"{self.labels[a]} {'→' if kind == 'hierarchy' else '↔'} {self.labels[b]}"
        self.descriptions[key] = description
        suffix = '세부 주제' if kind == 'hierarchy' else '공통 파일'
        self.selector.addItem(f'{self.labels[key]} · {suffix} {len(files):,}개',key)
        self.edges.append({'id':key,'source':a,'target':b,'files':files,'kind':kind})

    def _draw(self, focus):
        self.focus = focus
        self.scene.blockSignals(True)
        self.scene.clear()
        self.text_items=[]
        interests = [n for n in self.nodes if n['id'].startswith('I')]
        root = next((n for n in interests if n['id'] == focus),None)
        children = [n for n in self.nodes if n.get('parent') == focus] if root else []
        self.visible_nodes = [root]+children if root else interests
        self.breadcrumb.setText('전체 관심사 → '+root['label']+' → 세부 주제를 눌러 파일 보기' if root else '전체 관심사 · 노드를 눌러 세부 주제 펼치기')
        self.back.setEnabled(bool(root))
        positions = {}
        if root:
            positions[root['id']] = (-240,0)
            for i,node in enumerate(children):
                positions[node['id']] = (160, (i-(len(children)-1)/2)*180)
        else:
            for i,node in enumerate(interests):
                positions[node['id']] = ((i%3-1)*260, (i//3)*210)
        for edge in self.edges:
            a,b = edge['source'], edge['target']
            if a not in positions or b not in positions:
                continue
            x1,y1=positions[a];x2,y2=positions[b]
            pen=QPen(QColor('#728db6' if edge['kind']=='hierarchy' else '#b6c5dc'),
                     2 if edge['kind']=='hierarchy' else min(6,1.5+sqrt(len(edge['files']))))
            if edge['kind']=='shared':pen.setStyle(Qt.PenStyle.DashLine)
            item=self.scene.addLine(x1,y1,x2,y2,pen)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable);item.setData(0,edge['id'])
            item.setToolTip(escape(f"{self.labels[edge['id']]} · {len(edge['files']):,}개 파일"))
        palette=['#dfe8ff','#e0f4ec','#fff0d8','#eee5ff','#fce3eb','#dff1f7']
        for i,node in enumerate(self.visible_nodes):
            self._node(node,*positions[node['id']],palette[i%len(palette)])
        if not self.visible_nodes:
            text='전체 파일 요약 → 주제 통합 → 관심사 구성 → 파일 분류 순서로 진행합니다.\n분석·전송 확인에서 실행하거나 저장된 작업을 이어서 진행하세요.'
            self.scene.addText(text)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-35,-35,35,35))
        self.scene.blockSignals(False)
        self.canvas.fit_graph()

    def _node(self, node, x, y, color):
        diameter=172
        item=QGraphicsEllipseItem(x-diameter/2,y-diameter/2,diameter,diameter)
        item.setBrush(QColor(color));item.setPen(QPen(QColor('#7388af'),2))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item.setData(0,node['id']);item.setZValue(2)
        item.setToolTip(escape(node['label']+'\n'+node['description']))
        self.scene.addItem(item)
        title=QGraphicsTextItem(item)
        title.setDefaultTextColor(QColor('#17233b'))
        font=QFont();font.setPixelSize(17);font.setBold(True);title.setFont(font)
        title.setTextWidth(diameter-20)
        title.setHtml('<div align="center">'+escape(node['label'])+'</div>')
        while title.boundingRect().height()>96 and font.pixelSize()>11:
            font.setPixelSize(font.pixelSize()-1);title.setFont(font)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        title.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        title.setPos(x-(diameter-20)/2,y-title.boundingRect().height()/2-13)
        count=QGraphicsTextItem(f"{len(node['files']):,}개 파일",item)
        count.setDefaultTextColor(QColor('#17233b'))
        font2=QFont();font2.setPixelSize(15);count.setFont(font2)
        count.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        count.setPos(x-count.boundingRect().width()/2,y+34)
        self.text_items.extend((title,count))

    def _back(self):
        self.selector.setCurrentIndex(0)
        self._draw('')

    def _selected_combo(self):
        key=self.selector.currentData()
        if key is None:return
        if key.startswith('I'):
            focus=key
        elif key.startswith('C'):
            focus=next((n.get('parent','') for n in self.nodes if n['id']==key),'')
        elif key.startswith('hierarchy:'):
            focus=key.split(':')[1]
        elif key.startswith('edge:C'):
            source=key.split(':')[1]
            focus=next((n.get('parent','') for n in self.nodes if n['id']==source),'')
        else:focus=''
        if focus!=self.focus:self._draw(focus)
        self.filesRequested.emit(key)

    def _selected_scene(self):
        selected=self.scene.selectedItems()
        if selected:
            key=selected[0].data(0)
            # Finish the graphics mouse event before clearing/redrawing the clicked item.
            QTimer.singleShot(0,self,lambda:self._select_key(key))

    def _select_key(self,key):
        index=self.selector.findData(key)
        if index>=0:self.selector.setCurrentIndex(index)
