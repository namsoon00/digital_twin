"""Compile an immutable RuleBox release into an execution-oriented IR."""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Iterable, Mapping

from .ontology_rule_manifest import (
    rule_dependency_reverse_index,
    validate_rule_domain_manifests,
)
from .ontology_rulebox_governance import rulebox_semantic_violations


ONTOLOGY_COMPILER_VERSION = "ontology-compiler-ir-v1"


def _hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compile_ontology_release(rules: Iterable[object]) -> Dict[str, object]:
    """Validate and compile rule ownership, dependencies and hypotheses.

    This compiler performs static release checks only. It cannot evaluate a
    fact condition and therefore cannot author an investment action.
    """

    source_rules = list(rules or [])
    manifest_validation = validate_rule_domain_manifests(source_rules)
    manifests = [dict(item) for item in manifest_validation.get("manifests") or []]
    semantic_violations = rulebox_semantic_violations(source_rules)
    missing_hypotheses = sorted({
        str(item.get("ruleId") or "")
        for item in manifests
        if item.get("ruleKind") == "predictive-hypothesis"
        and not (
            item.get("thesisFamily")
            or item.get("hypothesisFamilyKey")
            or (item.get("knowledgeBasis") or {}).get("thesisFamily")
        )
    })
    missing_dependencies = sorted({
        str(item.get("ruleId") or "")
        for item in manifests
        if not item.get("triggerDependencies") or not item.get("requiredContext")
    })
    execution_units = {
        str(item.get("ruleId") or ""): {
            "evaluationGrain": str(item.get("evaluationGrain") or ""),
            "ownerWorld": str(item.get("ownerWorld") or ""),
            "triggerEventClasses": list(item.get("triggerEventClasses") or []),
            "executionCadence": str(item.get("executionCadence") or ""),
            "ruleKind": str(item.get("ruleKind") or ""),
            "theoryFamily": str(item.get("theoryFamily") or ""),
            "thesisFamily": str(item.get("thesisFamily") or ""),
            "decisionEligibility": str(item.get("decisionEligibility") or ""),
            "triggerDependencyKeys": sorted({
                str(key or "")
                for dependency in item.get("triggerDependencies") or []
                if isinstance(dependency, Mapping)
                for key in dependency.get("dependencyKeys") or []
                if str(key or "")
            }),
            "contextDependencyKeys": sorted({
                str(key or "")
                for dependency in item.get("requiredContext") or []
                if isinstance(dependency, Mapping)
                for key in dependency.get("dependencyKeys") or []
                if str(key or "")
            }),
        }
        for item in manifests
        if str(item.get("ruleId") or "")
    }
    reverse_index = rule_dependency_reverse_index(source_rules)
    failures = list(semantic_violations)
    failures.extend(rule_id + ": predictive rule has no hypothesis family" for rule_id in missing_hypotheses)
    failures.extend(rule_id + ": rule has no complete data dependency" for rule_id in missing_dependencies)
    failures.extend(
        rule_id + ": invalid domain manifest"
        for rule_id in manifest_validation.get("invalidRuleIds") or []
    )
    failures = sorted(set(failures))
    ir = {
        "version": ONTOLOGY_COMPILER_VERSION,
        "ruleCount": len(source_rules),
        "executionUnits": execution_units,
        "dependencyReverseIndex": reverse_index,
    }
    return {
        "status": "ready" if not failures and bool(source_rules) else "invalid",
        "valid": not failures and bool(source_rules),
        "version": ONTOLOGY_COMPILER_VERSION,
        "ruleCount": len(source_rules),
        "predictiveRuleCount": sum(
            1 for item in manifests if item.get("ruleKind") == "predictive-hypothesis"
        ),
        "missingHypothesisRuleIds": missing_hypotheses,
        "missingDependencyRuleIds": missing_dependencies,
        "failures": failures[:80],
        "irFingerprint": _hash(ir),
        "ir": ir,
    }
