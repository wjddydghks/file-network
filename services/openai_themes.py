"""AI 주제·관계 결과를 승인 인용과 대조한다. 사실성 판단을 대신하지 않는다."""
import json

from core.ai_questions import validate_text
from core.ai_themes import RELATION_LABELS, validate_theme_plan
from services.openai_questions import AIServiceError, _execute_approved_request


def _parse_themes(response, plan):
    try:
        choice = response['choices'][0]
        if choice['finish_reason'] != 'stop' or choice['message'].get('refusal'):
            raise ValueError()
        result = json.loads(choice['message']['content'])
        if not isinstance(result, dict) or set(result) != {'topics', 'relations', 'unclassified'}:
            raise ValueError()
        sources = {s.source_id: s for s in plan.sources}
        def refs(values):
            if (not isinstance(values, list) or len(values) > len(sources)
                    or any(not isinstance(v, str) or v not in sources for v in values)
                    or len(set(values)) != len(values)):
                raise ValueError()
            return set(values)
        def evidence(values, expected):
            if not isinstance(values, list) or len(values) != len(expected):
                raise ValueError()
            found = []
            for item in values:
                if not isinstance(item, dict) or set(item) != {'source_id', 'quote'}:
                    raise ValueError()
                source_id, quote = item['source_id'], item['quote']
                if not isinstance(source_id, str) or source_id not in sources:
                    raise ValueError()
                validate_text(quote, 300, '인용')
                if quote not in sources[source_id].text:
                    raise ValueError()
                found.append(source_id)
            if refs(found) != expected:
                raise ValueError()
        topics, relations = result['topics'], result['relations']
        if not isinstance(topics, list) or len(topics) > 6 or not isinstance(relations, list) or len(relations) > 12:
            raise ValueError()
        covered = refs(result['unclassified'])
        for topic in topics:
            if not isinstance(topic, dict) or set(topic) != {'label', 'summary', 'members', 'evidence'}:
                raise ValueError()
            validate_text(topic['label'], 80, '주제')
            validate_text(topic['summary'], 1200, '요약')
            members = refs(topic['members'])
            if not members or covered & members:
                raise ValueError()
            evidence(topic['evidence'], members)
            covered.update(members)
        if covered != set(sources):
            raise ValueError()
        pairs = set()
        for relation in relations:
            if not isinstance(relation, dict) or set(relation) != {'source', 'target', 'kind', 'explanation', 'evidence'}:
                raise ValueError()
            ends = refs([relation['source'], relation['target']])
            if len(ends) != 2 or sources[relation['source']].file_id == sources[relation['target']].file_id:
                raise ValueError()
            pair = tuple(sorted(ends))
            if pair in pairs or relation['kind'] not in RELATION_LABELS:
                raise ValueError()
            pairs.add(pair)
            validate_text(relation['explanation'], 1000, '관계 설명')
            evidence(relation['evidence'], ends)
        return {**result, 'sources': [s.__dict__.copy() for s in plan.sources], 'root': plan.root,
                'interest': plan.question, 'inferred': True}
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError, RecursionError):
        raise AIServiceError('AI 주제·관계 결과의 범위·구조·실제 인용을 검증하지 못했습니다. 다시 확인 후 요청하세요.') from None


def analyze_themes(plan, approval_id, *, api_key=None, enabled=None, is_cancelled=None):
    return _execute_approved_request(plan, approval_id, api_key=api_key, enabled=enabled,
        is_cancelled=is_cancelled, validator=validate_theme_plan, parser=_parse_themes)
