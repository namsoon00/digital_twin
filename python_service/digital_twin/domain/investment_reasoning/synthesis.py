"""Normalize TypeDB decision alternatives without reimplementing RuleBox policy."""

from __future__ import annotations

import hashlib
from typing import Dict, Iterable, Mapping, Tuple

from ..context_observation_notifications import typedb_context_observation_contract
from ..decision_evidence_contract import hypothesis_decision_eligibility
from .contracts import ActionAlternative, DataGap, DecisionSynthesis


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


def decision_data_gaps_from_relation_context(
    relation_context: Mapping[str, object],
    selected_rule_id: str = "",
) -> Tuple[DataGap, ...]:
    """Return only gaps with an explicit decision/runtime requirement.

    Optional enrichment remains available in the operational source payload,
    but it must not weaken an unrelated rule, hypothesis, or user message.
    """
    relation = _mapping(relation_context)
    facts = _mapping(relation.get("facts"))
    availability = _mapping(facts.get("dataAvailability"))
    availability_by_code = {
        "tradeStrength": _mapping(availability.get("tradeStrength")),
        "executionVolume": _mapping(availability.get("executionVolume")),
        "investorFlow": _mapping(availability.get("investorFlow")),
    }
    rows = []
    for raw in relation.get("missingData") or facts.get("missingData") or []:
        item = dict(raw) if isinstance(raw, Mapping) else {
            "key": str(raw or "missing"),
            "label": str(raw or ""),
        }
        code = str(item.get("key") or item.get("code") or "missing").strip()
        provider = availability_by_code.get(code, {})
        required_by_rule_ids = _texts(item.get("requiredByRuleIds"))
        selected_rule_requires_gap = bool(
            selected_rule_id
            and (
                selected_rule_id in set(required_by_rule_ids)
                or (
                    code == "valuationInputs"
                    and selected_rule_id.startswith("graph.valuation.")
                )
            )
        )
        core_required = code in {"currentPrice", "ontologyInference", "position"}
        raw_status = str(item.get("status") or provider.get("status") or "missing").lower()
        expected_at = str(provider.get("nextProviderUpdateAt") or "")
        provider_update_code = str(provider.get("providerUpdateCode") or "").lower()
        if code == "valuationInputs":
            state = "unvalidated-model-input"
        elif raw_status in {"error", "failed", "unavailable"}:
            state = "failed"
        elif raw_status in {"unsupported", "not-supported", "provider-unsupported"}:
            state = "unsupported"
        elif raw_status in {"stale", "unknown", "latency"}:
            state = "stale"
        elif expected_at or provider_update_code in {
            "before-first-publication",
            "outside-publication-window",
            "scheduled-later",
        }:
            state = "not-yet-published"
        elif raw_status in {"session-unavailable", "market-closed"}:
            state = "session-unavailable"
        else:
            state = "missing"
        if selected_rule_requires_gap or core_required:
            requirement_class = "rule-required"
        elif required_by_rule_ids:
            requirement_class = "unselected-rule-input"
        elif state == "not-yet-published":
            requirement_class = "scheduled-source"
        elif state == "session-unavailable":
            requirement_class = "session-bound-source"
        elif state == "unsupported":
            requirement_class = "provider-unsupported"
        else:
            requirement_class = "optional-context"
        # Optional enrichment must not make a valid decision look incomplete.
        # It remains available in the source/operations views and re-enters
        # this contract as soon as an exact matched rule declares it required.
        if requirement_class in {"optional-context", "unselected-rule-input"}:
            continue
        blocking = bool(
            item.get("blocking")
            or core_required
            or (state == "failed" and requirement_class == "rule-required")
        )
        details = {
            key: provider.get(key)
            for key in (
                "providerUpdateSlot",
                "providerUpdateCode",
                "latencyStatus",
                "latencyReason",
                "freshnessStatus",
                "sourceAsOf",
                "fetchedAt",
            )
            if provider.get(key) not in (None, "")
        }
        details.update({
            "requirementClass": requirement_class,
            "actionability": (
                "resolve-before-decision"
                if blocking
                else "wait-for-provider-window"
                if requirement_class in {"scheduled-source", "session-bound-source"}
                else "not-required-for-current-decision"
                if requirement_class == "provider-unsupported"
                else "improves-current-rule-confidence"
            ),
        })
        decision_impact = (
            "blocking"
            if blocking
            else "not-applicable"
            if requirement_class == "provider-unsupported"
            else "advisory"
        )
        rows.append(DataGap(
            code=code,
            label=str(item.get("label") or code),
            state=state,
            effect=str(item.get("effect") or provider.get("reason") or ""),
            source=str(item.get("source") or provider.get("source") or ""),
            expected_at=expected_at,
            blocking=blocking,
            decision_impact=decision_impact,
            required_by_rule_ids=required_by_rule_ids,
            details=details,
        ))
    unique = {}
    for item in rows:
        unique[(item.code, item.state, item.source)] = item
    return tuple(unique[key] for key in sorted(unique))


def _disposition_contract(
    *,
    context_observation: Mapping[str, object],
    judgement_blocked: bool,
    hypotheses: Tuple[Dict[str, object], ...],
    eligible_ids: Iterable[str],
    execution_eligible_ids: Iterable[str],
    selected_rule_id: str,
    investment_view_action: str,
    comparison_required: bool,
    data_gaps: Tuple[DataGap, ...],
) -> Tuple[str, str, str]:
    if context_observation:
        return "CONTEXT_OBSERVATION", "context-observation", "context-only"
    if judgement_blocked and any(item.blocking and item.state == "failed" for item in data_gaps):
        return "DATA_SOURCE_FAILURE", "data-source-failure", "blocked"
    if not hypotheses and (selected_rule_id or investment_view_action):
        return "RULE_COVERAGE_GAP_CANDIDATE", "hypothesis-materialization-gap", "candidate-gap"
    if judgement_blocked:
        return "JUDGEMENT_BLOCKED", "judgement-blocked", "blocked"
    if comparison_required:
        return "HYPOTHESIS_COMPARISON_REQUIRED", "comparison-required", "covered"
    if execution_eligible_ids:
        return "ACTIONABLE_DECISION", "action-ready", "covered"
    if eligible_ids:
        return (
            "HYPOTHESIS_QUALIFICATION_PENDING",
            "hypothesis-qualification-required",
            "covered",
        )
    if hypotheses:
        return "HYPOTHESIS_RESEARCH_ONLY", "hypothesis-research-only", "research-only"
    if any(item.state == "not-yet-published" for item in data_gaps):
        return "WAITING_FOR_SCHEDULED_SOURCE", "waiting-for-scheduled-source", "data-wait"
    return (
        "NO_MATERIAL_PREDICTIVE_RULE_MATCH",
        "no-material-predictive-rule-match",
        "no-material-match",
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
        opinion_assessment.get("selectedRuleId")
        or envelope.get("selectedRuleId")
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
    comparison_required = bool(
        opinion_assessment.get("actionConflict")
        and len(_texts(opinion_assessment.get("candidateActions"), uppercase=True)) > 1
    )
    if comparison_required:
        selected_decision_effect = "support"
    action_authority = (
        "originate"
        if (selected_decision_effect == "support" or comparison_required)
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
    execution_eligible_ids = []
    reference_ids = []
    qualification_reasons = []
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
            hypothesis_paths.append((
                action,
                hypothesis,
                bool(assessment.get("eligible")),
                bool(assessment.get("executionEligible")),
            ))
        for warning in assessment.get("qualificationWarnings") or []:
            if warning not in qualification_reasons:
                qualification_reasons.append(str(warning))
        (eligible_ids if assessment.get("eligible") else reference_ids).append(hypothesis_id)
        if assessment.get("executionEligible"):
            execution_eligible_ids.append(hypothesis_id)

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
        for _action, hypothesis, eligible, _execution_eligible in hypothesis_paths
    )
    eligible_comparison_paths = {
        action
        for action, _hypothesis, eligible, _execution_eligible in hypothesis_paths
        if eligible and action not in {"", "UNSPECIFIED", "NO_ACTION"}
    }
    selected_path_execution_eligible = any(
        execution_eligible
        and selected_rule_id
        and selected_rule_id in {
            str(value or "").strip()
            for value in (
                hypothesis.get("supportingRuleIds")
                or hypothesis.get("supporting_rule_ids")
                or []
            )
        }
        for _action, hypothesis, _eligible, execution_eligible in hypothesis_paths
    )
    if not selected_rule_id and comparison_required:
        selected_path_execution_eligible = any(
            execution_eligible
            for _action, _hypothesis, _eligible, execution_eligible in hypothesis_paths
        )
    if (
        action_authority == "originate"
        and not selected_path_eligible
        and not (comparison_required and len(eligible_comparison_paths) > 1)
    ):
        action_authority = "observe"
        graph_candidate_action = "NO_ACTION"
        execution_action = "NO_ACTION"

    alternatives = []
    for action, hypothesis, eligible, execution_eligible in sorted(
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
            execution_eligible=execution_eligible and not evidence_conflicts,
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
    execution_qualified = bool(
        selected_path_execution_eligible
        or graph_candidate_action not in {"BUY", "ADD", "TRIM", "SELL"}
    )
    if not execution_qualified and execution_action in {"BUY", "ADD", "TRIM", "SELL"}:
        execution_action = "NO_ACTION"
    qualification_state = (
        "active"
        if selected_path_execution_eligible
        else "conditional"
        if eligible_ids
        else "reference-only"
    )
    data_gaps = decision_data_gaps_from_relation_context(
        relation,
        selected_rule_id=selected_rule_id,
    )
    disposition_code, execution_disposition, rule_coverage_state = _disposition_contract(
        context_observation=context_observation,
        judgement_blocked=judgement_blocked,
        hypotheses=hypotheses,
        eligible_ids=eligible_ids,
        execution_eligible_ids=execution_eligible_ids,
        selected_rule_id=selected_rule_id,
        investment_view_action=investment_view_action,
        comparison_required=comparison_required,
        data_gaps=data_gaps,
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
        execution_disposition=execution_disposition,
        decision_effect=selected_decision_effect or ("support" if comparison_required else ""),
        decision_disposition=decision_disposition,
        action_authority=action_authority,
        allowed_actions=() if context_observation else allowed_actions,
        blocked_actions=() if context_observation else blocked_actions,
        alternatives=tuple(alternatives),
        eligible_hypothesis_ids=_texts(eligible_ids),
        execution_eligible_hypothesis_ids=_texts(execution_eligible_ids),
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
        missing_data=tuple(
            item.label
            for item in data_gaps
            if item.decision_impact != "not-applicable"
        ),
        data_gaps=data_gaps,
        disposition_code=disposition_code,
        rule_coverage_state=rule_coverage_state,
        next_checks=_texts(decision.get("nextChecks") or envelope.get("nextChecks")),
        reversal_conditions=_texts(
            decision.get("weakenConditions") or envelope.get("invalidationConditions")
        ),
        execution_qualified=execution_qualified,
        hypothesis_qualification_state=qualification_state,
        hypothesis_qualification_reasons=_texts(qualification_reasons),
        judgement_blocked=judgement_blocked,
        graph_trace_complete=graph_trace_complete,
        evidence_state="INVALID" if quality_blocked else ("VERIFIED" if graph_trace_complete else "PARTIAL"),
        hypothesis_state="NO_ELIGIBLE_THESIS" if no_eligible_thesis else ("ELIGIBLE" if eligible_ids else "REFERENCE_ONLY"),
        action_state=(
            "COMPARISON_REQUIRED"
            if comparison_required
            else "CONFLICTED"
            if selected_action_conflict
            else "SELECTED"
            if investment_view_action
            else "NOT_APPLICABLE"
        ),
        ai_state="BLOCKED" if judgement_blocked else ("INTERPRETATION_READY" if no_eligible_thesis else "JUDGEMENT_READY"),
    )


def synthesis_index(values: Iterable[DecisionSynthesis]) -> Dict[str, DecisionSynthesis]:
    return {item.synthesis_id: item for item in values or () if item.synthesis_id}
