"""데스크톱 워크스페이스의 레이아웃과 공통 시각 스타일."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QPushButton, QScrollArea, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

STYLE = '''
QWidget { font-family: "Segoe UI", "Noto Sans", sans-serif; font-size: 13px; color: #25334a; }
QMainWindow, QDialog, QWidget#workspaceBody { background: #f4f6fb; }
QFrame#sidebar { background: #15223b; border: 0; }
QFrame#sidebar QLabel { color: #aebbd1; background: transparent; }
QFrame#sidebar QLabel#brand { color: #ffffff; font-size: 21px; font-weight: 800; }
QLabel#sidebarSection { color: #8fa4c5; font-size: 11px; font-weight: 700; }
QLabel#navActive { background: #293b60; color: #e6edff; padding: 13px; border-radius: 9px; font-weight: 700; }
QLabel#pageTitle { font-size: 26px; font-weight: 800; color: #14213a; }
QLabel#muted { color: #6c7a91; }
QFrame#card { background: #ffffff; border: 1px solid #e3e8f2; border-radius: 12px; }
QLabel#sectionTitle { font-size: 15px; font-weight: 700; }
QLabel#metricValue { font-size: 25px; font-weight: 750; color: #172b4d; }
QLabel#badge { background: #edf1fc; color: #485aa8; padding: 6px 12px; border-radius: 10px; font-weight: 700; }
QLabel#badge[state="ready"] { background: #e4f5ee; color: #207556; }
QLabel#badge[state="attention"] { background: #fff1d9; color: #906015; }
QLineEdit, QComboBox, QSpinBox { background: #ffffff; border: 1px solid #dce2ee; border-radius: 7px; padding: 8px; min-height: 18px; selection-background-color: #5067d9; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #6079e7; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: #9aa5b8; background: #f2f4f8; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #dce2ee; border-radius: 7px; padding: 7px 11px; min-height: 20px; font-weight: 600; }
QPushButton:hover, QToolButton:hover { background: #eef2fc; border-color: #b9c6ec; }
QPushButton:pressed { background: #e0e7fa; }
QPushButton#primary { background: #4d63d8; color: white; border-color: #4d63d8; }
QPushButton#primary:hover { background: #4055c5; }
QPushButton:disabled, QToolButton:disabled { background: #f1f3f8; color: #a5aec0; border-color: #e8ecf3; }
QFrame#sidebar QPushButton { background: #293b60; color: #eef3ff; border: 1px solid #3a4d72; }
QFrame#sidebar QLineEdit { background: #1e2e4c; color: #ffffff; border-color: #3a4d72; }
QListWidget#folderList { background: transparent; border: none; outline: none; color: #c7d3e7; }
QListWidget#folderList::item { padding: 10px 4px; border-radius: 6px; }
QListWidget#folderList::item:selected { background: #293b60; color: white; }
QListWidget#folderList::item:hover { background: #213352; }
QTableWidget { background: white; alternate-background-color: #f8faff; border: none; gridline-color: transparent; selection-background-color: #eaf0ff; selection-color: #263b83; outline: none; }
QTableWidget::item { border-bottom: 1px solid #f0f2f7; padding: 8px; }
QHeaderView::section { background: #f8f9fd; color: #78869c; border: none; border-bottom: 1px solid #e6ebf4; padding: 11px; font-size: 12px; font-weight: 700; }
QPlainTextEdit { background: #ffffff; border: 1px solid #e3e8f2; border-radius: 8px; padding: 10px; selection-background-color: #cbd7ff; }
QCheckBox { spacing: 7px; }
QProgressBar { background: #e7ecf6; border: none; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #6479e3; border-radius: 3px; }
QSplitter::handle { background: #edf0f7; width: 5px; height: 5px; }
QScrollBar:vertical { background: #f3f5fa; width: 9px; border: none; }
QScrollBar::handle:vertical { background: #cad3e3; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #15223b; color: white; border: none; padding: 7px; }
'''


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def card():
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(12)
    return frame, layout


def build_workspace(w):
    w.resize(1400, 900)
    w.setMinimumSize(1080, 720)
    w.setStyleSheet(STYLE)
    central = QWidget()
    outer = QHBoxLayout(central)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    sidebar = QFrame()
    sidebar.setObjectName('sidebar')
    sidebar.setFixedWidth(244)
    side = QVBoxLayout(sidebar)
    side.setContentsMargins(20, 26, 20, 20)
    side.setSpacing(14)
    side.addWidget(label('FILE NETWORK', 'brand'))
    side.addWidget(label('흩어진 문서를 연결하는 공간'))
    side.addSpacing(20)
    side.addWidget(label('문서 탐색', 'navActive'))
    w.area_button = QPushButton('영역 지도 · 구조와 키워드')
    w.area_button.clicked.connect(w.open_area_map)
    side.addWidget(w.area_button)
    side.addWidget(label('현재 범위', 'sidebarSection'))
    w.folder.setToolTip(w.folder.text())
    side.addWidget(w.folder)
    side.addWidget(w.browse)
    side.addSpacing(10)
    side.addWidget(label('스캔 기록이 있는 폴더', 'sidebarSection'))
    w.folder_history = QListWidget()
    w.folder_history.setObjectName('folderList')
    w.folder_history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    side.addWidget(w.folder_history, 1)
    w.refresh_scope = QPushButton('저장 상태 새로고침')
    side.addWidget(w.refresh_scope)
    note = label('로컬 우선\nAI 전송은 확인과 승인 후에만')
    note.setWordWrap(True)
    side.addWidget(note)
    outer.addWidget(sidebar)

    body = QWidget()
    body.setObjectName('workspaceBody')
    body.setMinimumHeight(800)
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setWidget(body)
    main = QVBoxLayout(body)
    main.setContentsMargins(26, 24, 26, 20)
    main.setSpacing(12)
    outer.addWidget(scroll, 1)
    header = QHBoxLayout()
    titles = QVBoxLayout()
    titles.addWidget(label('파일 탐색', 'pageTitle'))
    titles.addWidget(label('분석 범위를 확인하고, 문서의 근거까지 탐색하세요.', 'muted'))
    header.addLayout(titles, 1)
    w.options_toggle = QToolButton()
    w.options_toggle.setText('분석 설정')
    w.options_toggle.setCheckable(True)
    header.addWidget(w.options_toggle)
    w.analyze.setObjectName('primary')
    w.analyze.setText('폴더 분석')
    header.addWidget(w.analyze)
    header.addWidget(w.cancel)
    main.addLayout(header)
    w.analysis_options, options = card()
    row = QHBoxLayout()
    for widget in (label('파일당 크기'), w.max_size, label('텍스트 인코딩'), w.text_encoding, w.reset_settings_button):
        row.addWidget(widget)
    options.addLayout(row)
    options.addWidget(label('분석에서 제외할 폴더명', 'muted'))
    options.addWidget(w.excluded_dirs)
    w.analysis_options.hide()
    w.options_toggle.toggled.connect(w.analysis_options.setVisible)
    main.addWidget(w.analysis_options)

    summary, summary_layout = card()
    summary_layout.setContentsMargins(18, 12, 18, 12)
    summary_layout.setSpacing(8)
    row = QHBoxLayout()
    w.scope_title = label('선택한 폴더의 저장 상태', 'sectionTitle')
    row.addWidget(w.scope_title, 1)
    w.scope_badge = label('폴더 선택', 'badge')
    row.addWidget(w.scope_badge)
    summary_layout.addLayout(row)
    metrics = QHBoxLayout()
    w.scope_metrics = {}
    for key, title in [('files', '저장된 파일'), ('extracted', '본문 검색 가능'), ('attention', '확인 필요'), ('skipped', '건너뜀')]:
        column = QVBoxLayout()
        value = label('—', 'metricValue')
        w.scope_metrics[key] = value
        column.addWidget(value)
        column.addWidget(label(title, 'muted'))
        metrics.addLayout(column, 1)
    summary_layout.addLayout(metrics)
    w.scope_detail = label('폴더를 선택하면 저장된 검색 범위를 확인합니다.', 'muted')
    w.scope_detail.setWordWrap(True)
    w.scope_detail.setMinimumHeight(38)
    summary_layout.addWidget(w.scope_detail)
    main.addWidget(summary)

    search_card, search_layout = card()
    search_layout.setContentsMargins(18, 12, 18, 12)
    search_layout.setSpacing(8)
    search_row = QHBoxLayout()
    w.query.setMinimumHeight(28)
    w.search.setObjectName('primary')
    w.search.setMinimumWidth(85)
    for widget in (w.search_target, w.query, w.search):
        search_row.addWidget(widget, 1 if widget is w.query else 0)
    search_layout.addLayout(search_row)
    filters = QHBoxLayout()
    for widget in (w.all_folders, label('최대 결과', 'muted'), w.limit):
        filters.addWidget(widget)
    filters.addStretch(1)
    filters.addWidget(w.analysis_filter)
    filters.addWidget(w.details_button)
    search_layout.addLayout(filters)
    main.addWidget(search_card)

    split = QSplitter(Qt.Orientation.Horizontal)
    results, result_layout = card()
    result_layout.addWidget(label('검색 결과', 'sectionTitle'))
    w.empty_hint = label('아직 결과가 없습니다. 폴더의 저장 상태를 확인하고 검색하거나 분석 내역을 열어보세요.', 'muted')
    w.empty_hint.setWordWrap(True)
    result_layout.addWidget(w.empty_hint)
    w.table.setShowGrid(False)
    w.table.setAlternatingRowColors(True)
    w.table.verticalHeader().hide()
    w.table.verticalHeader().setDefaultSectionSize(48)
    w.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    w.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    w.table.setColumnWidth(0, 165)
    w.table.setColumnWidth(1, 190)
    result_layout.addWidget(w.table, 1)
    split.addWidget(results)
    detail, detail_layout = card()
    detail_layout.setContentsMargins(14, 14, 14, 14)
    detail_layout.setSpacing(8)
    w.document_title = label('문서 미리보기', 'sectionTitle')
    w.document_title.setWordWrap(True)
    detail_layout.addWidget(w.document_title)
    w.selected_path.setObjectName('muted')
    w.selected_path.setMaximumHeight(75)
    detail_layout.addWidget(w.selected_path)
    detail_layout.addWidget(w.preview, 1)
    buttons = QGridLayout()
    w.ai_button.setObjectName('primary')
    buttons.addWidget(w.ai_button, 0, 0)
    buttons.addWidget(w.content_button, 1, 0)
    buttons.addWidget(w.citation_button, 1, 1)
    buttons.addWidget(w.open_button, 0, 1)
    buttons.addWidget(w.related_button, 2, 0)
    w.graph_button.setText('키워드 후보 그래프')
    buttons.addWidget(w.graph_button, 2, 1)
    detail_layout.addLayout(buttons)
    w.graph_button.setToolTip('공유 키워드 후보 그래프입니다. 영역 전체의 주제·관심사 지도는 아직 제공하지 않습니다.')
    split.addWidget(detail)
    split.setSizes([680, 360])
    main.addWidget(split, 1)
    w.progress.setTextVisible(False)
    main.addWidget(w.progress)
    main.addWidget(w.status)
    main.addWidget(w.settings_status)
    w.setCentralWidget(central)
