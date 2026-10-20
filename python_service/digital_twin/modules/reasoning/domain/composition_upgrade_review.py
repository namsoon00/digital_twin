"""Explain only declared, additive context rules in a paired release replay.

Original investment predicates, ABox facts, action envelopes and candidate
qualification must remain identical. Raw comparison failures are retained.
"""

from copy import deepcopy
import json

from digital_twin.modules.market_data.contracts import payload_fingerprint
from digital_twin.modules.model_registry.contracts import GraphInferenceRule, composition_response_violations, composition_rules, rulebox_rules_hash
from digital_twin.modules.reasoning.domain.reasoning_comparison_identity import COMPARISON_INPUT_CONTRACT_VERSION, comparison_input_contract
from digital_twin.modules.reasoning.domain.management_upgrade_review import management_review_fingerprint


VERSION = "native-composition-migration-review-v1"


def composition_upgrade_review_valid(comparison):
    review = comparison.get("compositionUpgradeReview") or {}
    return (review.get("version") == VERSION and review.get("status") == "explained-context-composition"
            and review.get("rawComparisonFingerprint") == management_review_fingerprint(comparison)
            and review.get("unchangedDecisionParity") == "equivalent"
            and bool(review.get("reviewedRuleIds")) and bool(review.get("baselineArtifactFingerprint"))
            and bool(review.get("candidateArtifactFingerprint"))
            and comparison.get("status") in {"reasoning-parity-gap", "unexplained-difference"}
            and (comparison.get("sourceInput") or {}).get("contractVersion") == COMPARISON_INPUT_CONTRACT_VERSION
            and (comparison.get("sourceInput") or {}).get("eligible") is True
            and comparison.get("factParityPct") == 100.0
            and comparison.get("ruleSlotCoveragePct") == 100.0
            and comparison.get("shadowDeliveryCount") == 0)


def declared_composition_additions(baseline, candidate, declaration):
    left = {r["rule_id"]: r for r in baseline["rules"]}
    right = {r["rule_id"]: r for r in candidate["rules"]}
    added = set(right) - set(left)
    if (len(left) != len(baseline["rules"]) or len(right) != len(candidate["rules"])
            or added != set(declaration.get("addedRuleIds") or []) or len(added) != 3
            or any(right.get(k) != v for k, v in left.items())):
        raise ValueError("existing-rules-changed-or-additions-undeclared")
    expected = {r.rule_id: GraphInferenceRule.from_dict(r.to_dict()).to_dict() for r in composition_rules([GraphInferenceRule.from_dict(r) for r in baseline["rules"]])}
    if any(right[rid] != expected.get(rid) for rid in added):
        raise ValueError("composition-is-not-the-declared-native-contract")
    for rid in added:
        rule = GraphInferenceRule.from_dict(right[rid])
        if (not rid.startswith("graph.composition.") or not rule.enabled
                or len(rule.derivations) != 1 or not rule.derivations[0].composition_response
                or composition_response_violations(rule, rule.derivations[0])):
            raise ValueError("addition-is-not-an-actionless-native-composition")
    old_nodes = {n["id"]: n for n in baseline["graph"]["entities"] if (n.get("properties") or {}).get("ontologyBox") == "TBox"}
    new_nodes = {n["id"]: n for n in candidate["graph"]["entities"] if (n.get("properties") or {}).get("ontologyBox") == "TBox"}
    if (new_nodes != old_nodes or declaration.get("tboxUnchanged") is not True
            or candidate.get("tboxMetadata") != baseline.get("tboxMetadata")
            or candidate.get("tboxFingerprint") != baseline.get("tboxFingerprint")):
        raise ValueError("undeclared-tbox-change")
    old_graph = {n["id"]: n for n in baseline["graph"]["entities"]}
    new_graph = {n["id"]: n for n in candidate["graph"]["entities"]}
    old_edges = {json.dumps(e, sort_keys=True) for e in baseline["graph"]["relations"]}
    new_edges = {json.dumps(e, sort_keys=True) for e in candidate["graph"]["relations"]}
    if (any(new_graph.get(k) != v for k, v in old_graph.items()) or not old_edges.issubset(new_edges)
            or any(e["source"] in old_graph and e["target"] in old_graph
                   for e in map(json.loads, new_edges - old_edges))):
        raise ValueError("existing-static-ontology-changed")
    return added


def _without_context_references(packet, added):
    from digital_twin.modules.reasoning.domain.reasoning_shadow import payload_hash
    result = deepcopy(packet)
    for row in result.get("candidates") or []:
        if row.get("selectedRuleId") in added:
            raise ValueError("context-rule-selected-an-investment-action")
        row["ruleIds"] = [rid for rid in row.get("ruleIds") or [] if rid not in added]
        row["relationSlots"] = [slot for slot in row.get("relationSlots") or []
                                if not set(slot.split("|")).intersection(added)]
        row["decisionSignature"] = payload_hash({k: row[k] for k in (
            "candidateAction", "selectedRuleId", "decisionStage", "decisionEffect",
            "actionGroup", "judgementBlocked", "reviewLevel", "dataState",
            "validationState", "ruleIds", "relationSlots", "semanticFactsHash")})
        row["evidenceSignature"] = payload_hash({k: row[k] for k in
                                               ("semanticFactsHash", "ruleIds", "relationSlots")})
    for row in result.get("projections") or []:
        row["nativeMatchedRuleIds"] = [rid for rid in row.get("nativeMatchedRuleIds") or [] if rid not in added]
    return result


def review_composition_upgrade(baseline_job, candidate_job, comparison, baseline_record, candidate_record):
    receipt = {"version": VERSION, "status": "not-explained"}
    try:
        source = comparison_input_contract(baseline_job, candidate_job)
        if (not source["eligible"] or any((comparison.get("sourceInput") or {}).get(k) != v for k, v in source.items())
                or source.get("contractVersion") != COMPARISON_INPUT_CONTRACT_VERSION
                or comparison.get("factParityPct") != 100.0 or comparison.get("ruleSlotCoveragePct") != 100.0
                or comparison.get("shadowDeliveryCount") != 0):
            raise ValueError("paired-input-or-native-parity-incomplete")
        for record in (baseline_record, candidate_record):
            if (record.get("valid") is not True or payload_fingerprint(record.get("artifact")) != record.get("artifactFingerprint")
                    or record.get("ruleboxFingerprint") != rulebox_rules_hash(record["artifact"]["rules"])):
                raise ValueError("immutable-artifact-unverified")
        baseline, candidate = baseline_record["artifact"], candidate_record["artifact"]
        declaration = candidate.get("compositionUpgradeContract") or {}
        if (declaration.get("version") != "native-composition-migration-v1"
                or declaration.get("baselineDeploymentId") != baseline_job.get("deploymentId")
                or declaration.get("baselineArtifactFingerprint") != baseline_record["artifactFingerprint"]
                or declaration.get("baselineRuleboxFingerprint") != baseline["ruleboxFingerprint"]
                or declaration.get("candidateRuleboxFingerprint") != candidate["ruleboxFingerprint"]):
            raise ValueError("composition-migration-declaration-mismatch")
        added = declared_composition_additions(baseline, candidate, declaration)
        from digital_twin.modules.reasoning.domain.reasoning_shadow import compare_engine_outcomes, independent_reasoning_outcome_packet
        original = [independent_reasoning_outcome_packet(j) for j in (baseline_job, candidate_job)]
        normalized = [_without_context_references(p, added) for p in original]
        if compare_engine_outcomes(*normalized, [source]).to_dict()["status"] != "equivalent":
            raise ValueError("investment-actions-or-original-evidence-changed")
        matched = added & {rid for p in original[1].get("projections") or [] for rid in p.get("nativeMatchedRuleIds") or []}
        if not matched:
            raise ValueError("native-composition-match-not-demonstrated")
        return {**receipt, "status": "explained-context-composition", "unchangedDecisionParity": "equivalent",
                "reviewedRuleIds": sorted(matched), "rawComparisonFingerprint": management_review_fingerprint(comparison),
                "baselineArtifactFingerprint": baseline_record["artifactFingerprint"],
                "candidateArtifactFingerprint": candidate_record["artifactFingerprint"]}
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        return {**receipt, "reasonCode": str(error)[:160]}
