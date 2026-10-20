"""Add composition semantics to a frozen release without reseeding its rules."""

from copy import deepcopy

from digital_twin.modules.market_data.contracts import payload_fingerprint
from digital_twin.modules.model_registry.contracts import GraphInferenceRule, composition_rules
from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import append_rule_to_release_artifact


def composition_release_artifact(baseline, baseline_deployment_id):
    artifact = deepcopy(baseline)
    rules = composition_rules([GraphInferenceRule.from_dict(r) for r in baseline["rules"]])
    if len(rules) != 3:
        raise ValueError("Composition release requires all three governed combinations")
    # These conclusions are existing NextCheck assertions. Adding a new TBox
    # type would unnecessarily change lifecycle metadata on every ABox fact.
    for rule in rules:
        artifact = append_rule_to_release_artifact(artifact, rule.to_dict())
    artifact["compositionUpgradeContract"] = {
        "version": "native-composition-migration-v1",
        "baselineDeploymentId": baseline_deployment_id,
        "baselineArtifactFingerprint": payload_fingerprint(baseline),
        "baselineRuleboxFingerprint": baseline["ruleboxFingerprint"],
        "candidateRuleboxFingerprint": artifact["ruleboxFingerprint"],
        "addedRuleIds": sorted(r.rule_id for r in rules),
        "tboxUnchanged": True,
    }
    return artifact
