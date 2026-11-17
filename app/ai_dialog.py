"""선택 인용에 한정된 외부 AI 승인 화면. 키·질문·답변은 저장하지 않는다."""
import json
import os
import sqlite3

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from core.ai_questions import DEFAULT_MODEL, ENDPOINT, POLICY_NOTICE, prepare_question
from database.document_chunks import get_document_chunk
from services.openai_questions import AIServiceError, ask_question


def safe_operation(operation):
    try:
        return operation()
    except (ValueError, AIServiceError):
        raise
    except (OSError, sqlite3.Error):
        raise RuntimeError('로컬 데이터 또는 전송 기록을 처리하지 못했습니다. 전송 상태를 확인하세요.') from None
    except Exception:
        raise RuntimeError('AI 작업을 처리하지 못했습니다. 자동 재시도하지 않습니다.') from None


def source_location(page, kind, label, segment):
    if page is not None:
        return f'PDF 페이지 {page}'
    if kind == 'sheet':
        return f'Excel 시트 {label}'
    if kind == 'slide':
        return f'PowerPoint 슬라이드 {label}'
    return f'텍스트 인용 {segment + 1}'


class AIQuestionDialog(QDialog):
    def __init__(self, candidates, selected=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle('선택 인용으로 AI 질문 · OpenAI')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(950, 800)
        self.worker = None
        self._closing = False
        self._plan = None
        self._selections = []
        self._answer = None
        self._sent_question = ''
        self._outcome = ('cancelled', None)
        self.candidates = QComboBox()
        for item in candidates:
            self.candidates.addItem(item['path'], item['file_id'])
        self.candidates.setCurrentIndex(selected)
        self.chunk = QSpinBox()
        self.chunk.setRange(1, 2**31 - 1)
        self.chunk.setPrefix('인용 ')
        self.add = QPushButton('근거에 추가')
        self.add.clicked.connect(self.add_source)
        self.remove = QPushButton('선택 근거 제거')
        self.remove.clicked.connect(self.remove_source)
        self.sources = QListWidget()
        self.sources.setMaximumHeight(85)
        self.question = QPlainTextEdit()
        self.question.setPlaceholderText('선택한 인용에 대해 질문·요약·비교를 요청하세요. 최대 2,000자.')
        self.question.setMaximumHeight(85)
        self.question.textChanged.connect(self.invalidate_plan)
        self.model = QLineEdit(os.environ.get('FILE_NETWORK_OPENAI_MODEL', DEFAULT_MODEL))
        self.model.setMaxLength(80)
        self.model.textChanged.connect(self.invalidate_plan)
        self.enabled = QCheckBox('이 창에서 외부 AI 사용 허용 (사용료 발생)')
        self.enabled.setChecked(False)
        self.enabled.toggled.connect(self.invalidate_approval)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMaxLength(1024)
        self.key.setPlaceholderText('OpenAI API 키 · 이 창에서만 보관 (비우면 OPENAI_API_KEY 사용)')
        self.key.textChanged.connect(self.invalidate_approval)
        self.prepare = QPushButton('전송 내용 미리보기 · 호출 없음')
        self.prepare.clicked.connect(self.prepare_request)
        self.approve = QCheckBox('위 질문과 아래 선택 본문을 확인했으며 OpenAI 전송·사용료에 동의합니다')
        self.approve.toggled.connect(self.update_buttons)
        self.send = QPushButton('승인한 내용 한 번 전송')
        self.send.clicked.connect(self.send_request)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.payload = QPlainTextEdit()
        self.payload.setReadOnly(True)
        self.answer = QPlainTextEdit()
        self.answer.setReadOnly(True)
        self.citations = QComboBox()
        self.open_citation = QPushButton('선택 근거 본문 확인 · 로컬')
        self.open_citation.clicked.connect(self.load_citation)
        self.reference = QPlainTextEdit()
        self.reference.setReadOnly(True)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.preview, '전송할 질문·본문')
        self.tabs.addTab(self.payload, '전송 JSON 상세')
        answer_page = QWidget()
        answer_layout = QVBoxLayout(answer_page)
        answer_layout.addWidget(self.answer)
        row = QHBoxLayout()
        row.addWidget(self.citations, 1)
        row.addWidget(self.open_citation)
        answer_layout.addLayout(row)
        answer_layout.addWidget(self.reference)
        self.tabs.addTab(answer_page, '답변·로컬 근거')
        notice = QLabel(POLICY_NOTICE)
        notice.setTextFormat(Qt.TextFormat.PlainText)
        notice.setWordWrap(True)
        self.status = QLabel('근거를 추가하고 질문을 입력하세요. 키 없이 미리보기까지 가능합니다. 키·질문·답변은 자동 저장하지 않습니다.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.close_button = QPushButton('닫기')
        self.close_button.clicked.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(notice)
        row = QHBoxLayout()
        for widget in (self.candidates, self.chunk, self.add, self.remove):
            row.addWidget(widget)
        layout.addLayout(row)
        layout.addWidget(self.sources)
        layout.addWidget(self.question)
        row = QHBoxLayout()
        row.addWidget(QLabel('모델'))
        row.addWidget(self.model)
        row.addWidget(self.enabled)
        layout.addLayout(row)
        layout.addWidget(self.key)
        layout.addWidget(self.prepare)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.approve)
        row = QHBoxLayout()
        row.addWidget(self.send)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        layout.addWidget(self.status)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.update_buttons()

    @Slot()
    def add_source(self):
        if self.worker is not None or self.candidates.currentIndex() < 0:
            return
        selection = (self.candidates.currentData(), self.chunk.value() - 1)
        if len(self._selections) >= 4 or selection in self._selections:
            self.status.setText('중복 없이 최대 4개 인용을 선택하세요.')
            return
        self._selections.append(selection)
        self.sources.addItem(f'{self.candidates.currentText()} · 인용 {selection[1] + 1}')
        self.invalidate_plan()

    @Slot()
    def remove_source(self):
        row = self.sources.currentRow()
        if self.worker is None and 0 <= row < len(self._selections):
            self._selections.pop(row)
            self.sources.takeItem(row)
            self.invalidate_plan()

    @Slot()
    def invalidate_plan(self):
        self._plan = None
        self.preview.clear()
        self.payload.clear()
        self.invalidate_approval()

    @Slot()
    def invalidate_approval(self):
        self.approve.setChecked(False)
        self.update_buttons()

    @Slot()
    def update_buttons(self):
        busy = self.worker is not None
        for widget in (self.candidates, self.chunk, self.add, self.remove, self.sources,
                       self.question, self.model, self.enabled, self.key, self.prepare):
            widget.setEnabled(not busy)
        self.approve.setEnabled(not busy and self._plan is not None)
        self.send.setEnabled(not busy and self._plan is not None and self.approve.isChecked() and self.enabled.isChecked())
        self.open_citation.setEnabled(not busy and self.citations.count() > 0)
        self.citations.setEnabled(not busy)

    def _start(self, kind, operation):
        from app.gui import TaskThread
        self._kind = kind
        self._outcome = ('cancelled', None)
        self.worker = TaskThread(lambda cancelled, progress: safe_operation(lambda: operation(cancelled)), self)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.update_buttons()
        self.worker.start()

    @Slot()
    def prepare_request(self):
        if self.worker is not None:
            return
        self.invalidate_plan()
        question, model, selections = self.question.toPlainText(), self.model.text(), tuple(self._selections)
        self.status.setText('로컬에서 선택 인용 확인 중… 외부 호출 없음.')
        self._start('prepare', lambda cancelled: prepare_question(question, selections, model))

    @Slot()
    def send_request(self):
        if (self.worker is not None or self._plan is None or not self.approve.isChecked()
                or not self.enabled.isChecked()):
            return
        plan, key = self._plan, self.key.text() or None
        approval = plan.approval_id()
        self._sent_question = plan.question
        self._plan = None  # 성공·오류·중단 후 다시 미리보기/승인해야 한다.
        self.approve.setChecked(False)
        self._answer = None
        self.answer.clear()
        self.citations.clear()
        self.reference.clear()
        self.status.setText('OpenAI 요청 중… 중복 전송하지 마세요. 닫기를 누르면 처리 종료를 기다립니다. 이미 전송된 요청은 취소할 수 없습니다.')
        self._start('send', lambda cancelled: ask_question(plan, approval, api_key=key, enabled=True, is_cancelled=cancelled))

    @Slot()
    def load_citation(self):
        if self.worker is not None or self.citations.currentIndex() < 0:
            return
        source = self.citations.currentData()
        self.reference.clear()
        self.status.setText('답변에 연결된 분석 시점의 로컬 인용 확인 중…')
        self._start('citation', lambda cancelled: get_document_chunk(source['file_id'], source['segment'], source['analyzed_at']))

    @Slot(object)
    def _completed(self, result):
        self._outcome = ('completed', result)

    @Slot(str)
    def _failed(self, message):
        self._outcome = ('failed', message)

    @Slot()
    def _finished(self):
        worker = self.worker
        self.worker = None
        worker.operation = None  # 완료한 요청의 키·본문을 캡처한 함수를 해제한다.
        worker.deleteLater()
        if self._closing:
            self.close()
            return
        outcome, result = self._outcome
        if outcome != 'completed':
            self.status.setText(result if outcome == 'failed' else '작업을 중단했습니다. 이미 전송된 요청은 과금될 수 있습니다.')
        elif self._kind == 'prepare':
            self._plan = result
            # 사람이 읽는 본문과 실제 JSON을 모두 제공한다. 경로는 로컬 안내에만 사용한다.
            sections = [f'전송 대상: {ENDPOINT}\n모델: {result.model}\n질문:\n{result.question}']
            for s in result.sources:
                sections.append(f'{s.source_id} · {s.path} · 인용 {s.segment + 1}\n'
                                f'{source_location(s.page, s.source_kind, s.source_label, s.segment)}\n[전송 본문]\n{s.text}')
            self.preview.setPlainText('\n\n'.join(sections))
            self.payload.setPlainText(json.dumps(result.payload(), ensure_ascii=False, indent=2))
            self.tabs.setCurrentIndex(0)
            self.status.setText('외부 호출 없음. 질문·본문과 정책을 확인한 뒤 외부 AI 허용 및 전송 동의를 선택하세요.')
        elif self._kind == 'send':
            self._answer = result
            self.answer.setPlainText('질문: ' + self._sent_question + '\n\n답변:\n' + result['answer'])
            for s in result['citations']:
                self.citations.addItem(f"{s['source_id']} · {s['path']} · 인용 {s['segment'] + 1} · {source_location(s['page'], s['source_kind'], s['source_label'], s['segment'])}", s)
            self.tabs.setCurrentIndex(2)
            self.status.setText('AI 답변은 참고용입니다. 로컬 근거를 확인하세요. 추가 요청은 미리보기와 승인을 다시 진행하세요.')
        elif self._kind == 'citation':
            if result and result['status'] == 'EXTRACTED' and result['chunk_count']:
                self.reference.setPlainText(result['text'])
                self.status.setText('답변에 연결된 분석 시점의 저장 인용입니다. 원본 변경 여부는 다시 분석해 확인하세요.')
            else:
                self.status.setText('로컬 근거가 삭제되거나 변경되었습니다. 다시 분석·검색하세요.')
        self.update_buttons()

    def _clear_sensitive(self):
        self.key.clear()
        self.question.clear()
        self.preview.clear()
        self.payload.clear()
        self.answer.clear()
        self.reference.clear()
        self._plan = self._answer = None
        self._outcome = ('cancelled', None)
        self._sent_question = ''

    def closeEvent(self, event):
        if self.worker is not None:
            self._closing = True
            self.worker.cancel_event.set()
            self.status.setText('처리가 끝나면 닫습니다. 이미 전송된 요청의 취소·과금 취소는 보장하지 않습니다.')
            event.ignore()
        else:
            self._clear_sensitive()
            super().closeEvent(event)

    def reject(self):
        if self.worker is not None:
            self.close()
        else:
            self._clear_sensitive()
            super().reject()
