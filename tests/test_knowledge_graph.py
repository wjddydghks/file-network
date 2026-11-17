from dataclasses import replace
import json
from threading import Event

import pytest

from app.analyze_content import run_content_analysis
from core.knowledge_graph import BATCH_SIZE, canonical, empty_state, graph_nodes
from core.operations import OperationCancelled
from database import db, knowledge
from services import openai_questions as transport
from services.knowledge_graph import prepare_knowledge, run_knowledge
from tests.test_ai_questions import audit_rows


@pytest.fixture
def area(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'state.db')
    root=tmp_path/'area';root.mkdir()
    for index in range(3):
        (root/f'doc{index}.txt').write_text(f'프로젝트 일정과 예산 검토 자료 {index}.',encoding='utf-8')
    (root/'blocked.txt').write_text('연락 user@example.com',encoding='utf-8')
    (root/'unsupported.bin').write_bytes(b'\xff')
    run_content_analysis(root)
    monkeypatch.setattr(transport,'_request',lambda *a:pytest.fail('Unexpected external request'))
    return root


def response(data):
    if 'groups' in data and all('members' in g for g in data['groups']):
        data={'groups':[{'id':f'G{i+1}',**{k:v for k,v in g.items() if k!='members'}} for i,g in enumerate(data['groups'])],
              'assignments':{member:f'G{i+1}' for i,g in enumerate(data['groups']) for member in g['members']}}
    elif 'topics' in data and all('sources' in t for t in data['topics']):
        data={'interests':data['interests'],'topics':[{k:v for k,v in t.items() if k!='sources'} for t in data['topics']],
              'assignments':{source:t['id'] for t in data['topics'] for source in t['sources']}}
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data,ensure_ascii=False)}}]}


class Provider:
    def __init__(self):self.calls=[]
    def __call__(self,request,key):
        self.calls.append(request)
        data=json.loads(request['messages'][1]['content'])
        name=request['response_format']['json_schema']['name']
        if name=='knowledge_summaries':
            return response({'summaries':[{'file':f['file'],'summary':f['sample'].split('[발췌 경계]')[0].strip()[:100],
                'keywords':['프로젝트 일정'], 'evidence_id':f['evidence'][0]['id']} for f in data['files']]})
        if name=='knowledge_consolidation':
            records=data['records']
            return response({'groups':[{'label':'프로젝트 관리','description':'프로젝트 일정과 예산', 'members':[r['id'] for r in records]}]})
        if name=='knowledge_hierarchy':
            records=data['records'];count=min(2,len(records))
            return response({'interests':[{'id':'I1','label':'프로젝트 관리','description':'프로젝트 실행과 관리'}],
                'topics':[{'id':f'C{i+1}','parent':'I1','label':['일정','예산'][i],
                    'description':['프로젝트 일정 관련 파일','예산 검토 관련 파일'][i],
                    'sources':[r['id'] for r in records[i::count]]} for i in range(count)]})
        assignments=[]
        for index,file in enumerate(data['files']):
            quote=file['sample'].split('[발췌 경계]')[0].strip()[:25].strip()
            memberships=[{'category':'C1','reason':'본문에서 일정 검토를 다룹니다.','evidence_id':file['evidence'][0]['id']}]
            if index%2==0 and len(data['topics'])>1:memberships.append({'category':'C2','reason':'예산과도 관련된 자료입니다.','evidence_id':file['evidence'][0]['id']})
            assignments.append({'file':file['file'],'memberships':memberships})
        return response({'assignments':assignments})


def execute(plan,calls=None,**options):
    calls=plan.calls if calls is None else calls
    return run_knowledge(plan,plan.approval_id(calls),max_calls=calls,api_key='fake-test-key',enabled=True,**options)


class SubjectProvider(Provider):
    """Mocks semantic grouping; assertions check coverage/provenance, not model quality."""
    def __call__(self,request,key):
        name=request['response_format']['json_schema']['name']
        if name=='knowledge_summaries':return super().__call__(request,key)
        self.calls.append(request)
        data=json.loads(request['messages'][1]['content'])
        if name in ('knowledge_consolidation','knowledge_hierarchy'):
            astro=[r['id'] for r in data['records'] if '천체' in r.get('sample',r.get('summary',''))]
            project=[r['id'] for r in data['records'] if r['id'] not in astro]
            parts=[(name,ids) for name,ids in [('프로젝트 일정',project),('천체 스펙트럼',astro)] if ids]
            if name=='knowledge_consolidation':
                return response({'groups':[{'label':label,'description':label+'에 관한 자료','members':ids} for label,ids in parts]})
            return response({'interests':[{'id':f'I{i+1}','label':label,'description':label+' 연구와 관리'} for i,(label,_) in enumerate(parts)],
                'topics':[{'id':f'C{i+1}','parent':f'I{i+1}','label':label+' 분석','description':label+'에 관한 파일','sources':ids}
                          for i,(label,ids) in enumerate(parts)]})
        return response({'assignments':[{'file':f['file'],'memberships':[{'category':'C2' if '천체' in f['sample'] else 'C1',
                'reason':'본문에서 해당 주제를 다룹니다.','evidence_id':f['evidence'][0]['id']}]} for f in data['files']]})


def test_recursive_reduction_covers_every_input_at_multiple_levels():
    from core.knowledge_graph import Document, KnowledgePlan, catalog_frontier, validate_state
    docs=tuple(Document(f'F{i}',i,f'/local/{i}',str(i),'EXTRACTED','revision','주제 근거 본문','') for i in range(4200))
    plan=KnowledgePlan('/local','','gpt-4.1-mini',docs)
    state=empty_state(plan);state['group_size']=64
    state['summaries']={d.alias:{'summary':'주제 근거 요약','keywords':['주제 근거'],'quote':'주제 근거'} for d in docs}
    levels=set()
    while True:
        records,task,_=catalog_frontier(plan,state)
        if task is None:break
        levels.add(task.split(':')[0])
        count=min(12,len(records))
        state['reductions'][task]=[{'label':f'연구 주제 {i}','description':f'연구 분야 {i}',
            'members':[r['id'] for r in records[i::count]]} for i in range(count)]
    assert len(levels)>=2 and len(records)<=64
    assert sum(r['count'] for r in records)==len(docs)
    aliases=[alias for record in records for alias in record['aliases']]
    assert len(aliases)==len(set(aliases))==len(docs)
    validate_state(state,plan)


@pytest.mark.parametrize('mode',['missing','duplicate','unknown','generic'])
def test_reduction_rejects_lost_duplicate_or_vague_subjects(mode):
    from core.knowledge_graph import validate_groups
    records=[{'id':'F1'},{'id':'F2'}]
    groups=[{'label':'천체 관측','description':'스펙트럼 관측 연구','members':['F1','F2']}]
    if mode=='missing':groups[0]['members'].pop()
    elif mode=='duplicate':groups[0]['members'].append('F1')
    elif mode=='unknown':groups[0]['members'][0]='F999'
    else:groups[0]['label']='일반 자료'
    with pytest.raises(ValueError):validate_groups(groups,records)


def test_numbered_evidence_keeps_original_excel_whitespace_and_pdf_boundaries(area,monkeypatch):
    text='회의록\r\n항목\t\t금액\r\n예산    100원\n일정\u00a0검토'
    (area/'sheet.txt').write_bytes(text.encode('utf-8'))
    run_content_analysis(area)
    from core.knowledge_graph import evidence_options, validate_summaries, validate_assignments
    doc=next(d for d in prepare_knowledge(area).eligible if d.name=='sheet.txt')
    options=evidence_options(doc)
    evidence_id=next(iter(options))
    summary=validate_summaries([{'file':doc.alias,'summary':'예산과 일정 검토','keywords':['예산'],
                               'evidence_id':evidence_id}],[doc],evidence_only=True)
    assert summary[doc.alias]['quote'] in text
    assert '\t\t' in summary[doc.alias]['quote'] and '\r\n' in summary[doc.alias]['quote']
    knowledge.verify_quotes([doc],[(doc.alias,summary[doc.alias]['quote'])])
    assignments=validate_assignments([{'file':doc.alias,'memberships':[{'category':'C1','reason':'예산 항목',
        'evidence_id':evidence_id}]}],[doc],[{'id':'C1'}],evidence_only=True)
    assert assignments[doc.alias][0]['quote']==summary[doc.alias]['quote']
    split=replace(doc,sample='첫 페이지 근거\n[발췌 경계]\n두 번째 페이지 근거')
    assert set(evidence_options(split).values())=={'첫 페이지 근거','두 번째 페이지 근거'}


@pytest.mark.parametrize('stage',['summary','classification'])
def test_unknown_or_other_file_evidence_ids_are_rejected(area,stage):
    from core.knowledge_graph import evidence_options, validate_summaries, validate_assignments
    first,second=prepare_knowledge(area).eligible[:2]
    wrong_id=next(iter(evidence_options(second)))
    with pytest.raises(ValueError,match='근거 번호'):
        if stage=='summary':
            validate_summaries([{'file':first.alias,'summary':'일정 검토','keywords':['일정'],
                'evidence_id':wrong_id}],[first],evidence_only=True)
        else:
            validate_assignments([{'file':first.alias,'memberships':[{'category':'C1','reason':'일정 검토',
                'evidence_id':wrong_id}]}],[first],[{'id':'C1'}],evidence_only=True)


@pytest.mark.parametrize('words', [
    ['주제1','주제2','주제3','주제4','주제5','user@example.com'],
    ['가'*50+' user@example.com'],
])
def test_keyword_cleanup_does_not_hide_sensitive_discarded_text(words):
    from core.knowledge_graph import normalize_keywords
    with pytest.raises(ValueError,match='민감정보'):normalize_keywords(words)


@pytest.mark.parametrize('catalog',[False,True])
def test_required_record_mapping_prevents_omission_and_keeps_legacy_checkpoint_format(catalog):
    from core.knowledge_graph import KnowledgePlan, payload, resolve_record_mapping
    records=[{'id':f'F{i}','summary':'일정 검토','aliases':[f'F{i}']} for i in range(64)]
    plan=KnowledgePlan('/local','','gpt-4.1-mini',())
    request=payload(plan,(),stage='catalog' if catalog else 'reduce',records=records)
    schema=request['response_format']['json_schema']['schema']
    mapping=schema['properties']['assignments']
    assert set(mapping['required'])=={r['id'] for r in records}
    assert mapping['additionalProperties'] is False
    if catalog:
        reply={'interests':[{'id':'I1','label':'프로젝트 관리','description':'프로젝트 진행'}],
               'topics':[{'id':'C1','parent':'I1','label':'일정 검토','description':'프로젝트 일정 검토'}],
               'assignments':{r['id']:'C1' for r in records}}
    else:
        reply={'groups':[{'id':'G1','label':'일정 검토','description':'프로젝트 일정 검토'}],
               'assignments':{r['id']:'G1' for r in records}}
    result=resolve_record_mapping(reply,records,catalog=catalog)
    members=result['topics'][0]['sources'] if catalog else result[0]['members']
    assert set(members)=={r['id'] for r in records}
    reply['assignments'].pop('F63')
    with pytest.raises(ValueError,match='필수 요약 배정'):resolve_record_mapping(reply,records,catalog=catalog)



def test_fast_whole_scope_one_call_and_no_per_file_ai(area,monkeypatch):
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    plan=prepare_knowledge(area)
    assert len(plan.documents)==5 and len(plan.eligible)==3 and plan.calls==1
    done=execute(plan)
    assert done['complete'] and len(done['assignments'])==3
    assert [p['response_format']['json_schema']['name'] for p in provider.calls]==['knowledge_hierarchy']
    data=json.loads(provider.calls[0]['messages'][1]['content'])
    assert {r['id'] for r in data['records']}=={d.alias for d in plan.eligible}
    assert all(r['sample']==next(d.sample for d in plan.eligible if d.alias==r['id']) for r in data['records'])
    sent=json.dumps(provider.calls,ensure_ascii=False)
    assert str(area) not in sent and 'user@example.com' not in sent
    loaded=prepare_knowledge(area)
    assert loaded.calls==0 and loaded.state()['complete']
    nodes=graph_nodes(loaded)
    assert {d.file_id for d in plan.documents}=={fid for n in nodes for fid in n['files']}


@pytest.mark.parametrize('mode',['approval','budget','disabled','changed_interest','changed_files','missing_key','cancel'])
def test_no_transmission_without_valid_job_approval(area,monkeypatch,mode):
    plan=prepare_knowledge(area);calls=plan.calls
    options={'max_calls':calls,'api_key':'test-key','enabled':True}
    approval=plan.approval_id(calls)
    if mode=='approval':approval=''
    elif mode=='budget':options['max_calls']=2
    elif mode=='disabled':options['enabled']=False
    elif mode=='changed_interest':plan=replace(plan,interest='다른 관심사')
    elif mode=='changed_files':
        (area/'doc0.txt').write_text('변경된 일정 내용',encoding='utf-8');run_content_analysis(area)
    elif mode=='missing_key':options['api_key']=''
    elif mode=='cancel':options['is_cancelled']=lambda:True
    with pytest.raises(OperationCancelled if mode=='cancel' else ValueError):run_knowledge(plan,approval,**options)
    assert not audit_rows()


def test_885_files_need_five_calls_with_two_parallel_reductions_and_resumable_budget(area,monkeypatch):
    from threading import Barrier,Lock
    for i in range(882):(area/f'extra{i:04}.txt').write_text('프로젝트 일정 검토 자료',encoding='utf-8')
    run_content_analysis(area)
    provider=Provider();barrier=Barrier(2);lock=Lock();active=peak=0
    def request(payload,key):
        nonlocal active,peak
        if payload['response_format']['json_schema']['name']!='knowledge_consolidation':return provider(payload,key)
        with lock:active+=1;peak=max(peak,active)
        try:
            if len(provider.calls)<2:barrier.wait(timeout=5)
            return provider(payload,key)
        finally:
            with lock:active-=1
    monkeypatch.setattr(transport,'_request',request)
    plan=prepare_knowledge(area)
    assert len(plan.eligible)==885 and plan.calls==5
    partial=execute(plan,2)
    assert peak==2 and len(provider.calls)==2 and len(partial['reductions'])==2
    assert len(partial['summaries'])==885 and partial['topics'] is None
    resumed=prepare_knowledge(area)
    assert resumed.calls==3
    complete=execute(resumed)
    assert complete['complete'] and len(complete['assignments'])==885 and len(provider.calls)==5
    assert not any(r['response_format']['json_schema']['name'] in ('knowledge_summaries','knowledge_memberships') for r in provider.calls)
    initial=[json.loads(r['messages'][1]['content'])['records'] for r in provider.calls if r['response_format']['json_schema']['name']=='knowledge_consolidation']
    ids=[record['id'] for batch in initial for record in batch]
    assert len(ids)==len(set(ids))==885


def test_new_scope_10000_file_call_upper_bound():
    from core.knowledge_graph import Document,KnowledgePlan
    docs=tuple(Document(f'F{i}',i,f'/area/{i}',str(i),'EXTRACTED','rev','주제 본문','') for i in range(10000))
    assert KnowledgePlan('/area','','gpt-4.1-mini',docs).calls==43


@pytest.mark.parametrize('mode',['missing','unknown','duplicate_topic','generic_label','sensitive_label','truncated','refusal'])
def test_bad_global_mapping_never_silently_drops_files_and_keeps_local_preparation(area,monkeypatch,mode):
    provider=Provider()
    def request(payload,key):
        reply=provider(payload,key);data=json.loads(reply['choices'][0]['message']['content'])
        if mode=='missing':data['assignments'].pop(next(iter(data['assignments'])))
        elif mode=='unknown':data['assignments']['F999']='C1'
        elif mode=='duplicate_topic':data['topics'][1]['id']='C1'
        elif mode=='generic_label':data['topics'][0]['label']='일반 자료'
        elif mode=='sensitive_label':data['topics'][0]['label']='user@example.com'
        reply=response(data)
        if mode=='truncated':reply['choices'][0]['finish_reason']='length'
        if mode=='refusal':reply['choices'][0]['message']['refusal']='refused'
        return reply
    monkeypatch.setattr(transport,'_request',request)
    with pytest.raises((ValueError,transport.AIServiceError)):execute(prepare_knowledge(area))
    saved=prepare_knowledge(area).state()
    assert len(saved['summaries'])==3 and saved['topics'] is None and not saved['assignments']
    assert len(provider.calls)==1


def test_cached_old_ai_summaries_are_reused_without_another_summary_call(area,monkeypatch):
    plan=prepare_knowledge(area);state=empty_state(plan);state.pop('group_size')
    state['summaries']={d.alias:{'summary':'이전에 생성한 일정 요약','keywords':['일정'],'quote':d.sample[:20]} for d in plan.eligible}
    token=knowledge.acquire(plan)
    try:knowledge.save(plan,token,state)
    finally:knowledge.release(plan,token)
    resumed=prepare_knowledge(area)
    assert resumed.state()['group_size']==256 and resumed.state()['summaries']==state['summaries']
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    assert execute(resumed)['complete'] and len(provider.calls)==1


def test_cached_taxonomy_finishes_locally_without_key_or_new_api(area,monkeypatch):
    from core.knowledge_graph import catalog_frontier,local_summary,resolve_record_mapping
    plan=prepare_knowledge(area);state=empty_state(plan)
    state['summaries']={d.alias:local_summary(d) for d in plan.eligible}
    records,_,_=catalog_frontier(plan,state)
    state['interests']=[{'id':'I1','label':'프로젝트 관리','description':'프로젝트 관리 자료'}]
    state['topics']=[{'id':'C1','parent':'I1','label':'일정 관리','description':'일정 검토','sources':[r['id'] for r in records]}]
    token=knowledge.acquire(plan)
    try:knowledge.save(plan,token,state)
    finally:knowledge.release(plan,token)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    resumed=prepare_knowledge(area)
    assert resumed.calls==0 and resumed.state()['complete'] and len(resumed.state()['assignments'])==3
    assert not audit_rows()


def test_cancel_after_local_preparation_has_no_paid_call_and_can_resume(area,monkeypatch):
    cancel=Event()
    def progress(data):cancel.set()
    with pytest.raises(OperationCancelled):execute(prepare_knowledge(area),is_cancelled=cancel.is_set,progress=progress)
    saved=prepare_knowledge(area)
    assert len(saved.state()['summaries'])==3 and not audit_rows()
    monkeypatch.setattr(transport,'_request',Provider())
    assert execute(saved)['complete']


def test_excluded_previously_indexed_folder_never_transmitted(area,monkeypatch):
    private=area/'private';private.mkdir();(private/'old.txt').write_text('not for external processing',encoding='utf-8')
    run_content_analysis(area)
    plan=prepare_knowledge(area,excluded_dirs=['private'])
    doc=next(d for d in plan.documents if d.path==str(private/'old.txt'))
    assert doc.blocked and not doc.eligible
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    assert execute(plan)['complete'] and 'not for external processing' not in json.dumps(provider.calls)


def test_schema_shared_enums_do_not_exceed_provider_limit():
    from core.knowledge_graph import KnowledgePlan,payload
    plan=KnowledgePlan('/local','','gpt-4.1-mini',())
    records=[{'id':f'F{i}','summary':'주제','aliases':[f'F{i}']} for i in range(256)]
    def enum_count(value):
        if isinstance(value,dict):return len(value.get('enum',[]))+sum(enum_count(v) for k,v in value.items() if k!='enum')
        if isinstance(value,list):return sum(enum_count(v) for v in value)
        return 0
    for stage in ('reduce','catalog'):
        schema=payload(plan,(),stage=stage,records=records)['response_format']['json_schema']['schema']
        assert enum_count(schema)<1000
        assert len(schema['properties']['assignments']['required'])==256


def test_failed_parallel_reduction_preserves_other_completed_out_of_order_group(area,monkeypatch):
    for i in range(297):(area/f'extra{i:03}.txt').write_text('프로젝트 일정 검토',encoding='utf-8')
    run_content_analysis(area)
    plan=prepare_knowledge(area);ready=Event();provider=Provider()
    def request(payload,key):
        records=json.loads(payload['messages'][1]['content'])['records']
        if records[0]['id']==plan.eligible[0].alias:
            assert ready.wait(5)
            raise transport.AIServiceError('mock failed first group')
        reply=provider(payload,key);ready.set();return reply
    monkeypatch.setattr(transport,'_request',request)
    with pytest.raises(transport.AIServiceError):execute(plan,2)
    resumed=prepare_knowledge(area)
    assert set(resumed.state()['reductions'])=={'0:256'}
    assert len(resumed.state()['summaries'])==300
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    assert execute(resumed)['complete'] and len(provider.calls)==2
    sent=json.loads(provider.calls[0]['messages'][1]['content'])['records']
    assert len(sent)==256 and sent[0]['id']==plan.eligible[0].alias


def test_old_64_record_reduction_checkpoints_are_preserved(area,monkeypatch):
    from core.knowledge_graph import local_summary
    for i in range(67):(area/f'extra{i:03}.txt').write_text('프로젝트 일정 검토',encoding='utf-8')
    run_content_analysis(area)
    plan=prepare_knowledge(area);state=empty_state(plan);state.pop('group_size')
    state['summaries']={d.alias:local_summary(d) for d in plan.eligible}
    state['reductions']={'0:0':[{'label':'프로젝트 일정','description':'일정 검토','members':[d.alias for d in plan.eligible[:64]]}]}
    token=knowledge.acquire(plan)
    try:knowledge.save(plan,token,state)
    finally:knowledge.release(plan,token)
    resumed=prepare_knowledge(area)
    assert 'group_size' not in resumed.state() and resumed.state()['reductions']==state['reductions']
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    assert execute(resumed)['complete'] and len(provider.calls)==2
    records=json.loads(provider.calls[0]['messages'][1]['content'])['records']
    assert len(records)==6 and all(r['id'] not in state['reductions']['0:0'][0]['members'] for r in records)


def test_expired_lock_recovery_cannot_be_overwritten_by_old_owner(area,monkeypatch):
    plan=prepare_knowledge(area)
    now=1000;monkeypatch.setattr(knowledge.time,'time',lambda:now)
    first=knowledge.acquire(plan)
    now=1200
    second=knowledge.acquire(plan)
    with pytest.raises(ValueError):knowledge.save(plan,first,empty_state(plan))
    knowledge.release(plan,first)
    knowledge.save(plan,second,empty_state(plan));knowledge.release(plan,second)



def test_concurrent_job_lock_and_changed_checkpoint(area):
    plan=prepare_knowledge(area);token=knowledge.acquire(plan)
    try:
        with pytest.raises(ValueError,match='실행 중'):execute(plan)
        knowledge.save(plan,token,empty_state(plan))
    finally:knowledge.release(plan,token)
    with pytest.raises(ValueError,match='저장 결과'):execute(plan)
    assert not audit_rows()



def test_unscanned_database_not_created(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'missing.db')
    plan=prepare_knowledge(tmp_path)
    assert not plan.documents and not db.DB_PATH.exists()


def test_rare_subject_keeps_its_file_through_fast_global_grouping(area,monkeypatch):
    for i in range(260):(area/f'extra{i:03}.txt').write_text('프로젝트 일정 관리',encoding='utf-8')
    rare=area/'extra126b.txt';rare.write_text('천체 스펙트럼과 적색편이 연구 자료',encoding='utf-8')
    run_content_analysis(area)
    provider=SubjectProvider();monkeypatch.setattr(transport,'_request',provider)
    plan=prepare_knowledge(area);state=execute(plan)
    assert state['complete'] and len(provider.calls)==3
    rare_doc=next(d for d in plan.eligible if d.path==str(rare))
    topic=next(t for t in state['topics'] if '천체' in t['label'])
    node=next(n for n in graph_nodes(plan,state) if n['id']==topic['id'])
    assert node['files']==[rare_doc.file_id]
    assert state['assignments'][rare_doc.alias][0]['quote'] in rare_doc.sample
    first=json.loads(provider.calls[0]['messages'][1]['content'])['records']
    assert all('summary' not in r and 'quote' not in r and 'aliases' not in r for r in first)
