"""Compact governance inventory for a frozen V2 RuleBox release."""

from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, Mapping


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def reasoning_rule_inventory(rules: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    rows = [dict(item) for item in rules or [] if isinstance(item, Mapping)]
    modules = Counter()
    stages = Counter()
    lifecycle = Counter()
    effects = Counter()
    invalid = []
    disabled = []
    high_cost = []
    signal_migration = Counter()
    signal_types = Counter()
    for rule in rows:
        rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
        manifest = _mapping(rule.get("domain_manifest") or rule.get("domainManifest"))
        profile = _mapping(rule.get("execution_profile") or rule.get("executionProfile"))
        module = str(manifest.get("module") or "unclassified")
        stage = str(
            manifest.get("executionStage")
            or profile.get("executionStage")
            or rule.get("execution_stage")
            or "unspecified"
        )
        lifecycle_class = str(manifest.get("lifecycleClass") or "unspecified")
        signal_contract = _mapping(manifest.get("statisticalSignalContract"))
        signal_migration[str(signal_contract.get("migrationState") or "missing")] += 1
        for signal_type in signal_contract.get("signalTypes") or []:
            signal_types[str(signal_type or "unknown")] += 1
        modules[module] += 1
        stages[stage] += 1
        lifecycle[lifecycle_class] += 1
        for effect in manifest.get("decisionEffects") or profile.get("decisionEffects") or []:
            effects[str(effect or "unspecified")] += 1
        warnings = list(profile.get("validationWarnings") or [])
        required = {
            "ruleId": bool(rule_id),
            "module": module != "unclassified",
            "dependencyContract": bool(manifest.get("dependencyContractVersion")),
            "triggerDependencies": bool(manifest.get("triggerDependencies")),
            "derivedOutputs": bool(manifest.get("derivedOutputs")),
            "invalidationContract": bool(manifest.get("invalidationContract")),
            "executionStage": stage != "unspecified",
        }
        missing = [key for key, present in required.items() if not present]
        if warnings or missing:
            invalid.append({
                "ruleId": rule_id,
                "missing": missing,
                "warnings": [str(item) for item in warnings[:4]],
            })
        if rule.get("enabled") is False:
            disabled.append(rule_id)
        try:
            cost_score = int(profile.get("costScore") or manifest.get("costScore") or 0)
        except (TypeError, ValueError):
            cost_score = 0
        if cost_score >= 15:
            high_cost.append({"ruleId": rule_id, "costScore": cost_score})
    return {
        "version": "reasoning-rule-inventory-v1",
        "ruleCount": len(rows),
        "enabledRuleCount": len(rows) - len(disabled),
        "disabledRuleIds": disabled[:30],
        "moduleCounts": dict(sorted(modules.items())),
        "executionStageCounts": dict(sorted(stages.items())),
        "lifecycleClassCounts": dict(sorted(lifecycle.items())),
        "decisionEffectCounts": dict(sorted(effects.items())),
        "invalidRuleCount": len(invalid),
        "invalidRules": invalid[:30],
        "highCostRuleCount": len(high_cost),
        "highCostRules": sorted(high_cost, key=lambda item: (-item["costScore"], item["ruleId"]))[:20],
        "statisticalSignalMigrationCounts": dict(sorted(signal_migration.items())),
        "statisticalSignalRuleCounts": dict(sorted(signal_types.items())),
        "releaseReady": bool(rows) and not invalid,
    }
