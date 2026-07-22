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


RELATION_DELIVERY_FINGERPRINT_VERSION = "ontology-relation-delivery-v1"
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
    state = _mapping(relation.get("decisionState"))
    if not state:
        state = {
            key: relation.get(key)
            for key in ["reviewLevel", "dataState", "changeState", "conflictState", "validationState"]
        }
    graph = _mapping(relation.get("graphStoreInference"))
    insight = _mapping(context.get("ontologyInsight"))
    evidence = []
    for source in [
        context.get("sourceEventKeys"),
        insight.get("sourceEventKeys"),
        relation.get("evidenceSubgraph"),
        _mapping(relation.get("facts")).get("researchEvidence"),
        graph.get("relations"),
        graph.get("traces"),
    ]:
        evidence.extend(_evidence_keys(source))
    return {
        "version": RELATION_DELIVERY_FINGERPRINT_VERSION,
        "decision": {
            "selectedRuleId": _normalized(_first(decision, "selectedRuleId", "selected_rule_id")),
            "decisionStage": _normalized(_first(decision, "decisionStage", "decision_stage")),
            "actionGroup": _normalized(_first(decision, "actionGroup", "action_group")),
            "actionPolicy": _normalized(_first(decision, "actionPolicy", "action_policy")),
            "primaryAction": _normalized(_first(_mapping(relation.get("executionPlan")), "primaryAction", "action")),
        },
        "state": {
            key: _normalized(state.get(key))
            for key in ["reviewLevel", "dataState", "changeState", "conflictState", "validationState"]
        },
        "activeRules": _rule_rows(relation.get("activeRules") or relation.get("matchedRules")),
        "relations": _relation_rows(graph.get("relations")),
        "traces": _trace_rows(graph.get("traces")),
        "evidenceKeys": sorted(set(evidence)),
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
    raw = json.dumps(components, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return {
        "version": RELATION_DELIVERY_FINGERPRINT_VERSION,
        "fingerprint": fingerprint,
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
        return {"changed": False, "reason": "No graph-backed relation context is available."}
    if not previous:
        return {
            "changed": True,
            "reason": "New graph-backed relation context.",
            "currentFingerprint": current.get("fingerprint"),
            "previousFingerprint": "",
            "changedComponents": ["initial"],
        }
    if current.get("fingerprint") == previous.get("fingerprint"):
        return {
            "changed": False,
            "reason": "Graph-backed relationship evidence is unchanged.",
            "currentFingerprint": current.get("fingerprint"),
            "previousFingerprint": previous.get("fingerprint"),
            "changedComponents": [],
        }
    changed = []
    current_components = current.get("components") or {}
    previous_components = previous.get("components") or {}
    for key in ["decision", "state", "activeRules", "relations", "traces", "evidenceKeys"]:
        if current_components.get(key) != previous_components.get(key):
            changed.append(key)
    labels = {
        "decision": "결정 단계",
        "state": "관계 상태",
        "activeRules": "성립 규칙",
        "relations": "추론 관계",
        "traces": "추론 경로",
        "evidenceKeys": "근거 원문",
    }
    return {
        "changed": bool(changed),
        "reason": "Meaningful graph relation change: " + ", ".join(labels.get(key, key) for key in changed),
        "currentFingerprint": current.get("fingerprint"),
        "previousFingerprint": previous.get("fingerprint"),
        "changedComponents": changed,
    }
