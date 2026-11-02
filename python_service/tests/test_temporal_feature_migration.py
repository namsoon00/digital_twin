"""Feature releases preserve authored rules and require real source/native proofs."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from functools import lru_cache

from digital_twin.modules.market_data.contracts import payload_fingerprint, VALID_PRICE_PATH_FEATURE_VERSION
from digital_twin.modules.model_registry.contracts import governed_graph_inference_rules, rulebox_rules_hash, GraphInferenceRule
from digital_twin.modules.reasoning.domain.temporal_feature_migration import (
    LEGACY_FEATURE_VERSION, RECEIPT_VERSION, feature_source_proof,
    review_temporal_feature_migration, temporal_migration_review_valid, validate_migration_artifacts,
)
from digital_twin.infrastructure.composition.temporal_feature_release import temporal_feature_release_artifact
from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import ontology_release_seed_artifact
from digital_twin.modules.reasoning.domain.projection_facts import build_factual_graph
from digital_twin.modules.reasoning.domain.reasoning_shadow import reasoning_comparison_summary
from test_management_upgrade_review import fixtures as management_fixtures, comparison, summary
from test_reasoning_engine_versions import input_scopes


def artifact_record(artifact):
    return {"valid": True, "artifact": artifact, "artifactFingerprint": payload_fingerprint(artifact),
            "ruleboxFingerprint": artifact["ruleboxFingerprint"]}


@lru_cache(maxsize=1)
def _artifacts():
    rules = [r for r in governed_graph_inference_rules(filtered_temporal_paths=False)
             if r.rule_id in {"graph.temporal.stale_observation.block.v1", "graph.holding.loss_guard.v1"}]
    baseline = ontology_release_seed_artifact(rules, release_bundle={"feature_set_version": LEGACY_FEATURE_VERSION})
    record = artifact_record(baseline)
    candidate = temporal_feature_release_artifact(record, "baseline")
    return [record, artifact_record(candidate)]


def fixtures():
    records = deepcopy(_artifacts())
    left, right, _ = management_fixtures()
    right["result"]["decision_syntheses"] = deepcopy(left["result"]["decision_syntheses"])
    for job, version in ((left, LEGACY_FEATURE_VERSION), (right, VALID_PRICE_PATH_FEATURE_VERSION)):
        p = job["result"]["projection_results"]["acct"]
        p.update(input_scopes(value=version))
        p["temporalFeatureInput"]["featureSetVersion"] = version
        p["temporalFeatureMigrationInput"] = {
            "version": RECEIPT_VERSION, "featureSetVersion": version, "accountId": "acct",
            "asOf": job["sourceSnapshotAt"], "legacyFactualFingerprint": "common-legacy-facts",
            "actualFactualFingerprint": version, "nonTemporalFingerprint": "common-non-temporal",
            "nonTemporalUnchanged": True, "rawWindowsFingerprint": "same-complete-windows",
        }
    return left, right, records


class TemporalFeatureMigrationTests(TestCase):
    def test_registration_binds_new_feature_without_rewriting_active_delivery(self):
        from digital_twin.modules.reasoning.application.reasoning_engine_platform import ReasoningEnginePlatformService
        from digital_twin.modules.reasoning.domain.reasoning_engine_versions import EngineControlState, EngineReleaseBundle
        _, _, records = fixtures()
        class Registry:
            def __init__(self):
                self.rows = {"baseline": {"deploymentId": "baseline", "engineVersion": "v2", "status": "active",
                    "graphStoreBinding": "production-db", "timeSeriesBackendId": "mysql-primary",
                    "releaseBundle": EngineReleaseBundle("tbox", "rulebox", "prompt", LEGACY_FEATURE_VERSION).to_dict()}}
                self.state = EngineControlState("baseline", "baseline", "", 1)
                self.saved = {}
            def get(self, key): return self.rows.get(key, {})
            def list(self): return list(self.rows.values())
            def control(self): return self.state
            def release_artifact(self, key): return records[0] if key == "baseline" else {}
            def upsert(self, descriptor): self.rows[descriptor.deployment_id] = descriptor.to_dict()
            def set_control(self, active, delivery, candidate, expected_version=None):
                self.state = EngineControlState(active, delivery, candidate, 2)
                return self.state
            def update_capabilities(self, key, value): self.rows[key]["capabilities"] = value
            def update_health(self, key, value): self.rows[key]["health"] = value
            def save_release_artifact(self, key, value):
                self.saved[key] = deepcopy(value)
                return {"status": "saved", **{k: v for k, v in artifact_record(value).items() if k != "artifact"}}
        registry = Registry()
        platform = ReasoningEnginePlatformService(registry, {"_runtimeIdentity": {"revision": "successor-code"}})
        bad = platform.register_v2_release("candidate", "release", feature_set_version="unknown-features")
        self.assertEqual("blocked", bad["status"])
        self.assertNotIn("candidate", registry.rows)
        missing_binding = platform.register_v2_release("candidate", "release", release_seed_artifact=records[1]["artifact"],
            expected_baseline_deployment_id="baseline")
        self.assertEqual(["declared-feature-version-not-selected"], missing_binding["blockers"])
        result = platform.register_v2_release("candidate", "release", release_seed_artifact=records[1]["artifact"],
            expected_baseline_deployment_id="baseline", feature_set_version=VALID_PRICE_PATH_FEATURE_VERSION)
        self.assertEqual("registered", result["status"], result)
        self.assertEqual(VALID_PRICE_PATH_FEATURE_VERSION, registry.saved["candidate"]["releaseBundle"]["feature_set_version"])
        self.assertEqual(LEGACY_FEATURE_VERSION, registry.rows["baseline"]["releaseBundle"]["feature_set_version"])
        self.assertEqual("baseline", registry.control().delivery_deployment_id)
        self.assertNotEqual("production-db", registry.rows["candidate"]["graphStoreBinding"])

    def test_review_preserves_raw_failure_and_qualifies_only_declared_migration(self):
        left, right, records = fixtures()
        raw = comparison(left, right)
        original = deepcopy(raw)
        review = review_temporal_feature_migration(left, right, raw, *records)
        self.assertEqual("explained-feature-migration", review["status"], review)
        self.assertEqual(original, raw)
        self.assertLess(raw["factParityPct"], 100)
        self.assertFalse(raw["sourceInput"]["eligible"])
        raw["temporalFeatureMigrationReview"] = review
        self.assertTrue(temporal_migration_review_valid(raw))
        result = summary(raw)
        self.assertEqual(1, result["sampleCount"])
        self.assertEqual(1, result["reviewedFeatureMigrationSampleCount"])
        self.assertEqual(0, result["unreviewedFactParityGapCount"])
        self.assertLess(result["minimumFactParityPct"], 100)
        self.assertEqual(1, summary(raw, duplicate=True)["unreviewedFactParityGapCount"])
        raw["sourceInput"]["candidate"]["featureMigrationInputs"][0]["proof"]["rawWindowsFingerprint"] = "tampered"
        self.assertFalse(temporal_migration_review_valid(raw))
        self.assertEqual(0, summary(raw)["sampleCount"])

    def test_missing_or_altered_evidence_never_becomes_promotion_evidence(self):
        for change in ("snapshot", "windows", "non-temporal", "missing-proof", "missing-manifest", "persisted-mismatch",
                       "wrong-feature", "wrong-clock", "native-failure", "shadow-delivery", "action", "allowed-action",
                       "other-rule", "declaration", "artifact", "condition"):
            with self.subTest(change=change):
                left, right, records = fixtures()
                p = right["result"]["projection_results"]["acct"]
                proof = p["temporalFeatureMigrationInput"]
                if change == "snapshot": right["sourcePayloadHash"] = "changed"
                elif change == "windows": proof["rawWindowsFingerprint"] = "changed"
                elif change == "non-temporal": proof["nonTemporalUnchanged"] = False
                elif change == "missing-proof": p.pop("temporalFeatureMigrationInput")
                elif change == "missing-manifest": p["comparisonScope"]["complete"] = False
                elif change == "persisted-mismatch": p["persistedComparisonScope"] = input_scopes(value="different")["persistedComparisonScope"]
                elif change == "wrong-feature": proof["featureSetVersion"] = LEGACY_FEATURE_VERSION
                elif change == "wrong-clock": proof["asOf"] = "2026-10-19T00:00:00Z"
                elif change == "native-failure": p["nativeTypeDbReasoningCompleted"] = False
                elif change == "shadow-delivery": right["result"].update(delivery_authorized=True, delivery_events=[{"rule": "investmentInsight"}])
                elif change == "action": right["result"]["decision_syntheses"][0]["graph_candidate_action"] = "BUY"
                elif change == "allowed-action": right["result"]["decision_syntheses"][0]["allowed_actions"] = ["BUY"]
                elif change == "other-rule": p["ruleboxExecution"]["typedbNativeRuleMatchedRuleIds"].append("unrelated-new-rule")
                elif change == "declaration": records[1]["artifact"].pop("temporalFeatureMigration")
                elif change == "artifact": records[1]["artifact"]["tboxMetadata"]["version"] = "changed"
                elif change == "condition":
                    records[1]["artifact"]["rules"][0]["conditions"][1]["target_property_filters"]["validObservationRatio"]["value"] = 0.1
                    a = records[1]["artifact"]
                    a["ruleboxFingerprint"] = rulebox_rules_hash(a["rules"])
                    a["temporalFeatureMigration"]["candidateRuleboxFingerprint"] = a["ruleboxFingerprint"]
                if change in {"declaration", "artifact", "condition"}:
                    records[1] = artifact_record(records[1]["artifact"])
                raw = comparison(left, right)
                raw["temporalFeatureMigrationReview"] = review_temporal_feature_migration(left, right, raw, *records)
                self.assertFalse(temporal_migration_review_valid(raw), raw["temporalFeatureMigrationReview"])
                self.assertEqual(0, summary(raw)["sampleCount"])

    def test_authoring_keeps_unrelated_rules_and_baseline_immutable(self):
        _, _, records = fixtures()
        baseline = records[0]["artifact"]
        original = deepcopy(baseline)
        candidate = temporal_feature_release_artifact(records[0], "baseline")
        self.assertEqual(original, baseline)
        self.assertEqual(len(baseline["rules"]), len(candidate["rules"]))
        self.assertEqual(baseline["tboxMetadata"], candidate["tboxMetadata"])
        declared = set(candidate["temporalFeatureMigration"]["changedRuleIds"])
        for before, after in zip(baseline["rules"], candidate["rules"]):
            if before["rule_id"] not in declared:
                self.assertEqual(before, after)
            self.assertEqual(before["derivations"], after["derivations"])
        validate_migration_artifacts(records[0], candidate, "baseline")

    def test_source_proof_compares_full_factual_input_and_detects_unrelated_changes(self):
        from test_reasoning_snapshot_replay import monitor_state
        from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state
        snapshot = account_snapshot_from_monitor_state(monitor_state())
        prepared = SimpleNamespace(observation_input=snapshot.projection_observation_input(),
            projection_external_signals={}, active_tbox={}, runtime_context={"asOf": snapshot.generated_at,
                "settings": {"_reasoningFeatureSetVersion": LEGACY_FEATURE_VERSION}})
        def graph():
            return build_factual_graph(snapshot, prepared.observation_input, {}, prepared.runtime_context, {}, {})
        legacy = feature_source_proof(graph(), prepared, snapshot)
        prepared.runtime_context["settings"]["_reasoningFeatureSetVersion"] = VALID_PRICE_PATH_FEATURE_VERSION
        built = graph()
        current = feature_source_proof(built, prepared, snapshot)
        self.assertTrue(current["nonTemporalUnchanged"])
        self.assertEqual(legacy["legacyFactualFingerprint"], current["legacyFactualFingerprint"])
        self.assertNotEqual(legacy["actualFactualFingerprint"], current["actualFactualFingerprint"])
        next(e for e in built.entities if e.kind == "stock").properties["currentPrice"] = 999
        self.assertFalse(feature_source_proof(built, prepared, snapshot)["nonTemporalUnchanged"])
