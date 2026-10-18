import unittest
from copy import deepcopy

from digital_twin.modules.model_registry.contracts import rulebox_rules_hash
from digital_twin.modules.market_data.contracts import payload_fingerprint
from digital_twin.modules.reasoning.domain.management_upgrade_review import review_management_upgrade, management_upgrade_review_valid
from digital_twin.modules.reasoning.domain.reasoning_comparison_identity import comparison_input_contract
from digital_twin.modules.reasoning.domain.reasoning_shadow import compare_engine_outcomes, independent_reasoning_outcome_packet, payload_hash, reasoning_comparison_summary
from test_reasoning_engine_versions import input_scopes


def fixtures():
    authored = {"code": "RESTORE_POSITION_LIMIT", "authority": "account-policy",
                "objective": {"field": "positionAccountWeight", "operator": "<=", "valueField": "strategyMaxPositionWeightPct"},
                "releaseCondition": "Fresh account weight returns within its limit", "calculation": "sale-proceeds-retained-in-account"}
    rule = {"rule_id": "policy-limit", "label": "계좌 비중 상한", "version": "v1", "conditions": [{"field": "weight", "value": 45}],
            "claim_contract": {"claimType": "risk-invariant", "decisionAuthority": "guardrail-only", "ruleFingerprint": "old"},
            "domain_manifest": {"outcomeContract": {"criteria": []}},
            "derivations": [{"relation_type": "BLOCKS_ACTION", "blocked_actions": ["ADD"]}]}
    updated = deepcopy(rule)
    updated["version"] = "v2"
    updated["claim_contract"]["ruleFingerprint"] = "new"
    updated["derivations"][0]["management_response"] = authored
    left_artifact = {"rules": [rule], "tboxMetadata": {"fingerprint": "same-tbox"}, "ruleboxFingerprint": rulebox_rules_hash([rule])}
    right_artifact = {"rules": [updated], "tboxMetadata": deepcopy(left_artifact["tboxMetadata"]), "ruleboxFingerprint": rulebox_rules_hash([updated])}
    right_artifact["managementUpgradeContract"] = {
        "version": "management-rule-migration-v1", "baselineDeploymentId": "baseline",
        "baselineArtifactFingerprint": payload_fingerprint(left_artifact), "baselineRuleboxFingerprint": left_artifact["ruleboxFingerprint"],
        "candidateRuleboxFingerprint": right_artifact["ruleboxFingerprint"], "rules": [{
            "ruleId": rule["rule_id"], "baselineRuleFingerprint": payload_hash(rule),
            "candidateRuleFingerprint": payload_hash(updated), "expectedResponseCode": authored["code"],
        }],
    }
    records = [{"valid": True, "artifact": artifact, "artifactFingerprint": payload_fingerprint(artifact),
                "ruleboxFingerprint": artifact["ruleboxFingerprint"]} for artifact in (left_artifact, right_artifact)]
    projection = {**input_scopes(), "status": "ok", "nativeTypeDbReasoningCompleted": True, "generationAligned": True,
                  "ruleboxExecution": {"typedbNativeRuleMatchedRuleIds": ["policy-limit"]},
                  "temporalFeatureInput": {"snapshotId": "temporal:1", "payloadHash": "temporal-payload",
                                           "windowsHash": "same-window", "backendId": "mysql", "featureSetVersion": "features",
                                           "asOf": "2026-10-18T00:00:00Z", "symbols": ["NVDA"]}}
    synthesis = {"account_id": "acct", "symbol": "NVDA", "graph_candidate_action": "NO_ACTION",
                 "blocked_actions": ["ADD"], "rulebox_judgment": {"sourceRuleIds": ["policy-limit"]}}
    job = {"jobId": "baseline-job", "deploymentId": "baseline", "scopeKey": "acct:NVDA", "sourceSnapshotId": "snapshot",
           "sourceSnapshotAt": "2026-10-18T00:00:00Z", "sourcePayloadHash": "source-payload",
           "sourceEventId": "source:1", "sourceEvent": {"payload": {"accountIds": ["acct"], "symbols": ["NVDA"]}},
           "result": {"projection_results": {"acct": projection}, "decision_syntheses": [synthesis]}}
    candidate = deepcopy(job)
    candidate.update(jobId="candidate-job", deploymentId="candidate")
    response = {"ruleId": "policy-limit", "code": authored["code"], "objective": deepcopy(authored["objective"]),
                "releaseCondition": authored["releaseCondition"], "calculation": authored["calculation"],
                "boundFacts": {"positionAccountWeight": 80, "strategyMaxPositionWeightPct": 45},
                "observedValue": 80, "targetValue": 45}
    candidate["result"]["decision_syntheses"][0]["management_decision"] = {
        "status": "POLICY_RESPONSE_SELECTED", "authority": "typedb-materialized-account-policy",
        "forecastActionSelected": False, "orderAuthorized": False, "responses": [response]}
    return job, candidate, records


def comparison(left, right):
    source = comparison_input_contract(left, right)
    result = compare_engine_outcomes(independent_reasoning_outcome_packet(left), independent_reasoning_outcome_packet(right), [source]).to_dict()
    result.update(sourceInput=source, baselineReleaseId="baseline-release", candidateReleaseId="candidate-release",
                  candidateReleaseFingerprint="candidate-frozen", validationCohortId="candidate-cohort")
    return result


def summary(result, duplicate=False):
    row = {**result, "comparisonId": "comparison", "payload": deepcopy(result), "createdAt": "2026-10-18T00:01:00Z"}
    return reasoning_comparison_summary([row, deepcopy(row)] if duplicate else [row])


class ManagementUpgradeReviewTests(unittest.TestCase):
    def test_declared_native_upgrade_preserves_raw_difference_and_qualifies_its_review(self):
        left, right, records = fixtures()
        raw = comparison(left, right)
        before = deepcopy(raw)
        self.assertNotEqual(payload_hash(records[0]["artifact"]), records[0]["artifactFingerprint"])
        for corrupt in ("wrong-canonical-contract", "changed-frozen-content"):
            altered = deepcopy(records)
            if corrupt == "wrong-canonical-contract":
                altered[0]["artifactFingerprint"] = payload_hash(altered[0]["artifact"])
            else:
                altered[0]["artifact"]["rules"][0]["label"] += " 변경"
            rejected = review_management_upgrade(left, right, raw, *altered)
            self.assertEqual("frozen-artifact-unverified", rejected["reasonCode"])
        review = review_management_upgrade(left, right, raw, *records)
        self.assertEqual("explained-policy-upgrade", review["status"], review)
        self.assertEqual(before, raw)
        self.assertEqual("reasoning-parity-gap", raw["status"])
        raw["managementUpgradeReview"] = review
        self.assertTrue(management_upgrade_review_valid(raw))
        result = summary(raw)
        self.assertEqual(1, result["sampleCount"])
        self.assertEqual(1, result["statusCounts"]["reasoning-parity-gap"])
        self.assertEqual(1, result["reviewedPolicyUpgradeSampleCount"])
        self.assertEqual(0, result["unreviewedReasoningParityGapCount"])
        self.assertEqual(1, summary(raw, duplicate=True)["unreviewedReasoningParityGapCount"])
        raw["decisionDifferences"][0]["changedFields"]["managementDecision"]["candidate"][0]["responses"][0]["targetValue"] = 50
        self.assertFalse(management_upgrade_review_valid(raw))
        self.assertEqual(1, summary(raw)["unreviewedReasoningParityGapCount"])

    def test_absent_intent_changed_conditions_and_changed_market_judgment_remain_unexplained(self):
        for change in ("absent-declaration", "changed-condition", "changed-market-action", "changed-action-envelope"):
            with self.subTest(change=change):
                left, right, records = fixtures()
                if change == "absent-declaration":
                    records[1]["artifact"].pop("managementUpgradeContract")
                elif change == "changed-condition":
                    artifact = records[1]["artifact"]
                    artifact["rules"][0]["conditions"][0]["value"] = 46
                    declaration = artifact["managementUpgradeContract"]
                    declaration["rules"][0]["candidateRuleFingerprint"] = payload_hash(artifact["rules"][0])
                    declaration["candidateRuleboxFingerprint"] = rulebox_rules_hash(artifact["rules"])
                    artifact["ruleboxFingerprint"] = declaration["candidateRuleboxFingerprint"]
                    records[1]["ruleboxFingerprint"] = artifact["ruleboxFingerprint"]
                elif change == "changed-market-action":
                    right["result"]["decision_syntheses"][0]["graph_candidate_action"] = "REDUCE"
                else:
                    right["result"]["decision_syntheses"][0]["allowed_actions"] = ["ADD"]
                records[1]["artifactFingerprint"] = payload_fingerprint(records[1]["artifact"])
                raw = comparison(left, right)
                review = review_management_upgrade(left, right, raw, *records)
                self.assertEqual("not-explained", review["status"])
                raw["managementUpgradeReview"] = review
                self.assertFalse(management_upgrade_review_valid(raw))
                self.assertGreater(summary(raw)["unreviewedReasoningParityGapCount"] + summary(raw)["unreviewedDecisionDifferenceCount"], 0)

    def test_incomplete_input_or_unverified_native_response_never_qualifies(self):
        for change in ("missing-input", "wrong-number", "missing-binding", "order-authorized", "unproven-authority", "wrong-response-code", "shadow-delivery"):
            with self.subTest(change=change):
                left, right, records = fixtures()
                management = right["result"]["decision_syntheses"][0]["management_decision"]
                response = management["responses"][0]
                if change == "missing-input":
                    right["result"]["projection_results"]["acct"]["persistedComparisonScope"]["complete"] = False
                elif change == "wrong-number":
                    response["targetValue"] = 50
                elif change == "missing-binding":
                    response["boundFacts"].pop("positionAccountWeight")
                elif change == "order-authorized":
                    management["orderAuthorized"] = True
                elif change == "unproven-authority":
                    management["authority"] = "python-narrative"
                elif change == "wrong-response-code":
                    response["code"] = "BUY"
                else:
                    right["result"].update(delivery_authorized=True, delivery_events=[{"rule": "investmentInsight"}])
                raw = comparison(left, right)
                review = review_management_upgrade(left, right, raw, *records)
                self.assertEqual("not-explained", review["status"], review)
                raw["managementUpgradeReview"] = review
                self.assertFalse(management_upgrade_review_valid(raw))
