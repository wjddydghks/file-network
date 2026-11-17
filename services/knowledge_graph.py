"""영역 전체 자동 분류. 전송 계획 승인 한 번, 단계별 검증/저장, 자동 재시도 없음."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hmac
import json
import os
from pathlib import Path
from types import SimpleNamespace

from core.ai_questions import DEFAULT_MODEL
from core.knowledge_graph import (BATCH_SIZE, KnowledgePlan, empty_state, payload,
                                  catalog_frontier, validate_assignments, validate_catalog, validate_config,
                                  validate_groups, validate_summaries, resolve_record_mapping)
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
        if not isinstance(data, dict) or set(data) != ({'interests','topics','assignments'} if key == 'catalog' else {'groups','assignments'} if key == 'reduce' else {key}):
            raise ValueError()
        return data if key in ('catalog','reduce') else data[key]
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        raise transport.AIServiceError('AI 분류 응답이 잘렸거나 형식이 올바르지 않습니다. 자동 재시도하지 않습니다.') from None


def _call(plan, docs, key, cancelled, *, stage, topics=None, records=(), interests=()):
    if records:
        aliases = {alias for record in records for alias in record['aliases']}
        docs = tuple(d for d in plan.eligible if d.alias in aliases)
    knowledge.verify_documents(docs, cancelled)
    request = payload(plan, docs, topics, stage=stage, records=records, interests=interests)
    if records:
        # Derived requests log their complete file provenance and actual derived-context size.
        context = request['messages'][1]['content']
        sources = tuple(SimpleNamespace(file_id=d.file_id, segment=-2, text=context if index == 0 else '')
                        for index, d in enumerate(docs))
    else:
        sources = tuple(SimpleNamespace(file_id=d.file_id, segment=-1, text=d.sample) for d in docs)
    audit = SimpleNamespace(model=plan.model, sources=sources)
    request_id = transport._audit_start(audit)
    try:
        check_cancelled(cancelled)
        response = transport._request(request, key)
        data = _decode(response, {'summarize':'summaries','reduce':'reduce','catalog':'catalog','classify':'assignments'}[stage])
        if stage == 'summarize':
            data = validate_summaries(data, docs, evidence_only=True)
            knowledge.verify_quotes(docs, [(alias, row['quote']) for alias, row in data.items()])
        elif stage == 'reduce':
            data = resolve_record_mapping(data, records)
        elif stage == 'catalog':
            data = resolve_record_mapping(data, records, catalog=True)
        else:
            data = validate_assignments(data, docs, topics, evidence_only=True)
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
    if type(max_calls) is not int or not 1 <= max_calls <= 10000:
        raise ValueError('호출 상한은 1~10000 사이의 정수여야 합니다.')
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
        def checkpoint(phase, done, total):
            knowledge.save(plan, token, state)
            if progress:
                progress({'phase': phase, 'done': done, 'total': total, 'calls': calls})

        def process_batches(documents, stage, target):
            nonlocal calls
            batches = [documents[i:i+BATCH_SIZE] for i in range(0,len(documents),BATCH_SIZE)]
            offset, error = 0, None
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='knowledge') as pool:
                pending = set()
                while pending or offset < len(batches):
                    # At most two already-approved calls in flight. Never retry or exceed the budget.
                    while (error is None and not (is_cancelled and is_cancelled()) and len(pending)<2
                           and offset<len(batches) and calls<max_calls):
                        batch=batches[offset];offset+=1;calls+=1
                        pending.add(pool.submit(_call,plan,batch,key,is_cancelled,stage=stage,
                                                topics=state['topics'],interests=state['interests']))
                    if not pending:
                        break
                    ready,pending=wait(pending,return_when=FIRST_COMPLETED)
                    for future in ready:
                        try:
                            result=future.result()
                        except Exception as exc:
                            if error is None:error=exc
                        else:
                            target.update(result)
                            checkpoint(stage,len(target),len(plan.eligible))
                    # Drain and save successful in-flight work even when another call failed/cancelled.
                if error is not None:raise error
            check_cancelled(is_cancelled)
            return offset==len(batches)

        # No taxonomy can be frozen until EVERY eligible file has contributed its summary.
        remaining_summaries = [d for d in plan.eligible if d.alias not in state['summaries']]
        if not process_batches(remaining_summaries,'summarize',state['summaries']):
            return state
        if not plan.eligible:
            state['topics'] = []
        elif not state['topics']:
            # Bounded context; coverage is exhaustive at every level, including minority subjects.
            while True:
                check_cancelled(is_cancelled)
                records, task, _ = catalog_frontier(plan, state)
                if task is None:
                    break
                if calls >= max_calls:
                    return state
                # Requests contain derived summaries, not another copy of the source samples.
                groups = _call(plan, (), key, is_cancelled, stage='reduce', records=records)
                state['reductions'][task] = groups
                calls += 1
                checkpoint('reduce', len(state['reductions']), len(state['summaries']))
            if calls >= max_calls:
                return state
            check_cancelled(is_cancelled)
            data = _call(plan, (), key, is_cancelled, stage='catalog', records=records)
            state['interests'], state['topics'] = data['interests'], data['topics']
            calls += 1
            checkpoint('catalog', len(state['topics']), len(state['topics']))
        remaining = [d for d in plan.eligible if d.alias not in state['assignments']]
        process_batches(remaining,'classify',state['assignments'])
        check_cancelled(is_cancelled)
        if knowledge.read_documents(plan.root, is_cancelled,excluded_dirs=plan.excluded_dirs) != plan.documents:
            raise ValueError('분석 중 영역의 저장 파일이 변경되었습니다. 다시 준비하세요.')
        state['complete'] = len(state['assignments']) == len(plan.eligible)
        knowledge.save(plan, token, state)
        return state
    finally:
        knowledge.release(plan, token)
