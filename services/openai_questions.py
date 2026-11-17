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
            raise AIServiceError('OpenAI API 요청 실패. 인증·한도·모델·서비스 상태를 확인하세요. 자동 재시도하지 않습니다.')
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


def ask_question(plan, approval_id):
    if os.environ.get('FILE_NETWORK_AI_ENABLED') != '1':
        raise ValueError('외부 AI가 비활성화되어 있습니다. FILE_NETWORK_AI_ENABLED=1로 명시적으로 활성화하세요.')
    if (not isinstance(approval_id, str) or not re.fullmatch(r'[0-9a-f]{64}', approval_id)
            or not hmac.compare_digest(approval_id, plan.approval_id())):
        raise ValueError('현재 전송 내용에 대한 승인이 필요합니다.')
    validate_plan(plan)
    check_current_sources(plan)
    key = os.environ.get('OPENAI_API_KEY', '')
    if not key or not key.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError('올바른 OPENAI_API_KEY 환경변수를 설정하세요. 키는 입력·로그·설정 파일에 저장하지 않습니다.')
    request_id = _audit_start(plan)
    try:
        response = _request(plan.payload(), key)
        result = _parse_answer(response, plan)
        _audit_finish(request_id, 'succeeded')
        return {**result, 'request_id': request_id, 'provider': 'openai', 'model': plan.model}
    except BaseException:
        # 중단·시간 초과도 이미 전송됐을 수 있다. 이 상태에서 자동 재시도하지 않는다.
        try:
            _audit_finish(request_id, 'failed_or_unknown')
        except Exception:
            pass  # 최초 attempted 기록은 남는다. 원문 포함 예외를 로깅하지 않는다.
        raise
