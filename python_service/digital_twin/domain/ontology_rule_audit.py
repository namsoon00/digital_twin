"""Join RuleBox semantics with operational runtime samples for human audit."""

from collections import Counter
from typing import Dict, Iterable, List

from .ontology_change_impact import rule_condition_dependency_profile
from .ontology_rule_execution_policy import rule_execution_profile


RULE_AUDIT_VERSION = "ontology-rule-audit-v1"


def _text(value: object) -> str:
    return str(value or "").strip()


def _rule_value(rule: object, snake: str, camel: str = "") -> object:
    if isinstance(rule, dict):
        return rule.get(snake) if snake in rule else rule.get(camel or snake)
    return getattr(rule, snake, None)


def _conditions(rule: object) -> List[object]:
    value = _rule_value(rule, "conditions")
    return list(value or []) if isinstance(value, (list, tuple)) else []


def rule_audit_payload(
    rules: Iterable[object],
    runtime_summary: Dict[str, object] = None,
) -> Dict[str, object]:
    """Build a read-only audit view; it never changes RuleBox governance."""

    runtime = dict(runtime_summary or {})
    runtime_by_id = {
        _text(item.get("ruleId")): dict(item)
        for item in runtime.get("rules") or []
        if isinstance(item, dict) and _text(item.get("ruleId"))
    }
    rows = []
    for rule in rules or []:
        rule_id = _text(_rule_value(rule, "rule_id", "ruleId"))
        if not rule_id:
            continue
        profile = rule_execution_profile(rule)
        sample = runtime_by_id.get(rule_id) or {}
        sample_count = int(sample.get("sampleCount") or 0)
        matched_count = int(sample.get("matchedCount") or 0)
        failure_count = int(sample.get("failureCount") or 0)
        p95_ms = int(sample.get("p95DurationMs") or 0)
        enabled_value = _rule_value(rule, "enabled")
        enabled = bool(enabled_value) if enabled_value is not None else True
        if not enabled:
            status = "disabled"
        elif failure_count:
            status = "failing"
        elif p95_ms >= 5000:
            status = "slow"
        elif sample_count >= 10 and matched_count == 0:
            status = "never-matched-in-sample"
        elif sample_count:
            status = "observed"
        else:
            status = "no-runtime-sample"

        dependency_profiles = [
            rule_condition_dependency_profile(condition)
            for condition in _conditions(rule)
        ]
        scope_families = sorted({
            _text(family)
            for dependency in dependency_profiles
            for family in dependency.get("scopeFamilies") or []
            if _text(family)
        })
        conservative_dependencies = len([
            item for item in dependency_profiles if bool(item.get("conservative"))
        ])
        review_reasons = []
        if status == "no-runtime-sample":
            review_reasons.append("실행 원장 표본 없음")
        if failure_count:
            review_reasons.append("실행 실패 " + str(failure_count) + "건")
        if p95_ms >= 5000:
            review_reasons.append("p95 " + str(p95_ms) + "ms")
        if status == "never-matched-in-sample":
            review_reasons.append("10회 이상 실행 표본에서 성립 없음")
        if conservative_dependencies:
            review_reasons.append("보수적 의존성 " + str(conservative_dependencies) + "개")
        review_reasons.extend(profile.get("validationWarnings") or [])
        rows.append({
            "ruleId": rule_id,
            "label": _text(_rule_value(rule, "label")) or rule_id,
            "enabled": enabled,
            "actionGroup": _text(_rule_value(rule, "action_group", "actionGroup")),
            "actionLevel": _text(_rule_value(rule, "action_level", "actionLevel")),
            "status": status,
            "executionProfile": profile,
            "scopeFamilies": scope_families,
            "conservativeDependencyCount": conservative_dependencies,
            "sampleCount": sample_count,
            "matchedCount": matched_count,
            "failureCount": failure_count,
            "averageDurationMs": int(sample.get("averageDurationMs") or 0),
            "p95DurationMs": p95_ms,
            "maxDurationMs": int(sample.get("maxDurationMs") or 0),
            "lastStatus": _text(sample.get("lastStatus")),
            "lastUpdatedAt": _text(sample.get("lastUpdatedAt")),
            "reviewReasons": review_reasons,
            "automaticRuleChange": False,
        })

    status_counts = Counter(item["status"] for item in rows)
    stage_counts = Counter(
        str(item["executionProfile"].get("executionStage") or "core")
        for item in rows
    )
    rows.sort(key=lambda item: (
        {"failing": 0, "slow": 1, "never-matched-in-sample": 2, "no-runtime-sample": 3}.get(item["status"], 4),
        -int(item.get("p95DurationMs") or 0),
        item["ruleId"],
    ))
    return {
        "version": RULE_AUDIT_VERSION,
        "status": "ok" if str(runtime.get("status") or "ok") != "error" else "partial",
        "ruleCount": len(rows),
        "runtimeSampleCount": int(runtime.get("sampleCount") or 0),
        "statusCounts": dict(sorted(status_counts.items())),
        "executionStageCounts": dict(sorted(stage_counts.items())),
        "rules": rows,
        "automaticRuleChange": False,
        "interpretation": (
            "Runtime samples identify review candidates only. No-sample does not mean unused, "
            "and performance never enables, disables, or edits a RuleBox rule automatically."
        ),
    }
