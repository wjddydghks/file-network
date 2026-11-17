"""기존 승인 UI를 이용한 선택 인용의 AI 주제·관계 분석."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QPushButton

from app.ai_dialog import AIQuestionDialog
from app.theme_graph import ThemeGraph
from core.ai_themes import MAX_THEME_SOURCES, RELATION_LABELS, prepare_themes
from services.openai_themes import analyze_themes


class ThemeAnalysisDialog(AIQuestionDialog):
    MAX_SOURCES = MAX_THEME_SOURCES

    def __init__(self, candidates, root, interest='', parent=None):
        self.root = root
        super().__init__(candidates, parent=parent)
        self.resize(1200, 900)
        self.setWindowTitle('AI 주제·관계 분석 · 선택 인용 기준')
        note = QLabel('선택 인용 기준 AI 추정 · 최대 12개 / 24,000자 · 최대 출력 6,000토큰 · 요청당 1회 유료 호출')
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        self.layout().insertWidget(0, note)
        self.form_widgets = []
        def collect(layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item.widget() is self.tabs:
                    break
                if item.widget() is not None and item.widget() is not note:
                    self.form_widgets.append(item.widget())
                elif item.layout() is not None:
                    collect(item.layout())
        collect(self.layout())
        self.form_toggle = QPushButton('분석 입력·전송 설정 접기')
        self.form_toggle.setAutoDefault(False)
        self.form_toggle.setCheckable(True)
        self.form_toggle.setChecked(True)
        self.form_toggle.toggled.connect(self.toggle_form)
        self.layout().insertWidget(1, self.form_toggle)
        self.theme_result = None
        self.theme_graph = ThemeGraph(self)
        self.graph_tab = self.tabs.addTab(self.theme_graph, 'AI 추정 지도')
        self.theme_graph.citationRequested.connect(self.open_graph_citation)
        self.question.setPlaceholderText('관심사와 분석 요청을 입력하세요. 최소 2개 문서에서 인용을 선택합니다.')
        self.question.setPlainText(interest or '선택한 인용의 주제와 공통점·보완·상충 가능성을 근거와 함께 분석해줘.')
        self.status.setText('현재 지도 페이지의 분석 가능한 문서 중 인용을 직접 선택하세요. 최대 12개/24,000자이며 영역 전체 자동 분석이 아닙니다.')

    def toggle_form(self, visible):
        for widget in self.form_widgets:
            widget.setVisible(visible)
        self.approve.setVisible(visible)
        self.send.setVisible(visible)
        self.form_toggle.setText('분석 입력·전송 설정 접기' if visible else '새 분석 · 입력·전송 설정 펼치기')

    def prepare_plan(self, question, selections, model):
        return prepare_themes(question, selections, self.root, model)

    def request_plan(self, plan, approval, key, cancelled):
        return analyze_themes(plan, approval, api_key=key, enabled=True, is_cancelled=cancelled)

    def clear_response(self):
        super().clear_response()
        self.theme_result = None
        if hasattr(self, 'theme_graph'):
            self.theme_graph.clear()

    def display_response(self, result):
        self.theme_result = result
        lines = ['선택 인용 기준 AI 추정 · 관계의 사실성은 근거와 함께 확인하세요.']
        for topic in result['topics']:
            lines.append(f"주제: {topic['label']} · {', '.join(topic['members'])}\n{topic['summary']}")
        for relation in result['relations']:
            lines.append(f"AI 추정 {RELATION_LABELS[relation['kind']]}: {relation['source']} ↔ {relation['target']}\n{relation['explanation']}")
        if result['unclassified']:
            lines.append('미분류: ' + ', '.join(result['unclassified']))
        super().display_response({'answer': '\n\n'.join(lines), 'citations': result['sources']})
        self.theme_graph.render_result(result)
        self.tabs.setCurrentIndex(self.graph_tab)
        self.form_toggle.setChecked(False)
        QTimer.singleShot(0, self.theme_graph, self.theme_graph.canvas.fit_graph)
        self.status.setText('AI 추정 지도를 표시했습니다. 주제·문서·관계 선택으로 이유와 인용을 확인하세요. 결과는 저장하지 않습니다.')

    def open_graph_citation(self, source_id):
        if self.worker is not None:
            return
        for index in range(self.citations.count()):
            if self.citations.itemData(index)['source_id'] == source_id:
                self.citations.setCurrentIndex(index)
                self.tabs.setCurrentIndex(2)
                self.load_citation()
                return

    def update_buttons(self):
        super().update_buttons()
        if hasattr(self, 'theme_graph'):
            self.theme_graph.setEnabled(self.worker is None)

    def _clear_sensitive(self):
        super()._clear_sensitive()
        self.clear_response()
