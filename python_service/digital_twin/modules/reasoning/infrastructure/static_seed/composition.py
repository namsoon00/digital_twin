"""Add composition semantics to a frozen release without reseeding its rules."""

from copy import deepcopy

from digital_twin.modules.market_data.contracts import payload_fingerprint
from digital_twin.modules.model_registry.contracts import GraphInferenceRule, composition_rules
from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import (
    ontology_seed_graph, append_rule_to_release_artifact,
)


def composition_release_artifact(baseline, baseline_deployment_id):
    artifact = deepcopy(baseline)
    rules = composition_rules([GraphInferenceRule.from_dict(r) for r in baseline["rules"]])
    if len(rules) != 3:
        raise ValueError("Composition release requires all three governed combinations")
    addition = ontology_seed_graph(rules)
    class_id = "tbox-class:RuleEvidenceComposition"
    node = next(e.to_dict() for e in addition.entities if e.entity_id == class_id)
    existing = {e["id"] for e in artifact["graph"]["entities"]}
    if class_id in existing:
        raise ValueError("Composition class already belongs to this release")
    edges = [e.to_dict() for e in addition.relations
             if class_id in {e.source, e.target} and
             {e.source, e.target}.issubset(existing | {class_id})]
    artifact["graph"]["entities"].append(node)
    artifact["graph"]["relations"].extend(edges)
    metadata = artifact["tboxMetadata"]
    metadata["version"] += "-rule-composition-v1"
    metadata["fingerprint"] = payload_fingerprint({
        "baselineTboxFingerprint": baseline["tboxFingerprint"],
        "addedClass": node, "addedRelations": edges,
    })[:16]
    metadata["entityCount"] += 1
    metadata["relationCount"] += len(edges)
    artifact["tboxFingerprint"] = metadata["fingerprint"]
    for rule in rules:
        artifact = append_rule_to_release_artifact(artifact, rule.to_dict())
    artifact["compositionUpgradeContract"] = {
        "version": "native-composition-migration-v1",
        "baselineDeploymentId": baseline_deployment_id,
        "baselineArtifactFingerprint": payload_fingerprint(baseline),
        "baselineRuleboxFingerprint": baseline["ruleboxFingerprint"],
        "candidateRuleboxFingerprint": artifact["ruleboxFingerprint"],
        "addedRuleIds": sorted(r.rule_id for r in rules),
        "addedTboxClassId": class_id,
    }
    return artifact
