"""User-facing investment case read model.

The durable DecisionEpisode remains the source of truth.  This module turns an
episode into a stable, compact case that can be read without querying TypeDB or
replaying inference during an HTTP request.
"""

from __future__ import annotations

import ast
import base64
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

    def to_dict(self, compact: bool = False) -> Dict[str, object]:
        stages = [dict(item) for item in self.stages]
        decision = dict(self.decision)
        outcome = dict(self.outcome)
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
                )
            }
            outcome = {
                "state": outcome.get("state"),
                "count": outcome.get("count", 0),
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
        }
        if not compact:
            payload.update({
                "scenarios": [dict(item) for item in self.scenarios],
                "evidence": dict(self.evidence),
                "traceRefs": dict(self.trace_refs),
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
    result = []
    for value in items or []:
        payload = item_dict(value)
        raw = text(value)
        if not payload and raw.startswith("{") and raw.endswith("}"):
            try:
                parsed = ast.literal_eval(raw)
                payload = item_dict(parsed)
            except (SyntaxError, ValueError):
                payload = {}
        if payload:
            label = text(payload.get("label") or payload.get("key"))
            effect = text(payload.get("effect") or payload.get("reason") or payload.get("description"))
            current = ": ".join(item for item in (label, effect) if item)
        else:
            current = raw
        if current and current not in result:
            result.append(current)
        if len(result) >= limit:
            break
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
        "ruleIds": unique_texts(
            list(values(item.get("supportingRuleIds") or item.get("supporting_rule_ids")))
            + list(values(item.get("counterRuleIds") or item.get("counter_rule_ids"))),
            limit=50,
        ),
    }


def _guardrail_rows(episode: Mapping[str, object]) -> List[Dict[str, object]]:
    result = []
    for value in episode.get("decisionGuardrails") or episode.get("decision_guardrails") or []:
        item = item_dict(value)
        if not item:
            continue
        result.append({
            "id": text(item.get("guardrailId") or item.get("guardrail_id")),
            "label": text(item.get("label")) or "확인 조건",
            "reason": text(item.get("reason")),
            "status": text(item.get("status")) or "active",
            "blockedActions": unique_texts(values(item.get("blockedActions") or item.get("blocked_actions"))),
            "requiredChecks": _human_descriptions(values(item.get("requiredChecks") or item.get("required_checks"))),
            "missingData": _human_descriptions(values(item.get("missingData") or item.get("missing_data"))),
        })
    return result


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

    supporting_ids = unique_texts(
        list(values(episode.get("evidenceIds") or episode.get("evidence_ids")))
        + list(selected.get("supportingEvidenceIds") or []),
    )
    counter_ids = unique_texts(
        list(values(episode.get("counterEvidenceIds") or episode.get("counter_evidence_ids")))
        + list(selected.get("counterEvidenceIds") or []),
    )
    missing_data = unique_texts(
        value
        for guardrail in guardrails
        for value in guardrail.get("missingData") or []
    )
    required_checks = unique_texts(
        list(values(episode.get("unresolvedQuestions") or episode.get("unresolved_questions")))
        + [value for guardrail in guardrails for value in guardrail.get("requiredChecks") or []],
        limit=30,
    )

    fact_state = text(flow_stages.get("source", {}).get("state")) or "warning"
    signal_state = _worst_state([
        flow_stages.get("evidence", {}).get("state"),
        flow_stages.get("relation", {}).get("state"),
    ])
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

    readiness_state = _worst_state([fact_state, signal_state, case_state, decision_state])
    blocking = next((item for item in stages[:4] if item.get("state") in {"error", "blocked"}), None)
    if not blocking:
        blocking = next((item for item in stages[:4] if item.get("state") in {"warning", "pending"}), None)
    phase = text((blocking or stages[-1]).get("id"))
    headline = text(episode.get("decisionSummary") or episode.get("decision_summary"))
    if not headline:
        headline = text((blocking or {}).get("detail")) or "판단 근거를 계속 관찰하고 있습니다."

    abstention = item_dict(episode.get("decisionAbstention") or episode.get("decision_abstention"))
    facts_at_decision = item_dict(episode.get("factsAtDecision") or episode.get("facts_at_decision"))
    source_snapshot_id = text(flow.get("sourceAboxSnapshotId"))
    latest_outcome = outcomes[0] if outcomes else {}
    latest_notification = notifications[0] if notifications else {}
    engine_manifest = item_dict(facts_at_decision.get("engineManifest"))
    case_status = "blocked" if readiness_state in {"blocked", "error"} else (
        "review" if readiness_state in {"warning", "pending"} else "active"
    )

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
        next_action=text(flow.get("nextAction")) or "판단과 무효화 조건의 변화를 계속 관찰하세요.",
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
            "action": text(flow.get("action")) or "HOLD",
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
            "requiredChecks": required_checks,
            "sourceSnapshotId": source_snapshot_id,
        },
        trace_refs={
            "flowId": text(flow.get("flowId")),
            "episodeId": text(flow.get("episodeId")),
            "sourceAboxSnapshotId": source_snapshot_id,
            "inferenceGenerationId": text(flow.get("inferenceGenerationId")),
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
            },
        },
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
