"""큰 관심사 → 세부 주제 → 관련 파일을 탐색하는 지식 그래프."""
from itertools import combinations
from math import sqrt
from html import escape

from PySide6.QtCore import Qt, QTimer, Signal, QRectF
from PySide6.QtGui import QColor, QFont, QPen, QPainterPath
from PySide6.QtWidgets import (QComboBox, QGraphicsPathItem, QGraphicsItem, QGraphicsScene,
    QGraphicsTextItem, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from app.graph_view import GraphCanvas
from core.knowledge_graph import graph_nodes


class ReadableGraphCanvas(GraphCanvas):
    scaleChanged = Signal(float)

    def zoom(self,factor):
        super().zoom(factor)
        self.scaleChanged.emit(self.transform().m11())

    def fit_graph(self):
        rect=self.sceneRect()
        if rect.isEmpty():return
        scale=max(0.9,min(1.1,self.viewport().width()/rect.width(),self.viewport().height()/rect.height()))
        self.resetTransform();self.scale(scale,scale)
        self.centerOn(rect.center().x(),rect.top()+self.viewport().height()/(2*scale))
        self.scaleChanged.emit(scale)

    def actual_size(self):
        self.resetTransform();self.scaleChanged.emit(1.0)


class KnowledgeGraphView(QWidget):
    filesRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.canvas = ReadableGraphCanvas(self.scene, self)
        self.selector = QComboBox()
        self.selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(18)
        self.selector.currentIndexChanged.connect(self._selected_combo)
        self.fit = QPushButton('읽기 맞춤')
        self.fit.clicked.connect(self.canvas.fit_graph)
        self.zoom_out=QPushButton('−');self.zoom_in=QPushButton('+')
        self.zoom_out.setFixedWidth(34);self.zoom_in.setFixedWidth(34)
        self.zoom_out.clicked.connect(lambda:self.canvas.zoom(1/1.2))
        self.zoom_in.clicked.connect(lambda:self.canvas.zoom(1.2))
        self.scale_label=QPushButton('100%');self.scale_label.setToolTip('원래 크기로 보기')
        self.scale_label.clicked.connect(self.canvas.actual_size)
        self.canvas.scaleChanged.connect(lambda scale:self.scale_label.setText(f'{round(scale*100)}%'))
        self.overview=QPushButton('전체 보기');self.overview.setToolTip('전체 구조를 한 화면에 보기 · 글씨가 작아질 수 있습니다')
        self.overview.clicked.connect(lambda:GraphCanvas.fit_graph(self.canvas))
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
        layout.addLayout(row);layout.addWidget(self.breadcrumb)
        tools=QHBoxLayout();tools.addWidget(QLabel('휠로 확대 · 드래그로 이동 · 주제를 누르면 파일 보기'));tools.addStretch()
        for button in (self.zoom_out,self.scale_label,self.zoom_in,self.overview):tools.addWidget(button)
        layout.addLayout(tools);layout.addWidget(self.status_counts)
        layout.addWidget(self.canvas,1)
        self.scene.selectionChanged.connect(self._selected_scene)
        self.file_ids = {}
        self.labels = {}
        self.descriptions = {}
        self.nodes = []
        self.edges = []
        self.visible_nodes = []
        self.focus = ''
        self._layout_columns=0

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
        columns=self._columns()
        self._layout_columns=columns
        if root:
            columns=min(columns,max(1,len(children)))
            positions[root['id']]=((columns-1)*140,0)
            for i,node in enumerate(children):
                positions[node['id']]=((i%columns)*280,220+(i//columns)*194)
        else:
            for i,node in enumerate(interests):
                positions[node['id']]=((i%columns)*280,(i//columns)*194)
        for edge in self.edges:
            a,b = edge['source'], edge['target']
            if a not in positions or b not in positions:
                continue
            x1,y1=positions[a];x2,y2=positions[b]
            pen=QPen(QColor('#728db6' if edge['kind']=='hierarchy' else '#b6c5dc'),
                     2 if edge['kind']=='hierarchy' else min(6,1.5+sqrt(len(edge['files']))))
            if edge['kind']=='shared':pen.setStyle(Qt.PenStyle.DashLine)
            path=QPainterPath()
            if edge['kind']=='hierarchy':
                path.moveTo(x1,y1+78)
                path.cubicTo(x1,y1+140,x2,y2-130,x2,y2-78)
            else:
                path.moveTo(x1,y1);path.lineTo(x2,y2)
            item=self.scene.addPath(path,pen)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable);item.setData(0,edge['id'])
            item.setToolTip(escape(f"{self.labels[edge['id']]} · {len(edge['files']):,}개 파일"))
        palette=['#dfe8ff','#e0f4ec','#fff0d8','#eee5ff','#fce3eb','#dff1f7']
        for i,node in enumerate(self.visible_nodes):
            self._node(node,*positions[node['id']],palette[i%len(palette)])
        if not self.visible_nodes:
            text='로컬 본문 준비 → 묶음별 주제 통합 → 관심사 구성 → 파일 연결 순서로 진행합니다.\n분석·전송 확인에서 실행하거나 저장된 작업을 이어서 진행하세요.'
            self.scene.addText(text)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-35,-35,35,35))
        self.scene.blockSignals(False)
        self.canvas.fit_graph()

    def _columns(self):
        return max(1,min(3,int(self.canvas.viewport().width()/0.9/280)))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        QTimer.singleShot(0,self,self._resize_graph)

    def _resize_graph(self):
        if self.nodes and self._columns()!=self._layout_columns:
            self._draw(self.focus)

    def _node(self,node,x,y,color):
        width,height=248,156
        path=QPainterPath();path.addRoundedRect(QRectF(-width/2,-height/2,width,height),14,14)
        item=QGraphicsPathItem(path);item.setPos(x,y)
        item.setBrush(QColor(color));item.setPen(QPen(QColor('#7388af'),1.5))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item.setData(0,node['id']);item.setZValue(2)
        item.setToolTip(escape(node['label']+'\n'+node['description']))
        self.scene.addItem(item)
        badge=QGraphicsTextItem('큰 관심사' if node['id'].startswith('I') else '세부 주제',item)
        badge.setDefaultTextColor(QColor('#526680'))
        badge_font=QFont();badge_font.setPixelSize(12);badge.setFont(badge_font)
        badge.setPos(-width/2+14,-height/2+8)
        title=QGraphicsTextItem(item);title.setDefaultTextColor(QColor('#17233b'))
        font=QFont();font.setPixelSize(18);font.setBold(True);title.setFont(font)
        title.setTextWidth(width-28)
        title.setPlainText(node['label'])
        while title.boundingRect().height()>78 and font.pixelSize()>14:
            font.setPixelSize(font.pixelSize()-1);title.setFont(font)
        title.setPos(-width/2+14,-48)
        count=QGraphicsTextItem(f"{len(node['files']):,}개 파일",item)
        count.setDefaultTextColor(QColor('#344865'))
        font2=QFont();font2.setPixelSize(14);count.setFont(font2)
        count.setPos(-width/2+14,height/2-32)
        for text in (badge,title,count):
            text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.text_items.extend((badge,title,count))

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
        if key.startswith('C'):
            item=next((item for item in self.scene.items() if item.data(0)==key),None)
            if item is not None:self.canvas.ensureVisible(item,24,24)
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
