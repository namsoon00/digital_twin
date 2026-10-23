"""Build an additive strategy release without reserializing its predecessor."""
from copy import deepcopy
import json

from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.modules.model_registry.contracts import operational_strategy_rules, rulebox_rules_hash


def operational_strategy_release_artifact(baseline_record, source_deployment):
    if (baseline_record.get("valid") is not True or baseline_record.get("status") != "ready"
            or baseline_record.get("deploymentId") != source_deployment.get("deploymentId")
            or not source_deployment.get("graphStoreBinding")):
        raise ValueError("verified source deployment artifact required")
    artifact = deepcopy(baseline_record["artifact"])
    rules = operational_strategy_rules(artifact["rules"], source_deployment)
    if any((r.get("claim_contract") or {}).get("strategyContract") for r in artifact["rules"]):
        raise ValueError("strategy release already present; author an explicit successor")
    graph = rulebox_graph_from_rules(rules, include_tbox=False)
    existing = {r["id"]: r for r in artifact["graph"]["entities"]}
    # Shared registries and threshold policies belong to the predecessor. A new
    # source catalog must not rewrite them or their TBox version during an add.
    shared_kinds = {"rule-registry", "threshold-policy-registry", "threshold-policy"}
    tbox = artifact["tboxMetadata"]
    def bound_row(item):
        row = item.to_dict()
        props = row.get("properties") or {}
        if props.get("ontologyBox") == "RuleBox":
            props.update(tboxVersion=tbox["version"], activeTboxVersion=tbox["version"],
                         tboxFingerprint=artifact["tboxFingerprint"])
        return row
    for entity in graph.entities:
        row = bound_row(entity)
        if row["id"] in existing and row["kind"] in shared_kinds:
            continue
        if row["id"] in existing and existing[row["id"]] != row:
            raise ValueError("strategy addition changes an existing static node")
        if row["id"] not in existing:
            artifact["graph"]["entities"].append(row)
    edges = {json.dumps(r, sort_keys=True) for r in artifact["graph"]["relations"]}
    for relation in graph.relations:
        row = bound_row(relation)
        if row["source"] in existing and row["target"] in existing:
            continue
        if json.dumps(row, sort_keys=True) not in edges:
            artifact["graph"]["relations"].append(row)
    artifact["rules"].extend(r.to_dict() for r in rules)
    artifact["ruleboxFingerprint"] = rulebox_rules_hash(artifact["rules"])
    artifact.pop("evolutionPlanFingerprint", None)
    artifact.pop("managementUpgradeContract", None)
    artifact["compositionUpgradeContract"] = {
        "version": "native-strategy-phase-migration-v1", "sourceDeployment": deepcopy(source_deployment),
        "baselineDeploymentId": source_deployment["deploymentId"],
        "baselineArtifactFingerprint": baseline_record["artifactFingerprint"],
        "baselineRuleboxFingerprint": baseline_record["ruleboxFingerprint"],
        "candidateRuleboxFingerprint": artifact["ruleboxFingerprint"],
        "addedRuleIds": [r.rule_id for r in rules], "tboxUnchanged": True,
    }
    return artifact
