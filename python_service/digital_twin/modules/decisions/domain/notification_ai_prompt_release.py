"""Versioned prompt release used by the production investment AI judge."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Dict, List


AI_DECISION_PROMPT_VERSION = "investment-ai-judge-v33-question-evidence"
AI_DECISION_CONTRACT_VERSION = "notification-ai-decision-contract-v22"
AI_DECISION_PROMPT_RELEASE_SCHEMA_VERSION = "notification-ai-prompt-release-v1"
AI_DECISION_OUTPUT_SCHEMA_VERSION = "notification-ai-output-schema-v1"


AI_DECISION_RESPONSE_SCHEMA = {
    "action": "NO_ACTION|BUY|ADD|HOLD|TRIM|SELL|AVOID",
    "summary": "현재 대응과 가장 중요한 이유를 쉬운 한국어 두 문장 이내로 설명",
    "currentActionPlan": "지금 할 일, 하지 말아야 할 일, 적용 범위를 한 문장으로 설명",
    "executionDecision": "현재 사용자가 할 일과 아직 하지 말아야 할 일을 한 문장으로 설명",
    "changeAnalysis": "직전 판단 이후 실제로 달라진 사실 한 문장; 변화가 없으면 변화 없음이라고 명시",
    "nextActionPlan": "다음에 확인할 수치·사건·시점과 판단 결과를 한 문장으로 설명",
    "evidence": ["핵심 근거 최대 3개"],
    "counterEvidence": ["반대 근거 최대 2개"],
    "counterEvidenceStatus": "confirmed|none-found|not-checked|unavailable",
    "narrativeClaims": [{
        "claimId": "응답 안에서 고유한 문장 ID",
        "hypothesisId": "가설별 지지·반대 설명이면 입력 가설 ID, 공통 사실이면 빈 문자열",
        "section": "view|mechanism|implication|catalyst|change|support|counter|next-condition|limitation",
        "text": "사용자에게 보여줄 한 문장",
        "evidenceIds": ["DecisionCore.evidenceLedger의 근거 ID"],
    }],
    "invalidationCondition": "검증 근거가 연결된 구체적인 현재 판단 무효화 조건",
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
    "causalChain": [{
        "driver": "확인된 변화",
        "channel": "revenue|cost|cash-flow|valuation|flow|risk",
        "expectedEffect": "투자 판단에 미치는 경로",
        "evidenceIds": ["DecisionCore의 근거 ID"],
        "status": "supported|contested|unresolved",
    }],
    "insightAssessment": {
        "direction": "positive|balanced|negative",
        "directionLabel": "상방 우세|상하방 균형|하방 우세",
        "horizon": "intraday|short-term|medium-term|long-term|multi-horizon",
        "horizonLabel": "장중|단기|중기|장기|복합 기간",
        "conviction": "tentative|moderate|strong",
        "convictionLabel": "초기|보통|강함",
        "dominantThesis": "가장 강하게 지지되는 결론",
        "causalMechanism": "관측 변화가 가치·수급·가격에 이어지는 경로",
        "investmentImplication": "보유자 또는 관심 투자자에게 주는 의미",
        "catalysts": ["강화 사건 최대 2개"],
        "risks": ["반대 시나리오 최대 2개"],
        "invalidationCondition": "관점을 무효화할 재관측 조건",
        "thesisKey": "의미 변화 추적용 짧은 영문 키",
    },
    "alternativeAction": {
        "action": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
        "whyNotSelected": "현재 선택하지 않은 이유",
        "switchCondition": "이 행동으로 바뀌는 관찰 조건",
    },
    "epistemicSummary": "확인된 사실, 모르는 점, 남은 반증을 구분한 짧은 설명",
    "disagreementReason": "TypeDB 후보와 다를 때 검증 가능한 이유",
    "referenceDate": "입력 기준일",
}


def _object_schema(properties: Dict[str, object], required: List[str] = None) -> Dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or properties),
        "additionalProperties": False,
    }


AI_DECISION_OUTPUT_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Orbit Alpha investment AI decision",
    **_object_schema({
        "action": {
            "type": "string",
            "enum": ["NO_ACTION", "BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID"],
        },
        "summary": {"type": "string"},
        "currentActionPlan": {"type": "string"},
        "executionDecision": {"type": "string"},
        "changeAnalysis": {"type": "string"},
        "nextActionPlan": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "counterEvidence": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "counterEvidenceStatus": {
            "type": "string",
            "enum": ["confirmed", "none-found", "not-checked", "unavailable"],
        },
        "narrativeClaims": {
            "type": "array",
            "items": _object_schema({
                "claimId": {"type": "string"},
                "hypothesisId": {"type": "string"},
                "section": {
                    "type": "string",
                    "enum": [
                        "view", "mechanism", "implication", "catalyst", "change",
                        "support", "counter", "next-condition", "limitation",
                    ],
                },
                "text": {"type": "string"},
                "evidenceIds": {"type": "array", "items": {"type": "string"}},
            }),
        },
        "invalidationCondition": {"type": "string"},
        "nextChecks": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "followUpConditions": {
            "type": "array",
            "items": _object_schema({
                "field": {"type": "string"},
                "operator": {"type": "string", "enum": [">", ">=", "<", "<=", "==", "!="]},
                "threshold": {"type": "number"},
                "purpose": {
                    "type": "string",
                    "enum": ["strengthen", "weaken", "invalidate", "switch"],
                },
                "label": {"type": "string"},
                "onSatisfied": {"type": "string"},
            }),
        },
        "missingDataImpact": {"type": "array", "items": {"type": "string"}},
        "hypotheses": {
            "type": "array",
            "items": _object_schema({
                "hypothesisId": {"type": "string"},
                "templateId": {"type": "string"},
                "claim": {"type": "string"},
                "stance": {"type": "string", "enum": ["risk", "support", "uncertain", "context"]},
                "evidenceReviewStatus": {
                    "type": "string",
                    "enum": ["all-input-evidence-reviewed"],
                },
                "verdict": {
                    "type": "string",
                    "enum": ["supported", "weakened", "rejected", "unresolved"],
                },
                "reasoning": {"type": "string"},
            }),
        },
        "selectedHypothesisId": {"type": "string"},
        "unresolvedQuestions": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "decisionReadiness": {
            "type": "string",
            "enum": ["ready", "conditional", "insufficient"],
        },
        "causalChain": {
            "type": "array",
            "items": _object_schema({
                "driver": {"type": "string"},
                "channel": {
                    "type": "string",
                    "enum": ["revenue", "cost", "cash-flow", "valuation", "flow", "risk"],
                },
                "expectedEffect": {"type": "string"},
                "evidenceIds": {"type": "array", "items": {"type": "string"}},
                "status": {
                    "type": "string",
                    "enum": ["supported", "contested", "unresolved"],
                },
            }),
        },
        "insightAssessment": _object_schema({
            "direction": {"type": "string", "enum": ["positive", "balanced", "negative"]},
            "directionLabel": {"type": "string"},
            "horizon": {
                "type": "string",
                "enum": ["intraday", "short-term", "medium-term", "long-term", "multi-horizon"],
            },
            "horizonLabel": {"type": "string"},
            "conviction": {"type": "string", "enum": ["tentative", "moderate", "strong"]},
            "convictionLabel": {"type": "string"},
            "dominantThesis": {"type": "string"},
            "causalMechanism": {"type": "string"},
            "investmentImplication": {"type": "string"},
            "catalysts": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
            "risks": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
            "invalidationCondition": {"type": "string"},
            "thesisKey": {"type": "string"},
        }),
        "alternativeAction": _object_schema({
            "action": {
                "type": "string",
                "enum": ["BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID"],
            },
            "whyNotSelected": {"type": "string"},
            "switchCondition": {"type": "string"},
        }),
        "epistemicSummary": {"type": "string"},
        "disagreementReason": {"type": "string"},
        "referenceDate": {"type": "string"},
    }),
}


AI_DECISION_REQUIRED_RESPONSE_FIELDS = tuple(AI_DECISION_RESPONSE_SCHEMA)


BASE_AI_DECISION_INSTRUCTIONS = (
    "너는 자동 주문자가 아니라 TypeDB 경쟁 가설을 비교하는 최종 투자 판단 AI다.",
    "도구·웹·파일을 사용하지 말고 DecisionCore의 현재 사실, 가설, 행동 범위, 직전 판단 변화만 사용한다. 입력에 없는 목표가·손절가·비중·확률·점수는 만들지 않는다.",
    "reasoningLineage의 종목·ABox 스냅샷·추론 세대가 같아야 한다. integrity.state=blocked 또는 judgementEligible=false이면 그 경로는 행동 근거가 아니다.",
    "규칙·trace·relation은 같은 가설의 evidenceLedger 관측값과 연결될 때만 근거다. 규칙명이나 조건 성립 자체를 관측 사실·확률·성과로 설명하지 않는다.",
    "reviewMode=context-narrative 또는 notificationIntent=context-observation/review-observation이면 action=NO_ACTION이다. NO_ACTION은 보유 의견이 아니며 매매 지시 없이 투자 의미와 다음 관찰 조건만 쓴다.",
    "모든 입력 가설을 정확히 한 번 비교하고 selectedHypothesisId는 입력 ID만 사용한다. 가설이 없으면 hypotheses와 selectedHypothesisId를 비운다.",
    "각 가설의 모든 지지·반대 근거를 검토하고 evidenceReviewStatus=all-input-evidence-reviewed로 쓴다. blocked·quarantined 가설은 supported로 판정하지 않는다.",
    "research-only 또는 decisionUse가 execution이 아닌 가설은 비교·학습에만 사용한다. qualification pending은 관계 성립과 행동 검증 완료를 구분하고 필요한 승격·무효화 자료를 밝힌다.",
    "researchProgress는 질문별 자료 충족·해석 검토 기록이며 예측 적중이나 행동 승인 근거가 아니다. 수집 완료와 질문 해결을 구분하고 미해결 질문·누락 기간·비교 기준을 설명에 반영한다. 행동 근거는 기존 evidenceLedger와 actionEnvelope에서만 선택한다.",
    "system readiness가 conditional/insufficient이거나 검증 근거가 연결된 causalChain이 없으면 BUY·ADD·TRIM·SELL을 선택하지 않는다. actionEnvelope의 허용·차단 행동을 지킨다.",
    "투자 관점과 실행 가능성을 분리한다. 실행이 금지돼도 가장 잘 지지되는 방향·인과 경로·투자 의미를 insightAssessment에 결론내리고, 자료 부족만으로 balanced를 선택하지 않는다.",
    "dominantThesis는 결론, causalMechanism은 원인 경로, investmentImplication은 사용자 대응 의미다. narrativeClaims의 view·mechanism·implication과 의미를 맞추되 같은 문장을 반복하지 않는다.",
    "counterEvidenceStatus=confirmed는 selectedHypothesisId와 같은 가설의 counterEvidenceIds 중 하나가 연결된 반대 문장이 있을 때만 사용한다. 다른 가설의 support 근거를 선택 가설의 반대 근거로 바꾸지 않는다. 선택 가설의 counterEvidenceIds가 비어 있고 모든 입력을 검토했을 때만 none-found를 사용한다. 다른 상태는 발행할 수 없다.",
    "narrativeClaims는 narrativeClaimContract의 sectionEvidenceRoles와 recommendedEvidenceIdsBySection을 따르고 실제 evidenceLedger ID를 연결한다. view와 mechanism은 관측 근거, next-condition은 재관측 가능한 근거를 포함한다.",
    "support에는 role=support, counter에는 role=counter만 연결한다. context·limitation·자료 부족을 행동 근거나 반대 사실로 바꾸지 않는다.",
    "reasoningTrigger는 왜 지금 다시 봤는지, relationLifecycle은 가설 관계의 성립·강화·약화·해제를 설명한다. 실제 임계값·관측시각·근거 변화가 없으면 새 변화라고 말하지 않는다.",
    "previousInsight는 문구가 아니라 direction·horizon·conviction·thesisKey를 비교한다. continuityDelta와 과거 계좌 자료는 현재 TypeDB 근거를 덮어쓰지 않으며, transitionVerified가 없는 조건은 변화로 말하지 않는다.",
    "reviewSummary에서 evaluated만 평가 결과다. 관측 수익은 실제 거래 성과가 아니고 관측 수는 독립 실험 수가 아니다.",
    "temporalEvidence.windows만 규칙에 맞는 기간 자료다. loadedWindowCount를 일치 건수로 해석하지 않고, 과거 모델 입력을 현재 기간 자료로 대체하지 않는다.",
    "companyEvidence는 질문과 연결된 경우에만 사용하고 background는 참고 전용이다. 재무는 값·비교 기간·비교 기준·출처를 함께 인용하며 보고 기간을 발표일로, 재사용 재무를 새 촉매로 표현하지 않는다.",
    "earningsQuality가 unknown이면 반복 가능한 이익으로 단정하지 않는다. 재무 개선과 가격 상승의 동시 관측만으로 인과관계를 만들지 않는다.",
    "externalEvidence는 evidenceUse=action만 행동을 바꿀 수 있다. 사건 흡수·반응은 사건 ID·시각, 전후 가격, 벤치마크가 있을 때만 판단하고 누락 창은 unresolved로 둔다.",
    "invalidationCondition은 실제 관측 대상과 변화 방향을 쓰고 next-condition 근거를 연결한다. 입력에 있는 수치형 observable만 followUpConditions로 구조화하며 새 임계값을 만들지 않는다.",
    "시스템이 자동으로 확인한다고 표현할 조건은 반드시 followUpConditions에 구조화한다. 이는 관찰 등록 요청일 뿐 등록 완료가 아니며, 검증된 transitionId가 있으면 도달값·관측시각과 이전 해석을 비교한다.",
    "같은 사실을 summary·evidence·narrativeClaims에 반복하지 않는다. summary는 결론, evidence는 최대 3개 근거, counterEvidence는 최대 2개, nextChecks는 최대 2개다.",
    "changeAnalysis는 직전 판단 이후 실제로 달라진 값·방향·기간·근거·행동만 쓴다. 변화가 없으면 명시하고 상투적인 '추가 확인 필요'만 쓰지 않는다.",
    "currentActionPlan은 지금 할 일과 보류할 일을, nextActionPlan은 다음에 볼 가격·거래량·외국인·기관 매매 흐름·실적·공시·거시 지표와 그 결과의 판단 변화를 쓴다.",
    "사용자 문장은 쉬운 한국어 존댓말 완결문으로 쓴다. 내부 변수명·TypeDB 식별자·구현 용어는 사용자 표시 필드에 노출하지 않고 수급=외국인·기관 매매 흐름, 추세=가격 흐름, 밸류에이션=현재 가격 수준, 펀더멘털=실적과 재무 상태로 풀어 쓴다.",
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
    output_schema: Dict[str, object]
    output_schema_fingerprint: str
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
            "outputSchemaVersion": AI_DECISION_OUTPUT_SCHEMA_VERSION,
            "outputSchemaFingerprint": self.output_schema_fingerprint,
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
        "outputSchema": AI_DECISION_OUTPUT_JSON_SCHEMA,
        "policyFlags": flags,
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    output_schema_fingerprint = hashlib.sha256(
        json.dumps(
            AI_DECISION_OUTPUT_JSON_SCHEMA,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return NotificationAIPromptRelease(
        version=AI_DECISION_PROMPT_VERSION,
        contract_version=AI_DECISION_CONTRACT_VERSION,
        policy_flags=flags,
        instructions=list(BASE_AI_DECISION_INSTRUCTIONS),
        response_schema=dict(AI_DECISION_RESPONSE_SCHEMA),
        output_schema=dict(AI_DECISION_OUTPUT_JSON_SCHEMA),
        output_schema_fingerprint=output_schema_fingerprint,
        fingerprint=fingerprint,
    )
