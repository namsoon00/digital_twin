"""Determine whether a frozen native release reads portfolio report facts.

Subject outcome/calibration history is a separate mandatory factual input.
An account-wide analytics report is not a prerequisite of unrelated rules.
Unknown releases/condition forms retain the conservative full-input path.
"""

from collections.abc import Mapping
from hashlib import sha256
import json


PERFORMANCE_PROJECTION_VERSION = "rule-bound-performance-context-v1"
_PERFORMANCE_KINDS = {
    "decisionperformance", "ruleperformance", "hypothesisperformance",
    "graphinferencerule", "hypothesistemplate", "hypothesisfamily",
}
_PERFORMANCE_RELATIONS = {
    "HAS_DECISION_PERFORMANCE", "HAS_PERFORMANCE_SLICE", "EVALUATES_RULE", "EVALUATES_HYPOTHESIS",
}


def _mentions_report(value):
    if isinstance(value, Mapping):
        return any(_mentions_report(key) or _mentions_report(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_mentions_report(item) for item in value)
    if not isinstance(value, str):
        return False
    token = value.rsplit(":", 1)[-1].split(".", 1)[0]
    return (token.upper() in _PERFORMANCE_RELATIONS
            or token.lower().replace("-", "").replace("_", "") in _PERFORMANCE_KINDS)


def performance_projection_plan(settings, frozen_catalog):
    """Exclude only proven-unused analytics; never suppress subject outcomes."""
    settings = settings or {}
    version = str(settings.get("_reasoningEngineVersion") or "").lower()
    incremental = settings.get("ontologyIncrementalCurrentStateReasoningEnabled")
    enabled = (version == "v2" if incremental is None else
               str(incremental).lower() in {"1", "true", "yes", "on", "enabled"})
    base = {"version": PERFORMANCE_PROJECTION_VERSION, "required": True,
            "subjectCalibrationRequired": True}
    if version != "v2" or not enabled:
        return {**base, "reason": "legacy-full-context"}
    if str(settings.get("ontologyDecisionPerformanceAboxProjectionEnabled") or "").lower() in {"1", "true", "yes", "on", "enabled"}:
        return {**base, "reason": "explicit-report-context"}
    if not isinstance(frozen_catalog, Mapping) or not isinstance(frozen_catalog.get("rules"), list) or not frozen_catalog["rules"]:
        return {**base, "reason": "unverified-rule-dependencies"}
    if not any(frozen_catalog.get(key) for key in ("ruleboxRulesHash", "rulesHash", "sourceRulesHash")):
        return {**base, "reason": "unverified-rule-dependencies"}
    try:
        if int(frozen_catalog.get("ruleCount", len(frozen_catalog["rules"]))) != len(frozen_catalog["rules"]):
            return {**base, "reason": "unverified-rule-dependencies"}
    except (TypeError, ValueError):
        return {**base, "reason": "unverified-rule-dependencies"}
    if any(not isinstance(row, Mapping) for row in frozen_catalog["rules"]):
        return {**base, "reason": "unverified-rule-dependencies"}
    rules = [row for row in frozen_catalog["rules"] if row.get("enabled") is not False]
    if not rules:
        return {**base, "reason": "unverified-rule-dependencies"}
    for rule in rules:
        if not str(rule.get("rule_id") or rule.get("ruleId") or "").strip():
            return {**base, "reason": "unverified-rule-dependencies"}
        source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "").strip()
        if not source_kind or "*" in source_kind:
            return {**base, "reason": "unverified-rule-dependencies"}
        conditions = rule.get("conditions")
        try:
            if "conditionCount" in rule and int(rule["conditionCount"]) != len(conditions or []):
                return {**base, "reason": "unverified-rule-dependencies"}
        except (TypeError, ValueError):
            return {**base, "reason": "unverified-rule-dependencies"}
        model = rule.get("model_input_contract") or rule.get("modelInputContract")
        if model and (not isinstance(model, Mapping) or model.get("conservative") is not False):
            return {**base, "reason": "unverified-model-dependencies"}
        if not isinstance(conditions, list) or not conditions or any(
            not isinstance(item, Mapping) or item.get("kind") not in {"subject_property", "relation"}
            or (item.get("kind") == "relation" and (not (item.get("relation_type") or item.get("relationType"))
                                                   or (item.get("relation_type") or item.get("relationType")) == "*"))
            for item in conditions
        ):
            return {**base, "reason": "unverified-rule-dependencies"}
        if _mentions_report(rule):
            return {**base, "reason": "frozen-rule-reads-report"}
    try:
        digest = sha256(json.dumps(frozen_catalog["rules"], sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        return {**base, "reason": "unverified-rule-dependencies"}
    return {**base, "required": False, "reason": "unused-by-frozen-rules",
            "ruleCount": len(rules), "dependencyFingerprint": digest}
