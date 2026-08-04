"""Stable delivery identities for graph-backed investment notifications.

The identity deliberately excludes price, P&L, timestamps, and inference
generation ids.  Those values change often and must not defeat the cooldown
for an otherwise unchanged TypeDB relationship state.  It includes only
semantic relationship evidence that can make a repeated notification newly
meaningful.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Dict, Iterable, List, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


RELATION_DELIVERY_FINGERPRINT_VERSION = "ontology-relation-delivery-v2"
VOLATILE_EVENT_SUFFIX = re.compile(r":[+-]?\d+(?:\.\d+)?%?$")
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _normalized(value: object) -> str:
    return _text(value).casefold()


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _items(value: object) -> List[object]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def _first(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _event_key(value: object) -> str:
    return VOLATILE_EVENT_SUFFIX.sub("", _normalized(value))


def _canonical_evidence_url(value: object) -> str:
    """Remove transport-only URL noise while preserving document identity."""

    raw = _text(value)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return _event_key(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return _event_key(raw)
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_QUERY_KEYS and not key.casefold().startswith("utm_")
    ]
    return _event_key(urlunsplit((
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        parsed.path.rstrip("/"),
        urlencode(query, doseq=True),
        "",
    )))


def _rule_rows(value: object) -> List[Dict[str, str]]:
    rows = []
    for item in _items(value):
        row = _mapping(item)
        if not row or row.get("referenceOnly") or row.get("reference_only"):
            continue
        rule_id = _first(row, "ruleId", "rule_id", "id", "sourceRuleId")
        if not rule_id:
            continue
        rows.append({
            "ruleId": _normalized(rule_id),
            "decisionStage": _normalized(_first(row, "decisionStage", "decision_stage")),
            "actionGroup": _normalized(_first(row, "actionGroup", "action_group")),
        })
    return sorted(rows, key=lambda item: (item["ruleId"], item["decisionStage"], item["actionGroup"]))


def _relation_rows(value: object) -> List[Dict[str, str]]:
    rows = []
    for item in _items(value):
        row = _mapping(item)
        relation_type = _first(row, "relationType", "type", "derivedRelationType")
        rule_id = _first(row, "ruleId", "sourceRuleId", "rule_id")
        if relation_type or rule_id:
            rows.append({
                "relationType": _normalized(relation_type),
                "ruleId": _normalized(rule_id),
            })
    return sorted(rows, key=lambda item: (item["relationType"], item["ruleId"]))


def _trace_rows(value: object) -> List[Dict[str, str]]:
    rows = []
    for item in _items(value):
        row = _mapping(item)
        rule_id = _first(row, "ruleId", "sourceRuleId", "rule_id")
        if not rule_id:
            continue
        rows.append({
            "ruleId": _normalized(rule_id),
            "decisionStage": _normalized(_first(row, "decisionStage", "decision_stage")),
            "actionGroup": _normalized(_first(row, "actionGroup", "action_group")),
        })
    return sorted(rows, key=lambda item: (item["ruleId"], item["decisionStage"], item["actionGroup"]))


def _evidence_keys(value: object) -> List[str]:
    keys = set()
    queue = list(_items(value))
    visited = 0
    while queue and visited < 300:
        visited += 1
        current = queue.pop(0)
        if isinstance(current, Mapping):
            row = dict(current)
            for key in [
                "sourceEventKey",
                "eventKey",
                "evidenceId",
                "articleId",
                "disclosureId",
                "filingId",
                "accessionNumber",
                "url",
            ]:
                value = _canonical_evidence_url(row.get(key)) if key == "url" else _event_key(row.get(key))
                if value:
                    keys.add(value[:280])
            # Inference trace/relation ids include a generation and are not
            # stable evidence. Keep a generic id only when the row itself is
            # recognisably a source document or evidence item.
            row_kind = _normalized(_first(row, "kind", "type", "entityType"))
            row_id = _event_key(row.get("id"))
            if row_id and any(marker in row_kind + " " + row_id for marker in (
                "article", "news", "rss", "disclosure", "dart", "filing", "sec", "evidence", "research",
            )):
                keys.add(row_id[:280])
            for key in [
                "sourceEventKeys",
                "eventKeys",
                "evidenceIds",
                "researchEvidence",
                "articles",
                "disclosures",
                "items",
            ]:
                nested = row.get(key)
                if nested not in (None, ""):
                    queue.extend(_items(nested))
        elif isinstance(current, (list, tuple, set)):
            queue.extend(current)
        else:
            value = _event_key(current)
            if value and any(marker in value for marker in (":news:", ":article:", ":rss:", ":dart:", ":filing:", ":sec:", "http")):
                keys.add(value[:280])
    return sorted(keys)


def relation_delivery_components(
    relation_context: Mapping[str, object],
    notification_context: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Extract only categorical graph facts relevant to repeat delivery."""

    relation = _mapping(relation_context)
    context = _mapping(notification_context)
    decision = _mapping(relation.get("decision"))
    action_envelope = _mapping(relation.get("actionEnvelope")) or _mapping(decision.get("actionEnvelope"))
    state = _mapping(relation.get("decisionState"))
    if not state:
        state = {
            key: relation.get(key)
            for key in ["reviewLevel", "dataState", "changeState", "conflictState", "validationState"]
        }
    graph = _mapping(relation.get("graphStoreInference"))
    insight = _mapping(context.get("ontologyInsight"))
    semantic_components = _mapping(insight.get("semanticComponents"))
    source_evidence = []
    for source in [
        context.get("sourceEventKeys"),
        insight.get("sourceEventKeys"),
        relation.get("evidenceSubgraph"),
        _mapping(relation.get("facts")).get("researchEvidence"),
    ]:
        source_evidence.extend(_evidence_keys(source))
    # Inference trace identifiers and relation-local evidence are diagnostic
    # provenance. They can change when a graph generation is rebuilt without
    # a new source event, so they must not break a delivery cooldown.
    inference_evidence = []
    for source in [
        graph.get("relations"),
        graph.get("traces"),
    ]:
        inference_evidence.extend(_evidence_keys(source))
    material_source_evidence = []
    for source in [
        semantic_components.get("materialSourceEventKeys"),
        insight.get("materialSourceEventKeys"),
        context.get("materialSourceEventKeys"),
    ]:
        material_source_evidence.extend(_evidence_keys(source))
    return {
        "version": RELATION_DELIVERY_FINGERPRINT_VERSION,
        "decision": {
            "selectedRuleId": _normalized(_first(decision, "selectedRuleId", "selected_rule_id")),
            "decisionStage": _normalized(_first(decision, "decisionStage", "decision_stage")),
            "actionGroup": _normalized(_first(decision, "actionGroup", "action_group")),
            "actionPolicy": _normalized(_first(decision, "actionPolicy", "action_policy")),
            "primaryAction": _normalized(_first(_mapping(relation.get("executionPlan")), "primaryAction", "action")),
            "candidateAction": _normalized(_first(decision, "candidateAction", "candidate_action")),
            "decisionEffect": _normalized(_first(decision, "decisionEffect", "decision_effect")),
        },
        "actionEnvelope": {
            "status": _normalized(_first(action_envelope, "status")),
            "preferredAction": _normalized(_first(action_envelope, "preferredAction")),
            "selectedRuleId": _normalized(_first(action_envelope, "selectedRuleId")),
            "selectedDecisionEffect": _normalized(_first(action_envelope, "selectedDecisionEffect")),
            "drivingRuleIds": sorted(_normalized(item) for item in _items(action_envelope.get("drivingRuleIds")) if _normalized(item)),
            "supportRuleIds": sorted(_normalized(item) for item in _items(action_envelope.get("supportRuleIds")) if _normalized(item)),
            "deferRuleIds": sorted(_normalized(item) for item in _items(action_envelope.get("deferRuleIds")) if _normalized(item)),
            "constraintRuleIds": sorted(_normalized(item) for item in _items(action_envelope.get("constraintRuleIds")) if _normalized(item)),
            "blockingRuleIds": sorted(_normalized(item) for item in _items(action_envelope.get("blockingRuleIds")) if _normalized(item)),
            "dataReadiness": _normalized(_first(_mapping(action_envelope.get("dataReadiness")), "state", "dataState")),
        },
        "state": {
            key: _normalized(state.get(key))
            for key in ["reviewLevel", "dataState", "changeState", "conflictState", "validationState"]
        },
        "activeRules": _rule_rows(relation.get("activeRules") or relation.get("matchedRules")),
        "relations": _relation_rows(graph.get("relations")),
        "traces": _trace_rows(graph.get("traces")),
        "evidenceKeys": sorted(set(source_evidence)),
        "materialSourceEventKeys": sorted(set(material_source_evidence)),
        "inferenceEvidenceKeys": sorted(set(inference_evidence)),
    }


def _initial_relation_is_material(components: Mapping[str, object]) -> bool:
    components = _mapping(components)
    decision = _mapping(components.get("decision"))
    envelope = _mapping(components.get("actionEnvelope"))
    state = _mapping(components.get("state"))
    action = _first(envelope, "preferredAction") or _first(decision, "candidateAction", "primaryAction")
    effect = _first(envelope, "selectedDecisionEffect") or _first(decision, "decisionEffect")
    review_level = _first(state, "reviewLevel")
    return bool(
        action in {"buy", "add", "trim", "sell"}
        or effect == "block"
        or review_level in {"act", "action", "urgent", "immediate", "blocked"}
        or components.get("materialSourceEventKeys")
    )


def _decision_transition(
    current_components: Mapping[str, object],
    previous_components: Mapping[str, object] = None,
    initial_material: bool = True,
) -> Dict[str, object]:
    current_components = _mapping(current_components)
    previous_components = _mapping(previous_components)
    current_decision = _mapping(current_components.get("decision"))
    previous_decision = _mapping(previous_components.get("decision"))
    current_envelope = _mapping(current_components.get("actionEnvelope"))
    previous_envelope = _mapping(previous_components.get("actionEnvelope"))
    current_action = _first(current_envelope, "preferredAction") or _first(current_decision, "candidateAction", "primaryAction")
    previous_action = _first(previous_envelope, "preferredAction") or _first(previous_decision, "candidateAction", "primaryAction")
    current_status = _first(current_envelope, "status")
    previous_status = _first(previous_envelope, "status")
    first = not bool(previous_components)
    action_changed = bool(not first and current_action != previous_action)
    status_changed = bool(not first and current_status != previous_status)
    readiness_changed = bool(
        not first
        and _first(current_envelope, "dataReadiness") != _first(previous_envelope, "dataReadiness")
    )
    if first:
        kind = "initial"
        summary = "즉시 알릴 최초 조건입니다." if initial_material else "최초 관계 상태를 알림 없이 기준선으로 저장했습니다."
    elif action_changed:
        kind = "action-changed"
        summary = (previous_action or "이전 판단") + "에서 " + (current_action or "현재 판단") + "으로 바뀌었습니다."
    elif status_changed:
        kind = "envelope-changed"
        summary = (previous_status or "이전 조건") + "에서 " + (current_status or "현재 조건") + "으로 바뀌었습니다."
    elif readiness_changed:
        kind = "readiness-changed"
        summary = "판단에 쓸 자료 상태가 바뀌었습니다."
    else:
        kind = "unchanged"
        summary = "실행 판단 범위는 이전과 같습니다."
    return {
        "changed": bool(first or action_changed or status_changed or readiness_changed),
        "material": bool((first and initial_material) or action_changed or status_changed or readiness_changed),
        "kind": kind,
        "summary": summary,
        "previousAction": previous_action,
        "currentAction": current_action,
        "previousStatus": previous_status,
        "currentStatus": current_status,
        "previousDataReadiness": _first(previous_envelope, "dataReadiness"),
        "currentDataReadiness": _first(current_envelope, "dataReadiness"),
    }


def relation_delivery_metadata(
    relation_context: Mapping[str, object],
    notification_context: Mapping[str, object] = None,
) -> Dict[str, object]:
    components = relation_delivery_components(relation_context, notification_context)
    has_graph_state = bool(
        components["decision"]["selectedRuleId"]
        or components["activeRules"]
        or components["relations"]
        or components["traces"]
    )
    if not has_graph_state:
        return {}
    fingerprint_components = {
        key: value
        for key, value in components.items()
        if key not in {"traces", "inferenceEvidenceKeys"}
    }
    raw = json.dumps(fingerprint_components, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    envelope = _mapping(components.get("actionEnvelope"))
    decision = _mapping(components.get("decision"))
    has_envelope_state = any(
        envelope.get(key) not in (None, "", [], {})
        for key in ("status", "preferredAction", "dataReadiness")
    )
    if has_envelope_state:
        state_signature = "|".join(part for part in [
            str(envelope.get("status") or ""),
            str(envelope.get("preferredAction") or ""),
            str(envelope.get("dataReadiness") or ""),
            str(decision.get("actionPolicy") or ""),
        ] if part)
    else:
        # Keep the legacy behavior only until a TypeDB action envelope is
        # present. New relation-row or trace churn must never become a new
        # cooldown group once the bounded envelope is available.
        state_signature = "legacy|" + fingerprint
    return {
        "version": RELATION_DELIVERY_FINGERPRINT_VERSION,
        "fingerprint": fingerprint,
        "stateSignature": state_signature,
        "components": components,
        "signature": "rule=" + str(components["decision"]["selectedRuleId"] or "-")
        + ";rules=" + str(len(components["activeRules"]))
        + ";evidence=" + str(len(components["evidenceKeys"])),
    }


def relation_delivery_diff(
    current_relation_context: Mapping[str, object],
    previous_relation_context: Mapping[str, object],
    current_notification_context: Mapping[str, object] = None,
    previous_notification_context: Mapping[str, object] = None,
) -> Dict[str, object]:
    current = relation_delivery_metadata(current_relation_context, current_notification_context)
    previous = relation_delivery_metadata(previous_relation_context, previous_notification_context)
    if not current:
        return {
            "changed": False,
            "material": False,
            "changeClass": "unavailable",
            "reason": "No graph-backed relation context is available.",
            "addedEvidenceKeys": [],
            "removedEvidenceKeys": [],
        }
    if not previous:
        current_components = current.get("components") or {}
        initial_material = _initial_relation_is_material(current_components)
        transition = _decision_transition(current_components, initial_material=initial_material)
        return {
            "changed": True,
            "material": initial_material,
            "changeClass": "material" if initial_material else "baseline",
            "reason": (
                "New actionable graph-backed relation context."
                if initial_material
                else "Initial non-actionable graph state recorded as a delivery baseline."
            ),
            "currentFingerprint": current.get("fingerprint"),
            "previousFingerprint": "",
            "changedComponents": ["initial"],
            "materialComponents": ["initial"] if initial_material else [],
            "contextComponents": [] if initial_material else ["initial"],
            "addedEvidenceKeys": list(current_components.get("evidenceKeys") or []),
            "removedEvidenceKeys": [],
            "decisionTransition": transition,
        }
    if current.get("fingerprint") == previous.get("fingerprint"):
        current_components = current.get("components") or {}
        previous_components = previous.get("components") or {}
        context_components = [
            key
            for key in ["traces", "inferenceEvidenceKeys"]
            if current_components.get(key) != previous_components.get(key)
        ]
        labels = {
            "traces": "추론 경로",
            "inferenceEvidenceKeys": "추론 근거 식별자",
        }
        return {
            "changed": bool(context_components),
            "material": False,
            "changeClass": "context-drift" if context_components else "unchanged",
            "reason": (
                "Graph context drift without a new decision-changing source event: "
                + ", ".join(labels.get(key, key) for key in context_components)
                if context_components
                else "Graph-backed relationship evidence is unchanged."
            ),
            "currentFingerprint": current.get("fingerprint"),
            "previousFingerprint": previous.get("fingerprint"),
            "changedComponents": context_components,
            "materialComponents": [],
            "contextComponents": context_components,
            "addedEvidenceKeys": [],
            "removedEvidenceKeys": [],
            "decisionTransition": _decision_transition(current_components, previous_components),
        }
    changed = []
    current_components = current.get("components") or {}
    previous_components = previous.get("components") or {}
    material_components = []
    context_components = []
    transition = _decision_transition(current_components, previous_components)
    current_evidence = set(current_components.get("evidenceKeys") or [])
    previous_evidence = set(previous_components.get("evidenceKeys") or [])
    for key in [
        "decision", "actionEnvelope", "state", "activeRules", "relations",
        "evidenceKeys", "materialSourceEventKeys",
    ]:
        if current_components.get(key) != previous_components.get(key):
            # Rule-set churn alone must not reopen a cooldown.  It becomes
            # material only when it changed the bounded action, readiness, or
            # introduced a new source document.
            if key in {"decision", "actionEnvelope"} and transition.get("material"):
                material_components.append(key)
            elif key == "state" and transition.get("kind") == "readiness-changed":
                material_components.append(key)
            elif key in {"evidenceKeys", "materialSourceEventKeys"}:
                material_components.append(key)
            else:
                context_components.append(key)
    for key in ["traces", "inferenceEvidenceKeys"]:
        if current_components.get(key) != previous_components.get(key):
            context_components.append(key)
    labels = {
        "decision": "결정 단계",
        "actionEnvelope": "실행 범위",
        "state": "관계 상태",
        "activeRules": "성립 규칙",
        "relations": "추론 관계",
        "traces": "추론 경로",
        "evidenceKeys": "근거 원문",
        "materialSourceEventKeys": "판단 변경 원문",
        "inferenceEvidenceKeys": "추론 근거 식별자",
    }
    changed = material_components + context_components
    material = bool(material_components)
    return {
        "changed": bool(changed),
        "material": material,
        "changeClass": "material" if material else "context-drift",
        "reason": (
            "Meaningful graph relation change: " + ", ".join(labels.get(key, key) for key in material_components)
            if material
            else "Graph context drift without a new decision-changing source event: "
            + ", ".join(labels.get(key, key) for key in context_components)
        ),
        "currentFingerprint": current.get("fingerprint"),
        "previousFingerprint": previous.get("fingerprint"),
        "changedComponents": changed,
        "materialComponents": material_components,
        "contextComponents": context_components,
        "addedEvidenceKeys": sorted(current_evidence - previous_evidence),
        "removedEvidenceKeys": sorted(previous_evidence - current_evidence),
        "decisionTransition": transition,
    }
