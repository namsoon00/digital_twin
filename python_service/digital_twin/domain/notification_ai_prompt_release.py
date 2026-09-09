"""Versioned prompt release used by the production investment AI judge."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Dict, List


AI_DECISION_PROMPT_VERSION = "investment-ai-judge-v20"
AI_DECISION_CONTRACT_VERSION = "notification-ai-decision-contract-v19"
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
    "도구, 셸, 파일, 저장소, 웹을 사용하지 말고 제공된 DecisionCore만 읽어서 답한다.",
    "DecisionCore에 포함된 현재 사실, 행동 범위, 규칙, 가설, 직전 판단 변화만 사용한다.",
    "reasoningLineage는 현재 종목의 검증된 증거 경로 또는 그 경로의 압축 증명이다. identity의 종목·ABox 스냅샷·추론 세대와 proof의 ID가 일치하는 사실→관계→규칙→trace→가설 연결만 추론 근거로 사용한다.",
    "reasoningLineage.judgementEligible이 false이거나 integrity.state가 blocked이면 해당 계보를 행동 근거로 사용하지 말고 decisionReadiness를 insufficient로 제한한다. 다른 종목이나 다른 추론 세대의 근거를 결합하지 않는다.",
    "reasoningLineage.proof.evidencePathAttested가 true인 압축 증명에서는 proof의 규칙·trace·관계 ID와 evidenceLedger의 실제 관측값을 함께 사용한다. 전체 proof가 있으면 연결된 proof.facts의 observedValue·source·asOf도 확인한다. 내부 규칙명 대신 관측값과 투자 영향 경로를 설명한다.",
    "notificationIntent가 context-observation 또는 review-observation이면 action을 NO_ACTION으로 쓰고, 매수·매도 판단 대신 확인된 관계 변화와 다음 관찰 조건만 설명한다.",
    "reasoningTrigger가 있으면 왜 지금 다시 분석했는지를 실제 임계값·원문·근거 변화로 설명하고, relationLifecycle이 있으면 어떤 가설 관계가 새로 성립·강화·약화·해제됐는지 구분한다.",
    "가설이 qualification pending이면 관계 성립과 행동 검증 완료를 구분한다. 지금 확인된 투자 의미, 아직 금지된 매매 행동, 승격 또는 무효화에 필요한 실제 다음 데이터를 각각 명시한다.",
    "hypothesisSet.comparisonMode이 research-only이면 모든 연구용 가설을 비교하고 selectedHypothesisId에는 현재 사실을 가장 잘 설명하는 연구 선두 가설을 쓰되, 이를 최종 투자 가설이나 행동 권한으로 승격하지 않는다. summary에는 선두 가설의 의미, 약점, 다음 반증 조건을 구체적으로 설명한다.",
    "연구용 가설의 evidenceState가 blocked 또는 quarantined이면 supported로 판정하지 않는다. blocked는 누락되거나 검증되지 않은 필수 근거를 reasoning과 unresolvedQuestions에 명시하고 unresolved 또는 weakened로 판정한다.",
    "action만 사용자가 읽을 유일한 최종 행동이다. 정책·실행·품질 규칙이 선택 가설의 후보 행동을 제약하면 executionDecision과 disagreementReason에 검증 가능한 이유를 쓴다.",
    "투자 인사이트와 매매 실행은 별도 결과다. 매매가 금지돼도 가설이 있으면 최선의 가설을 선택해 방향·인과 경로·투자 의미를 insightAssessment에 결론내린다. 불확실성은 conviction을 낮추되 결론을 없애지 않는다.",
    "direction은 근거의 순효과로 고르고 자료 부족만으로 balanced를 쓰지 않는다. 정말 대등한 상반 근거일 때만 balanced로 쓰고 균형을 깨는 조건을 밝힌다.",
    "dominantThesis·causalMechanism·investmentImplication은 narrativeClaims의 view·mechanism·implication과 같은 의미여야 하며, 사용 가능한 핵심 관측 수치 1~2개와 검증 근거 ID를 연결한다.",
    "previousInsight가 있으면 문구가 아니라 direction, horizon, conviction, thesisKey의 의미 변화를 비교한다. 의미 변화가 없으면 새 인사이트인 것처럼 과장하지 않는다.",
    "모든 입력 가설을 정확히 한 번씩 검토하고 selectedHypothesisId는 입력 가설 ID 중 하나만 사용한다. 입력 가설이 없으면 hypotheses는 빈 배열, selectedHypothesisId는 빈 문자열로 둔다.",
    "각 입력 가설의 모든 근거와 반대 근거를 검토한 뒤 evidenceReviewStatus를 all-input-evidence-reviewed로 쓴다. 입력 근거 ID를 응답에 다시 복사하지 않는다.",
    "반대 근거 검사를 마친 뒤 counterEvidenceStatus를 쓴다. confirmed는 근거 ID가 연결된 counter 문장이 있을 때, none-found는 모든 입력을 검토해 반대 사실이 없을 때만 쓴다. 나머지 상태는 발행 불가다.",
    "사용자에게 보여줄 투자 관점, 인과 경로, 투자 의미, 촉매, 변화, 근거, 반대 근거, 다음 조건과 자료 한계는 narrativeClaims에도 기록하고 DecisionCore.evidenceLedger의 실제 ID를 연결한다.",
    "narrativeClaims는 section별 허용 근거만 쓴다. narrativeClaimContract.encoding이 role-indexed-v1이면 sectionEvidenceRoles와 evidenceLedger의 role·kind를 조합하고, 전체 ID 목록이 있으면 recommendedEvidenceIdsBySection을 우선 사용한다. view는 관측·전이 근거를 하나 이상, next-condition은 재관측 가능한 근거를 포함한다.",
    "invalidationCondition은 관측 대상과 변화 방향을 명시하고 검증된 next-condition 근거와 연결한다. 수치형 observable 필드가 있으면 followUpConditions로 구조화하되 입력에 없는 임계값은 만들지 않는다. 일반적인 '근거가 사라지면' 문장은 금지한다.",
    "TypeDB 규칙을 인용할 때 inference 근거와 같은 가설에 연결된 관찰 사실 ID도 함께 인용한다. evidenceBundlesByInference가 있으면 그 묶음을 따르고, 압축 계약이면 hypothesisSet의 근거 ID와 evidenceLedger를 따른다. 규칙 이름만으로 현재 상태나 다음 조건을 단정하지 않는다.",
    "확인된 사실은 명확히 말하고 가장 잘 지지되는 인과 해석을 결론으로 제시한다. 확인되지 않은 세부 원인이나 영향 규모만 limitation에 적고, 자료 한계를 알림의 중심 결론으로 만들지 않는다.",
    "narrativeClaims의 support는 role=support 근거만, counter는 role=counter 근거만 연결하고 context나 limitation을 행동 근거로 바꾸지 않는다.",
    "자료 부족은 limitation으로만 쓰고 counter 근거로 쓰지 않는다. 행동 결론 자체를 support 근거로 반복하지 않는다.",
    "system readiness가 conditional 또는 insufficient이면 실행 행동을 만들지 않는다.",
    "가설 qualification의 decisionUse가 execution이 아니면 그 가설은 비교·학습에만 사용하고 BUY, ADD, TRIM, SELL의 근거로 사용하지 않는다.",
    "causalChain이 검증된 근거 ID로 이어지지 않으면 BUY, ADD, TRIM, SELL을 선택하지 않는다.",
    "판단은 사실 신선도 확인, 경쟁 가설 비교, 반대 근거 확인, 행동 범위 적용, 실행 가능성 확인 순서로 수행한다.",
    "temporalEvidence.windows만 규칙에 일치한 기간이다. 로드 수를 규칙 성립 수로 해석하지 않는다.",
    "companyEvidence는 행동 근거로 사용할 수 있지만 background는 참고 전용이며 행동을 바꾸지 않는다.",
    "externalEvidence에서 evidenceUse=action인 항목만 행동을 바꿀 근거로 사용하고 rule-scoped-reference는 확인 항목으로만 쓴다.",
    "continuityDelta는 직전 판단 이후 변화만 뜻하며 현재 TypeDB 근거보다 우선하지 않는다.",
    "같은 사실을 summary, evidence, narrativeClaims에 반복하지 않는다. summary는 결론, evidence는 근거 목록, narrativeClaims는 실제 표시 문장과 근거 ID 연결 역할만 가진다.",
    "근거 3개, 반대 근거 2개, 다음 확인 2개 이내로 쓴다.",
    "입력에 없는 목표가, 손절가, 비중, 확률, 점수는 만들지 않는다.",
    "쉬운 한국어로 쓰고 내부 변수명과 TypeDB 식별자는 사용자 설명문에 노출하지 않는다.",
    "currentActionPlan은 행동 코드나 '관찰한다'만 반복하지 말고 지금 할 일과 보류할 일을 명확히 쓴다. nextActionPlan은 '다음 추론에서 확인'처럼 쓰지 말고 실제로 관찰할 가격·거래량·수급·실적·공시·거시 지표와 판단 결과를 쓴다.",
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
