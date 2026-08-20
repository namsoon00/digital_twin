"""Versioned prompt release used by the production investment AI judge."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Dict, List


AI_DECISION_PROMPT_VERSION = "investment-ai-judge-v6"
AI_DECISION_CONTRACT_VERSION = "notification-ai-decision-contract-v5"
AI_DECISION_PROMPT_RELEASE_SCHEMA_VERSION = "notification-ai-prompt-release-v1"


AI_DECISION_RESPONSE_SCHEMA = {
    "action": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
    "investmentView": "현재 투자 매력과 위험을 비교한 한 문단",
    "executionDecision": "현재 계정에서 지금 할 행동과 실행 제약",
    "changeAnalysis": "이전 판단에서 실제로 달라진 점",
    "evidence": ["핵심 근거 최대 3개"],
    "counterEvidence": ["반대 근거 최대 2개"],
    "invalidationCondition": "현재 판단을 무효화할 조건",
    "nextChecks": ["판단을 바꿀 다음 확인 최대 2개"],
    "followUpConditions": [{
        "field": "facts.marketEvidenceProfile.observableFollowUpFields의 필드",
        "operator": ">|>=|<|<=|==|!=",
        "threshold": "입력에서 재현 가능한 숫자",
        "purpose": "strengthen|weaken|invalidate|switch",
        "label": "조건 설명",
        "onSatisfied": "성립 시 다시 비교할 행동",
    }],
    "missingDataImpact": ["누락 자료가 판단에 미치는 영향"],
    "hypotheses": [{
        "hypothesisId": "입력 가설 ID",
        "templateId": "입력 template ID",
        "claim": "입력 가설",
        "stance": "risk|support|uncertain|context",
        "supportingEvidenceIds": ["입력 근거 ID"],
        "counterEvidenceIds": ["입력 반대 근거 ID"],
        "verdict": "supported|weakened|rejected|unresolved",
        "reasoning": "비교 이유",
    }],
    "selectedHypothesisId": "입력 가설 ID 하나, 입력 가설이 없으면 빈 문자열",
    "unresolvedQuestions": ["추가 조사 질문"],
    "epistemicSummary": "자료와 판단 한계",
    "decisionReadiness": "ready|conditional|insufficient",
    "causalChain": [{
        "driver": "검증된 변화",
        "channel": "매출|비용|현금흐름|가치평가|수급|위험",
        "expectedEffect": "행동 판단에 미치는 영향",
        "evidenceIds": ["입력 근거 ID"],
        "status": "supported|contested|unresolved",
    }],
    "disagreementReason": "TypeDB 후보와 다를 때 검증 가능한 이유",
    "referenceDate": "입력 기준일",
}


BASE_AI_DECISION_INSTRUCTIONS = (
    "너는 자동 주문자가 아니라 TypeDB 경쟁 가설을 비교하는 최종 투자 판단 AI다.",
    "DecisionCore에 포함된 현재 사실, 행동 범위, 규칙, 가설, 직전 판단 변화만 사용한다.",
    "action은 actionEnvelope가 허용한 범위에서 고르고 대상 역할에 맞지 않는 행동은 선택하지 않는다.",
    "모든 입력 가설을 정확히 한 번씩 검토하고 selectedHypothesisId는 입력 가설 ID 중 하나만 사용한다. 입력 가설이 없으면 hypotheses는 빈 배열, selectedHypothesisId는 빈 문자열로 둔다.",
    "근거 ID는 해당 입력 가설에 실제 연결된 ID만 사용하며 검증되지 않은 외부 사실을 만들지 않는다.",
    "system readiness가 conditional 또는 insufficient이면 실행 행동을 만들지 않는다.",
    "causalChain이 검증된 근거 ID로 이어지지 않으면 BUY, ADD, TRIM, SELL을 선택하지 않는다.",
    "temporalEvidence.windows만 규칙에 일치한 기간이다. 로드 수를 규칙 성립 수로 해석하지 않는다.",
    "companyEvidence는 행동 근거로 사용할 수 있지만 background는 참고 전용이며 행동을 바꾸지 않는다.",
    "externalEvidence에서 evidenceUse=action인 항목만 행동을 바꿀 근거로 사용하고 rule-scoped-reference는 확인 항목으로만 쓴다.",
    "continuityDelta는 직전 판단 이후 변화만 뜻하며 현재 TypeDB 근거보다 우선하지 않는다.",
    "같은 사실을 여러 필드에 반복하지 말고 근거 3개, 반대 근거 2개, 다음 확인 2개 이내로 쓴다.",
    "입력에 없는 목표가, 손절가, 비중, 확률, 점수는 만들지 않는다.",
    "쉬운 한국어로 쓰고 내부 변수명과 TypeDB 식별자는 사용자 설명문에 노출하지 않는다.",
    "설명 문장 없이 응답 스키마를 따르는 JSON 객체 하나만 출력한다.",
)


def _policy_flags(value: object) -> Dict[str, object]:
    flags: Dict[str, object] = {}
    for raw in str(value or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = [part.strip() for part in line.split("=", 1)]
        if not key:
            continue
        normalized = raw_value.lower()
        if normalized in {"1", "true", "yes", "on"}:
            flags[key] = True
        elif normalized in {"0", "false", "no", "off"}:
            flags[key] = False
        else:
            flags[key] = raw_value[:120]
    return flags


@dataclass(frozen=True)
class NotificationAIPromptRelease:
    version: str
    contract_version: str
    policy_flags: Dict[str, object]
    instructions: List[str]
    response_schema: Dict[str, object]
    fingerprint: str

    def to_public_dict(self) -> Dict[str, object]:
        return {
            "schemaVersion": AI_DECISION_PROMPT_RELEASE_SCHEMA_VERSION,
            "ontologyBox": "TBox",
            "tboxClass": "PromptRelease",
            "version": self.version,
            "contractVersion": self.contract_version,
            "fingerprint": self.fingerprint,
            "policySetting": "aiPromptPolicy",
            "policyFlags": dict(self.policy_flags),
            "instructionCount": len(self.instructions),
            "responseFieldCount": len(self.response_schema),
            "instructions": list(self.instructions),
            "responseSchema": dict(self.response_schema),
            "status": "active",
        }


def active_notification_ai_prompt_release(settings: Dict[str, object] = None) -> NotificationAIPromptRelease:
    settings = dict(settings or {})
    flags = _policy_flags(settings.get("aiPromptPolicy"))
    material = {
        "version": AI_DECISION_PROMPT_VERSION,
        "contractVersion": AI_DECISION_CONTRACT_VERSION,
        "instructions": list(BASE_AI_DECISION_INSTRUCTIONS),
        "responseSchema": AI_DECISION_RESPONSE_SCHEMA,
        "policyFlags": flags,
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return NotificationAIPromptRelease(
        version=AI_DECISION_PROMPT_VERSION,
        contract_version=AI_DECISION_CONTRACT_VERSION,
        policy_flags=flags,
        instructions=list(BASE_AI_DECISION_INSTRUCTIONS),
        response_schema=dict(AI_DECISION_RESPONSE_SCHEMA),
        fingerprint=fingerprint,
    )
