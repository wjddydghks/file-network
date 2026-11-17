"""영역 전체 자동 분류. 전송 계획 승인 한 번, 단계별 검증/저장, 자동 재시도 없음."""
import hmac
import json
import os
from pathlib import Path
from types import SimpleNamespace

from core.ai_questions import DEFAULT_MODEL
from core.knowledge_graph import (BATCH_SIZE, KnowledgePlan, empty_state, payload,
                                  validate_assignments, validate_config, validate_topics)
from core.operations import check_cancelled
from core.scanner.exclusions import validate_excluded_dirs
from database import knowledge
from services import openai_questions as transport


def prepare_knowledge(root, interest='', model=DEFAULT_MODEL, is_cancelled=None, progress=None, excluded_dirs=()):
    root = str(Path(root).expanduser().resolve())
    if interest is None and model is None:
        interest, model = knowledge.latest_config(root) or ('', DEFAULT_MODEL)
    if not isinstance(interest, str):
        raise ValueError('관심사를 문자열로 입력하세요.')
    interest = interest.strip()
    validate_config(interest, model)
    excluded_dirs=validate_excluded_dirs(excluded_dirs)
    documents = knowledge.read_documents(root, is_cancelled, progress, excluded_dirs)
    return KnowledgePlan(root, interest, model, documents, knowledge.read_cache(root, interest, model),excluded_dirs)


def _decode(response, key):
    try:
        choice = response['choices'][0]
        if choice['finish_reason'] != 'stop' or choice['message'].get('refusal'):
            raise ValueError()
        data = json.loads(choice['message']['content'])
        if not isinstance(data, dict) or set(data) != {key}:
            raise ValueError()
        return data[key]
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        raise transport.AIServiceError('AI 분류 응답이 잘렸거나 형식이 올바르지 않습니다. 자동 재시도하지 않습니다.') from None


def _call(plan, docs, topics, key, cancelled):
    knowledge.verify_documents(docs, cancelled)
    # segment=-1은 사용자가 고르는 번호가 아닌 자동 준비 본문임을 기록한다.
    audit = SimpleNamespace(model=plan.model, sources=tuple(SimpleNamespace(file_id=d.file_id, segment=-1, text=d.sample) for d in docs))
    request_id = transport._audit_start(audit)
    try:
        check_cancelled(cancelled)
        response = transport._request(payload(plan, docs, topics), key)
        data = _decode(response, 'topics' if topics is None else 'assignments')
        if topics is None:
            validate_topics(data, docs)
            knowledge.verify_quotes(docs, [(t['file'],t['quote']) for t in data])
        else:
            data = validate_assignments(data, docs, topics)
            knowledge.verify_quotes(docs, [(alias,m['quote']) for alias,ms in data.items() for m in ms])
        transport._audit_finish(request_id, 'succeeded')
        return data
    except BaseException:
        try:
            transport._audit_finish(request_id, 'failed_or_unknown')
        except Exception:
            pass
        raise


def run_knowledge(plan, approval_id, *, max_calls, api_key=None, enabled=False, is_cancelled=None, progress=None):
    if type(max_calls) is not int or not 1 <= max_calls <= 1000:
        raise ValueError('호출 상한은 1~1000 사이의 정수여야 합니다.')
    if enabled is not True:
        raise ValueError('외부 AI 사용 허용과 전송 확인이 필요합니다.')
    if not isinstance(approval_id, str) or not approval_id.isascii() or not hmac.compare_digest(approval_id, plan.approval_id(max_calls)):
        raise ValueError('현재 영역·관심사·호출 상한에 대한 전송 승인이 필요합니다.')
    validate_config(plan.interest, plan.model)
    check_cancelled(is_cancelled)
    key = os.environ.get('OPENAI_API_KEY', '') if api_key is None else api_key
    if not isinstance(key, str) or not key or not key.isascii() or any(ord(c)<33 or ord(c)>126 for c in key):
        raise ValueError('앱에 OpenAI API 키를 입력하거나 OPENAI_API_KEY 환경변수를 설정하세요.')
    fresh = knowledge.read_documents(plan.root, is_cancelled,excluded_dirs=plan.excluded_dirs)
    if fresh != plan.documents:
        raise ValueError('영역의 저장 파일이 변경되었습니다. 다시 준비하세요.')
    state = plan.state() or empty_state(plan)
    if state['complete']:
        return state
    token = knowledge.acquire(plan)
    calls = 0
    try:
        knowledge.save(plan, token, state)
        if not state['topics'] and plan.eligible:
            check_cancelled(is_cancelled)
            state['topics'] = _call(plan, plan.representatives, None, key, is_cancelled)
            calls += 1
            knowledge.save(plan, token, state)
        if not plan.eligible:
            state['topics'] = []
        remaining = [d for d in plan.eligible if d.alias not in state['assignments']] if state['topics'] else []
        for start in range(0, len(remaining), BATCH_SIZE):
            if calls >= max_calls:
                break
            check_cancelled(is_cancelled)
            batch = remaining[start:start+BATCH_SIZE]
            state['assignments'].update(_call(plan, batch, state['topics'], key, is_cancelled))
            calls += 1
            knowledge.save(plan, token, state)
            if progress:
                progress({'phase': 'classify', 'done': len(state['assignments']), 'total': len(plan.eligible), 'calls': calls})
        check_cancelled(is_cancelled)
        if knowledge.read_documents(plan.root, is_cancelled,excluded_dirs=plan.excluded_dirs) != plan.documents:
            raise ValueError('분석 중 영역의 저장 파일이 변경되었습니다. 다시 준비하세요.')
        state['complete'] = len(state['assignments']) == len(plan.eligible)
        knowledge.save(plan, token, state)
        return state
    finally:
        knowledge.release(plan, token)
