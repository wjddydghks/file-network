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
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data,ensure_ascii=False)}}]}


class Provider:
    def __init__(self):self.calls=[]
    def __call__(self,request,key):
        self.calls.append(request)
        data=json.loads(request['messages'][1]['content'])
        name=request['response_format']['json_schema']['name']
        if name=='knowledge_summaries':
            return response({'summaries':[{'file':f['file'],'summary':f['sample'].split('[발췌 경계]')[0].strip()[:100],
                'keywords':['프로젝트 일정'], 'quote':next(p.strip()[:25].strip() for p in f['sample'].split('[발췌 경계]') if p.strip())} for f in data['files']]})
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
            memberships=[{'category':'C1','reason':'본문에서 일정 검토를 다룹니다.','quote':quote}]
            if index%2==0 and len(data['topics'])>1:memberships.append({'category':'C2','reason':'예산과도 관련된 자료입니다.','quote':quote})
            assignments.append({'file':file['file'],'memberships':memberships})
        return response({'assignments':assignments})


def execute(plan,calls=None,**options):
    calls=plan.calls if calls is None else calls
    return run_knowledge(plan,plan.approval_id(calls),max_calls=calls,api_key='fake-test-key',enabled=True,**options)


def test_whole_scope_automatic_samples_and_blocked_files(area):
    plan=prepare_knowledge(area)
    assert len(plan.documents)==5 and len(plan.eligible)==3
    assert plan.calls==3
    assert any(d.blocked for d in plan.documents)
    assert not any(d.name=='unsupported.bin' for d in plan.eligible)
    assert not db.DB_PATH.with_name('knowledge_graph.json').exists()
    assert not audit_rows()
    nodes=graph_nodes(plan)
    assert set(i for n in nodes for i in n['files'])=={d.file_id for d in plan.documents}


def test_unscanned_database_not_created(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'missing.db')
    plan=prepare_knowledge(tmp_path)
    assert not plan.documents and not db.DB_PATH.exists()


def test_full_classification_cache_and_no_sensitive_data_sent(area,monkeypatch):
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    plan=prepare_knowledge(area)
    state=execute(plan)
    assert state['complete'] and len(state['assignments'])==3 and len(provider.calls)==3
    sent=json.dumps(provider.calls,ensure_ascii=False)
    assert str(area) not in sent and 'blocked.txt' not in sent and 'user@example.com' not in sent
    loaded=prepare_knowledge(area)
    assert loaded.calls==0 and loaded.state()['complete']
    assert len(loaded.state()['assignments'])==3
    nodes=graph_nodes(loaded)
    assert {d.file_id for d in loaded.documents}==set(i for n in nodes for i in n['files'])
    cache=knowledge.read_cache(plan.root,plan.interest,plan.model)
    assert 'fake-test-key' not in cache
    assert all(r['status']=='succeeded' for r in audit_rows())


def test_multiple_batches_pause_and_resume_do_not_resend_completed(area,monkeypatch):
    for index in range(2*BATCH_SIZE+3):(area/f'extra{index:03}.txt').write_text('프로젝트 일정 예산 자료',encoding='utf-8')
    run_content_analysis(area)
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    first=prepare_knowledge(area)
    paused=execute(first,(len(first.eligible)+BATCH_SIZE-1)//BATCH_SIZE+2)
    assert not paused['complete'] and len(paused['assignments'])==BATCH_SIZE
    old=set(paused['assignments']);sent_before=len(provider.calls)
    resumed=prepare_knowledge(area)
    assert resumed.calls==2
    done=execute(resumed)
    assert done['complete']
    requests=provider.calls[sent_before:]
    assert len(requests)==2
    newfiles=[f['file'] for r in requests for f in json.loads(r['messages'][1]['content'])['files']]
    assert not old & set(newfiles)
    assert len(done['assignments'])==len(first.eligible)


@pytest.mark.parametrize('mode',['approval','budget','disabled','changed_interest','changed_files','missing_key','cancel'])
def test_no_transmission_without_valid_job_approval(area,monkeypatch,mode):
    plan=prepare_knowledge(area);calls=plan.calls
    options={'max_calls':calls,'api_key':'test-key','enabled':True}
    approval=plan.approval_id(calls)
    if mode=='approval':approval=''
    elif mode=='budget':options['max_calls']=1
    elif mode=='disabled':options['enabled']=False
    elif mode=='changed_interest':plan=replace(plan,interest='다른 관심사')
    elif mode=='changed_files':
        (area/'doc0.txt').write_text('변경된 일정 내용',encoding='utf-8');run_content_analysis(area)
    elif mode=='missing_key':options['api_key']=''
    elif mode=='cancel':options['is_cancelled']=lambda:True
    with pytest.raises(OperationCancelled if mode=='cancel' else ValueError):
        run_knowledge(plan,approval,**options)
    assert not audit_rows()


def test_concurrent_job_lock_and_changed_checkpoint(area):
    plan=prepare_knowledge(area);token=knowledge.acquire(plan)
    try:
        with pytest.raises(ValueError,match='실행 중'):execute(plan)
        knowledge.save(plan,token,empty_state(plan))
    finally:knowledge.release(plan,token)
    with pytest.raises(ValueError,match='저장 결과'):execute(plan)
    assert not audit_rows()


def test_cancel_after_batch_persists_and_can_resume(area,monkeypatch):
    for index in range(BATCH_SIZE+2):(area/f'extra{index}.txt').write_text('프로젝트 일정 예산 자료',encoding='utf-8')
    run_content_analysis(area)
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    plan=prepare_knowledge(area);cancel=Event()
    def progress(data):
        if data['phase']=='classify':cancel.set()
    with pytest.raises(OperationCancelled):execute(plan,is_cancelled=cancel.is_set,progress=progress)
    resumed=prepare_knowledge(area)
    assert len(resumed.state()['assignments'])==BATCH_SIZE
    assert execute(resumed)['complete']


@pytest.mark.parametrize('bad',['missing','duplicate','unknown_file','unknown_category','fake_quote','extra','truncated','refusal'])
def test_invalid_batch_does_not_save_partial_or_retry(area,monkeypatch,bad):
    provider=Provider()
    def request(payload,key):
        reply=provider(payload,key)
        if payload['response_format']['json_schema']['name']!='knowledge_memberships':return reply
        data=json.loads(reply['choices'][0]['message']['content'])
        if bad=='missing':data['assignments'].pop()
        elif bad=='duplicate':data['assignments'][1]['file']=data['assignments'][0]['file']
        elif bad=='unknown_file':data['assignments'][0]['file']='F99999'
        elif bad=='unknown_category':data['assignments'][0]['memberships'][0]['category']='C12'
        elif bad=='fake_quote':data['assignments'][0]['memberships'][0]['quote']='없는 근거 문장'
        elif bad=='extra':data['assignments'][0]['tool']='delete_file'
        reply=response(data)
        if bad=='truncated':reply['choices'][0]['finish_reason']='length'
        elif bad=='refusal':reply['choices'][0]['message']['refusal']='refused'
        return reply
    monkeypatch.setattr(transport,'_request',request)
    plan=prepare_knowledge(area)
    with pytest.raises((ValueError,transport.AIServiceError)):execute(plan)
    state=prepare_knowledge(area).state()
    assert not state['complete'] and state['assignments']=={}
    assert len(provider.calls)==3
    assert audit_rows()[-1]['status']=='failed_or_unknown'


def test_changed_scope_invalidates_saved_graph(area,monkeypatch):
    monkeypatch.setattr(transport,'_request',Provider())
    assert execute(prepare_knowledge(area))['complete']
    (area/'new.txt').write_text('새 프로젝트 일정',encoding='utf-8');run_content_analysis(area)
    plan=prepare_knowledge(area)
    assert plan.state() is None and plan.calls>0
    assert all(n['id'].startswith('status:') for n in graph_nodes(plan))


def test_representative_sampling_and_pdf_boundary_marker(area):
    (area/'long.txt').write_text('프로젝트 예산 검토. '*1000,encoding='utf-8')
    from tests.test_pdf_extraction import make_pdf
    make_pdf(area/'pages.pdf',('first project page','second budget page'))
    run_content_analysis(area)
    plan=prepare_knowledge(area)
    assert all(len(d.sample)<=900 for d in plan.documents)
    pdf=next(d for d in plan.documents if d.name=='pages.pdf')
    assert pdf.eligible and '[발췌 경계]' in pdf.sample
    assert not pdf.blocked


def test_all_files_beyond_old_page_limit_are_classified(area,monkeypatch):
    for i in range(85):(area/f'many{i:03}.txt').write_text('프로젝트 일정 예산 자료',encoding='utf-8')
    run_content_analysis(area)
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    plan=prepare_knowledge(area);state=execute(plan)
    classified=[f['file'] for r in provider.calls if r['response_format']['json_schema']['name']=='knowledge_memberships' for f in json.loads(r['messages'][1]['content'])['files']]
    assert len(classified)==len(plan.eligible)>80
    assert set(classified)=={d.alias for d in plan.eligible}
    assert state['complete']


@pytest.mark.parametrize('mode',['unknown_source','missing_source','duplicate_topic','sensitive_label'])
def test_invalid_taxonomy_never_reaches_file_classification(area,monkeypatch,mode):
    provider=Provider()
    def request(payload,key):
        reply=provider(payload,key)
        if payload['response_format']['json_schema']['name']!='knowledge_hierarchy':return reply
        data=json.loads(reply['choices'][0]['message']['content'])
        if mode=='unknown_source':data['topics'][0]['sources']=['F99999']
        elif mode=='missing_source':data['topics'].pop()
        elif mode=='duplicate_topic':data['topics'][1]['id']='C1'
        else:data['topics'][0]['label']='user@example.com'
        return response(data)
    monkeypatch.setattr(transport,'_request',request)
    with pytest.raises(ValueError):execute(prepare_knowledge(area))
    assert len(provider.calls)==2
    assert prepare_knowledge(area).state()['topics'] is None


def test_no_topics_does_not_pretend_all_files_were_classified(area,monkeypatch):
    provider=Provider()
    def request(payload,key):
        reply=provider(payload,key)
        if payload['response_format']['json_schema']['name']=='knowledge_hierarchy':return response({'interests':[],'topics':[]})
        return reply
    monkeypatch.setattr(transport,'_request',request)
    plan=prepare_knowledge(area)
    with pytest.raises(ValueError):execute(plan)
    state=prepare_knowledge(area).state()
    assert len(provider.calls)==2 and not state['complete'] and not state['assignments']
    assert any(n['id']=='status:waiting' for n in graph_nodes(plan,state))
    assert prepare_knowledge(area).calls>0


def test_expired_lock_recovery_cannot_be_overwritten_by_old_owner(area,monkeypatch):
    plan=prepare_knowledge(area)
    now=1000;monkeypatch.setattr(knowledge.time,'time',lambda:now)
    first=knowledge.acquire(plan)
    now=1200
    second=knowledge.acquire(plan)
    with pytest.raises(ValueError):knowledge.save(plan,first,empty_state(plan))
    knowledge.release(plan,first)
    knowledge.save(plan,second,empty_state(plan));knowledge.release(plan,second)


def test_excluded_previously_indexed_folder_never_transmitted(area,monkeypatch):
    private=area/'private';private.mkdir()
    (private/'old.txt').write_text('not for external processing',encoding='utf-8')
    run_content_analysis(area)
    plan=prepare_knowledge(area,excluded_dirs=['private'])
    excluded=next(d for d in plan.documents if d.path==str(private/'old.txt'))
    assert excluded.blocked and not excluded.eligible
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    assert execute(plan)['complete']
    assert 'not for external processing' not in json.dumps(provider.calls)
    assert excluded.file_id in next(n for n in graph_nodes(plan,prepare_knowledge(area,excluded_dirs=['private']).state()) if n['id']=='status:blocked')['files']


@pytest.mark.parametrize('bad',['missing','duplicate','fake_quote','sensitive_summary','generic_keyword'])
def test_invalid_document_summary_never_creates_taxonomy(area,monkeypatch,bad):
    provider=Provider()
    def request(payload,key):
        reply=provider(payload,key)
        data=json.loads(reply['choices'][0]['message']['content'])
        rows=data['summaries']
        if bad=='missing':rows.pop()
        elif bad=='duplicate':rows[1]['file']=rows[0]['file']
        elif bad=='fake_quote':rows[0]['quote']='본문에 없는 인용'
        elif bad=='sensitive_summary':rows[0]['summary']='연락 user@example.com'
        else:rows[0]['keywords']=[]
        return response(data)
    monkeypatch.setattr(transport,'_request',request)
    with pytest.raises(ValueError):execute(prepare_knowledge(area))
    state=prepare_knowledge(area).state()
    assert state['summaries']=={} and state['topics'] is None
    assert len(provider.calls)==1


def test_taxonomy_waits_for_every_summary_and_resume_reuses_them(area,monkeypatch):
    for i in range(40):(area/f'extra{i:03}.txt').write_text('프로젝트 일정 예산 자료',encoding='utf-8')
    run_content_analysis(area)
    provider=Provider();monkeypatch.setattr(transport,'_request',provider)
    first=prepare_knowledge(area)
    partial=execute(first,1)
    assert len(partial['summaries'])==BATCH_SIZE
    assert partial['topics'] is None and not partial['assignments']
    old=set(partial['summaries'])
    execute(prepare_knowledge(area))
    stages=[r['response_format']['json_schema']['name'] for r in provider.calls]
    assert stages[:3]==['knowledge_summaries']*3
    assert stages[3]=='knowledge_hierarchy'
    resent=[f['file'] for r in provider.calls[1:] if r['response_format']['json_schema']['name']=='knowledge_summaries'
            for f in json.loads(r['messages'][1]['content'])['files']]
    assert not old.intersection(resent)
    catalog=json.loads(provider.calls[3]['messages'][1]['content'])
    assert {r['id'] for r in catalog['records']}=={d.alias for d in first.eligible}


class SubjectProvider(Provider):
    """Mocks semantic grouping; assertions check coverage/provenance, not model quality."""
    def __call__(self,request,key):
        name=request['response_format']['json_schema']['name']
        if name=='knowledge_summaries':return super().__call__(request,key)
        self.calls.append(request)
        data=json.loads(request['messages'][1]['content'])
        if name in ('knowledge_consolidation','knowledge_hierarchy'):
            astro=[r['id'] for r in data['records'] if '천체' in r['summary']]
            project=[r['id'] for r in data['records'] if r['id'] not in astro]
            parts=[(name,ids) for name,ids in [('프로젝트 일정',project),('천체 스펙트럼',astro)] if ids]
            if name=='knowledge_consolidation':
                return response({'groups':[{'label':label,'description':label+'에 관한 자료','members':ids} for label,ids in parts]})
            return response({'interests':[{'id':f'I{i+1}','label':label,'description':label+' 연구와 관리'} for i,(label,_) in enumerate(parts)],
                'topics':[{'id':f'C{i+1}','parent':f'I{i+1}','label':label+' 분석','description':label+'에 관한 파일','sources':ids}
                          for i,(label,ids) in enumerate(parts)]})
        return response({'assignments':[{'file':f['file'],'memberships':[{'category':'C2' if '천체' in f['sample'] else 'C1',
                'reason':'본문에서 해당 주제를 다룹니다.','quote':f['sample'][:25].strip()}]} for f in data['files']]})


def test_all_summary_coverage_preserves_minority_subject_through_reduction(area,monkeypatch):
    for i in range(90):(area/f'extra{i:03}.txt').write_text('프로젝트 일정 예산 자료',encoding='utf-8')
    rare=area/'extra043b.txt';rare.write_text('천체 스펙트럼과 적색편이 연구 자료',encoding='utf-8')
    run_content_analysis(area)
    provider=SubjectProvider();monkeypatch.setattr(transport,'_request',provider)
    plan=prepare_knowledge(area)
    summary_calls=(len(plan.eligible)+BATCH_SIZE-1)//BATCH_SIZE
    paused=execute(plan,summary_calls+1)
    assert len(paused['summaries'])==len(plan.eligible) and len(paused['reductions'])==1
    assert paused['topics'] is None
    old_calls=len(provider.calls)
    resumed=prepare_knowledge(area);done=execute(resumed)
    assert done['complete'] and len(done['interests'])==2
    assert all(r['response_format']['json_schema']['name']!='knowledge_summaries' for r in provider.calls[old_calls:])
    catalog=next(json.loads(r['messages'][1]['content']) for r in provider.calls if r['response_format']['json_schema']['name']=='knowledge_hierarchy')
    assert sum(r['count'] for r in catalog['records'])==len(plan.eligible)
    assert any('천체' in r['summary'] for r in catalog['records'])
    rare_doc=next(d for d in plan.eligible if d.path==str(rare))
    nodes=graph_nodes(resumed,done)
    assert next(n for n in nodes if n['id']=='I2')['files']==[rare_doc.file_id]
    assert done['assignments'][rare_doc.alias][0]['category']=='C2'
    sent=json.dumps(provider.calls,ensure_ascii=False)
    assert str(area) not in sent
    assert all('aliases' not in record for request in provider.calls for record in json.loads(request['messages'][1]['content']).get('records',[]))
    assert any(any(s['segment']==-2 for s in json.loads(row['targets'])) for row in audit_rows())


def test_recursive_reduction_covers_every_input_at_multiple_levels():
    from core.knowledge_graph import Document, KnowledgePlan, catalog_frontier, validate_state
    docs=tuple(Document(f'F{i}',i,f'/local/{i}',str(i),'EXTRACTED','revision','주제 근거 본문','') for i in range(4200))
    plan=KnowledgePlan('/local','','gpt-4.1-mini',docs)
    state=empty_state(plan)
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


def test_old_flat_taxonomy_cache_is_not_reused(area,monkeypatch):
    plan=prepare_knowledge(area)
    old=empty_state(plan);old['version']=1
    token=knowledge.acquire(plan)
    try:knowledge.save(plan,token,old)
    finally:knowledge.release(plan,token)
    current=prepare_knowledge(area)
    assert current.state() is None and current.calls==3
