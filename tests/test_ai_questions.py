from dataclasses import replace
import io
import json
import sys

import pytest

from app import analyze_content, ask_document
from core.ai_questions import prepare_question, sensitive_categories
from database import db
from database.file_search import search_filename
from services import openai_questions as service


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    root = tmp_path / 'documents'
    root.mkdir()
    path = root / 'private-filename.txt'
    path.write_text('프로젝트 일정은 다음 주 금요일입니다. ' * 160, encoding='utf-8')
    analyze_content.run_content_analysis(root)
    file_id = search_filename('private-filename')[0]['file_id']
    monkeypatch.setenv('FILE_NETWORK_AI_ENABLED', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key-never-send')
    def forbidden(*args):
        pytest.fail('Unexpected network request')
    monkeypatch.setattr(service, '_request', forbidden)
    return file_id, path


def plan_for(context):
    return prepare_question('일정은 언제인가요?', [(context[0], 0)])


def response(answer='다음 주 금요일입니다.', citations=None):
    return {'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'answer': answer, 'citations': ['S1'] if citations is None else citations})}}]}


def audit_rows():
    with db.get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='ai_transmissions'").fetchone()
        return [dict(r) for r in conn.execute('SELECT * FROM ai_transmissions')] if exists else []


def test_preview_is_bounded_alias_only_and_never_opens_original(context):
    context[1].unlink()
    plan = prepare_question('일정?', [(context[0], 0), (context[0], 1)])
    payload = plan.payload()
    body = json.loads(payload['messages'][1]['content'])
    assert len(body['sources']) == 2
    assert all(set(s) == {'id', 'text'} and len(s['text']) <= 2000 for s in body['sources'])
    assert str(context[1]) not in json.dumps(payload)
    assert 'private-filename' not in json.dumps(payload)
    assert payload['store'] is False and 'tools' not in payload
    assert not audit_rows()


@pytest.mark.parametrize('text,category', [
    ('user@example.com', 'email'), ('010-1234-5678', 'phone'),
    ('901010-1234567', 'resident_id'), ('4111 1111 1111 1111', 'payment_card'),
    ('api_key=supersecret', 'credential'), ('password: hunter2', 'credential'),
    ('sk-proj-abcdefghijklmnop', 'credential'), ('-----BEGIN PRIVATE KEY-----', 'credential'),
    ('대외비 문서', 'confidential'), ('CONFIDENTIAL report', 'confidential'),
])
def test_sensitive_question_blocks_before_network(context, text, category):
    assert category in sensitive_categories(text)
    with pytest.raises(ValueError, match='차단') as caught:
        prepare_question(text, [(context[0], 0)])
    assert text not in str(caught.value)
    assert not audit_rows()


def test_sensitive_source_cannot_bypass_by_changing_question(context):
    context[1].write_text('Contact: user@example.com', encoding='utf-8')
    analyze_content.run_content_analysis(context[1].parent)
    with pytest.raises(ValueError, match='email'):
        plan_for(context)
    assert not audit_rows()


@pytest.mark.parametrize('question', ['', 'x' * 2001, '\ud800', 'a\x00b', '\x1b[31m'])
def test_invalid_question(context, question):
    with pytest.raises(ValueError):
        prepare_question(question, [(context[0], 0)])


@pytest.mark.parametrize('selections', [[], [(1, 0)] * 2, [(1, i) for i in range(5)]])
def test_selection_limits(context, selections):
    with pytest.raises(ValueError):
        prepare_question('언제?', selections)


def test_missing_and_invalid_chunk(context):
    for selection in [(context[0], 99999), (999999, 0), (True, 0), (context[0], -1)]:
        with pytest.raises(ValueError):
            prepare_question('언제?', [selection])


@pytest.mark.parametrize('mode', ['disabled', 'approval', 'question_changed', 'model_changed', 'source_changed', 'missing_key'])
def test_no_network_without_current_approval_and_config(context, monkeypatch, mode):
    plan = plan_for(context)
    approval = plan.approval_id()
    if mode == 'disabled':
        monkeypatch.delenv('FILE_NETWORK_AI_ENABLED')
    elif mode == 'approval':
        approval = ''
    elif mode == 'question_changed':
        plan = replace(plan, question='다른 질문')
    elif mode == 'model_changed':
        plan = replace(plan, model='gpt-4.1')
    elif mode == 'source_changed':
        plan = replace(plan, sources=(replace(plan.sources[0], text='다른 본문'),))
    elif mode == 'missing_key':
        monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(ValueError):
        service.ask_question(plan, approval)
    assert not audit_rows()


@pytest.mark.parametrize('change', ['reanalysis', 'deletion', 'move'])
def test_stale_review_is_rejected(context, change):
    plan = plan_for(context)
    if change == 'reanalysis':
        context[1].write_text('일정 변경', encoding='utf-8')
    elif change == 'deletion':
        context[1].unlink()
    else:
        context[1].rename(context[1].with_name('moved.txt'))
    analyze_content.run_content_analysis(context[1].parent)
    with pytest.raises(ValueError):
        service.ask_question(plan, plan.approval_id())
    assert not audit_rows()


def test_approved_request_one_call_and_minimal_audit(context, monkeypatch):
    plan = plan_for(context)
    calls = []
    def request(payload, key):
        assert audit_rows()[0]['status'] == 'attempted'
        calls.append((payload, key))
        return response()
    monkeypatch.setattr(service, '_request', request)
    result = service.ask_question(plan, plan.approval_id())
    assert len(calls) == 1 and calls[0][0] == plan.payload()
    assert result['citations'][0]['file_id'] == context[0]
    assert result['citations'][0]['analyzed_at'] == plan.sources[0].analyzed_at
    records = audit_rows()
    assert records[0]['status'] == 'succeeded'
    log = json.dumps(records, ensure_ascii=False)
    for secret in [plan.question, plan.sources[0].text, str(context[1]), 'test-key-never-send', result['answer']]:
        assert secret not in log


@pytest.mark.parametrize('bad', [
    {}, {'choices': []}, response(citations=['S99']), response(citations=['S1', 'S1']),
    response(citations=[123]), response(answer=''), response(answer='x' * 8001),
    response(answer='\x1b[31m'), response(answer='\ud800'),
    {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
    {'choices': [{'finish_reason': 'stop', 'message': {'refusal': 'Cannot help', 'content': '{}'}}]},
])
def test_bad_or_untrusted_answer_rejected_and_audited(context, monkeypatch, bad):
    monkeypatch.setattr(service, '_request', lambda *_: bad)
    plan = plan_for(context)
    with pytest.raises(service.AIServiceError, match='형식'):
        service.ask_question(plan, plan.approval_id())
    assert audit_rows()[0]['status'] == 'failed_or_unknown'


def test_provider_failure_no_retry(context, monkeypatch):
    calls = []
    def fail(*args):
        calls.append(1)
        raise service.AIServiceError('연결 실패')
    monkeypatch.setattr(service, '_request', fail)
    plan = plan_for(context)
    with pytest.raises(service.AIServiceError):
        service.ask_question(plan, plan.approval_id())
    assert len(calls) == 1 and audit_rows()[0]['status'] == 'failed_or_unknown'


def test_audit_failure_prevents_network(context, monkeypatch):
    monkeypatch.setattr(service, '_audit_start', lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    plan = plan_for(context)
    with pytest.raises(OSError):
        service.ask_question(plan, plan.approval_id())


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize('mode', ['preview', 'cancel', 'approve', 'pipe'])
def test_cli_preview_and_explicit_interactive_approval(context, monkeypatch, capsys, mode):
    args = ['ask_document', '--source', f'{context[0]}:1', '--question', '일정은 언제인가요?']
    if mode != 'preview':
        args.append('--send')
    monkeypatch.setattr(sys, 'argv', args)
    approval = '전송 ' + plan_for(context).approval_id()[:12]
    monkeypatch.setattr(sys, 'stdin', Terminal(approval + '\n' if mode == 'approve' else '취소\n'))
    terminal = Terminal()
    monkeypatch.setattr(sys, 'stderr', terminal if mode != 'pipe' else io.StringIO())
    calls = []
    monkeypatch.setattr(service, '_request', lambda *a: calls.append(a) or response())
    if mode == 'pipe':
        with pytest.raises(SystemExit) as caught:
            ask_document.main()
        assert caught.value.code == 2
    else:
        ask_document.main()
    assert len(calls) == (1 if mode == 'approve' else 0)
    if mode == 'approve':
        assert json.loads(capsys.readouterr().out)['citations'][0]['source_id'] == 'S1'
    else:
        assert not audit_rows()


@pytest.mark.parametrize('status', [200, 302, 401, 429])
def test_https_transport_fixed_host_no_redirect_and_no_error_body(monkeypatch, status):
    calls = []
    raw = json.dumps(response()).encode()
    class Response:
        def __init__(self):
            self.status = status
            self.body = raw
        def read1(self, size):
            chunk, self.body = self.body[:size], self.body[size:]
            return chunk
    class Connection:
        sock = None
        def __init__(self, host, **kwargs):
            assert host == 'api.openai.com' and kwargs['timeout'] == 30
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
        def getresponse(self):
            return Response()
        def close(self):
            calls.append('closed')
    monkeypatch.setattr(service.http.client, 'HTTPSConnection', Connection)
    if status == 200:
        assert service._request({'store': False}, 'key') == response()
    else:
        with pytest.raises(service.AIServiceError):
            service._request({'store': False}, 'key')
    assert calls[0][0:2] == ('POST', '/v1/chat/completions')
    assert calls[0][2]['headers']['Authorization'] == 'Bearer key'
    assert calls[-1] == 'closed' and len(calls) == 2


@pytest.mark.parametrize('mode', ['oversize', 'invalid_json', 'timeout', 'network_error'])
def test_transport_limits_and_errors_are_scrubbed(monkeypatch, mode):
    closed = []
    class Response:
        status = 200
        def read1(self, size):
            if mode == 'oversize':
                return b'x' * size
            if mode == 'invalid_json':
                return b'private body' if not closed else b''
            raise TimeoutError('secret-internal-network-detail')
    reply = Response()
    def read(size):
        if mode == 'invalid_json':
            if getattr(reply, 'done', False):
                return b''
            reply.done = True
            return b'private body'
        return Response.read1(reply, size)
    reply.read1 = read
    class Connection:
        sock = None
        def __init__(self, *a, **kw):
            pass
        def request(self, *a, **kw):
            if mode == 'network_error':
                raise OSError('secret-internal-network-detail')
        def getresponse(self):
            return reply
        def close(self):
            closed.append(True)
    monkeypatch.setattr(service.http.client, 'HTTPSConnection', Connection)
    with pytest.raises(service.AIServiceError) as caught:
        service._request({}, 'private-key')
    assert closed == [True]
    assert 'private' not in str(caught.value) and 'secret' not in str(caught.value)


def test_unrecognized_person_name_is_not_claimed_as_anonymized(context):
    # 정책 한계: 임의 이름을 자동 탐지했다고 주장하지 않는다.
    plan = prepare_question('김철수의 일정을 요약', [(context[0], 0)])
    assert '김철수' in plan.payload()['messages'][1]['content']


def test_invalid_model_and_non_ascii_approval_rejected(context):
    with pytest.raises(ValueError, match='모델'):
        prepare_question('일정?', [(context[0], 0)], 'invalid\nmodel')
    plan = plan_for(context)
    with pytest.raises(ValueError, match='승인'):
        service.ask_question(plan, '승인')
