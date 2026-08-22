"""User-facing investment case read model.

The durable DecisionEpisode remains the source of truth.  This module turns an
episode into a stable, compact case that can be read without querying TypeDB or
replaying inference during an HTTP request.
"""

from __future__ import annotations

import ast
import base64
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional

from .investment_flow import (
    FLOW_STATE_LABELS,
    FLOW_STATE_RANK,
    decision_flow_projection,
    hypothesis_id,
    item_dict,
    text,
    unique_texts,
    values,
)
from .investment_reasoning_detail import reasoning_detail_from_episode


INVESTMENT_CASE_VERSION = "investment-case-v2"

CASE_STAGES = (
    ("fact", "확인된 사실"),
    ("signal", "핵심 신호"),
    ("case", "투자 케이스"),
    ("decision", "현재 의견"),
    ("outcome", "결과 추적"),
)

CASE_STAGE_LABELS = dict(CASE_STAGES)


@dataclass(frozen=True)
class InvestmentCaseSnapshot:
    """Stable read-side contract for one account and instrument."""

    case_id: str
    episode_id: str
    account_id: str
    symbol: str
    name: str
    status: str
    phase: str
    phase_label: str
    readiness_state: str
    readiness_label: str
    headline: str
    next_action: str
    decided_at: str
    updated_at: str
    stages: List[Dict[str, object]] = field(default_factory=list)
    facts: Dict[str, object] = field(default_factory=dict)
    signals: Dict[str, object] = field(default_factory=dict)
    scenarios: List[Dict[str, object]] = field(default_factory=list)
    decision: Dict[str, object] = field(default_factory=dict)
    outcome: Dict[str, object] = field(default_factory=dict)
    evidence: Dict[str, object] = field(default_factory=dict)
    trace_refs: Dict[str, object] = field(default_factory=dict)
    status_dimensions: List[Dict[str, object]] = field(default_factory=list)
    explanation: Dict[str, object] = field(default_factory=dict)
    reasoning: Dict[str, object] = field(default_factory=dict)

    def to_dict(self, compact: bool = False) -> Dict[str, object]:
        stages = [dict(item) for item in self.stages]
        decision = dict(self.decision)
        outcome = dict(self.outcome)
        explanation = dict(self.explanation)
        if compact:
            stages = [{
                **item,
                "detail": (
                    f"비교 시나리오 {item.get('scenarioCount', 0)}개"
                    if item.get("id") == "case"
                    else f"{item.get('action', decision.get('action', 'HOLD'))} 판단"
                    if item.get("id") == "decision"
                    else item.get("detail", "")
                ),
            } for item in stages]
            decision = {
                key: decision.get(key)
                for key in (
                    "action",
                    "reviewLevel",
                    "dataState",
                    "validationState",
                    "validationLabel",
                    "state",
                    "stateLabel",
                    "reasonCode",
                )
            }
            outcome = {
                "state": outcome.get("state"),
                "count": outcome.get("count", 0),
            }
            explanation = {
                "primaryCause": dict(explanation.get("primaryCause") or {}),
                "supportingCauses": [dict(item) for item in (explanation.get("supportingCauses") or [])[:2]],
                "counterCauses": [dict(item) for item in (explanation.get("counterCauses") or [])[:2]],
                "constraints": [dict(item) for item in (explanation.get("constraints") or [])[:2]],
                "dataGaps": [dict(item) for item in (explanation.get("dataGaps") or [])[:2]],
                "changeConditions": list(explanation.get("changeConditions") or [])[:2],
                "comparison": dict(explanation.get("comparison") or {}),
            }
        payload = {
            "version": INVESTMENT_CASE_VERSION,
            "caseId": self.case_id,
            "episodeId": self.episode_id,
            "accountId": self.account_id,
            "symbol": self.symbol,
            "name": self.name,
            "status": self.status,
            "caseStatus": self.status,
            "phase": self.phase,
            "phaseLabel": self.phase_label,
            "readinessState": self.readiness_state,
            "readinessLabel": self.readiness_label,
            "headline": self.headline,
            "nextAction": self.next_action,
            "decidedAt": self.decided_at,
            "updatedAt": self.updated_at,
            "stages": stages,
            "facts": dict(self.facts),
            "signals": dict(self.signals),
            "decision": decision,
            "outcome": outcome,
            "statusDimensions": [dict(item) for item in self.status_dimensions],
            "explanation": explanation,
        }
        if not compact:
            payload.update({
                "scenarios": [dict(item) for item in self.scenarios],
                "evidence": dict(self.evidence),
                "traceRefs": dict(self.trace_refs),
                "reasoning": dict(self.reasoning),
            })
        return payload


def investment_case_id(account_id: object, symbol: object) -> str:
    """Return an ID that remains stable when a new decision episode is stored."""

    def encode(value: str) -> str:
        return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")

    return "case:" + encode(text(account_id) or "default") + "." + encode(text(symbol).upper())


def parse_investment_case_id(case_id: object) -> Optional[Dict[str, str]]:
    value = text(case_id)
    if not value.startswith("case:") or "." not in value:
        return None
    account_part, symbol_part = value[5:].split(".", 1)

    def decode(encoded: str) -> str:
        padding = "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")

    try:
        account_id = decode(account_part)
        symbol = decode(symbol_part).upper()
    except (UnicodeDecodeError, UnicodeEncodeError, ValueError):
        return None
    if not account_id or not symbol:
        return None
    return {"accountId": account_id, "symbol": symbol}


def _worst_state(states: Iterable[object], default: str = "warning") -> str:
    clean = [text(value) for value in states if text(value) in FLOW_STATE_RANK]
    return max(clean, key=lambda value: FLOW_STATE_RANK[value]) if clean else default


def _stage(stage_id: str, state: str, detail: str, **extra) -> Dict[str, object]:
    normalized = state if state in FLOW_STATE_LABELS else "warning"
    return {
        "id": stage_id,
        "label": CASE_STAGE_LABELS[stage_id],
        "state": normalized,
        "stateLabel": FLOW_STATE_LABELS[normalized],
        "detail": text(detail),
        **extra,
    }


def _hypotheses(episode: Mapping[str, object]) -> List[Dict[str, object]]:
    hypothesis_set = item_dict(episode.get("hypothesisSet") or episode.get("hypothesis_set"))
    return [item_dict(item) for item in hypothesis_set.get("hypotheses") or [] if item_dict(item)]


def _human_descriptions(items: Iterable[object], limit: int = 20) -> List[str]:
    return [item["text"] for item in _structured_descriptions(items, limit=limit)]


def _humanize_embedded_payloads(value: object) -> str:
    """Replace embedded legacy dict reprs with their user-facing label and effect."""

    raw = text(value)
    for candidate in re.findall(r"\{[^{}]+\}", raw):
        try:
            payload = item_dict(ast.literal_eval(candidate))
        except (SyntaxError, ValueError):
            payload = {}
        if not payload:
            continue
        label = text(payload.get("label") or payload.get("key"))
        detail = text(payload.get("effect") or payload.get("reason") or payload.get("detail"))
        replacement = ": ".join(item for item in (label, detail) if item) or "확인 자료"
        raw = raw.replace(candidate, replacement)
    return re.sub(r"\s+", " ", raw).strip()


def _humanize_change_condition(value: object) -> str:
    raw = _humanize_embedded_payloads(value)
    if raw.startswith("TypeDB 조건 ") and "다음 추론 세대" in raw:
        return "현재 관계 규칙이 다음 추론에서도 유지되는지, 반대 근거가 더 강해지는지 확인합니다."
    return raw


def _structured_descriptions(items: Iterable[object], limit: int = 20) -> List[Dict[str, object]]:
    """Normalize legacy strings and structured gaps without leaking Python reprs."""

    result: List[Dict[str, object]] = []
    for value in items or []:
        raw = text(value)
        payloads = [item_dict(value)] if item_dict(value) else []
        if not payloads and raw:
            candidates = [raw] if raw.startswith("{") and raw.endswith("}") else re.findall(r"\{[^{}]+\}", raw)
            for candidate in candidates:
                try:
                    parsed = ast.literal_eval(candidate)
                except (SyntaxError, ValueError):
                    continue
                payload = item_dict(parsed)
                if payload:
                    payloads.append(payload)
        if not payloads and raw:
            payloads = [{"description": raw}]
        for payload in payloads:
            label = text(payload.get("label") or payload.get("key"))
            detail = text(
                payload.get("effect")
                or payload.get("reason")
                or payload.get("detail")
                or payload.get("description")
            )
            current = ": ".join(item for item in (label, detail) if item)
            if not current:
                continue
            status = text(payload.get("status")) or "unknown"
            lower = (current + " " + status).lower()
            applicability = "not-applicable" if (
                status in {"not-applicable", "not_applicable", "market-closed"}
                or "휴장" in current
                or "해당 시장에서 제공" in current
            ) else "applicable"
            row = {
                "label": label or "확인 항목",
                "detail": detail or current,
                "text": current,
                "status": status,
                "source": text(payload.get("source")),
                "applicability": applicability,
                "reasonCode": text(payload.get("reasonCode") or payload.get("reason_code")),
            }
            if row["text"] not in [item["text"] for item in result]:
                result.append(row)
            if len(result) >= limit:
                return result
    return result


def _scenario(item: Mapping[str, object], selected_id: str) -> Dict[str, object]:
    current_id = hypothesis_id(item)
    support_ids = unique_texts(values(item.get("supportingEvidenceIds") or item.get("supporting_evidence_ids")))
    counter_ids = unique_texts(values(item.get("counterEvidenceIds") or item.get("counter_evidence_ids")))
    assumptions = unique_texts(values(item.get("assumptions")), limit=20)
    invalidations = unique_texts(
        values(item.get("invalidationConditions") or item.get("invalidation_conditions")),
        limit=20,
    )
    knowledge_basis = item_dict(item.get("knowledgeBasis") or item.get("knowledge_basis"))
    supporting_rule_ids = unique_texts(values(item.get("supportingRuleIds") or item.get("supporting_rule_ids")), limit=50)
    counter_rule_ids = unique_texts(values(item.get("counterRuleIds") or item.get("counter_rule_ids")), limit=50)
    return {
        "id": current_id,
        "title": text(item.get("templateLabel") or item.get("template_label")) or "검토 시나리오",
        "claim": text(item.get("claim")) or "설명 문장이 아직 저장되지 않았습니다.",
        "stance": text(item.get("stance")) or "uncertain",
        "horizon": text(item.get("horizon")) or "multi-horizon",
        "state": text(item.get("evidenceState") or item.get("evidence_state")) or "unresolved",
        "stateLabel": text(item.get("evidenceStateLabel") or item.get("evidence_state_label")) or "확인 중",
        "verificationStatus": text(item.get("verificationStatus") or item.get("verification_status")),
        "selected": bool(current_id and current_id == selected_id),
        "supportCount": len(support_ids),
        "counterCount": len(counter_ids),
        "supportingEvidenceIds": support_ids,
        "counterEvidenceIds": counter_ids,
        "assumptions": assumptions,
        "invalidationConditions": invalidations,
        "supportingRuleIds": supporting_rule_ids,
        "counterRuleIds": counter_rule_ids,
        "ruleIds": unique_texts(
            supporting_rule_ids + counter_rule_ids,
            limit=50,
        ),
        "relationIds": unique_texts(values(item.get("causalPathIds") or item.get("causal_path_ids")), limit=50),
        "candidateAction": text(item.get("candidateAction") or item.get("candidate_action")),
        "allowedActions": unique_texts(values(item.get("allowedActions") or item.get("allowed_actions"))),
        "blockedActions": unique_texts(values(item.get("blockedActions") or item.get("blocked_actions"))),
        "decisionEligibility": text(item.get("decisionEligibility") or item.get("decision_eligibility")),
        "decisionEligibilityReasons": _human_descriptions(
            values(item.get("decisionEligibilityReasons") or item.get("decision_eligibility_reasons")),
        ),
        "predictionTarget": text(item.get("predictionTarget") or item.get("prediction_target")),
        "expectedOutcome": text(item.get("expectedOutcome") or item.get("expected_outcome")),
        "outcomeMetric": text(item.get("outcomeMetric") or item.get("outcome_metric")),
        "marketConditionIds": unique_texts(values(item.get("marketConditionIds") or item.get("market_condition_ids")), limit=50),
        "marketRelationTypes": unique_texts(values(item.get("marketRelationTypes") or item.get("market_relation_types")), limit=50),
        "accountConditionIds": unique_texts(values(item.get("accountConditionIds") or item.get("account_condition_ids")), limit=50),
        "accountFields": unique_texts(values(item.get("accountFields") or item.get("account_fields")), limit=50),
        "accountRelationTypes": unique_texts(values(item.get("accountRelationTypes") or item.get("account_relation_types")), limit=50),
        "accountTargetKinds": unique_texts(values(item.get("accountTargetKinds") or item.get("account_target_kinds")), limit=50),
        "targetRoles": unique_texts(values(item.get("targetRoles") or item.get("target_roles")), limit=50),
        "actionPolicies": unique_texts(values(item.get("actionPolicies") or item.get("action_policies")), limit=50),
        "knowledgeBasis": knowledge_basis,
        "plainLanguageBasis": text(knowledge_basis.get("plainLanguageBasis") or knowledge_basis.get("plain_language_basis")),
    }


def _guardrail_rows(episode: Mapping[str, object]) -> List[Dict[str, object]]:
    result = []
    for value in episode.get("decisionGuardrails") or episode.get("decision_guardrails") or []:
        item = item_dict(value)
        if not item:
            continue
        missing_rows = _structured_descriptions(values(item.get("missingData") or item.get("missing_data")))
        result.append({
            "id": text(item.get("guardrailId") or item.get("guardrail_id")),
            "label": text(item.get("label")) or "확인 조건",
            "reason": _humanize_embedded_payloads(item.get("reason")),
            "status": text(item.get("status")) or "active",
            "blockedActions": unique_texts(values(item.get("blockedActions") or item.get("blocked_actions"))),
            "requiredChecks": _human_descriptions(values(item.get("requiredChecks") or item.get("required_checks"))),
            "missingData": [row["text"] for row in missing_rows],
            "missingDataItems": missing_rows,
            "source": text(item.get("source")),
            "sourceRuleIds": unique_texts(values(item.get("sourceRuleIds") or item.get("source_rule_ids")), limit=50),
            "knowledgeBasis": item_dict(item.get("knowledgeBasis") or item.get("knowledge_basis")),
        })
    return result


def _dimension(
    dimension_id: str,
    label: str,
    state: str,
    state_label: str,
    reason_code: str,
    reason: str,
    effect: str,
) -> Dict[str, object]:
    return {
        "id": dimension_id,
        "label": label,
        "state": state,
        "stateLabel": state_label,
        "reasonCode": reason_code,
        "reason": text(reason),
        "effect": effect,
    }


def _status_dimensions(
    *,
    action: str,
    data_state: str,
    validation_state: str,
    source_snapshot_id: str,
    inference_generation_id: str,
    relation_count: int,
    selected_id: str,
    abstention: Mapping[str, object],
    outcome_count: int,
) -> List[Dict[str, object]]:
    abstained = bool(abstention)
    if abstained:
        decision = _dimension(
            "decision",
            "판단 상태",
            "blocked",
            "판단 유보",
            "AI_HYPOTHESIS_COMPARISON_INCOMPLETE",
            text(abstention.get("reason")) or "AI가 현재 가설을 모두 비교하지 못했습니다.",
            "매수·매도 행동을 확정하지 않습니다.",
        )
    elif action:
        decision = _dimension(
            "decision", "판단 상태", "pass", "판단 가능", "DECISION_COMPLETED",
            "현재 추론 세대에서 투자 의견이 저장되었습니다.", "현재 행동 의견을 사용할 수 있습니다.",
        )
    else:
        decision = _dimension(
            "decision", "판단 상태", "warning", "추가 관찰", "DECISION_NOT_FINAL",
            "최종 투자 의견이 아직 저장되지 않았습니다.", "관찰은 가능하지만 행동 판단은 확정하지 않습니다.",
        )

    normalized_data = data_state.lower()
    if source_snapshot_id and normalized_data == "sufficient":
        data = _dimension(
            "data", "자료 상태", "pass", "자료 충분", "DATA_SUFFICIENT",
            "판단 시점의 원천 자료가 고정되어 있습니다.", "현재 판단의 사실 입력으로 사용했습니다.",
        )
    elif source_snapshot_id:
        data = _dimension(
            "data", "자료 상태", "warning", "일부 자료만 사용", "DATA_PARTIAL",
            "판단 시점 자료는 저장됐지만 일부 확인 항목이 남아 있습니다.", "확인된 자료만 사용하고 판단 강도를 제한합니다.",
        )
    else:
        data = _dimension(
            "data", "자료 상태", "blocked", "원천 자료 없음", "SOURCE_SNAPSHOT_MISSING",
            "판단 시점의 원천 자료를 연결하지 못했습니다.", "투자 행동 판단을 사용할 수 없습니다.",
        )

    if inference_generation_id and relation_count:
        inference = _dimension(
            "inference", "추론 상태", "pass", "추론 완료", "INFERENCE_COMPLETE",
            f"같은 추론 세대에서 {relation_count}개 관계 경로를 확인했습니다.", "규칙과 가설의 근거로 사용했습니다.",
        )
    elif inference_generation_id:
        inference = _dimension(
            "inference", "추론 상태", "warning", "관계 확인 필요", "INFERENCE_WITHOUT_RELATION",
            "추론 세대는 있지만 사용자에게 설명할 관계 경로가 없습니다.", "행동 근거로 사용하지 않습니다.",
        )
    else:
        inference = _dimension(
            "inference", "추론 상태", "blocked", "추론 없음", "INFERENCE_GENERATION_MISSING",
            "TypeDB 추론 세대가 연결되지 않았습니다.", "투자 행동 판단을 사용할 수 없습니다.",
        )

    if abstained:
        ai = _dimension(
            "ai", "AI 상태", "blocked", "비교 미완료", "AI_COMPARISON_INCOMPLETE",
            text(abstention.get("reason")) or "AI 가설 비교가 완료되지 않았습니다.", "TypeDB 관계는 보존하지만 AI 최종 의견은 사용하지 않습니다.",
        )
    elif selected_id:
        ai = _dimension(
            "ai", "AI 상태", "pass", "검증 완료", "AI_VALIDATED",
            "AI가 경쟁 가설을 비교하고 선택 가설을 저장했습니다.", "TypeDB 행동 범위 안에서 최종 의견을 작성했습니다.",
        )
    else:
        ai = _dimension(
            "ai", "AI 상태", "warning", "조건부 사용", "AI_SELECTION_NOT_RECORDED",
            "선택 가설이 명시적으로 저장되지 않았습니다.", "AI 문장은 참고하되 선택 근거를 재확인합니다.",
        )

    outcome = _dimension(
        "outcome",
        "결과 추적",
        "pass" if outcome_count else "pending",
        "관측 완료" if outcome_count else "관측 대기",
        "OUTCOME_RECORDED" if outcome_count else "OUTCOME_PENDING",
        f"판단 이후 결과 {outcome_count}건을 관측했습니다." if outcome_count else "판단 이후 성과를 아직 관측 중입니다.",
        "과거 판단의 성과 검증에 사용합니다." if outcome_count else "현재 투자 의견을 차단하지 않습니다.",
    )
    if validation_state in {"blocked", "error"} and decision["state"] == "pass":
        decision = {**decision, "state": "warning", "stateLabel": "조건부 판단", "reasonCode": "DECISION_VALIDATION_LIMITED"}
    return [decision, data, inference, ai, outcome]


def _case_explanation(
    *,
    action: str,
    headline: str,
    scenarios: List[Dict[str, object]],
    selected_id: str,
    guardrails: List[Dict[str, object]],
    missing_data_items: List[Dict[str, object]],
    abstention: Mapping[str, object],
    source_snapshot_id: str,
    inference_generation_id: str,
    reasoning_detail: Mapping[str, object],
) -> Dict[str, object]:
    selected = next((item for item in scenarios if item.get("selected")), {})
    candidate_scope = scenarios
    detail_facts = [item_dict(item) for item in reasoning_detail.get("facts") or [] if item_dict(item)]
    detail_relations = [item_dict(item) for item in reasoning_detail.get("relations") or [] if item_dict(item)]
    detail_rules = [item_dict(item) for item in reasoning_detail.get("rules") or [] if item_dict(item)]
    detail_traces = [item_dict(item) for item in reasoning_detail.get("traces") or [] if item_dict(item)]
    detail_hypotheses = [item_dict(item) for item in reasoning_detail.get("hypotheses") or [] if item_dict(item)]
    primary = {
        "id": "decision-primary",
        "layer": "ai" if abstention else "hypothesis" if selected else "decision",
        "role": "constraint" if abstention else "support",
        "status": "confirmed" if not abstention else "incomplete",
        "title": "AI 가설 비교 미완료" if abstention else text(selected.get("title")) or "현재 판단",
        "summary": text(abstention.get("reason")) if abstention else text(selected.get("claim")) or headline,
        "effect": "행동 판단을 유보합니다." if abstention else f"{action or '현재'} 의견의 핵심 설명입니다.",
        "reasonCode": "AI_HYPOTHESIS_COMPARISON_INCOMPLETE" if abstention else "SELECTED_HYPOTHESIS",
    }
    supporting = []
    counter = []
    paths = []
    for index, scenario in enumerate(candidate_scope[:6]):
        scenario_id = text(scenario.get("id"))
        scenario_rule_ids = set(scenario.get("ruleIds") or [])
        scenario_path_ids = set(scenario.get("relationIds") or [])
        scenario_evidence_ids = set(
            list(scenario.get("supportingEvidenceIds") or [])
            + list(scenario.get("counterEvidenceIds") or [])
        )
        rule_items = [item for item in detail_rules if text(item.get("id")) in scenario_rule_ids]
        trace_items = [
            item for item in detail_traces
            if text(item.get("id")) in scenario_path_ids or text(item.get("ruleId")) in scenario_rule_ids
        ]
        relation_items = [
            item for item in detail_relations
            if text(item.get("ruleId")) in scenario_rule_ids
            or text(item.get("id")) in scenario_path_ids
            or text(item.get("id")) in scenario_evidence_ids
        ]
        fact_items = [
            item for item in detail_facts
            if scenario_rule_ids.intersection(set(item.get("ruleIds") or []))
            or scenario_path_ids.intersection(set(item.get("traceIds") or []))
        ]
        if not fact_items and len(candidate_scope) == 1:
            fact_items = detail_facts
        hypothesis_items = [item for item in detail_hypotheses if text(item.get("id")) == scenario_id]
        if not hypothesis_items:
            hypothesis_items = [scenario]
        support_count = int(scenario.get("supportCount") or 0)
        counter_count = int(scenario.get("counterCount") or 0)
        row = {
            "id": text(scenario.get("id")) or f"scenario:{index}",
            "layer": "hypothesis",
            "role": "support",
            "status": "confirmed" if support_count else "conditional",
            "title": text(scenario.get("title")) or "투자 가설",
            "summary": text(scenario.get("plainLanguageBasis")) or text(scenario.get("claim")),
            "effect": f"지지 {support_count}건 · 반박 {counter_count}건",
            "reasonCode": "TYPE_DB_HYPOTHESIS",
        }
        if support_count or scenario.get("selected"):
            supporting.append(row)
        if counter_count:
            counter.append({
                **row,
                "id": row["id"] + ":counter",
                "role": "counter",
                "title": row["title"] + "의 반대 근거",
                "effect": f"반박 근거 {counter_count}건이 판단 강도를 낮춥니다.",
                "reasonCode": "COUNTER_EVIDENCE_PRESENT",
            })
        paths.append({
            "id": row["id"] + ":path",
            "title": row["title"],
            "selected": bool(scenario.get("selected")),
            "eligibility": text(scenario.get("decisionEligibility")) or "unknown",
            "nodes": [
                {
                    "layer": "fact",
                    "label": "판단 시점 사실",
                    "refIds": [text(item.get("id")) for item in fact_items] or ([source_snapshot_id] if source_snapshot_id else []),
                    "items": fact_items,
                },
                {
                    "layer": "relation",
                    "label": "TypeDB 관계",
                    "refIds": [text(item.get("id")) for item in relation_items] or list(scenario.get("relationIds") or []),
                    "items": relation_items,
                },
                {
                    "layer": "rule",
                    "label": "성립 규칙",
                    "refIds": [text(item.get("id")) for item in rule_items] or list(scenario.get("ruleIds") or []),
                    "items": rule_items,
                    "traces": trace_items,
                },
                {
                    "layer": "hypothesis",
                    "label": text(scenario.get("title")) or "투자 가설",
                    "refIds": [scenario_id] if scenario_id else [],
                    "items": hypothesis_items,
                },
                {
                    "layer": "decision",
                    "label": action or "판단 유보",
                    "refIds": [],
                    "items": [{
                        "id": "decision-current",
                        "label": action or "판단 유보",
                        "reason": headline,
                        "selectedHypothesisId": selected_id,
                        "abstained": bool(abstention),
                    }],
                },
            ],
            "inferenceGenerationId": inference_generation_id,
        })

    constraints = []
    for guardrail in guardrails[:8]:
        blocked_actions = list(guardrail.get("blockedActions") or [])
        constraints.append({
            "id": text(guardrail.get("id")),
            "layer": "guardrail",
            "role": "constraint",
            "status": text(guardrail.get("status")) or "active",
            "title": text(guardrail.get("label")) or "판단 안전 제한",
            "summary": text(guardrail.get("reason")),
            "effect": ("차단 행동: " + ", ".join(blocked_actions)) if blocked_actions else "판단 강도와 다음 확인 조건에 반영합니다.",
            "reasonCode": "ACTION_GUARDRAIL" if blocked_actions else "DECISION_GUARDRAIL",
        })

    change_conditions = unique_texts(
        _humanize_change_condition(condition)
        for scenario in scenarios
        for condition in scenario.get("invalidationConditions") or []
    )
    type_db_actions = unique_texts(
        scenario.get("candidateAction")
        for scenario in scenarios
        if text(scenario.get("candidateAction"))
    )
    return {
        "primaryCause": primary,
        "supportingCauses": supporting[:5],
        "counterCauses": counter[:5],
        "constraints": constraints,
        "dataGaps": missing_data_items,
        "changeConditions": change_conditions[:8],
        "causalPaths": paths,
        "comparison": {
            "typeDbCandidateActions": type_db_actions,
            "aiFinalAction": action,
            "selectedHypothesisId": selected_id,
            "different": bool(type_db_actions and action not in type_db_actions),
            "reason": text(abstention.get("reason")) if abstention else headline,
        },
    }


def _outcomes(episode: Mapping[str, object]) -> List[Dict[str, object]]:
    rows = [item_dict(value) for value in episode.get("outcomes") or [] if item_dict(value)]
    rows.sort(
        key=lambda item: text(item.get("observedAt") or item.get("observed_at")),
        reverse=True,
    )
    return [{
        "id": text(item.get("outcomeId") or item.get("outcome_id")),
        "observedAt": text(item.get("observedAt") or item.get("observed_at")),
        "price": item.get("price"),
        "profitLossRate": item.get("profitLossRate") if "profitLossRate" in item else item.get("profit_loss_rate"),
        "priceChangeFromDecisionPct": item.get("priceChangeFromDecisionPct") if "priceChangeFromDecisionPct" in item else item.get("price_change_from_decision_pct"),
        "selectedHypothesisStatus": text(item.get("selectedHypothesisStatus") or item.get("selected_hypothesis_status")),
        "contradictedEvidenceIds": unique_texts(values(item.get("contradictedEvidenceIds") or item.get("contradicted_evidence_ids"))),
    } for item in rows]


def investment_case_snapshot(
    episode_value: object,
    jobs: Optional[Iterable[object]] = None,
) -> InvestmentCaseSnapshot:
    """Project one persisted episode into the canonical user-facing case."""

    episode = item_dict(episode_value)
    flow = decision_flow_projection(episode, jobs or [])
    flow_stages = {text(item.get("id")): item_dict(item) for item in flow.get("stages") or []}
    assurance = item_dict(flow.get("assurance"))
    hypotheses = _hypotheses(episode)
    selected_id = text(episode.get("selectedHypothesisId") or episode.get("selected_hypothesis_id"))
    scenarios = [_scenario(item, selected_id) for item in hypotheses]
    selected = next((item for item in scenarios if item.get("selected")), {})
    guardrails = _guardrail_rows(episode)
    outcomes = _outcomes(episode)
    notifications = list(flow.get("notifications") or [])

    evidence_scenarios = [selected] if selected else scenarios
    supporting_ids = unique_texts(
        list(values(episode.get("evidenceIds") or episode.get("evidence_ids")))
        + [
            evidence_id
            for scenario in evidence_scenarios
            for evidence_id in scenario.get("supportingEvidenceIds") or []
        ],
    )
    counter_ids = unique_texts(
        list(values(episode.get("counterEvidenceIds") or episode.get("counter_evidence_ids")))
        + [
            evidence_id
            for scenario in evidence_scenarios
            for evidence_id in scenario.get("counterEvidenceIds") or []
        ],
    )
    missing_data_items = _structured_descriptions(
        value
        for guardrail in guardrails
        for value in guardrail.get("missingDataItems") or guardrail.get("missingData") or []
    )
    missing_data = [item["text"] for item in missing_data_items]
    required_checks = unique_texts(
        _human_descriptions(values(episode.get("unresolvedQuestions") or episode.get("unresolved_questions")))
        + [value for guardrail in guardrails for value in guardrail.get("requiredChecks") or []],
        limit=30,
    )

    fact_state = text(flow_stages.get("source", {}).get("state")) or "warning"
    relation_count = len(flow.get("relationIds") or [])
    signal_state = "pass" if supporting_ids or counter_ids or relation_count else (
        "warning" if text(flow.get("inferenceGenerationId")) else "blocked"
    )
    case_state = _worst_state([
        flow_stages.get("hypothesis", {}).get("state"),
        flow_stages.get("inference", {}).get("state"),
        assurance.get("state"),
    ])
    decision_state = text(flow_stages.get("decision", {}).get("state")) or "warning"
    outcome_state = "pass" if outcomes else "pending"

    stages = [
        _stage(
            "fact",
            fact_state,
            "판단 시점 데이터가 저장되었습니다." if fact_state == "pass" else "최신 원천 데이터 연결이 필요합니다.",
            count=max(1, len(item_dict(episode.get("factsAtDecision") or episode.get("facts_at_decision")))) if fact_state == "pass" else 0,
        ),
        _stage(
            "signal",
            signal_state,
            f"지지 {len(supporting_ids)}개 · 반박 {len(counter_ids)}개 · 관계 {len(flow.get('relationIds') or [])}개",
            supportCount=len(supporting_ids),
            counterCount=len(counter_ids),
            relationCount=len(flow.get("relationIds") or []),
        ),
        _stage(
            "case",
            case_state,
            text(selected.get("claim")) or (f"비교 시나리오 {len(scenarios)}개" if scenarios else "비교할 투자 시나리오가 없습니다."),
            scenarioCount=len(scenarios),
            selectedScenarioId=selected_id,
        ),
        _stage(
            "decision",
            decision_state,
            text(episode.get("decisionSummary") or episode.get("decision_summary")) or text(flow.get("action")) or "판단 보류",
            action=text(flow.get("action")) or "HOLD",
        ),
        _stage(
            "outcome",
            outcome_state,
            f"관측 결과 {len(outcomes)}건" if outcomes else "다음 관측 결과를 기다리는 중입니다.",
            outcomeCount=len(outcomes),
        ),
    ]

    abstention = item_dict(episode.get("decisionAbstention") or episode.get("decision_abstention"))
    facts_at_decision = item_dict(episode.get("factsAtDecision") or episode.get("facts_at_decision"))
    source_snapshot_id = text(flow.get("sourceAboxSnapshotId"))
    inference_generation_id = text(flow.get("inferenceGenerationId"))
    action = text(flow.get("action")) or "HOLD"
    dimensions = _status_dimensions(
        action=action,
        data_state=text(flow.get("dataState")),
        validation_state=text(flow.get("validationState")),
        source_snapshot_id=source_snapshot_id,
        inference_generation_id=inference_generation_id,
        relation_count=relation_count,
        selected_id=selected_id,
        abstention=abstention,
        outcome_count=len(outcomes),
    )
    dimension_by_id = {item["id"]: item for item in dimensions}
    decision_dimension = dimension_by_id["decision"]
    inference_dimension = dimension_by_id["inference"]
    data_dimension = dimension_by_id["data"]
    if decision_dimension["state"] == "blocked":
        readiness_state = "blocked"
        phase = "decision"
    elif inference_dimension["state"] == "blocked" or data_dimension["state"] == "blocked":
        readiness_state = "blocked"
        phase = "case" if inference_dimension["state"] == "blocked" else "fact"
    elif any(item["state"] in {"warning", "pending"} for item in dimensions[:4]):
        readiness_state = "warning"
        phase = "case" if dimension_by_id["ai"]["state"] == "warning" else "signal"
    else:
        readiness_state = "pass"
        phase = "outcome"
    headline = text(episode.get("decisionSummary") or episode.get("decision_summary"))
    if not headline:
        headline = text(decision_dimension.get("reason")) or "판단 근거를 계속 관찰하고 있습니다."

    latest_outcome = outcomes[0] if outcomes else {}
    latest_notification = notifications[0] if notifications else {}
    engine_manifest = item_dict(facts_at_decision.get("engineManifest"))
    reasoning_detail = reasoning_detail_from_episode(episode, scenarios, guardrails)
    case_status = "blocked" if readiness_state in {"blocked", "error"} else (
        "review" if readiness_state in {"warning", "pending"} else "active"
    )

    explanation = _case_explanation(
        action=action,
        headline=headline,
        scenarios=scenarios,
        selected_id=selected_id,
        guardrails=guardrails,
        missing_data_items=missing_data_items,
        abstention=abstention,
        source_snapshot_id=source_snapshot_id,
        inference_generation_id=inference_generation_id,
        reasoning_detail=reasoning_detail,
    )
    change_conditions = list(explanation.get("changeConditions") or [])
    if abstention:
        next_action = "시스템이 비교하지 못한 가설을 다시 검증한 뒤 AI 판단을 자동 갱신해야 합니다."
    elif change_conditions:
        next_action = change_conditions[0]
    elif required_checks:
        next_action = required_checks[0]
    else:
        next_action = text(flow.get("nextAction")) or "판단과 무효화 조건의 변화를 계속 관찰하세요."

    return InvestmentCaseSnapshot(
        case_id=investment_case_id(flow.get("accountId"), flow.get("symbol")),
        episode_id=text(flow.get("episodeId")),
        account_id=text(flow.get("accountId")) or "default",
        symbol=text(flow.get("symbol")).upper(),
        name=text(flow.get("name")) or text(flow.get("symbol")).upper(),
        status=case_status,
        phase=phase,
        phase_label=CASE_STAGE_LABELS.get(phase, "결과 추적"),
        readiness_state=readiness_state,
        readiness_label=FLOW_STATE_LABELS.get(readiness_state, "확인 필요"),
        headline=headline,
        next_action=next_action,
        decided_at=text(flow.get("decidedAt")),
        updated_at=text(flow.get("updatedAt")),
        stages=stages,
        facts={
            "state": fact_state,
            "dataState": text(flow.get("dataState")),
            "sourceSnapshotId": source_snapshot_id,
            "storedFieldCount": len(facts_at_decision),
            "summary": stages[0]["detail"],
        },
        signals={
            "state": signal_state,
            "supportCount": len(supporting_ids),
            "counterCount": len(counter_ids),
            "relationCount": len(flow.get("relationIds") or []),
            "summary": stages[1]["detail"],
        },
        scenarios=scenarios,
        decision={
            "action": action,
            "state": text(decision_dimension.get("state")),
            "stateLabel": text(decision_dimension.get("stateLabel")),
            "reasonCode": text(decision_dimension.get("reasonCode")),
            "reviewLevel": text(flow.get("reviewLevel")),
            "dataState": text(flow.get("dataState")),
            "validationState": text(flow.get("validationState")),
            "validationLabel": text(flow.get("validationLabel")),
            "assuranceState": text(assurance.get("state")),
            "assuranceLabel": text(assurance.get("label")),
            "reason": headline,
            "investmentView": text(episode.get("investmentView") or episode.get("investment_view")),
            "executionDecision": text(episode.get("executionDecision") or episode.get("execution_decision")),
            "abstained": bool(abstention),
            "abstention": abstention,
            "requiredChecks": required_checks,
            "guardrails": guardrails,
        },
        outcome={
            "state": outcome_state,
            "count": len(outcomes),
            "latest": latest_outcome,
            "items": outcomes,
            "notificationCount": len(notifications),
            "latestNotification": latest_notification,
            "delivery": dict(flow.get("delivery") or {}),
        },
        evidence={
            "supportCount": len(supporting_ids),
            "counterCount": len(counter_ids),
            "missingCount": len(missing_data),
            "supportingIds": supporting_ids,
            "counterIds": counter_ids,
            "missingData": missing_data,
            "missingDataItems": missing_data_items,
            "requiredChecks": required_checks,
            "sourceSnapshotId": source_snapshot_id,
            "scope": "selected-hypothesis" if selected else "all-candidate-hypotheses",
        },
        trace_refs={
            "flowId": text(flow.get("flowId")),
            "episodeId": text(flow.get("episodeId")),
            "sourceAboxSnapshotId": source_snapshot_id,
            "inferenceGenerationId": inference_generation_id,
            "selectedHypothesisId": selected_id,
            "ruleIds": list(flow.get("ruleIds") or []),
            "relationIds": list(flow.get("relationIds") or []),
            "notificationIds": [text(item.get("jobId")) for item in notifications if text(item.get("jobId"))],
            "modelRelease": {
                "deploymentId": text(engine_manifest.get("deploymentId")),
                "releaseFingerprint": text(engine_manifest.get("releaseFingerprint")),
                "reasoningEngineVersion": text(engine_manifest.get("reasoningEngineVersion")),
                "tboxFingerprint": text(engine_manifest.get("tboxFingerprint")),
                "ruleboxFingerprint": text(engine_manifest.get("ruleboxFingerprint")),
                "promptVersion": text(engine_manifest.get("promptVersion")),
                "modelVersion": text(engine_manifest.get("modelVersion")),
                "decisionContractVersion": text(engine_manifest.get("decisionContractVersion")),
                "lineageState": "complete" if (
                    text(engine_manifest.get("deploymentId"))
                    and text(engine_manifest.get("releaseFingerprint"))
                ) else "partial",
                "lineageLabel": "판단 당시 모델 확인 완료" if (
                    text(engine_manifest.get("deploymentId"))
                    and text(engine_manifest.get("releaseFingerprint"))
                ) else "판단 당시 모델 식별자 일부 미저장",
            },
        },
        status_dimensions=dimensions,
        explanation=explanation,
        reasoning=reasoning_detail,
    )


def investment_case_history_item(
    snapshot: InvestmentCaseSnapshot,
    previous: Optional[InvestmentCaseSnapshot] = None,
) -> Dict[str, object]:
    current = snapshot.to_dict(compact=True)
    prior_action = text((previous.decision if previous else {}).get("action"))
    current_action = text(snapshot.decision.get("action"))
    prior_validation = text((previous.decision if previous else {}).get("validationState"))
    current_validation = text(snapshot.decision.get("validationState"))
    return {
        "caseId": snapshot.case_id,
        "episodeId": snapshot.episode_id,
        "decidedAt": snapshot.decided_at,
        "updatedAt": snapshot.updated_at,
        "action": current_action,
        "readinessState": snapshot.readiness_state,
        "readinessLabel": snapshot.readiness_label,
        "summary": snapshot.headline,
        "phase": snapshot.phase,
        "phaseLabel": snapshot.phase_label,
        "outcome": current.get("outcome") or {},
        "change": {
            "hasPrevious": previous is not None,
            "actionChanged": bool(previous and current_action != prior_action),
            "previousAction": prior_action,
            "currentAction": current_action,
            "validationChanged": bool(previous and current_validation != prior_validation),
            "previousValidationState": prior_validation,
            "currentValidationState": current_validation,
        },
    }
