from copy import deepcopy
from dataclasses import replace
import json

import pytest

from app.analyze_content import run_content_analysis
from core.ai_questions import prepare_question
from core.ai_themes import prepare_themes
from core.operations import OperationCancelled
from database import db
from database.file_search import search_filename
from services import openai_questions as service
from services.openai_themes import analyze_themes
from tests.test_ai_questions import audit_rows


@pytest.fixture
def theme_context(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'state.db')
    root = tmp_path / 'area'
    root.mkdir()
    for index in range(13):
        (root / f'doc{index:02}.txt').write_text(f'출시일 금요일 검토 회의. 문서 {index}. ' * 150, encoding='utf-8')
    run_content_analysis(root)
    ids = [r['file_id'] for r in search_filename('doc')]
    monkeypatch.setenv('FILE_NETWORK_AI_ENABLED', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only-key')
    monkeypatch.setattr(service, '_request', lambda *a: pytest.fail('Unexpected external request'))
    return root, ids


def make_plan(context):
    root, ids = context
    return prepare_themes('일정과 주제 관계를 분석', [(i, 0) for i in ids[:3]], root)


def theme_data(plan):
    sources = plan.sources
    evidence = [{'source_id': s.source_id, 'quote': s.text[:18]} for s in sources]
    return {'topics': [{'label': '일정 검토', 'summary': '선택 인용의 일정 검토 주제입니다.',
                        'members': [s.source_id for s in sources], 'evidence': evidence}],
            'relations': [{'source': sources[0].source_id, 'target': sources[-1].source_id,
                'kind': 'shared_topic', 'explanation': '두 인용이 금요일 검토 일정을 언급합니다.',
                'evidence': [evidence[0], evidence[-1]]}], 'unclassified': []}


def theme_response(data):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(data, ensure_ascii=False)}}]}


def test_theme_payload_aliases_scope_approval_and_boundaries(theme_context):
    root, ids = theme_context
    plan = prepare_themes('관심사 일정', [(ids[0], 0), (ids[0], 1), (ids[1], 0)], root)
    payload = plan.payload()
    data = json.loads(payload['messages'][1]['content'])
    assert [s['document'] for s in data['sources']] == ['D1','D1','D2']
    assert all(set(s) == {'id','text','document'} for s in data['sources'])
    assert str(root) not in json.dumps(payload) and 'doc00.txt' not in json.dumps(payload)
    assert payload['store'] is False and payload['max_completion_tokens'] == 6000
    assert replace(plan, root=str(root.parent)).approval_id() != plan.approval_id()
    assert not audit_rows()


def test_twelve_sources_supported_without_changing_qa_limit(theme_context):
    root, ids = theme_context
    assert len(prepare_themes('일정', [(i,0) for i in ids[:12]], root).sources) == 12
    with pytest.raises(ValueError):
        prepare_themes('일정', [(i,0) for i in ids], root)
    with pytest.raises(ValueError):
        prepare_question('일정', [(i,0) for i in ids[:5]])


@pytest.mark.parametrize('mode', ['one_document','outside','sensitive_question','sensitive_source','missing_key','disabled','approval','changed_plan','cancelled'])
def test_theme_fails_closed(theme_context, monkeypatch, mode):
    root, ids = theme_context
    if mode == 'one_document':
        with pytest.raises(ValueError, match='서로 다른'):
            prepare_themes('일정', [(ids[0],0),(ids[0],1)], root)
    elif mode == 'outside':
        with pytest.raises(ValueError, match='영역 밖'):
            prepare_themes('일정', [(ids[0],0),(ids[1],0)], root / 'different')
    elif mode == 'sensitive_question':
        with pytest.raises(ValueError, match='차단'):
            prepare_themes('user@example.com', [(ids[0],0),(ids[1],0)], root)
    elif mode == 'sensitive_source':
        (root / 'doc00.txt').write_text('password: secret-value', encoding='utf-8')
        run_content_analysis(root)
        with pytest.raises(ValueError, match='차단'):
            make_plan(theme_context)
    else:
        plan = make_plan(theme_context)
        approval = plan.approval_id()
        options = {}
        if mode == 'missing_key':
            monkeypatch.delenv('OPENAI_API_KEY')
        elif mode == 'disabled':
            options['enabled'] = False
        elif mode == 'approval':
            approval = ''
        elif mode == 'changed_plan':
            plan = replace(plan, question='다른 관심사')
        elif mode == 'cancelled':
            options['is_cancelled'] = lambda: True
        with pytest.raises(OperationCancelled if mode == 'cancelled' else ValueError):
            analyze_themes(plan, approval, **options)
    assert not audit_rows()


def test_valid_result_exact_quotes_and_minimal_audit(theme_context, monkeypatch):
    plan = make_plan(theme_context)
    calls = []
    monkeypatch.setattr(service, '_request', lambda *a: calls.append(a) or theme_response(theme_data(plan)))
    result = analyze_themes(plan, plan.approval_id())
    assert len(calls) == 1 and result['inferred'] is True
    assert len(result['topics']) == 1 and len(result['relations']) == 1
    assert len(result['sources']) == 3
    log = json.dumps(audit_rows(), ensure_ascii=False)
    for secret in [plan.question, plan.sources[0].text, plan.sources[0].path, 'test-only-key']:
        assert secret not in log
    assert audit_rows()[0]['status'] == 'succeeded'


@pytest.mark.parametrize('mode', ['omitted','duplicate','unknown','empty_topic','invented_quote','wrong_source',
    'missing_endpoint_quote','same_endpoint','duplicate_pair','bad_kind','long_label','long_summary','long_quote',
    'long_explanation','control','extra_key','too_many_topics','too_many_relations','bad_unclassified'])
def test_invalid_results_never_become_a_map(theme_context, monkeypatch, mode):
    plan = make_plan(theme_context)
    data = deepcopy(theme_data(plan))
    topic, relation = data['topics'][0], data['relations'][0]
    if mode == 'omitted':
        topic['members'].pop();topic['evidence'].pop()
    elif mode == 'duplicate':data['unclassified'] = ['S1']
    elif mode == 'unknown':topic['members'][0] = 'S99'
    elif mode == 'empty_topic':topic['members'] = [];topic['evidence'] = []
    elif mode == 'invented_quote':topic['evidence'][0]['quote'] = '원문에 없는 새로운 문장'
    elif mode == 'wrong_source':topic['evidence'][0]['source_id'] = 'S2'
    elif mode == 'missing_endpoint_quote':relation['evidence'].pop()
    elif mode == 'same_endpoint':relation['target'] = relation['source']
    elif mode == 'duplicate_pair':data['relations'].append(deepcopy(relation))
    elif mode == 'bad_kind':relation['kind'] = 'verified_revision'
    elif mode == 'long_label':topic['label'] = 'x'*81
    elif mode == 'long_summary':topic['summary'] = 'x'*1201
    elif mode == 'long_quote':topic['evidence'][0]['quote'] = plan.sources[0].text[:301]
    elif mode == 'long_explanation':relation['explanation'] = 'x'*1001
    elif mode == 'control':relation['explanation'] = '\x1b[31m'
    elif mode == 'extra_key':topic['tool'] = 'delete_file'
    elif mode == 'too_many_topics':data['topics'] = [deepcopy(topic) for _ in range(7)]
    elif mode == 'too_many_relations':data['relations'] = [deepcopy(relation) for _ in range(13)]
    elif mode == 'bad_unclassified':data['unclassified'] = ['S99']
    calls = []
    monkeypatch.setattr(service, '_request', lambda *a: calls.append(a) or theme_response(data))
    with pytest.raises(service.AIServiceError, match='검증'):
        analyze_themes(plan, plan.approval_id())
    assert len(calls) == 1 and audit_rows()[0]['status'] == 'failed_or_unknown'


@pytest.mark.parametrize('mode', ['truncated','refusal','bad_json','missing_choices'])
def test_incomplete_response_is_not_partial_success(theme_context, monkeypatch, mode):
    plan = make_plan(theme_context)
    response = theme_response(theme_data(plan))
    if mode == 'truncated':response['choices'][0]['finish_reason'] = 'length'
    elif mode == 'refusal':response['choices'][0]['message']['refusal'] = 'refused'
    elif mode == 'bad_json':response['choices'][0]['message']['content'] = '{'
    else:response = {}
    monkeypatch.setattr(service, '_request', lambda *a: response)
    with pytest.raises(service.AIServiceError):
        analyze_themes(plan, plan.approval_id())


def test_unclassified_kept_and_same_document_relation_rejected(theme_context, monkeypatch):
    root, ids = theme_context
    plan = prepare_themes('일정', [(ids[0],0),(ids[0],1),(ids[1],0)], root)
    data = {'topics': [], 'relations': [], 'unclassified': ['S1','S2','S3']}
    monkeypatch.setattr(service, '_request', lambda *a: theme_response(data))
    result = analyze_themes(plan, plan.approval_id())
    assert len(result['unclassified']) == 3
    bad = theme_data(plan)
    bad['relations'][0]['target'] = 'S2'
    bad['relations'][0]['evidence'][1] = {'source_id': 'S2', 'quote': plan.sources[1].text[:18]}
    monkeypatch.setattr(service, '_request', lambda *a: theme_response(bad))
    with pytest.raises(service.AIServiceError):
        analyze_themes(plan, plan.approval_id())


def test_review_invalid_after_reanalysis(theme_context):
    plan = make_plan(theme_context)
    (theme_context[0] / 'doc00.txt').write_text('일정 변경', encoding='utf-8')
    run_content_analysis(theme_context[0])
    with pytest.raises(ValueError):
        analyze_themes(plan, plan.approval_id())
    assert not audit_rows()
