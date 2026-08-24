"""Decision evidence boundaries shared by TypeDB inference and the AI judge.

Loaded observations, matched rules, selectable hypotheses, and executable
decisions are different concepts.  This module keeps those states explicit so
an extra reference rule or an overlapping time window cannot strengthen an
investment action by accident.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Set


DECISION_EVIDENCE_CONTRACT_VERSION = "decision-evidence-contract-v1"
TEMPORAL_EVIDENCE_CONTRACT_VERSION = "temporal-evidence-contract-v1"
DECISION_READINESS_CONTRACT_VERSION = "decision-readiness-contract-v1"
MATERIAL_TRANSITION_CONTRACT_VERSION = "material-decision-transition-v1"

EXECUTABLE_ACTIONS = {"BUY", "ADD", "TRIM", "SELL"}
EXPOSURE_INCREASING_ACTIONS = {"BUY", "ADD"}
DECISION_ELIGIBLE_EVIDENCE_STATES = {"supported", "contested"}
READINESS_RANK = {"insufficient": 0, "conditional": 1, "ready": 2}

TEMPORAL_HORIZON_GROUPS = (
    ("intraday", ("15M", "1H")),
    ("session", ("SESSION", "1D")),
    ("swing", ("3D", "5D")),
    ("trend", ("20D", "60D")),
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _items(value: object) -> List[object]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def _unique_texts(values: Iterable[object], limit: int = 100) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        normalized = _text(value)
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
        if len(result) >= max(1, int(limit or 1)):
            break
    return result


def relation_context_from(value: Mapping[str, object]) -> Dict[str, object]:
    context = _mapping(value)
    relation = _mapping(context.get("ontologyRelationContext"))
    return relation or context


def hypothesis_decision_eligibility(candidate: Mapping[str, object]) -> Dict[str, object]:
    """Classify one graph hypothesis without removing it from audit history."""

    item = _mapping(candidate)
    evidence_state = _text(item.get("evidenceState") or item.get("evidence_state")).lower()
    approval_status = _text(item.get("approvalStatus") or item.get("approval_status")).lower()
    verification_status = _text(
        item.get("verificationStatus") or item.get("verification_status")
    ).lower()
    status = _text(item.get("status")).lower()
    reasons: List[str] = []
    knowledge_basis = _mapping(item.get("knowledgeBasis") or item.get("knowledge_basis"))
    knowledge_eligibility = _text(
        knowledge_basis.get("decisionEligibility")
        or knowledge_basis.get("decision_eligibility")
    ).lower()
    knowledge_validation = _text(
        knowledge_basis.get("validationStatus")
        or knowledge_basis.get("validation_status")
    ).lower()
    threshold_origin = _text(
        knowledge_basis.get("thresholdOrigin")
        or knowledge_basis.get("threshold_origin")
    ).lower()
    migration_disposition = _text(
        knowledge_basis.get("migrationDisposition")
        or knowledge_basis.get("migration_disposition")
    ).lower()
    qualification_warnings: List[str] = []
    claim_contract = _mapping(item.get("claimContract") or item.get("claim_contract"))
    qualification = _mapping(item.get("qualification"))
    qualification_status = _text(qualification.get("status")).lower()
    if claim_contract and _text(claim_contract.get("claimType")) != "market-hypothesis":
        reasons.append("claim-boundary:not-a-market-hypothesis")
    if qualification_status == "quarantined":
        reasons.append("hypothesis-qualification:quarantined")
    elif qualification_status in {"shadow", "observed", "limited-active"}:
        qualification_warnings.append("hypothesis-qualification:" + qualification_status)
    if knowledge_validation in {
        "replay-required", "candidate-replay-required", "authored-review-required",
    }:
        qualification_warnings.append("knowledge-validation:" + knowledge_validation)
    if threshold_origin in {"authored-heuristic", "legacy-threshold"}:
        qualification_warnings.append("threshold-origin:" + threshold_origin)
    if migration_disposition in {
        "replace-with-model-signal-rule", "candidate-awaiting-promotion",
    }:
        qualification_warnings.append("signal-migration:" + migration_disposition)
    if knowledge_eligibility in {"guardrail-only", "reference-only"}:
        reasons.append("knowledge-eligibility:" + knowledge_eligibility)
    if knowledge_basis and not bool(
        knowledge_basis.get("requiresHypothesis")
        if "requiresHypothesis" in knowledge_basis
        else knowledge_basis.get("requires_hypothesis")
    ):
        reasons.append("knowledge-boundary:not-a-hypothesis")
    rule_ids = item.get("supportingRuleIds") or item.get("supporting_rule_ids") or []
    if not rule_ids:
        reasons.append("missing-rule-evidence")
    if evidence_state and evidence_state not in DECISION_ELIGIBLE_EVIDENCE_STATES:
        reasons.append("evidence-state:" + evidence_state)
    if approval_status and approval_status not in {"approved-active", "active"}:
        reasons.append("approval-status:" + approval_status)
    if status in {"rejected", "retired", "disabled", "superseded"}:
        reasons.append("hypothesis-status:" + status)
    if verification_status in {"stale", "invalid", "rejected", "superseded"}:
        reasons.append("verification-status:" + verification_status)
    eligible = not reasons
    return {
        "version": DECISION_EVIDENCE_CONTRACT_VERSION,
        "status": "eligible" if eligible else "reference-only",
        "eligible": eligible,
        "evidenceState": evidence_state or "unspecified",
        "reasons": reasons,
        "qualificationState": "conditional" if qualification_warnings else "qualified",
        "qualificationWarnings": qualification_warnings,
        "outcomeQualificationStatus": qualification_status or "not-recorded",
    }


def decision_eligible_hypothesis_payload(candidate: Mapping[str, object]) -> bool:
    return bool(hypothesis_decision_eligibility(candidate).get("eligible"))


def hypothesis_set_evidence_summary(hypothesis_set: Mapping[str, object]) -> Dict[str, object]:
    payload = _mapping(hypothesis_set)
    hypotheses = [
        _mapping(item)
        for item in payload.get("hypotheses") or []
        if isinstance(item, Mapping)
    ]
    eligible = [item for item in hypotheses if decision_eligible_hypothesis_payload(item)]
    reference = [item for item in hypotheses if not decision_eligible_hypothesis_payload(item)]

    def identity(item: Mapping[str, object]) -> str:
        return _text(item.get("hypothesisId") or item.get("hypothesis_id"))

    eligible_families = _unique_texts(
        _mapping(item.get("knowledgeBasis") or item.get("knowledge_basis")).get("evidenceIndependenceKey")
        or _mapping(item.get("knowledgeBasis") or item.get("knowledge_basis")).get("evidence_independence_key")
        or item.get("familyId")
        or item.get("family_id")
        or item.get("causalSignature")
        or item.get("causal_signature")
        or identity(item)
        for item in eligible
    )
    reference_rows = []
    for item in reference:
        assessment = hypothesis_decision_eligibility(item)
        reference_rows.append({
            "hypothesisId": identity(item),
            "templateId": _text(item.get("templateId") or item.get("template_id")),
            "stance": _text(item.get("stance") or "context").lower(),
            "evidenceState": assessment["evidenceState"],
            "status": assessment["status"],
            "reasons": list(assessment["reasons"]),
        })
    return {
        "version": DECISION_EVIDENCE_CONTRACT_VERSION,
        "totalHypothesisCount": len(hypotheses),
        "eligibleHypothesisCount": len(eligible),
        "eligibleFamilyCount": len(eligible_families),
        "referenceHypothesisCount": len(reference),
        "eligibleHypothesisIds": [identity(item) for item in eligible if identity(item)],
        "referenceHypothesisIds": [identity(item) for item in reference if identity(item)],
        "eligibleFamilyIds": eligible_families,
        "qualifiedHypothesisCount": len([
            item for item in eligible
            if hypothesis_decision_eligibility(item).get("qualificationState") == "qualified"
        ]),
        "conditionalHypothesisCount": len([
            item for item in eligible
            if hypothesis_decision_eligibility(item).get("qualificationState") == "conditional"
        ]),
        "qualificationWarnings": _unique_texts(
            warning
            for item in eligible
            for warning in hypothesis_decision_eligibility(item).get("qualificationWarnings") or []
        ),
        "referenceHypotheses": reference_rows,
    }


def _inference_traces(relation: Mapping[str, object]) -> List[Dict[str, object]]:
    graph = _mapping(relation.get("graphStoreInference"))
    native = _mapping(relation.get("typedbInference"))
    rows = graph.get("traces") or native.get("traces") or []
    return [_mapping(item) for item in rows if isinstance(item, Mapping)]


def _action_envelope(relation: Mapping[str, object]) -> Dict[str, object]:
    direct = _mapping(relation.get("actionEnvelope"))
    decision = _mapping(relation.get("decision"))
    return direct or _mapping(decision.get("actionEnvelope"))


def _eligible_rule_ids(relation: Mapping[str, object]) -> Set[str]:
    envelope = _action_envelope(relation)
    readiness = _mapping(envelope.get("dataReadiness"))
    values = [
        *(readiness.get("eligibleRuleIds") or []),
        *(envelope.get("eligibleRuleIds") or []),
    ]
    return {_text(item) for item in values if _text(item)}


def _matched_temporal_windows(
    relation: Mapping[str, object],
    loaded_keys: Set[str],
) -> List[Dict[str, object]]:
    eligible_rule_ids = _eligible_rule_ids(relation)
    selected_rule_id = _text(_action_envelope(relation).get("selectedRuleId"))
    if not eligible_rule_ids and selected_rule_id:
        eligible_rule_ids.add(selected_rule_id)
    matches: List[Dict[str, object]] = []
    seen = set()
    for trace in _inference_traces(relation):
        rule_id = _text(trace.get("ruleId") or trace.get("rule_id"))
        if eligible_rule_ids and rule_id not in eligible_rule_ids:
            continue
        trace_id = _text(trace.get("id") or trace.get("inferenceTraceId"))
        for condition in trace.get("matchedConditions") or []:
            if not isinstance(condition, Mapping):
                continue
            row = _mapping(condition)
            target = _mapping(
                row.get("matchedTargetProperties")
                or row.get("targetProperties")
                or row.get("matched_target_properties")
            )
            relation_type = _text(row.get("relationType") or row.get("relation_type")).upper()
            target_kind = _text(row.get("targetKind") or row.get("target_kind")).lower()
            window_key = _text(target.get("windowKey") or target.get("window_key")).upper()
            if not window_key or window_key not in loaded_keys:
                continue
            if relation_type != "HAS_TEMPORAL_WINDOW" and target_kind != "temporal-window":
                continue
            signature = (rule_id, trace_id, window_key)
            if signature in seen:
                continue
            seen.add(signature)
            matches.append({
                "ruleId": rule_id,
                "traceId": trace_id,
                "windowKey": window_key,
                "conditionId": _text(row.get("conditionId") or row.get("condition_id")),
            })
    return matches


def temporal_evidence_summary(
    temporal_windows: Iterable[Mapping[str, object]],
    context_or_relation: Mapping[str, object],
) -> Dict[str, object]:
    """Separate loaded windows from the windows that matched an eligible rule."""

    windows = [
        _mapping(item)
        for item in temporal_windows or []
        if isinstance(item, Mapping) and _text(item.get("windowKey") or item.get("window_key"))
    ]
    loaded_keys = _unique_texts(
        _text(item.get("windowKey") or item.get("window_key")).upper()
        for item in windows
    )
    loaded_set = set(loaded_keys)
    sufficient_keys = _unique_texts(
        _text(item.get("windowKey") or item.get("window_key")).upper()
        for item in windows
        if bool(item.get("hasSufficientHistory") or item.get("has_sufficient_history"))
    )
    relation = relation_context_from(context_or_relation)
    matched_evidence = _matched_temporal_windows(relation, loaded_set)
    matched_keys = _unique_texts(item.get("windowKey") for item in matched_evidence)
    horizon_groups = []
    for group, keys in TEMPORAL_HORIZON_GROUPS:
        loaded = [key for key in keys if key in loaded_set]
        matched = [key for key in keys if key in set(matched_keys)]
        if loaded or matched:
            horizon_groups.append({
                "group": group,
                "loadedWindowKeys": loaded,
                "matchedWindowKeys": matched,
                "matched": bool(matched),
            })
    return {
        "version": TEMPORAL_EVIDENCE_CONTRACT_VERSION,
        "loadedWindowCount": len(loaded_keys),
        "loadedWindowKeys": loaded_keys,
        "sufficientWindowCount": len(sufficient_keys),
        "sufficientWindowKeys": sufficient_keys,
        "matchedWindowCount": len(matched_keys),
        "matchedWindowKeys": matched_keys,
        "matchedRuleIds": _unique_texts(item.get("ruleId") for item in matched_evidence),
        "matchedEvidence": matched_evidence,
        "horizonGroups": horizon_groups,
        "temporalEvidenceFamilyCount": 1 if matched_keys else 0,
        "interpretation": (
            "loaded-windows-are-coverage; matched-windows-are-rule-evidence"
        ),
    }


def _hypothesis_set_from_relation(relation: Mapping[str, object]) -> Dict[str, object]:
    brain = _mapping(relation.get("investmentBrain"))
    return _mapping(brain.get("hypothesisSet")) or _mapping(relation.get("hypothesisSet"))


def minimum_hypothesis_comparison_count(hypothesis_set: Mapping[str, object]) -> int:
    """Preserve an explicit zero for observation or abstention contracts."""

    payload = _mapping(hypothesis_set)
    raw = payload.get("minimumComparisonCount")
    if raw in (None, ""):
        raw = 0 if payload.get("comparisonRequired") is False else 3
    try:
        value = int(float(str(raw)))
    except (TypeError, ValueError):
        value = 0 if payload.get("comparisonRequired") is False else 3
    return max(0, min(6, value))


def decision_readiness_contract(context_or_relation: Mapping[str, object]) -> Dict[str, object]:
    """Compute the maximum executable readiness from graph-owned evidence."""

    relation = relation_context_from(context_or_relation)
    hypothesis_set = _hypothesis_set_from_relation(relation)
    if not hypothesis_set:
        return {
            "version": DECISION_READINESS_CONTRACT_VERSION,
            "status": "not-evaluated",
            "state": "ready",
            "evaluated": False,
            "reasons": ["current context has no versioned hypothesis set"],
        }
    evidence = hypothesis_set_evidence_summary(hypothesis_set)
    minimum = minimum_hypothesis_comparison_count(hypothesis_set)
    envelope = _action_envelope(relation)
    data_readiness = _mapping(envelope.get("dataReadiness"))
    eligible_rule_ids = _eligible_rule_ids(relation)
    selected_rule_id = _text(envelope.get("selectedRuleId"))
    selected_core_eligible = bool(
        not selected_rule_id or not eligible_rule_ids or selected_rule_id in eligible_rule_ids
    )
    reasons: List[str] = []
    state = "ready"
    if (
        bool(envelope.get("judgementBlocked"))
        or data_readiness.get("usable") is False
        or _text(data_readiness.get("state")).lower() in {"blocked", "unavailable"}
    ):
        state = "insufficient"
        reasons.append("action envelope or required data blocks judgement")
    elif not selected_core_eligible:
        state = "insufficient"
        reasons.append("selected TypeDB core inference is not eligible")
    elif int(evidence.get("eligibleFamilyCount") or 0) < minimum:
        state = "conditional"
        reasons.append(
            "eligible causal families "
            + str(evidence.get("eligibleFamilyCount") or 0)
            + " < required "
            + str(minimum)
        )
    eligible_ids = set(evidence.get("eligibleHypothesisIds") or [])
    eligible_hypotheses = [
        _mapping(item)
        for item in hypothesis_set.get("hypotheses") or []
        if _text(_mapping(item).get("hypothesisId") or _mapping(item).get("hypothesis_id")) in eligible_ids
    ]
    directional_stances = {
        _text(item.get("stance")).lower()
        for item in eligible_hypotheses
        if _text(item.get("stance")).lower() in {"risk", "support"}
    }
    if state == "ready" and bool(hypothesis_set.get("comparisonRequired", True)) and len(directional_stances) < 2:
        state = "conditional"
        reasons.append("eligible hypotheses do not cover both support and risk paths")
    selected_hypothesis_id = _text(
        hypothesis_set.get("selectedHypothesisId")
        or hypothesis_set.get("selected_hypothesis_id")
    )
    selected_path_hypotheses = [
        item for item in eligible_hypotheses
        if (
            selected_hypothesis_id
            and _text(item.get("hypothesisId") or item.get("hypothesis_id")) == selected_hypothesis_id
        ) or (
            not selected_hypothesis_id
            and selected_rule_id
            and selected_rule_id in {
                _text(value)
                for value in _items(
                    item.get("supportingRuleIds") or item.get("supporting_rule_ids")
                )
            }
        )
    ]
    qualification_scope = selected_path_hypotheses or eligible_hypotheses
    selected_path_requires_qualification = bool(qualification_scope) and (
        any(
            hypothesis_decision_eligibility(item).get("qualificationState") == "conditional"
            for item in qualification_scope
        )
        if selected_path_hypotheses
        else all(
            hypothesis_decision_eligibility(item).get("qualificationState") == "conditional"
            for item in qualification_scope
        )
    )
    if state == "ready" and selected_path_requires_qualification:
        state = "conditional"
        reasons.append("selected hypothesis path still requires replay or model-signal qualification")
    return {
        "version": DECISION_READINESS_CONTRACT_VERSION,
        "status": "evaluated",
        "state": state,
        "evaluated": True,
        "minimumEligibleFamilyCount": minimum,
        "eligibleHypothesisCount": evidence.get("eligibleHypothesisCount"),
        "eligibleFamilyCount": evidence.get("eligibleFamilyCount"),
        "referenceHypothesisCount": evidence.get("referenceHypothesisCount"),
        "selectedCoreInferenceEligible": selected_core_eligible,
        "reasons": reasons,
        "evidenceSummary": evidence,
    }


def cap_decision_readiness(ai_state: object, system_state: object) -> str:
    ai = _text(ai_state).lower()
    system = _text(system_state).lower()
    if ai not in READINESS_RANK:
        ai = "conditional"
    if system not in READINESS_RANK:
        return ai
    return ai if READINESS_RANK[ai] <= READINESS_RANK[system] else system


def material_action_transition_contract(
    context: Mapping[str, object],
    proposed_action: object,
) -> Dict[str, object]:
    """Prevent a rebaseline generation from masquerading as new market evidence."""

    payload = _mapping(context)
    previous = _mapping(payload.get("previousInvestmentDecisionEpisode"))
    previous_action = _text(previous.get("action")).upper()
    proposed = _text(proposed_action).upper()
    transition = _mapping(payload.get("decisionTransition")) or _mapping(
        _mapping(payload.get("ontologyRelationDiff")).get("decisionTransition")
    )
    relation = relation_context_from(payload)
    why_now = _mapping(relation.get("whyNow"))
    semantic = _mapping(_mapping(payload.get("ontologyInsight")).get("semanticComponents"))
    material_sources = _unique_texts(
        semantic.get("materialSourceEventKeys")
        or _mapping(payload.get("ontologyInsight")).get("materialSourceEventKeys")
        or payload.get("materialSourceEventKeys")
        or []
    )
    changed_facts = [
        _mapping(item)
        for item in why_now.get("changedFacts") or []
        if isinstance(item, Mapping)
    ]
    factual_deltas = [
        item
        for item in changed_facts
        if item.get("previous") not in (None, "")
        and item.get("current") not in (None, "")
        and item.get("previous") != item.get("current")
    ]
    history_available = bool(previous_action)
    changed = bool(history_available and proposed and proposed != previous_action)
    graph_transition_available = bool(transition)
    graph_material = bool(transition.get("material"))
    source_material = bool(material_sources)
    fact_material = bool(factual_deltas)
    material = bool(graph_material or source_material or fact_material)
    rebaseline = bool(
        changed
        and graph_transition_available
        and _text(transition.get("kind")).lower() == "initial"
        and not material
    )
    allows_change = not changed or not graph_transition_available or material
    return {
        "version": MATERIAL_TRANSITION_CONTRACT_VERSION,
        "evaluated": bool(history_available and graph_transition_available),
        "historyAvailable": history_available,
        "previousAction": previous_action,
        "proposedAction": proposed,
        "actionChanged": changed,
        "material": material,
        "allowsActionChange": allows_change,
        "rebaseline": rebaseline,
        "graphTransitionKind": _text(transition.get("kind")).lower(),
        "materialSourceEventCount": len(material_sources),
        "materialFactDeltaCount": len(factual_deltas),
    }
