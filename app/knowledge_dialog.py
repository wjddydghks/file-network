"""선택 영역 자동 분석 → 관심사 지식 그래프 → 관련 파일. 수동 인용/채팅 없음."""
from dataclasses import replace
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from app.ai_dialog import safe_operation
from app.analyze_content import run_content_analysis
from app.analysis_details import STATUS_LABELS
from app.knowledge_graph_view import KnowledgeGraphView
from core.ai_questions import DEFAULT_MODEL, POLICY_URL
from core.knowledge_graph import CATALOG_PROMPT, CLASSIFY_PROMPT
from database import knowledge
from database.document_chunks import get_document_chunk
from services.knowledge_graph import prepare_knowledge, run_knowledge

PAGE_SIZE = 80
POLICY = ('선택 영역의 파일을 자동으로 준비한 대표 본문과 관심사로 분류합니다. 이름·경로·실제 파일 ID는 별도로 보내지 않습니다. '
    '본문 자체의 식별정보는 포함될 수 있고 민감정보 자동 탐지는 완전하지 않습니다. OpenAI API는 유료이며 기본적으로 학습에 사용되지 않지만 '
    '악용 방지 로그는 통상 최대 30일 보관될 수 있습니다. store=false가 무보관을 보장하지 않습니다. '
    '새 관심사 목록은 다음 파일 분류 요청에 재사용합니다. 전송할 파일·본문과 호출 상한을 확인하세요. 정책: ' + POLICY_URL)


def label(text=''):
    widget=QLabel(text);widget.setWordWrap(True);widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class KnowledgeDialog(QDialog):
    def __init__(self, root, parent=None, analysis_options=None):
        super().__init__(parent)
        self.root=str(Path(root).expanduser().resolve())
        self.analysis_options=analysis_options or {'max_bytes':10*1024*1024}
        self.setWindowTitle('AI 지식 그래프 · 선택 영역')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1420,920);self.setMinimumSize(1080,720)
        self.worker=None;self._closing=False;self._outcome=('cancelled',None)
        self.plan=None;self.view_plan=None;self.state=None
        self.review_offset=0;self.file_offset=0;self.selected_key='all';self.filtered_ids=[];self._preview=None
        self.path=QLineEdit(self.root);self.path.setReadOnly(True)
        self.prepare=QPushButton('영역 분석 준비');self.prepare.setObjectName('primary')
        self.prepare.clicked.connect(lambda:self.prepare_area())
        self.summary=label('선택한 영역의 파일과 저장된 지식 그래프를 확인하고 있습니다.')
        self.status=label()
        self.graph=KnowledgeGraphView(self);self.graph.filesRequested.connect(self.select_node)
        self.node_title=label('영역 전체 파일')
        self.table=QTableWidget(0,2);self.table.setHorizontalHeaderLabels(['파일','상태'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False);self.table.setAlternatingRowColors(True);self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True);self.table.setColumnWidth(0,220)
        self.table.itemSelectionChanged.connect(self.file_selected)
        self.table.itemDoubleClicked.connect(lambda *_:self.show_body())
        self.file_previous=QPushButton('이전 파일');self.file_next=QPushButton('다음 파일')
        self.file_previous.clicked.connect(lambda:self.page_files(-1));self.file_next.clicked.connect(lambda:self.page_files(1))
        self.file_count=label();self.selected_path=label();self.reason=QPlainTextEdit();self.reason.setReadOnly(True);self.reason.setMaximumHeight(130)
        self.body=QPlainTextEdit();self.body.setReadOnly(True)
        self.body.setPlaceholderText('파일을 선택해 분류 이유를 확인하고 본문을 미리 볼 수 있습니다.')
        self.preview=QPushButton('본문 미리보기');self.preview.clicked.connect(lambda:self.show_body())
        self.original=QPushButton('원본 열기');self.original.clicked.connect(self.open_original)
        self.body_previous=QPushButton('이전 내용');self.body_next=QPushButton('다음 내용')
        self.body_previous.clicked.connect(lambda:self.show_body(self._preview['segment']-1))
        self.body_next.clicked.connect(lambda:self.show_body(self._preview['segment']+1))
        self.location=label()
        right=QWidget();r=QVBoxLayout(right);r.setContentsMargins(0,0,0,0)
        r.addWidget(self.node_title);r.addWidget(self.file_count);r.addWidget(self.table,2)
        row=QHBoxLayout();row.addWidget(self.file_previous);row.addWidget(self.file_next);r.addLayout(row)
        r.addWidget(self.selected_path);r.addWidget(self.reason)
        row=QHBoxLayout();row.addWidget(self.preview);row.addWidget(self.original);r.addLayout(row)
        r.addWidget(self.body,1);r.addWidget(self.location)
        row=QHBoxLayout();row.addWidget(self.body_previous);row.addWidget(self.body_next);r.addLayout(row)
        split=QSplitter(Qt.Orientation.Horizontal);split.addWidget(self.graph);split.addWidget(right);split.setSizes([850,460])
        self.tabs=QTabWidget();self.tabs.addTab(split,'지식 그래프 · 노드를 눌러 관련 파일 보기')
        review=QWidget();v=QVBoxLayout(review)
        row=QHBoxLayout();self.interest=QLineEdit();self.interest.setMaxLength(1000)
        self.interest.setPlaceholderText('관심사 선택 사항 · 예: 일정, 비용, 보안 (비우면 자동 구성)')
        self.model=QLineEdit(os.environ.get('FILE_NETWORK_OPENAI_MODEL',DEFAULT_MODEL));self.model.setMaxLength(80)
        row.addWidget(self.interest,1);row.addWidget(label('모델'));row.addWidget(self.model);v.addLayout(row)
        self.interest.textChanged.connect(self.config_changed);self.model.textChanged.connect(self.config_changed)
        self.plan_summary=label();v.addWidget(self.plan_summary);v.addWidget(label(POLICY))
        self.review_table=QTableWidget(0,2);self.review_table.setHorizontalHeaderLabels(['파일 · 전송 대상 확인','처리'])
        self.review_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.review_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.review_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.review_table.horizontalHeader().setStretchLastSection(True);self.review_table.setColumnWidth(0,550)
        self.review_table.itemSelectionChanged.connect(self.show_review)
        self.sample=QPlainTextEdit();self.sample.setReadOnly(True)
        self.review_previous=QPushButton('이전 확인 파일');self.review_next=QPushButton('다음 확인 파일')
        self.review_previous.clicked.connect(lambda:self.page_review(-1));self.review_next.clicked.connect(lambda:self.page_review(1))
        inner=QSplitter(Qt.Orientation.Horizontal);inner.addWidget(self.review_table);inner.addWidget(self.sample);inner.setSizes([650,520]);v.addWidget(inner,1)
        row=QHBoxLayout();row.addWidget(self.review_previous);row.addWidget(self.review_next);v.addLayout(row)
        self.instructions=QPlainTextEdit();self.instructions.setReadOnly(True);self.instructions.setMaximumHeight(90)
        self.instructions.setPlainText('1. 대표 파일로 관심사 구성\n'+CATALOG_PROMPT+'\n\n2. 모든 전송 가능 파일을 16개씩 분류\n'+CLASSIFY_PROMPT)
        v.addWidget(self.instructions)
        row=QHBoxLayout();self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setMaxLength(1024)
        self.key.setPlaceholderText('OpenAI API 키 · 이 창에서만 사용 (비우면 OPENAI_API_KEY)')
        self.enabled=QCheckBox('외부 AI 사용 허용 · 유료')
        self.max_calls=QSpinBox();self.max_calls.setRange(1,1000)
        row.addWidget(self.key,1);row.addWidget(self.enabled);row.addWidget(label('이번 호출 상한'));row.addWidget(self.max_calls);v.addLayout(row)
        self.approve=QCheckBox('이 영역의 전송 대상·자동 준비 본문·호출 수를 확인했고 자동 일괄 분석에 동의합니다.')
        v.addWidget(self.approve)
        self.run=QPushButton('승인하고 지식 그래프 만들기');self.run.setObjectName('primary');self.run.clicked.connect(self.start_analysis);v.addWidget(self.run)
        self.key.textChanged.connect(self.revoke);self.enabled.toggled.connect(self.revoke);self.max_calls.valueChanged.connect(self.revoke);self.approve.toggled.connect(self.update_buttons)
        review.setMinimumHeight(640)
        review_scroll=QScrollArea();review_scroll.setWidgetResizable(True);review_scroll.setWidget(review)
        self.tabs.addTab(review_scroll,'분석·전송 확인')
        self.cancel=QPushButton('분석 중단');self.cancel.clicked.connect(self.cancel_work)
        self.close_button=QPushButton('닫기');self.close_button.clicked.connect(self.close)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,20,20,20);layout.setSpacing(12)
        row=QHBoxLayout();row.addWidget(self.path,1);row.addWidget(self.prepare);layout.addLayout(row)
        layout.addWidget(self.summary);layout.addWidget(self.tabs,1)
        layout.addWidget(label('관심사 분류는 대표 본문 기준 AI 추정입니다. 연결은 두 관심사에 공통으로 분류된 파일을 뜻합니다. 파일 변경은 영역 분석 준비를 다시 누르면 로컬에서 확인합니다.'))
        row=QHBoxLayout();row.addWidget(self.status,1);row.addWidget(self.cancel);row.addWidget(self.close_button);layout.addLayout(row)
        for button in self.findChildren(QPushButton):button.setAutoDefault(False)
        self.update_buttons();QTimer.singleShot(0,lambda:self.prepare_area(initial=True))

    def config_changed(self):
        self.plan=None;self.revoke();self.plan_summary.setText('설정이 변경되었습니다. 영역 분석 준비를 다시 누르세요.')

    def revoke(self):
        self.approve.setChecked(False);self.update_buttons()

    def _start(self,kind,operation):
        from app.gui import TaskThread
        self._kind=kind;self._outcome=('cancelled',None)
        self.worker=TaskThread(lambda cancelled,progress:safe_operation(lambda:operation(cancelled,progress)),self)
        self.worker.completed.connect(lambda result:setattr(self,'_outcome',('completed',result)))
        self.worker.failed.connect(lambda error:setattr(self,'_outcome',('failed',error)))
        self.worker.progress.connect(self.progress);self.worker.finished.connect(self.finished_work)
        self.update_buttons();self.worker.start()

    def prepare_area(self,initial=False):
        if self.worker is not None or self._closing:return
        self.plan=None;self.revoke();self.status.setText('영역의 파일과 자동 준비 본문을 확인하는 중… 외부 호출 없음.')
        interest,model=(None,None) if initial else (self.interest.text(),self.model.text())
        def operation(cancelled,progress):
            run_content_analysis(self.root,is_cancelled=cancelled,progress_callback=progress,**self.analysis_options)
            plan=prepare_knowledge(self.root,interest,model,cancelled,progress,excluded_dirs=self.analysis_options.get('excluded_dirs',()))
            plan.state();plan.calls
            return plan
        self._start('prepare',operation)

    def start_analysis(self):
        if self.worker is not None or not self.plan or not self.approve.isChecked() or not self.enabled.isChecked():return
        plan=self.plan;calls=self.max_calls.value();approval=plan.approval_id(calls);key=self.key.text() or None
        self.revoke();self.plan=None;self.status.setText('관심사별 자동 분류 중… 중단한 경우 완료 배치는 저장됩니다.')
        self._run_plan=plan
        self._start('run',lambda cancelled,progress:run_knowledge(plan,approval,max_calls=calls,api_key=key,enabled=True,is_cancelled=cancelled,progress=progress))

    def progress(self,data):
        if data['phase']=='prepare':self.status.setText(f"영역 준비 · 저장 파일 {data['files']:,}개 확인")
        elif data['phase']=='scan':self.status.setText(f"파일 확인 중 · {data['processed']:,}개")
        elif data['phase']=='analysis':self.status.setText(f"로컬 본문 준비 · {data['processed']:,}/{data['total']:,}개")
        else:self.status.setText(f"자동 분류 · {data['done']:,}/{data['total']:,}개 · 이번 요청 {data['calls']}회 완료")

    def cancel_work(self):
        if self.worker is not None:
            self.worker.cancel_event.set();self.cancel.setEnabled(False)
            self.status.setText('중단 요청됨. 진행 중인 호출은 전송·과금되었을 수 있으며 종료 후 완료 배치를 보존합니다.')

    @Slot()
    def finished_work(self):
        worker,self.worker=self.worker,None;worker.operation=None;worker.deleteLater()
        if self._closing:self.close();return
        outcome,result=self._outcome
        if self._kind=='prepare' and outcome=='completed':
            self.plan=result
            for field,value in ((self.interest,result.interest),(self.model,result.model)):
                field.blockSignals(True);field.setText(value);field.blockSignals(False)
            state=result.state();self.render_result(result,state)
            self.max_calls.setValue(max(1,result.calls));self.review_offset=0;self.render_review()
            remaining=len(result.remaining);blocked=sum(bool(d.blocked) for d in result.documents)
            chars=sum(len(d.sample) for d in result.remaining)
            self.plan_summary.setText(f"영역 전체 {len(result.documents):,}개 · 새 전송 {remaining:,}개 / {chars:,}자 · 차단 {blocked:,}개 · 최대 예정 {result.calls}회\n대표 본문은 파일당 최대 900자로 자동 준비합니다. 관심사 구성용 대표 파일은 한 번 더 전송될 수 있습니다. 같은 영역 상태에서 완료한 배치는 재전송하지 않습니다. 파일·관심사·모델이 바뀌면 새 분석이 필요합니다.")
            if result.calls:
                self.tabs.setCurrentIndex(1);self.status.setText('파일별 전송 본문과 호출 수를 확인하고 키·허용·동의 후 실행하세요. 아직 외부 호출 없음.')
            else:
                self.tabs.setCurrentIndex(0);self.status.setText('저장된 지식 그래프를 재사용했습니다. 외부 호출 없음.' if state else '분류할 본문이 없습니다. 미분석·차단 파일을 확인하세요.')
        elif self._kind=='run':
            plan=self._run_plan
            updated=replace(plan,cached_json=knowledge.read_cache(plan.root,plan.interest,plan.model))
            state=updated.state();self.render_result(updated,state);self.tabs.setCurrentIndex(0)
            self.plan=None;self.approve.setChecked(False)
            if outcome=='completed' and not result['topics'] and len(plan.eligible)>0:
                self.status.setText('관심사를 도출하지 못했습니다. 관심사를 구체화해 다시 준비하세요. 파일을 분류한 것으로 표시하지 않습니다.')
            elif outcome=='completed':
                self.status.setText('지식 그래프 저장 완료. 관심사 노드나 연결을 눌러 관련 파일을 확인하세요.' if result['complete'] else '이번 호출 상한까지 저장했습니다. 영역 분석 준비 후 남은 파일만 승인해 재개하세요.')
            else:self.status.setText((result if outcome=='failed' else '분석을 중단했습니다.')+' 완료한 배치는 저장되어 있습니다. 다시 준비 후 남은 파일만 승인해 재개하세요.')
        elif self._kind=='preview' and outcome=='completed' and result and result['status']=='EXTRACTED' and result['chunk_count']:
            self._preview=result;self.body.setPlainText(result['text'])
            position=f"PDF 페이지 {result['page']}" if result['page'] else {'sheet':'Excel 시트','slide':'PowerPoint 슬라이드'}.get(result['source_kind'],'본문')+(' '+str(result['source_label']) if result['source_label'] else '')
            self.location.setText(position);self.status.setText('저장된 본문 미리보기입니다.')
        else:
            if self._kind=='preview':self._preview=None;self.body.clear()
            self.status.setText(str(result) if outcome=='failed' else '조회를 중단했거나 저장 본문이 없습니다.')
        self.update_buttons()

    def render_result(self,plan,state):
        self.view_plan=plan;self.state=state
        done=len(state['assignments']) if state else 0
        complete=bool(state and state['complete'])
        self.summary.setText(f"{plan.root}\n저장 파일 {len(plan.documents):,}개 · AI 분류 처리 {done:,}/{len(plan.eligible):,}개 · {'저장 결과' if complete else '분류 준비/진행 중'} · 관심사: {plan.interest or '자동 구성'}")
        self.graph.render_graph(plan,state);self.file_offset=0;self.select_node('all')
        QTimer.singleShot(0,self.graph,self.graph.canvas.fit_graph)

    def select_node(self,key):
        self.selected_key=key;self.filtered_ids=self.graph.file_ids.get(key,[]);self.file_offset=0
        self.node_title.setText(self.graph.labels.get(key,'관련 파일')+'\n'+self.graph.descriptions.get(key,''));self.render_files()

    def render_files(self):
        docs={d.file_id:d for d in self.view_plan.documents} if self.view_plan else {}
        self.page_documents=[docs[i] for i in self.filtered_ids[self.file_offset:self.file_offset+PAGE_SIZE]]
        self.table.setRowCount(len(self.page_documents))
        for row,d in enumerate(self.page_documents):
            for col,text in enumerate((d.name,'전송 차단' if d.blocked else STATUS_LABELS.get(d.status,d.status))):
                item=QTableWidgetItem(text);item.setToolTip(d.path);self.table.setItem(row,col,item)
        self.file_count.setText(f"관련 파일 {len(self.filtered_ids):,}개 · {self.file_offset+1 if self.page_documents else 0}–{self.file_offset+len(self.page_documents)}")
        if self.page_documents:self.table.selectRow(0)
        self.file_selected();self.update_buttons()

    def page_files(self,delta):
        self.file_offset=max(0,self.file_offset+delta*PAGE_SIZE);self.render_files()

    def selected_document(self):
        row=self.table.currentRow()
        docs=getattr(self,'page_documents',[])
        return docs[row] if 0<=row<len(docs) else None

    def file_selected(self):
        self._preview=None;self.body.clear();self.location.clear();self.reason.clear()
        doc=self.selected_document()
        self.selected_path.setText(doc.path if doc else '')
        if doc and self.state:
            memberships=self.state['assignments'].get(doc.alias,[])
            texts=[]
            for member in memberships:
                if self.selected_key.startswith('C') and member['category']!=self.selected_key:continue
                title=next(t['label'] for t in self.state['topics'] if t['id']==member['category'])
                texts.append(f"{title} · AI 분류\n{member['reason']}\n근거: {member['quote']}")
            self.reason.setPlainText('\n\n'.join(texts) or (doc.blocked if doc.blocked else '아직 AI 관심사로 분류되지 않은 파일입니다.'))
        self.update_buttons()

    def show_body(self,segment=0):
        doc=self.selected_document()
        if self.worker is not None or not doc or doc.status!='EXTRACTED':return
        self.body.clear();self.location.clear()
        def operation(cancelled,progress):
            try:
                return get_document_chunk(doc.file_id,segment,doc.analyzed_at)
            except ValueError:
                raise ValueError('저장된 본문이 변경되었습니다. 영역 분석 준비를 다시 누르세요.') from None
        self._start('preview',operation)

    def open_original(self):
        doc=self.selected_document()
        if self.worker is not None or doc is None:return
        try:
            if not Path(doc.path).is_file():raise ValueError('원본 파일이 없습니다. 영역 분석 준비를 다시 누르세요.')
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(doc.path)):raise ValueError('원본 파일을 열지 못했습니다. 기본 앱 연결을 확인하세요.')
        except (ValueError,OSError) as error:self.status.setText(str(error))

    def render_review(self):
        docs=self.plan.documents if self.plan else ()
        done=self.plan.checkpoint['assignments'] if self.plan and self.plan.checkpoint else {}
        self.review_documents=docs[self.review_offset:self.review_offset+PAGE_SIZE]
        self.review_table.setRowCount(len(self.review_documents))
        for row,d in enumerate(self.review_documents):
            status='전송 차단' if d.blocked else '저장 분류 재사용' if d.alias in done else '전송 예정' if d.eligible else '본문 없음'
            for col,text in enumerate((d.path,status)):self.review_table.setItem(row,col,QTableWidgetItem(text))
        if self.review_documents:self.review_table.selectRow(0)
        self.show_review();self.update_buttons()

    def show_review(self):
        row=self.review_table.currentRow();docs=getattr(self,'review_documents',())
        if 0<=row<len(docs):
            doc=docs[row];self.sample.setPlainText(f"{doc.path}\n{'전송 차단: '+doc.blocked if doc.blocked else '자동 준비 본문 · 실제 전송은 별칭으로 처리'}\n\n{doc.sample}")
        else:self.sample.clear()

    def page_review(self,delta):
        self.review_offset=max(0,self.review_offset+delta*PAGE_SIZE);self.render_review()

    def update_buttons(self):
        busy=self.worker is not None
        for widget in (self.prepare,self.interest,self.model,self.key,self.enabled,self.max_calls,self.approve,self.review_table,self.graph,self.table):widget.setEnabled(not busy)
        self.run.setEnabled(not busy and self.plan is not None and self.plan.calls>0 and self.approve.isChecked() and self.enabled.isChecked())
        self.cancel.setEnabled(busy)
        self.file_previous.setEnabled(not busy and self.file_offset>0)
        self.file_next.setEnabled(not busy and self.file_offset+PAGE_SIZE<len(self.filtered_ids))
        self.review_previous.setEnabled(not busy and self.review_offset>0)
        self.review_next.setEnabled(not busy and self.plan is not None and self.review_offset+PAGE_SIZE<len(self.plan.documents))
        doc=self.selected_document()
        self.preview.setEnabled(not busy and doc is not None and doc.status=='EXTRACTED')
        self.original.setEnabled(not busy and doc is not None)
        self.body_previous.setEnabled(not busy and self._preview is not None and self._preview['segment']>0)
        self.body_next.setEnabled(not busy and self._preview is not None and self._preview['segment']+1<self._preview['chunk_count'])

    def closeEvent(self,event):
        self._closing=True
        if self.worker is not None:self.cancel_work();event.ignore()
        else:self.key.clear();super().closeEvent(event)

    def reject(self):
        if self.worker is not None:self.close()
        else:self._closing=True;self.key.clear();super().reject()
