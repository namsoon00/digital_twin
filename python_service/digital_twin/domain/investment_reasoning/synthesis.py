"""Normalize TypeDB decision alternatives without reimplementing RuleBox policy."""

from __future__ import annotations

import hashlib
from typing import Dict, Iterable, Mapping, Tuple

from ..context_observation_notifications import typedb_context_observation_contract
from ..decision_evidence_contract import hypothesis_decision_eligibility
from .contracts import ActionAlternative, DecisionSynthesis


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _texts(values: object, uppercase: bool = False) -> Tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if values is None or isinstance(values, Mapping):
        return ()
    result = []
    try:
        candidates = list(values)
    except TypeError:
        candidates = []
    for value in candidates:
        if isinstance(value, Mapping):
            text = str(value.get("label") or value.get("key") or value.get("id") or "").strip()
        else:
            text = str(value or "").strip()
        if uppercase:
            text = text.upper()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _stable_id(*parts: object) -> str:
    material = "|".join(str(part or "").strip() for part in parts)
    return "decision-synthesis:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _hypotheses(relation_context: Mapping[str, object]) -> Tuple[Dict[str, object], ...]:
    relation = _mapping(relation_context)
    brain = _mapping(relation.get("investmentBrain"))
    hypothesis_set = _mapping(relation.get("hypothesisSet")) or _mapping(brain.get("hypothesisSet"))
    return tuple(
        dict(item)
        for item in hypothesis_set.get("hypotheses") or []
        if isinstance(item, Mapping)
        and str(item.get("hypothesisId") or item.get("hypothesis_id") or "").strip()
    )


def decision_synthesis_from_relation_context(
    account_id: str,
    relation_context: Mapping[str, object],
) -> DecisionSynthesis:
    """Create a deterministic AI handoff from materialized graph output only."""

    relation = _mapping(relation_context)
    subject = _mapping(relation.get("subject"))
    decision = _mapping(relation.get("decision"))
    envelope = _mapping(relation.get("actionEnvelope")) or _mapping(decision.get("actionEnvelope"))
    assessments = _mapping(relation.get("assessmentBundle"))
    opinion_assessment = _mapping(assessments.get("investmentOpinion"))
    portfolio_assessment = _mapping(assessments.get("portfolioFit"))
    execution_assessment = _mapping(assessments.get("executionReadiness"))
    quality_assessment = _mapping(assessments.get("evidenceQuality"))
    recommended_plan = _mapping(assessments.get("recommendedPlan"))
    graph = _mapping(relation.get("graphStoreInference"))
    hypotheses = _hypotheses(relation)
    investment_view_action = str(
        opinion_assessment.get("candidateAction")
        or envelope.get("investmentViewAction")
        or ""
    ).upper().strip()
    graph_candidate_action = investment_view_action or "NO_ACTION"
    execution_action = str(
        envelope.get("executionAction")
        or envelope.get("preferredAction")
        or "NO_ACTION"
    ).upper().strip()
    selected_rule_id = str(
        (
            opinion_assessment.get("selectedRuleId")
            if assessments else envelope.get("selectedRuleId")
        )
        or ""
    )
    context_observation = typedb_context_observation_contract(relation)
    selected_decision_effect = str(
        envelope.get("selectedDecisionEffect")
        or opinion_assessment.get("decisionEffect")
        or decision.get("decisionEffect")
        or ("support" if opinion_assessment.get("status") == "supported" else "")
        or ""
    ).lower().strip()
    decision_disposition = str(envelope.get("decisionDisposition") or "").lower().strip()
    action_authority = (
        "originate"
        if selected_decision_effect == "support"
        and bool(envelope.get("investmentJudgementAvailable", True))
        else "modify"
        if selected_decision_effect in {"constrain", "defer", "block"}
        else "observe"
    )
    if context_observation:
        graph_candidate_action = "NO_ACTION"
        investment_view_action = ""
        execution_action = "NO_ACTION"
    actions_by_rule: Dict[str, list] = {}
    for row in [
        *[item for item in graph.get("relations") or [] if isinstance(item, Mapping)],
        *[item for item in relation.get("activeRules") or [] if isinstance(item, Mapping)],
        *[item for item in relation.get("matchedRules") or [] if isinstance(item, Mapping)],
    ]:
        rule_id = str(row.get("ruleId") or row.get("rule_id") or "").strip()
        candidate_action = str(
            row.get("candidateAction") or row.get("candidate_action") or ""
        ).upper().strip()
        if rule_id and candidate_action:
            actions_by_rule.setdefault(rule_id, [])
            if candidate_action not in actions_by_rule[rule_id]:
                actions_by_rule[rule_id].append(candidate_action)
    hypothesis_paths = []
    eligible_ids = []
    reference_ids = []
    for hypothesis in hypotheses:
        hypothesis_id = str(hypothesis.get("hypothesisId") or hypothesis.get("hypothesis_id") or "").strip()
        supporting_rule_ids = _texts(
            hypothesis.get("supportingRuleIds") or hypothesis.get("supporting_rule_ids")
        )
        explicit_action = str(
            hypothesis.get("candidateAction") or hypothesis.get("candidate_action") or ""
        ).upper().strip()
        actions = [explicit_action] if explicit_action else []
        for rule_id in supporting_rule_ids:
            for candidate_action in actions_by_rule.get(rule_id, []):
                if candidate_action not in actions:
                    actions.append(candidate_action)
        if not actions and selected_rule_id in supporting_rule_ids and graph_candidate_action:
            actions.append(graph_candidate_action)
        if not actions:
            actions.append("UNSPECIFIED")
        assessment = hypothesis_decision_eligibility(hypothesis)
        for action in actions:
            hypothesis_paths.append((action, hypothesis, bool(assessment.get("eligible"))))
        (eligible_ids if assessment.get("eligible") else reference_ids).append(hypothesis_id)

    selected_path_eligible = any(
        eligible
        and selected_rule_id
        and selected_rule_id in {
            str(value or "").strip()
            for value in (
                hypothesis.get("supportingRuleIds")
                or hypothesis.get("supporting_rule_ids")
                or []
            )
        }
        for _action, hypothesis, eligible in hypothesis_paths
    )
    if action_authority == "originate" and not selected_path_eligible:
        action_authority = "observe"
        graph_candidate_action = "NO_ACTION"
        execution_action = "NO_ACTION"

    alternatives = []
    for action, hypothesis, eligible in sorted(
        hypothesis_paths,
        key=lambda item: (str(item[0]), str(item[1].get("hypothesisId") or item[1].get("hypothesis_id") or "")),
    ):
        supporting_evidence_ids = _texts(
            hypothesis.get("supportingEvidenceIds") or hypothesis.get("supporting_evidence_ids")
        )
        counter_evidence_ids = _texts(
            hypothesis.get("counterEvidenceIds") or hypothesis.get("counter_evidence_ids")
        )
        evidence_conflicts = tuple(
            evidence_id for evidence_id in supporting_evidence_ids
            if evidence_id in set(counter_evidence_ids)
        )
        alternatives.append(ActionAlternative(
            action=action,
            hypothesis_ids=_texts([hypothesis.get("hypothesisId") or hypothesis.get("hypothesis_id")]),
            supporting_rule_ids=_texts(hypothesis.get("supportingRuleIds") or hypothesis.get("supporting_rule_ids")),
            supporting_evidence_ids=supporting_evidence_ids,
            counter_evidence_ids=counter_evidence_ids,
            evidence_conflict_ids=evidence_conflicts,
            invalidation_conditions=_texts(hypothesis.get("invalidationConditions") or hypothesis.get("invalidation_conditions")),
            decision_eligible=eligible and not evidence_conflicts,
        ))

    symbol = str(subject.get("symbol") or relation.get("symbol") or "").upper().strip()
    source_abox_snapshot_id = str(
        relation.get("sourceAboxSnapshotId") or graph.get("sourceAboxSnapshotId") or ""
    )
    inference_generation_id = str(
        relation.get("inferenceGenerationId") or graph.get("inferenceGenerationId") or ""
    )
    traces = [item for item in graph.get("traces") or [] if isinstance(item, Mapping)]
    allowed_actions = _texts(
        relation.get("allowedActions")
        or decision.get("allowedActions")
        or envelope.get("allowedActions"),
        uppercase=True,
    )
    blocked_actions = _texts(
        relation.get("blockedActions")
        or decision.get("blockedActions")
        or envelope.get("blockedActions"),
        uppercase=True,
    )
    overlap = tuple(action for action in allowed_actions if action in set(blocked_actions))
    allowed_actions = tuple(action for action in allowed_actions if action not in set(blocked_actions))
    candidate_contract_conflict = bool(
        investment_view_action
        and (
            investment_view_action in set(blocked_actions)
            or (allowed_actions and investment_view_action not in set(allowed_actions))
        )
    )
    graph_trace_complete = bool(
        source_abox_snapshot_id
        and inference_generation_id
        and relation.get("generationAligned")
        and traces
    )
    quality_blocked = bool(quality_assessment.get("judgementBlocked"))
    no_eligible_thesis = not eligible_ids
    selected_action_conflict = candidate_contract_conflict
    judgement_blocked = bool(
        envelope.get("judgementBlocked")
        or quality_blocked
        or selected_action_conflict
        or bool(investment_view_action and not selected_path_eligible)
    )
    return DecisionSynthesis(
        synthesis_id=_stable_id(
            account_id,
            symbol,
            source_abox_snapshot_id,
            inference_generation_id,
            graph_candidate_action,
        ),
        account_id=str(account_id or relation.get("accountId") or ""),
        symbol=symbol,
        source_abox_snapshot_id=source_abox_snapshot_id,
        inference_generation_id=inference_generation_id,
        graph_candidate_action=graph_candidate_action,
        investment_view_action=investment_view_action,
        execution_action=execution_action,
        execution_disposition=str(
            envelope.get("executionDisposition")
            or recommended_plan.get("status")
            or "judgement-blocked"
        ),
        decision_effect=selected_decision_effect,
        decision_disposition=decision_disposition,
        action_authority=action_authority,
        allowed_actions=() if context_observation else allowed_actions,
        blocked_actions=() if context_observation else blocked_actions,
        alternatives=tuple(alternatives),
        eligible_hypothesis_ids=_texts(eligible_ids),
        reference_hypothesis_ids=_texts(reference_ids),
        selected_rule_id=selected_rule_id,
        portfolio_constraint_rule_ids=_texts(
            envelope.get("portfolioConstraintRuleIds") or portfolio_assessment.get("ruleIds")
        ),
        execution_constraint_rule_ids=_texts(
            envelope.get("executionConstraintRuleIds") or execution_assessment.get("ruleIds")
        ),
        data_quality_rule_ids=_texts(
            envelope.get("dataQualityRuleIds") or quality_assessment.get("ruleIds")
        ),
        review_level=str(relation.get("reviewLevel") or decision.get("reviewLevel") or ""),
        data_state=str(relation.get("dataState") or decision.get("dataState") or ""),
        change_state=str(relation.get("changeState") or ""),
        conflict_state=(
            "action-envelope-conflict"
            if candidate_contract_conflict
            else "action-constraints-applied"
            if overlap
            else str(relation.get("conflictState") or "")
        ),
        missing_data=_texts(relation.get("missingData")),
        next_checks=_texts(decision.get("nextChecks") or envelope.get("nextChecks")),
        reversal_conditions=_texts(
            decision.get("weakenConditions") or envelope.get("invalidationConditions")
        ),
        judgement_blocked=judgement_blocked,
        graph_trace_complete=graph_trace_complete,
        evidence_state="INVALID" if quality_blocked else ("VERIFIED" if graph_trace_complete else "PARTIAL"),
        hypothesis_state="NO_ELIGIBLE_THESIS" if no_eligible_thesis else ("ELIGIBLE" if eligible_ids else "REFERENCE_ONLY"),
        action_state="CONFLICTED" if selected_action_conflict else ("SELECTED" if investment_view_action else "NOT_APPLICABLE"),
        ai_state="BLOCKED" if judgement_blocked else ("INTERPRETATION_READY" if no_eligible_thesis else "JUDGEMENT_READY"),
    )


def synthesis_index(values: Iterable[DecisionSynthesis]) -> Dict[str, DecisionSynthesis]:
    return {item.synthesis_id: item for item in values or () if item.synthesis_id}
