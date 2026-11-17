"""승인된 요청만 OpenAI로 전송. 자동 재시도·도구 실행·원문 로깅 없음."""
from datetime import datetime, timezone
import hmac
import http.client
import json
import os
import re
import socket
import ssl
import time
import uuid

from core.ai_questions import check_current_sources, validate_plan
from database import db
from core.operations import check_cancelled

MAX_RESPONSE_BYTES = 65536


class AIServiceError(RuntimeError):
    pass


def _audit_start(plan):
    request_id = uuid.uuid4().hex
    # 외부 호출 전에 기록할 수 없으면 전송하지 않는다. 질문/본문/경로/키/답변은 저장하지 않는다.
    with db.get_connection() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS ai_transmissions (
            request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL,
            targets TEXT NOT NULL, context_chars INTEGER NOT NULL,
            status TEXT NOT NULL)''')
        conn.execute('INSERT INTO ai_transmissions VALUES (?, ?, ?, ?, ?, ?, ?)', (
            request_id, datetime.now(timezone.utc).isoformat(), 'openai', plan.model,
            json.dumps([{'file_id': s.file_id, 'segment': s.segment} for s in plan.sources]),
            sum(len(s.text) for s in plan.sources), 'attempted'))
    return request_id


def _audit_finish(request_id, status):
    with db.get_connection() as conn:
        conn.execute('UPDATE ai_transmissions SET status=? WHERE request_id=?', (status, request_id))


def _http_error(status, response):
    # Provider messages can echo credentials or document text: only allow known identifiers.
    allowed_codes={'invalid_api_key','insufficient_quota','rate_limit_exceeded','billing_hard_limit_reached',
                   'invalid_json_schema','context_length_exceeded','model_not_found','unsupported_parameter',
                   'invalid_request_error','server_error','model_access_denied'}
    code=''
    try:
        body=bytearray()
        while len(body)<8192:
            chunk=response.read1(min(2048,8192-len(body)))
            if not chunk:break
            body.extend(chunk)
        data=json.loads(body)
        error=data.get('error',{}) if isinstance(data,dict) else {}
        candidate=error.get('code') if isinstance(error,dict) else None
        if isinstance(candidate,str) and candidate in allowed_codes:code=candidate
    except (OSError,http.client.HTTPException,ValueError,TypeError,RecursionError):
        pass
    if status==429 and code in ('insufficient_quota','billing_hard_limit_reached'):
        help_text='API 잔액·결제·프로젝트 지출 한도를 확인하세요. ChatGPT 구독 결제와 별개입니다.'
    elif status==429:
        help_text='API 요청/토큰 속도 한도에 도달했습니다. 잠시 뒤 다시 준비해 재개하세요.'
    elif status==401:help_text='API 키가 유효한지, 키가 속한 프로젝트가 맞는지 확인하세요.'
    elif status==403:help_text='이 프로젝트의 모델 사용 권한을 확인하세요.'
    elif status==404:help_text='지정한 모델이 존재하며 계정에 허용되어 있는지 확인하세요.'
    elif status==400 and code=='context_length_exceeded':help_text='모델의 입력 길이 제한을 초과했습니다. 더 작은 폴더 또는 긴 입력을 지원하는 모델을 사용하세요.'
    elif status==400:help_text='요청 형식 또는 모델 옵션이 거부되었습니다. 표시된 오류 코드와 앱 버전을 확인하세요.'
    elif status>=500:help_text='OpenAI 서버 오류입니다. 잠시 뒤 다시 준비해 재개하세요.'
    elif 300<=status<400:help_text='예상하지 않은 리디렉션을 차단했습니다.'
    else:help_text='API 서비스 상태와 프로젝트 접근 권한을 확인하세요.'
    return AIServiceError(f'OpenAI HTTP {status}'+(f' ({code})' if code else '')+'. '+help_text+' 자동 재시도하지 않습니다.')


def _request(payload, key):
    connection = http.client.HTTPSConnection('api.openai.com', timeout=30, context=ssl.create_default_context())
    deadline = time.monotonic() + 60
    try:
        connection.request('POST', '/v1/chat/completions',
            body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        # 리디렉션을 따라가지 않아 인증정보와 본문을 다른 호스트로 보내지 않는다.
        if response.status != 200:
            raise _http_error(response.status,response)
        chunks = []
        count = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if connection.sock is not None:
                connection.sock.settimeout(min(30, remaining))
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
            if count > MAX_RESPONSE_BYTES:
                raise AIServiceError('OpenAI 응답 크기 제한을 초과했습니다.')
        return json.loads(b''.join(chunks))
    except (OSError, socket.timeout, http.client.HTTPException, ValueError, RecursionError):
        raise AIServiceError('OpenAI 연결 또는 응답 처리에 실패했습니다. 전송되었을 수 있으며 자동 재시도하지 않습니다.') from None
    finally:
        connection.close()


def _parse_answer(response, plan):
    try:
        choice = response['choices'][0]
        if choice['finish_reason'] != 'stop' or choice['message'].get('refusal'):
            raise ValueError()
        data = json.loads(choice['message']['content'])
        if not isinstance(data, dict) or set(data) != {'answer', 'citations'}:
            raise ValueError()
        answer, citations = data['answer'], data['citations']
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
            raise ValueError()
        answer.encode('utf-8')
        if any(ord(c) < 32 and c not in '\n\r\t' for c in answer):
            raise ValueError()
        if (not isinstance(citations, list) or len(citations) > len(plan.sources)
                or any(not isinstance(s, str) for s in citations)
                or len(set(citations)) != len(citations)
                or not set(citations) <= {s.source_id for s in plan.sources}):
            raise ValueError()
        return {'answer': answer, 'citations': [{
            'source_id': s.source_id, 'file_id': s.file_id, 'segment': s.segment,
            'analyzed_at': s.analyzed_at, 'path': s.path, 'page': s.page,
            'source_kind': s.source_kind, 'source_label': s.source_label,
        } for s in plan.sources if s.source_id in citations]}
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError, RecursionError):
        raise AIServiceError('AI 응답 형식 또는 근거 인용이 올바르지 않습니다.') from None


def ask_question(plan, approval_id, *, api_key=None, enabled=None, is_cancelled=None):
    return _execute_approved_request(plan, approval_id, api_key=api_key, enabled=enabled,
                                     is_cancelled=is_cancelled)


def _execute_approved_request(plan, approval_id, *, api_key=None, enabled=None, is_cancelled=None,
                              validator=validate_plan, parser=_parse_answer):
    active = os.environ.get('FILE_NETWORK_AI_ENABLED') == '1' if enabled is None else enabled is True
    if not active:
        raise ValueError('외부 AI가 비활성화되어 있습니다. FILE_NETWORK_AI_ENABLED=1로 명시적으로 활성화하세요.')
    if (not isinstance(approval_id, str) or not re.fullmatch(r'[0-9a-f]{64}', approval_id)
            or not hmac.compare_digest(approval_id, plan.approval_id())):
        raise ValueError('현재 전송 내용에 대한 승인이 필요합니다.')
    check_cancelled(is_cancelled)
    validator(plan)
    check_current_sources(plan)
    key = os.environ.get('OPENAI_API_KEY', '') if api_key is None else api_key
    if not isinstance(key, str) or not key or not key.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError('올바른 OpenAI API 키를 입력하거나 OPENAI_API_KEY 환경변수를 설정하세요. 키는 로그·설정 파일에 저장하지 않습니다.')
    check_cancelled(is_cancelled)
    request_id = _audit_start(plan)
    try:
        check_cancelled(is_cancelled)
        response = _request(plan.payload(), key)
        result = parser(response, plan)
        _audit_finish(request_id, 'succeeded')
        return {**result, 'request_id': request_id, 'provider': 'openai', 'model': plan.model}
    except BaseException:
        # 중단·시간 초과도 이미 전송됐을 수 있다. 이 상태에서 자동 재시도하지 않는다.
        try:
            _audit_finish(request_id, 'failed_or_unknown')
        except Exception:
            pass  # 최초 attempted 기록은 남는다. 원문 포함 예외를 로깅하지 않는다.
        raise
