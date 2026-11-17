"""선택 인용에 대한 외부 AI 질문. 기본값은 로컬 미리보기만 수행한다."""
import argparse
import json
import os
import sqlite3
import sys

from app.console import configure_console
from core.ai_questions import DEFAULT_MODEL, ENDPOINT, POLICY_NOTICE, prepare_question
from services.openai_questions import AIServiceError, ask_question


def source_selection(value):
    try:
        file_id, chunk = map(int, value.split(':'))
        if not 1 <= file_id < 2**63 or not 1 <= chunk < 2**63:
            raise ValueError()
        return file_id, chunk - 1
    except ValueError:
        raise argparse.ArgumentTypeError('인용은 파일ID:인용번호 형식이며 둘 다 1 이상의 정수입니다.') from None


def main():
    configure_console()
    parser = argparse.ArgumentParser(description='File Network - Approved OpenAI Document Q&A')
    parser.add_argument('--source', action='append', required=True, type=source_selection,
                        help='파일ID:인용번호 (1부터 시작, 최대 4개, 반복 지정)')
    parser.add_argument('--question', help='질문 (생략 시 입력; 민감한 내용 입력 금지)')
    parser.add_argument('--model', default=os.environ.get('FILE_NETWORK_OPENAI_MODEL', DEFAULT_MODEL))
    parser.add_argument('--send', action='store_true', help='내용 확인 후 대화형 승인 시 한 번 전송')
    args = parser.parse_args()
    try:
        question = args.question if args.question is not None else input('질문: ')
        plan = prepare_question(question, args.source, args.model)
        print(POLICY_NOTICE, file=sys.stderr)
        print('전송 대상: ' + ENDPOINT, file=sys.stderr)
        print('로컬 근거 (아래 경로/식별자는 전송하지 않음):', file=sys.stderr)
        for source in plan.sources:
            print(json.dumps({'id': source.source_id, 'path': source.path, 'chunk': source.segment + 1,
                              'page': source.page, 'source_label': source.source_label}, ensure_ascii=False), file=sys.stderr)
        print('실제 전송 JSON (원본 파일 업로드 없음):', file=sys.stderr)
        print(json.dumps(plan.payload(), ensure_ascii=False, indent=2), file=sys.stderr)
        if not args.send:
            print('미리보기 완료. 외부 전송 없음. 전송하려면 --send로 다시 실행해 내용을 확인하세요.', file=sys.stderr)
            return
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ValueError('전송 승인은 미리보기를 볼 수 있는 대화형 터미널에서만 가능합니다.')
        token = '전송 ' + plan.approval_id()[:12]
        print('위 내용 전송에 동의하면 다음 문구를 입력하세요: ' + token, file=sys.stderr)
        if input().strip() != token:
            print('취소했습니다. 외부 전송 없음.', file=sys.stderr)
            return
        result = ask_question(plan, plan.approval_id())
        print(json.dumps(result, ensure_ascii=False))
    except (EOFError, KeyboardInterrupt):
        print('중단했습니다. 이미 요청한 API 호출은 전송되었을 수 있습니다.', file=sys.stderr)
        raise SystemExit(130) from None
    except (OSError, sqlite3.Error):
        parser.error('로컬 데이터 또는 전송 기록 처리에 실패했습니다. 기록의 attempted 상태는 전송되었을 수 있음을 뜻합니다.')
    except (ValueError, AIServiceError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
