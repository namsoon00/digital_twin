"""Author an isolated valid-price-path successor from its actual frozen baseline."""
from copy import deepcopy

from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.modules.market_data.contracts import VALID_PRICE_PATH_FEATURE_VERSION, payload_fingerprint
from digital_twin.modules.model_registry.contracts import GraphInferenceRule, rulebox_rules_hash
from digital_twin.modules.reasoning.domain.temporal_feature_migration import (
    LEGACY_FEATURE_VERSION, MIGRATION_VERSION, transformed_rule_payloads, validate_migration_artifacts,
)


def temporal_feature_release_artifact(baseline_record, baseline_deployment_id):
    artifact = deepcopy(baseline_record["artifact"])
    original = artifact["rules"]
    updated = transformed_rule_payloads(original)
    changed = {before["rule_id"] for before, after in zip(original, updated) if before != after}
    if not changed:
        raise ValueError("no legacy temporal predicates in the frozen baseline")
    graph = rulebox_graph_from_rules([GraphInferenceRule.from_dict(r) for r in updated if r["rule_id"] in changed], include_tbox=False)
    prior_graph = artifact["graph"]
    removed = {row["id"] for row in prior_graph["entities"] if (row.get("properties") or {}).get("ruleId") in changed}
    replacements = []
    for entity in graph.entities:
        row = entity.to_dict()
        props = row["properties"]
        if props.get("ruleId") not in changed:
            continue
        props.update(tboxVersion=artifact["tboxMetadata"]["version"],
                     activeTboxVersion=artifact["tboxMetadata"]["version"], tboxFingerprint=artifact["tboxFingerprint"])
        replacements.append(row)
    replacement_ids = {row["id"] for row in replacements}
    if not removed.issubset(replacement_ids):
        raise ValueError("temporal upgrade unexpectedly removes static rule nodes")
    prior_graph["entities"] = [row for row in prior_graph["entities"] if row["id"] not in removed] + replacements
    prior_graph["relations"] = [row for row in prior_graph["relations"] if row["source"] not in removed and row["target"] not in removed]
    known = {row["id"] for row in prior_graph["entities"]}
    for edge in graph.relations:
        row = edge.to_dict()
        if row["source"] not in replacement_ids and row["target"] not in replacement_ids:
            continue
        if row["source"] not in known or row["target"] not in known:
            raise ValueError("temporal upgrade contains an unbound static endpoint")
        prior_graph["relations"].append(row)
    artifact["rules"] = updated
    artifact["ruleboxFingerprint"] = rulebox_rules_hash(updated)
    artifact["temporalFeatureMigration"] = {
        "version": MIGRATION_VERSION, "baselineDeploymentId": baseline_deployment_id,
        "baselineArtifactFingerprint": baseline_record["artifactFingerprint"],
        "baselineRuleboxFingerprint": baseline_record["ruleboxFingerprint"],
        "candidateRuleboxFingerprint": artifact["ruleboxFingerprint"],
        "baselineFeatureSetVersion": LEGACY_FEATURE_VERSION,
        "candidateFeatureSetVersion": VALID_PRICE_PATH_FEATURE_VERSION,
        "changedRuleIds": sorted(changed),
    }
    validate_migration_artifacts(baseline_record, artifact, baseline_deployment_id)
    return artifact
