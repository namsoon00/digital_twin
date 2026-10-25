"""Build a strategy successor while preserving the immutable predecessor."""
from copy import deepcopy
import json

from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.modules.model_registry.contracts import operational_strategy_rules, scenario_successor_rules, rulebox_rules_hash


def operational_strategy_release_artifact(baseline_record, source_deployment):
    if any((r.get("claim_contract") or {}).get("strategyContract") for r in baseline_record["artifact"]["rules"]):
        raise ValueError("strategy release already present; author an explicit successor")
    return _additive_strategy_artifact(baseline_record, source_deployment,
        operational_strategy_rules(baseline_record["artifact"]["rules"], source_deployment),
        "native-strategy-phase-migration-v1")


def scenario_strategy_release_artifact(baseline_record, source_deployment):
    rules = scenario_successor_rules(baseline_record["artifact"]["rules"])
    predecessors = {r.resolved_claim_contract.strategy_contract["predecessorContractFingerprint"] for r in rules}
    retired = {r["rule_id"] for r in baseline_record["artifact"]["rules"]
               if (r.get("claim_contract") or {}).get("strategyContract", {}).get("contractFingerprint") in predecessors}
    return _additive_strategy_artifact(baseline_record, source_deployment, rules,
        "native-strategy-scenario-migration-v1", retired)


def _additive_strategy_artifact(baseline_record, source_deployment, rules, migration_version, retired=(), extend_model_bridges=False):
    if (baseline_record.get("valid") is not True or baseline_record.get("status") != "ready"
            or baseline_record.get("deploymentId") != source_deployment.get("deploymentId")
            or not source_deployment.get("graphStoreBinding")):
        raise ValueError("verified source deployment artifact required")
    artifact = deepcopy(baseline_record["artifact"])
    if {r.rule_id for r in rules} & {r["rule_id"] for r in artifact["rules"]}:
        raise ValueError("strategy successor already registered")
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
        if extend_model_bridges and row["id"] in existing and row["kind"] == "model-signal-bridge":
            current = existing[row["id"]]
            aggregates = {"policyIds", "lineageRuleIds", "policyCount"}
            if ({k: v for k, v in current["properties"].items() if k not in aggregates}
                    != {k: v for k, v in row["properties"].items() if k not in aggregates}
                    or any(current.get(k) != v for k, v in row.items() if k != "properties")):
                raise ValueError("flow addition changes native bridge semantics")
            for key in ("policyIds", "lineageRuleIds"):
                if set(current["properties"].get(key, [])) & set(row["properties"].get(key, [])):
                    raise ValueError("flow addition repeats an existing bridge policy")
                current["properties"][key] = sorted([*current["properties"].get(key, []), *row["properties"].get(key, [])])
            current["properties"]["policyCount"] = len(current["properties"]["policyIds"])
            continue
        if row["id"] in existing and existing[row["id"]] != row:
            raise ValueError("strategy addition changes an existing static node: " + row["id"])
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
    # Only the successor artifact disables its exact eight predecessor phase
    # rules. The old release stays immutable; native predicates run once.
    for row in artifact["rules"]:
        if row["rule_id"] in retired:
            row["enabled"] = False
    for node in artifact["graph"]["entities"]:
        if node["kind"] == "rule" and node["properties"].get("ruleId") in retired:
            node["properties"]["enabled"] = False
    artifact["ruleboxFingerprint"] = rulebox_rules_hash(artifact["rules"])
    artifact.pop("evolutionPlanFingerprint", None)
    artifact.pop("managementUpgradeContract", None)
    artifact["compositionUpgradeContract"] = {
        "version": migration_version, "sourceDeployment": deepcopy(source_deployment),
        "baselineDeploymentId": source_deployment["deploymentId"],
        "baselineArtifactFingerprint": baseline_record["artifactFingerprint"],
        "baselineRuleboxFingerprint": baseline_record["ruleboxFingerprint"],
        "candidateRuleboxFingerprint": artifact["ruleboxFingerprint"],
        "addedRuleIds": [r.rule_id for r in rules], "tboxUnchanged": True,
    }
    if retired:
        artifact["compositionUpgradeContract"]["retiredRuleIds"] = sorted(retired)
    return artifact


def current_flow_release_artifact(baseline_record, source_deployment):
    """Append reviewed current-source contracts without enabling legacy history."""
    from digital_twin.modules.model_registry.contracts import default_graph_inference_rules, current_flow_strategy_rules
    rules = [r for r in default_graph_inference_rules() if r.rule_id.endswith(".current-flow.v3")]
    if len(rules) != 5 or not all(r.enabled for r in rules):
        raise ValueError("complete current-flow cohort required")
    baseline = baseline_record["artifact"]["rules"]
    phases = current_flow_strategy_rules([*baseline, *(r.to_dict() for r in rules)], source_deployment)
    artifact = _additive_strategy_artifact(baseline_record, source_deployment, [*rules, *phases],
        "native-current-flow-migration-v1", extend_model_bridges=True)
    artifact["currentFlowUpgradeContract"] = artifact.pop("compositionUpgradeContract")
    return artifact
