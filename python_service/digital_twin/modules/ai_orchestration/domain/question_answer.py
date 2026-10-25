"""Cited answer parts and explicit gaps; neither can waive the original contract."""
from copy import deepcopy

VERSION = "question-answer-parts-v1"
CAUSES = ("unread-source", "access-failed", "unconfirmed-disclosure", "future-data",
          "scope-undefined", "verification-needed")
CAUSE_LABELS = {"unread-source": "필요한 원문 미확보", "access-failed": "출처 접근 실패",
    "unconfirmed-disclosure": "공개 자료에서 확인하지 못함", "future-data": "발표·관측 대기",
    "scope-undefined": "질문 범위 미확정", "verification-needed": "기간·단위·출처 등 검증 필요"}
INSTRUCTIONS = """질문 답변 분리 계약:
questionResolutions.answerParts={confirmed,gaps}를 기록하세요. research 질문에만 작성하고 다른 capability는 두 배열을 비우세요. confirmed는 최대 세 개의 {text,evidenceIds}이며 이번 질문에서 실제로 확인한 답만 담습니다. 원래 질문 전체가 미완료여도 이 답을 보존하세요. 현재 선택한 해당 질문의 사용 가능한 facts를 인용하고, 이전 AI 설명을 근거로 쓰지 마세요. 확인한 답이 없으면 빈 배열입니다.
gaps는 missingRequirements 각각에 대한 {requirementIndex,role,cause,nextAction}입니다. requirementIndex는 missingRequirements의 영부터 시작하는 인덱스이며 각 항목을 한 번씩 연결합니다. 긴 부족 항목 문장을 다시 복사하지 마세요. role은 essential/supplementary입니다. 대상·기간·통화·단위·비교 기준·원문 일치와 원래 완료 조건에 명시된 항목은 essential입니다. supplementary는 원래 질문의 답을 바꾸지 않는 보강 조사이며 기존 필수 조건을 낮추는 수단이 아닙니다. 어느 역할이든 남은 원래 조건은 삭제하거나 answered로 닫지 마세요.
cause는 unread-source/access-failed/unconfirmed-disclosure/future-data/scope-undefined/verification-needed입니다. nextAction에는 필요한 문서·표·기간 또는 재개 조건을 짧게 쓰세요. 원문 미수집·접근 실패·발췌 누락을 회사가 공개하지 않았다는 결론으로 바꾸지 마세요. 미공개 단정은 실제 확인한 원문 범위와 근거가 있어야 합니다.
새 질문은 하나의 확인 대상과 보고기간, 최소 필수 근거로 좁히세요. 사실 비교와 인과 기여액 추정·미래 전망을 한 완료 조건에 묶지 마세요. 보강 항목은 기존 질문의 gaps에 남기고 같은 질문을 새로 예약하지 마세요. 원래 저장된 질문의 계약·조사 한도는 유지합니다.
"""


def enabled(packet):
    return packet.get("questionAnswerPolicy") == VERSION


def instructions(packet):
    return INSTRUCTIONS if enabled(packet) else ""


def read_instructions(packet):
    return ("확인 가능한 답과 남은 항목을 분리할 근거를 선택하세요. 원래 질문의 대상·기간·완료 조건을 유지하고, "
        "다른 질문이나 보강 조사의 부족을 새 필수 조건으로 추가하지 마세요. 원문 미수집·첨부 누락·접근 실패는 미공개 증명이 아닙니다. "
        "일부만 확인했으면 그 근거를 선택해 assess로 넘기고 부족분은 해당 questionScopes에 남기세요.\n" if enabled(packet) else "")


def review_instructions(packet):
    return ("answerParts.confirmed의 각 답을 해당 질문의 현재 선택 근거와 대조하세요. 과거 AI 문장이나 확인하지 않은 수치·인과 주장을 "
        "확인된 답으로 올리면 거부하세요. gaps는 missingRequirements의 인덱스마다 한 번씩 원인·다음 수집 대상을 연결해야 합니다. "
        "원래 필수 조건·기간·단위·출처 검증을 supplementary로 낮추거나 미수집·접근 실패를 미공개로 단정하면 거부하세요. "
        "부분 답은 제공할 수 있지만 남은 원래 조건이 있으면 질문 완료는 승인하지 마세요.\n" if enabled(packet) else "")


def schema(fact_ids):
    from .insight_schema import obj, array, choice
    return obj({"confirmed": {**array(obj({
        "text": {"type": "string", "minLength": 8, "maxLength": 400},
        "evidenceIds": {**array(choice(fact_ids or [""])), "minItems": 1, "maxItems": 4}})), "maxItems": 3},
        "gaps": {**array(obj({"requirementIndex": {"type": "integer", "minimum": 0, "maximum": 7},
            "role": choice(["essential", "supplementary"]), "cause": choice(CAUSES),
            "nextAction": {"type": "string", "minLength": 8, "maxLength": 250}})), "maxItems": 8}})


def validate(parts, resolution, review, scope, facts):
    from .research_evidence_use import usable_fact
    if not isinstance(parts, dict) or set(parts) != {"confirmed", "gaps"}:
        raise ValueError("question answer requires confirmed parts and gaps")
    confirmed, gaps = parts["confirmed"], parts["gaps"]
    if not isinstance(confirmed, list) or len(confirmed) > 3 or not isinstance(gaps, list) or len(gaps) > 8:
        raise ValueError("question answer parts exceed bounds")
    allowed = set(scope.get("selectedFactIds", [])) & set(resolution["evidenceIds"]) & set(review["evidenceIds"])
    for part in confirmed:
        if (not isinstance(part, dict) or set(part) != {"text", "evidenceIds"}
                or not isinstance(part["text"], str) or not 8 <= len(part["text"].strip()) <= 400):
            raise ValueError("question answer requires bounded confirmed text")
        ids = part["evidenceIds"]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 4
                or any(not isinstance(key, str) or key not in allowed or not usable_fact(facts[key]) for key in ids)):
            raise ValueError("confirmed answer requires its own usable selected evidence")
    remaining = []
    for gap in gaps:
        if (not isinstance(gap, dict) or set(gap) != {"requirementIndex", "role", "cause", "nextAction"}
                or type(gap["requirementIndex"]) is not int or not 0 <= gap["requirementIndex"] < len(resolution["missingRequirements"])
                or gap["role"] not in {"essential", "supplementary"} or gap["cause"] not in CAUSES
                or not isinstance(gap["nextAction"], str) or not 8 <= len(gap["nextAction"].strip()) <= 250):
            raise ValueError("question gap requires original requirement, cause and next action")
        remaining.append(gap["requirementIndex"])
    if len(remaining) != len(set(remaining)) or set(remaining) != set(range(len(resolution["missingRequirements"]))):
        raise ValueError("question answer cannot drop or duplicate unmet requirements")
    if review["action"] == "answered" and not confirmed:
        raise ValueError("completed question requires a cited answer")
    return deepcopy(parts)
