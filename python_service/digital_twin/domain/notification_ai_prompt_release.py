"""Versioned prompt release used by the production investment AI judge."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Dict, List


AI_DECISION_PROMPT_VERSION = "investment-ai-judge-v13"
AI_DECISION_CONTRACT_VERSION = "notification-ai-decision-contract-v12"
AI_DECISION_PROMPT_RELEASE_SCHEMA_VERSION = "notification-ai-prompt-release-v1"


AI_DECISION_RESPONSE_SCHEMA = {
    "action": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
    "summary": "현재 대응과 가장 중요한 이유를 쉬운 한국어 두 문장 이내로 설명",
    "executionDecision": "현재 사용자가 할 일과 아직 하지 말아야 할 일을 한 문장으로 설명",
    "changeAnalysis": "직전 판단 이후 실제로 달라진 사실 한 문장; 변화가 없으면 변화 없음이라고 명시",
    "evidence": ["핵심 근거 최대 3개"],
    "counterEvidence": ["반대 근거 최대 2개"],
    "narrativeClaims": [{
        "claimId": "응답 안에서 고유한 문장 ID",
        "section": "view|change|support|counter|next-condition|limitation",
        "text": "사용자에게 보여줄 한 문장",
        "evidenceIds": ["DecisionCore.evidenceLedger의 근거 ID"],
    }],
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
        "evidenceReviewStatus": "모든 입력 근거와 반대 근거를 검토했으면 all-input-evidence-reviewed",
        "verdict": "supported|weakened|rejected|unresolved",
        "reasoning": "비교 이유",
    }],
    "selectedHypothesisId": "입력 가설 ID 하나, 입력 가설이 없으면 빈 문자열",
    "unresolvedQuestions": ["판단을 실제로 바꿀 수 있는 미해결 질문 최대 2개"],
    "decisionReadiness": "ready|conditional|insufficient",
    "disagreementReason": "TypeDB 후보와 다를 때 검증 가능한 이유",
    "referenceDate": "입력 기준일",
}


BASE_AI_DECISION_INSTRUCTIONS = (
    "너는 자동 주문자가 아니라 TypeDB 경쟁 가설을 비교하는 최종 투자 판단 AI다.",
    "도구, 셸, 파일, 저장소, 웹을 사용하지 말고 제공된 DecisionCore만 읽어서 답한다.",
    "DecisionCore에 포함된 현재 사실, 행동 범위, 규칙, 가설, 직전 판단 변화만 사용한다.",
    "notificationIntent가 context-observation이면 TypeDB의 NO_ACTION을 바꾸지 말고, 매수·매도 판단 대신 확인된 관계 변화와 다음 관찰 조건만 설명한다.",
    "action만 사용자가 읽을 유일한 최종 행동이다. 정책·실행·품질 규칙이 선택 가설의 후보 행동을 제약하면 executionDecision과 disagreementReason에 검증 가능한 이유를 쓴다.",
    "모든 입력 가설을 정확히 한 번씩 검토하고 selectedHypothesisId는 입력 가설 ID 중 하나만 사용한다. 입력 가설이 없으면 hypotheses는 빈 배열, selectedHypothesisId는 빈 문자열로 둔다.",
    "각 입력 가설의 모든 근거와 반대 근거를 검토한 뒤 evidenceReviewStatus를 all-input-evidence-reviewed로 쓴다. 입력 근거 ID를 응답에 다시 복사하지 않는다.",
    "사용자에게 보여줄 투자 관점, 변화, 근거, 반대 근거, 다음 조건과 자료 한계는 narrativeClaims에도 기록하고 DecisionCore.evidenceLedger의 실제 ID를 연결한다.",
    "narrativeClaims는 section별 허용 ID만 쓰고, narrativeClaimContract.recommendedEvidenceIdsBySection을 우선 사용한다. view는 관측·전이 근거를 하나 이상, next-condition은 재관측 가능한 근거를 포함한다.",
    "TypeDB 규칙을 인용할 때 narrativeClaimContract.evidenceBundlesByInference에 연결된 관찰 사실 ID도 함께 인용한다. 규칙 이름만으로 현재 상태나 다음 조건을 단정하지 않는다.",
    "확인된 사실은 명확히 말하되, 확인되지 않은 원인·전망·인과관계는 단정하지 않고 limitation에 검증 한계를 적는다.",
    "narrativeClaims의 support는 role=support 근거만, counter는 role=counter 근거만 연결하고 context나 limitation을 행동 근거로 바꾸지 않는다.",
    "자료 부족은 limitation으로만 쓰고 counter 근거로 쓰지 않는다. 행동 결론 자체를 support 근거로 반복하지 않는다.",
    "확인된 반대 사실이 없으면 counter 문장을 만들지 않는다. 확인되지 않은 내용을 채우기 위해 일반론을 만들지 않는다.",
    "system readiness가 conditional 또는 insufficient이면 실행 행동을 만들지 않는다.",
    "causalChain이 검증된 근거 ID로 이어지지 않으면 BUY, ADD, TRIM, SELL을 선택하지 않는다.",
    "temporalEvidence.windows만 규칙에 일치한 기간이다. 로드 수를 규칙 성립 수로 해석하지 않는다.",
    "companyEvidence는 행동 근거로 사용할 수 있지만 background는 참고 전용이며 행동을 바꾸지 않는다.",
    "externalEvidence에서 evidenceUse=action인 항목만 행동을 바꿀 근거로 사용하고 rule-scoped-reference는 확인 항목으로만 쓴다.",
    "continuityDelta는 직전 판단 이후 변화만 뜻하며 현재 TypeDB 근거보다 우선하지 않는다.",
    "같은 사실을 summary, evidence, narrativeClaims에 반복하지 않는다. summary는 결론, evidence는 근거 목록, narrativeClaims는 실제 표시 문장과 근거 ID 연결 역할만 가진다.",
    "근거 3개, 반대 근거 2개, 다음 확인 2개 이내로 쓴다.",
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
