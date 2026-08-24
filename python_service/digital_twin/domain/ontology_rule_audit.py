"""Join RuleBox semantics with operational runtime samples for human audit."""

from collections import Counter
import json
from typing import Dict, Iterable, List

from .ontology_change_impact import rule_condition_dependency_profile
from .ontology_rule_execution_policy import rule_execution_profile
from .ontology_rule_manifest import rule_domain_manifest
from .ontology_rule_knowledge import knowledge_basis_summary, resolved_rule_knowledge_basis
from .rule_claim_contract import resolved_rule_claim_contract, rule_claim_coverage


RULE_AUDIT_VERSION = "ontology-rule-audit-v4"


def _text(value: object) -> str:
    return str(value or "").strip()


def _rule_value(rule: object, snake: str, camel: str = "") -> object:
    if isinstance(rule, dict):
        return rule.get(snake) if snake in rule else rule.get(camel or snake)
    return getattr(rule, snake, None)


def _conditions(rule: object) -> List[object]:
    value = _rule_value(rule, "conditions")
    return list(value or []) if isinstance(value, (list, tuple)) else []


def _contract_value(value: object) -> object:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {
            str(key): _contract_value(item)
            for key, item in sorted(value.items())
            if key not in {"description", "label"}
            and item not in (None, "", [], {})
        }
    if isinstance(value, (list, tuple, set)):
        return [_contract_value(item) for item in value]
    return value


def _rule_semantic_signature(rule: object, manifest: Dict[str, object]) -> str:
    conditions = [_contract_value(item) for item in _conditions(rule)]
    derivations = _rule_value(rule, "derivations") or []
    payload = {
        "assessmentScope": manifest.get("assessmentScope"),
        "conditions": conditions,
        "derivations": [_contract_value(item) for item in derivations],
        "outputContract": _contract_value(manifest.get("outputContract") or {}),
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def rule_audit_payload(
    rules: Iterable[object],
    runtime_summary: Dict[str, object] = None,
) -> Dict[str, object]:
    """Build a read-only audit view; it never changes RuleBox governance."""

    runtime = dict(runtime_summary or {})
    authored_rules = list(rules or [])
    runtime_by_id = {
        _text(item.get("ruleId")): dict(item)
        for item in runtime.get("rules") or []
        if isinstance(item, dict) and _text(item.get("ruleId"))
    }
    rows = []
    for rule in authored_rules:
        rule_id = _text(_rule_value(rule, "rule_id", "ruleId"))
        if not rule_id:
            continue
        profile = rule_execution_profile(rule)
        manifest = rule_domain_manifest(rule, execution=profile)
        knowledge_basis = resolved_rule_knowledge_basis(rule)
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
            status = "observed-no-match"
        elif sample_count:
            status = "observed"
        elif manifest.get("lifecycleClass") == "event-driven":
            status = "waiting-for-event"
        elif manifest.get("lifecycleClass") == "cold":
            status = "cold-no-sample"
        else:
            status = "routing-gap-review"

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
        if status == "waiting-for-event":
            review_reasons.append("사건 데이터가 들어올 때만 실행")
        if status == "cold-no-sample":
            review_reasons.append("저빈도 규칙 · 실행 표본 없음")
        if status == "routing-gap-review":
            review_reasons.append("상시 규칙인데 실행 표본 없음")
        if failure_count:
            review_reasons.append("실행 실패 " + str(failure_count) + "건")
        if p95_ms >= 5000:
            review_reasons.append("p95 " + str(p95_ms) + "ms")
        if status == "observed-no-match":
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
            "domainManifest": manifest,
            "knowledgeBasis": knowledge_basis.to_dict(),
            "claimContract": resolved_rule_claim_contract(rule, knowledge_basis).to_dict(),
            "ruleKind": knowledge_basis.rule_kind,
            "theoryFamily": knowledge_basis.theory_family,
            "thesisFamily": knowledge_basis.thesis_family,
            "knowledgeValidationStatus": knowledge_basis.validation_status,
            "assessmentScope": manifest.get("assessmentScope"),
            "triggerFamilies": list(manifest.get("triggerFamilies") or []),
            "requiredFacts": list(manifest.get("requiredFacts") or []),
            "evidenceFamilies": list(manifest.get("evidenceFamilies") or []),
            "outputContract": dict(manifest.get("outputContract") or {}),
            "lifecycleClass": manifest.get("lifecycleClass"),
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
            "semanticSignature": _rule_semantic_signature(rule, manifest),
            "retirementCandidate": bool(
                enabled
                and sample_count >= 50
                and matched_count == 0
                and manifest.get("lifecycleClass") != "event-driven"
            ),
            "automaticRuleChange": False,
        })

    signature_groups: Dict[tuple, List[Dict[str, object]]] = {}
    for row in rows:
        signature = (
            row.get("assessmentScope"),
            row.get("semanticSignature"),
        )
        signature_groups.setdefault(signature, []).append(row)
    duplicate_groups = []
    for index, group in enumerate(
        (items for items in signature_groups.values() if len(items) > 1),
        start=1,
    ):
        group_id = "duplicate-candidate-" + str(index)
        rule_ids = sorted(item["ruleId"] for item in group)
        duplicate_groups.append({"groupId": group_id, "ruleIds": rule_ids})
        for item in group:
            item["duplicateCandidateGroup"] = group_id
            item["reviewReasons"].append("의존성·출력 계약이 같은 중복 후보 " + str(len(group)) + "개")

    for item in rows:
        item.pop("semanticSignature", None)

    status_counts = Counter(item["status"] for item in rows)
    stage_counts = Counter(
        str(item["executionProfile"].get("executionStage") or "core")
        for item in rows
    )
    scope_counts = Counter(str(item.get("assessmentScope") or "unknown") for item in rows)
    lifecycle_counts = Counter(str(item.get("lifecycleClass") or "unknown") for item in rows)
    knowledge_summary = knowledge_basis_summary([
        resolved_rule_knowledge_basis(rule)
        for rule in authored_rules
        if _text(_rule_value(rule, "rule_id", "ruleId"))
    ])
    rows.sort(key=lambda item: (
        {"failing": 0, "slow": 1, "routing-gap-review": 2, "observed-no-match": 3}.get(item["status"], 4),
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
        "assessmentScopeCounts": dict(sorted(scope_counts.items())),
        "lifecycleClassCounts": dict(sorted(lifecycle_counts.items())),
        "knowledgeBasisSummary": knowledge_summary,
        "ruleClaimCoverage": rule_claim_coverage(authored_rules),
        "duplicateCandidateGroups": duplicate_groups,
        "retirementCandidateCount": len([item for item in rows if item.get("retirementCandidate")]),
        "rules": rows,
        "automaticRuleChange": False,
        "interpretation": (
            "Runtime samples identify review candidates only. No-sample does not mean unused, "
            "and performance never enables, disables, or edits a RuleBox rule automatically."
        ),
    }
