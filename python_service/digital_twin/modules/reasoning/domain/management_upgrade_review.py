"""Explain a frozen additive policy migration without relaxing input parity."""

from copy import deepcopy
import hashlib
import json
import math

from digital_twin.modules.model_registry.contracts import rulebox_rules_hash
from digital_twin.modules.reasoning.domain.reasoning_comparison_identity import COMPARISON_INPUT_CONTRACT_VERSION, comparison_input_contract


MANAGEMENT_UPGRADE_REVIEW_VERSION = "management-rule-migration-review-v1"


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def management_review_fingerprint(comparison):
    return _hash({key: comparison.get(key) for key in (
        "status", "factParityPct", "ruleSlotCoveragePct", "unexplainedDecisionDifferenceCount",
        "shadowDeliveryCount", "decisionDifferences", "sourceInput", "baselineReleaseId",
        "candidateReleaseId", "candidateReleaseFingerprint", "validationCohortId",
    )})


def management_upgrade_review_valid(comparison):
    review = comparison.get("managementUpgradeReview") or {}
    if not isinstance(review, dict):
        return False
    source = comparison.get("sourceInput") or {}
    return bool(review.get("version") == MANAGEMENT_UPGRADE_REVIEW_VERSION
                and review.get("status") == "explained-policy-upgrade"
                and review.get("rawComparisonFingerprint") == management_review_fingerprint(comparison)
                and review.get("baselineArtifactFingerprint") and review.get("candidateArtifactFingerprint")
                and review.get("reviewedRuleIds") and review.get("nonManagementParity") == "equivalent"
                and source.get("contractVersion") == COMPARISON_INPUT_CONTRACT_VERSION and source.get("eligible") is True
                and comparison.get("status") in {"reasoning-parity-gap", "unexplained-difference"}
                and comparison.get("factParityPct") == comparison.get("ruleSlotCoveragePct") == 100.0
                and comparison.get("shadowDeliveryCount") == 0)


def _without_management(job):
    clean = deepcopy(job)
    result = clean.get("result") or {}
    rows = result.get("decision_syntheses") or result.get("decisionSyntheses") or []
    if not rows:
        raise ValueError("native-decision-synthesis-missing")
    for row in rows:
        row.pop("management_decision", None)
        row.pop("managementDecision", None)
    return clean


def _declared_additions(baseline, candidate, declaration):
    left = {row["rule_id"]: row for row in baseline["rules"]}
    right = {row["rule_id"]: row for row in candidate["rules"]}
    declared = {row["ruleId"]: row for row in declaration.get("rules") or []}
    changed = {key for key in set(left) | set(right) if left.get(key) != right.get(key)}
    if (not changed or changed != set(declared) or set(left) != set(right)
            or len(left) != len(baseline["rules"]) or len(right) != len(candidate["rules"])
            or len(declared) != len(declaration.get("rules") or [])):
        raise ValueError("undeclared-rule-change")
    additions = {}
    for rule_id in sorted(changed):
        before, after = deepcopy(left[rule_id]), deepcopy(right[rule_id])
        contract = declared[rule_id]
        if (contract.get("baselineRuleFingerprint") != _hash(before)
                or contract.get("candidateRuleFingerprint") != _hash(after)
                or before.get("conditions") != after.get("conditions")):
            raise ValueError("rule-condition-or-fingerprint-changed")
        for row in (before, after):
            claim = row.get("claim_contract") or {}
            if claim.get("claimType") != "risk-invariant" or claim.get("decisionAuthority") != "guardrail-only":
                raise ValueError("migration-is-not-a-guardrail")
            claim.pop("ruleFingerprint", None)
            row.pop("version", None)
            # Legacy manifests omitted these non-predictive observation defaults.
            outcome = (row.get("domain_manifest") or {}).get("outcomeContract") or {}
            for key in ("maximumObservationDelayMinutes", "requiredObservationDomains", "minimumIndependentEpisodes", "outcomeHorizonMinutes"):
                outcome.pop(key, None)
        for derivation in after.get("derivations") or []:
            response = derivation.pop("management_response", None)
            if response:
                if rule_id in additions or response.get("code") != contract.get("expectedResponseCode"):
                    raise ValueError("management-addition-not-declared")
                additions[rule_id] = response
        if before != after or rule_id not in additions:
            raise ValueError("non-management-rule-semantics-changed")
    return additions


def _verify_response(response, authored):
    if (response.get("code") != authored.get("code") or response.get("objective") != authored.get("objective")
            or response.get("releaseCondition") != authored.get("releaseCondition")
            or response.get("calculation") != authored.get("calculation")):
        raise ValueError("native-response-contract-mismatch")
    objective, facts = response.get("objective") or {}, response.get("boundFacts") or {}
    observed = facts.get(objective.get("field"))
    target = facts.get(objective["valueField"]) if objective.get("valueField") else objective.get("value")
    if (any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in (observed, target))
            or response.get("observedValue") != observed or response.get("targetValue") != target):
        raise ValueError("native-response-numeric-binding-mismatch")


def review_management_upgrade(baseline_job, candidate_job, comparison, baseline_record, candidate_record):
    receipt = {"version": MANAGEMENT_UPGRADE_REVIEW_VERSION, "status": "not-explained"}
    try:
        if comparison.get("status") not in {"reasoning-parity-gap", "unexplained-difference"}:
            raise ValueError("comparison-not-a-policy-difference")
        source = comparison.get("sourceInput") or {}
        captured = comparison_input_contract(baseline_job, candidate_job)
        if (source.get("contractVersion") != COMPARISON_INPUT_CONTRACT_VERSION or source.get("eligible") is not True
                or not captured["eligible"] or any(source.get(key) != value for key, value in captured.items())
                or comparison.get("factParityPct") != 100.0 or comparison.get("ruleSlotCoveragePct") != 100.0
                or comparison.get("shadowDeliveryCount") != 0):
            raise ValueError("input-native-rule-or-delivery-parity-incomplete")
        for record in (baseline_record, candidate_record):
            if record.get("valid") is not True or _hash(record.get("artifact")) != record.get("artifactFingerprint"):
                raise ValueError("frozen-artifact-unverified")
            artifact = record["artifact"]
            if not (record.get("ruleboxFingerprint") == artifact.get("ruleboxFingerprint") == rulebox_rules_hash(artifact["rules"])):
                raise ValueError("authored-rulebox-fingerprint-mismatch")
        baseline, candidate = baseline_record["artifact"], candidate_record["artifact"]
        declaration = candidate.get("managementUpgradeContract") or {}
        if (declaration.get("version") != "management-rule-migration-v1"
                or declaration.get("baselineDeploymentId") != baseline_job.get("deploymentId")
                or declaration.get("baselineArtifactFingerprint") != baseline_record["artifactFingerprint"]
                or declaration.get("baselineRuleboxFingerprint") != rulebox_rules_hash(baseline["rules"])
                or declaration.get("candidateRuleboxFingerprint") != rulebox_rules_hash(candidate["rules"])
                or baseline.get("tboxMetadata") != candidate.get("tboxMetadata")):
            raise ValueError("migration-declaration-or-baseline-mismatch")
        additions = _declared_additions(baseline, candidate, declaration)
        from digital_twin.modules.reasoning.domain.reasoning_shadow import compare_engine_outcomes, independent_reasoning_outcome_packet
        unchanged = compare_engine_outcomes(independent_reasoning_outcome_packet(_without_management(baseline_job)),
                                            independent_reasoning_outcome_packet(_without_management(candidate_job)), [source]).to_dict()
        if unchanged["status"] != "equivalent":
            raise ValueError("non-management-decision-parity-incomplete")
        packets = [independent_reasoning_outcome_packet(job) for job in (baseline_job, candidate_job)]
        old_responses = {(row["accountId"], row["symbol"], response["ruleId"]): response
                         for row in packets[0]["candidates"] for response in (row.get("managementDecision") or {}).get("responses") or []}
        retained, reviewed = set(), set()
        for row in packets[1]["candidates"]:
            management = row.get("managementDecision") or {}
            for response in management.get("responses") or []:
                key = (row["accountId"], row["symbol"], response["ruleId"])
                if key in old_responses:
                    if response != old_responses[key]:
                        raise ValueError("existing-management-response-changed")
                    retained.add(key)
                    continue
                if (response["ruleId"] not in additions or management.get("status") != "POLICY_RESPONSE_SELECTED"
                        or management.get("authority") != "typedb-materialized-account-policy"
                        or management.get("forecastActionSelected") is not False or management.get("orderAuthorized") is not False):
                    raise ValueError("native-policy-authority-or-response-unverified")
                _verify_response(response, additions[response["ruleId"]])
                reviewed.add(response["ruleId"])
        if set(old_responses) != retained or not reviewed:
            raise ValueError("management-response-deleted-or-addition-missing")
        return {**receipt, "status": "explained-policy-upgrade", "nonManagementParity": "equivalent",
                "reviewedRuleIds": sorted(reviewed), "rawComparisonFingerprint": management_review_fingerprint(comparison),
                "baselineArtifactFingerprint": baseline_record["artifactFingerprint"],
                "candidateArtifactFingerprint": candidate_record["artifactFingerprint"]}
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        return {**receipt, "reasonCode": str(error)[:160]}
