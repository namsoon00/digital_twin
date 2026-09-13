import copy
import hashlib
import json
import sqlite3
import unittest
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.infrastructure.ontology_evolution_runtime import claim_binding, OntologyEvolutionRuntime
from digital_twin.modules.model_registry.domain.experiment_observations import (
    observation_requirements, assess_requirement, freeze_dataset, validate_dataset,
)
from digital_twin.modules.model_registry.domain.ontology_evolution import create_plan, fingerprint
from digital_twin.modules.model_registry.infrastructure.evolution_policy import evolution_policy
from digital_twin.modules.model_registry.infrastructure.experiment_inputs import source_packet, metric_sample, read_dataset, capabilities
from digital_twin.modules.model_registry.infrastructure.experiment_observation_writes import capture_prediction, capture_outcome
from digital_twin.modules.model_registry.infrastructure.mysql_experiment_observations import MySQLExperimentObservationStore, active_observation_plans
from digital_twin.modules.model_registry.contracts import GraphInferenceRule, default_graph_inference_rules
from digital_twin.modules.outcomes.contracts import ShadowHypothesisObservationEpisode
from digital_twin.modules.decisions.contracts import FactDelta


START = "2026-09-13T00:00:00Z"
NOW = "2026-09-13T00:10:00Z"


def fixture_plan(extra=None):
    baseline = next(row for row in default_graph_inference_rules() if row.resolved_claim_contract.is_predictive).to_dict()
    candidate = copy.deepcopy(baseline)
    candidate["rule_id"] = "graph.experiment.fixture.v1"
    claim = GraphInferenceRule.from_dict(candidate).resolved_claim_contract.to_dict()
    claim.update({"claimContractId": "claim:experiment", "ruleId": candidate["rule_id"]})
    candidate["claim_contract"] = claim
    candidate["model_input_contract"] = {"observationRequirements": extra or []}
    case = SimpleNamespace(case_id="case:fixture", account_id="fixture", symbol="TEST",
                           supporting_evidence_ids=["evidence:fixture"], counter_evidence_ids=[],
                           invalidation_conditions=["opposite movement"], validation_requirements=[])
    return create_plan(case, candidate, {"deploymentId": "base", "artifactFingerprint": "basehash",
                                        "candidateClaim": claim_binding(candidate), "comparisonClaim": claim_binding(baseline),
                                        "comparisonHorizonMinutes": 60,
                                        "observationRequirements": observation_requirements(candidate, baseline)},
                       evolution_policy(), START), baseline


def source_row(price=100, at="2026-09-13T00:05:00Z", recorded="2026-09-13T00:05:02Z"):
    payload = {"accountId": "fixture", "generatedAt": at,
               "positions": {"TEST": {"symbol": "TEST", "current_price": price, "volume": 0,
                                      "quantity": 1, "profit_loss_rate": 0, "currency": "USD",
                                      "source_as_of": at, "source_fetched_at": recorded}}}
    hash_ = hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return dict(snapshot_id="source:" + at, account_id="fixture", generated_at=at, created_at=recorded,
                contract_version="reasoning-source-snapshot-v1", mode="live", fingerprint=hash_,
                symbols_json='["TEST"]', payload_json=json.dumps(payload))


class SqliteBoundConnection:
    """Portable transaction tests; the real MySQL schema is checked separately."""
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = lambda cursor, row: {col[0]: row[i] for i, col in enumerate(cursor.description)}
        self.db.create_function("LEAST", 2, min)
        self.db.executescript('''
            CREATE TABLE verified_reasoning_source_snapshots (snapshot_id TEXT PRIMARY KEY, account_id TEXT,
                generated_at TEXT, created_at TEXT, contract_version TEXT, mode TEXT, fingerprint TEXT, symbols_json TEXT, payload_json TEXT);
            CREATE TABLE ontology_experiment_observation_plans (plan_fingerprint TEXT PRIMARY KEY, account_id TEXT, symbol TEXT,
                deployment_id TEXT, status TEXT, plan_json TEXT, monitoring_from TEXT, created_at TEXT, capture_until TEXT, expires_at TEXT);
            CREATE TABLE ontology_experiment_datasets (plan_fingerprint TEXT, dataset_id TEXT, source_snapshot_id TEXT,
                payload_json TEXT, created_at TEXT, expires_at TEXT, PRIMARY KEY(plan_fingerprint, dataset_id), UNIQUE(plan_fingerprint, source_snapshot_id));
            CREATE TABLE ontology_experiment_dataset_members (plan_fingerprint TEXT, phase_at TEXT, bucket_key TEXT, side TEXT,
                episode_id TEXT, dataset_id TEXT, input_status TEXT, input_reason TEXT, episode_json TEXT, outcome_json TEXT,
                created_at TEXT, expires_at TEXT, PRIMARY KEY(plan_fingerprint, phase_at, bucket_key, side));
        ''')

    def execute(self, sql, params=()):
        return self.db.execute(sql.replace("%s", "?").replace(" FOR UPDATE", "").replace("INSERT IGNORE", "INSERT OR IGNORE"), params)

    @contextmanager
    def connect(self):
        yield self

    @contextmanager
    def transaction(self):
        with self.db:
            yield self

    def insert_source(self, row):
        self.execute("INSERT INTO verified_reasoning_source_snapshots (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))


class ExperimentObservationTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.baseline = fixture_plan()
        self.connection = SqliteBoundConnection()
        self.connection.insert_source(source_row())
        self.store = object.__new__(MySQLExperimentObservationStore)
        self.store.connect = self.connection.connect
        self.store.transaction = self.connection.transaction
        self.store.register(self.plan, "candidate")

    def tearDown(self):
        self.connection.db.close()

    def episode(self, side="candidate", **changes):
        rule = self.plan["candidateRule"] if side == "candidate" else self.baseline
        claim = GraphInferenceRule.from_dict(rule).resolved_claim_contract.to_dict()
        source = source_row()
        value = ShadowHypothesisObservationEpisode(episode_id="episode:" + side, candidate_set_id="set:1", account_id="fixture", symbol="TEST",
            hypothesis_id="hypothesis:" + side, claim_contract_id=claim["claimContractId"], claim_identity=claim["claimContractId"],
            source_abox_snapshot_id="abox:1", observed_from_at=source["generated_at"],
            independence_bucket="day:1", market_independence_key="event:1", readiness={"eligible": True},
            hypothesis={"claimContract": claim}, outcome_contract={"contractFingerprint": "outcome:fixture"},
            input_provenance={"experimentPlanFingerprint": self.plan["fingerprint"], "experimentSide": side,
                              "deploymentId": "candidate", "sourceBoundaries": [{"accountId": "fixture", "snapshotId": source["snapshot_id"], "fingerprint": source["fingerprint"]}]})
        return replace(value, **changes)

    def outcome(self, side="candidate"):
        payload = {"horizonMinutes": 60, "contractFingerprint": "outcome:fixture", "calibrationEligibility": "eligible",
                   "sourceAboxSnapshotId": "abox:1", "targetAt": "2026-09-13T01:05:00Z"}
        value = {"outcomeId": "outcome:" + side, "observedAt": "2026-09-13T01:05:00Z",
                 "selectedHypothesisStatus": "corroborated" if side == "candidate" else "contradicted", "payload": payload}
        return SimpleNamespace(payload=payload, to_dict=lambda: value)

    def test_requirements_are_versioned_and_keep_both_windows(self):
        extra = [{"metric": "price", "lookbackMinutes": 60}, {"metric": "price", "lookbackMinutes": 120}]
        plan, _ = fixture_plan(extra)
        self.assertEqual([0, 60, 120], [r["lookbackMinutes"] for r in plan["observationRequirements"]["inputs"]])
        self.assertTrue(plan["observationRequirements"]["outcomes"])
        self.assertNotEqual(self.plan["fingerprint"], plan["fingerprint"])

    def test_invalid_requirements_cannot_override_source_identity(self):
        for extra in ([{"metric": "source-packet"}], [{"metric": "price", "cadenceSeconds": 0}], [{"metric": "price", "minimumSamples": True}]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                fixture_plan(extra)

    def test_future_correction_cannot_rewrite_a_past_observation(self):
        requirement = self.plan["observationRequirements"]["inputs"][0]
        sample = {"observedAt": START, "recordedAt": START, "unit": "packet", "value": "original"}
        corrected = {**sample, "recordedAt": NOW, "value": "corrected"}
        result = assess_requirement(requirement, capabilities()["source-packet"], [corrected, sample], as_of=START, known_at=START)
        self.assertEqual("original", result["samples"][0]["value"])
        self.assertEqual("ready", result["state"])

    def test_missing_or_nonfinite_value_is_not_zero(self):
        requirement = {**self.plan["observationRequirements"]["inputs"][0], "metric": "volume"}
        for value in (None, float("nan"), float("inf"), True):
            sample = {"observedAt": START, "recordedAt": START, "value": value, "unit": "shares"}
            result = assess_requirement(requirement, capabilities()["volume"], [sample], as_of=START, known_at=START)
            self.assertEqual("future-collection", result["state"])
        self.assertEqual(0, metric_sample(source_packet(source_row()), "TEST", "volume")["value"])

    def test_unsupported_and_lost_history_have_distinct_states(self):
        req = self.plan["observationRequirements"]["inputs"][0]
        self.assertEqual("unsupported", assess_requirement(req, None, [], as_of=START, known_at=START)["state"])
        self.assertEqual("historical-unrecoverable", assess_requirement(req, capabilities()["source-packet"], [], as_of=START, known_at=START, historical=True)["state"])
        self.assertEqual("unsupported", assess_requirement({**req, "lookbackMinutes": 1441}, capabilities()["source-packet"], [], as_of=START, known_at=START)["state"])

    def test_sparse_rows_do_not_satisfy_requested_frequency(self):
        req = {"metric": "price", "lookbackMinutes": 10, "minimumSamples": 2, "cadenceSeconds": 60, "maximumDelayMinutes": 1}
        samples = [{"observedAt": at, "recordedAt": at, "unit": "quote-currency", "value": 100} for at in (START, NOW)]
        result = assess_requirement(req, capabilities()["price"], samples, as_of=NOW, known_at=NOW)
        self.assertEqual("cadence-gap", result["reason"])

    def test_wrong_unit_and_clock_order_are_rejected(self):
        req = self.plan["observationRequirements"]["inputs"][0]
        self.assertEqual(0, assess_requirement(req, capabilities()["source-packet"], [{"value": 1, "unit": "USD", "observedAt": START, "recordedAt": START}], as_of=START, known_at=START)["sampleCount"])
        with self.assertRaises(ValueError):
            assess_requirement(req, capabilities()["source-packet"], [], as_of=NOW, known_at=START)

    def test_source_full_hash_and_subject_boundary_are_verified(self):
        row = source_row()
        row["payload_json"] = row["payload_json"].replace("100", "101")
        with self.assertRaisesRegex(ValueError, "content-mismatch"):
            source_packet(row)
        source = source_packet(source_row())
        for change in ({"accountId": "someone-else"}, {"symbols": ["OTHER"]}):
            with self.assertRaises(ValueError):
                freeze_dataset(self.plan, {**source, **change}, [], captured_at=NOW)

    def test_price_uses_provider_time_not_repeated_poll_time(self):
        source = source_packet(source_row())
        source["payload"]["positions"]["TEST"]["source_as_of"] = START
        self.assertEqual(START, metric_sample(source, "TEST", "price")["observedAt"])
        source["payload"]["positions"]["TEST"]["source_as_of"] = ""
        self.assertIsNone(metric_sample(source, "TEST", "price"))
        self.assertIsNone(metric_sample(source, "OTHER", "price"))

    def test_input_is_copied_not_only_hashed_and_cannot_mutate(self):
        dataset = read_dataset(self.connection, self.plan, {"snapshotId": source_row()["snapshot_id"]}, NOW)
        self.assertEqual(100, dataset["source"]["payload"]["positions"]["TEST"]["current_price"])
        validate_dataset(dataset)
        dataset["source"]["payload"]["positions"]["TEST"]["current_price"] = 101
        with self.assertRaises(ValueError):
            validate_dataset(dataset)

    def test_two_claims_share_one_input_and_survive_source_cleanup(self):
        for side in ("candidate", "baseline"):
            capture_prediction(self.connection, self.episode(side), NOW)
        self.assertEqual(1, self.connection.execute("SELECT COUNT(*) AS n FROM ontology_experiment_datasets").fetchone()["n"])
        self.connection.execute("DELETE FROM verified_reasoning_source_snapshots")
        for side in ("candidate", "baseline"):
            capture_outcome(self.connection, self.episode(side), self.outcome(side), "2026-09-13T01:06:00Z")
        with patch('digital_twin.modules.model_registry.infrastructure.mysql_experiment_observations.utc_now_iso', return_value="2026-09-13T01:06:00Z"):
            result = self.store.comparison(self.plan)
        self.assertTrue(result["pairs"][0]["eligible"])
        self.assertEqual("corroborated", result["pairs"][0]["candidateOutcome"])
        self.assertEqual(1, result["dataSummary"]["capturedInputs"])

    def test_later_poll_does_not_replace_first_failed_anchor(self):
        missing = self.episode(input_provenance={**self.episode().input_provenance, "sourceBoundaries": []})
        capture_prediction(self.connection, missing, NOW)
        capture_prediction(self.connection, self.episode(episode_id="later"), NOW)
        row = self.connection.execute("SELECT * FROM ontology_experiment_dataset_members").fetchone()
        self.assertEqual("episode:candidate", row["episode_id"])
        self.assertEqual("unavailable", row["input_status"])

    def test_unknown_old_input_never_falls_back_to_latest(self):
        episode = self.episode(input_provenance={**self.episode().input_provenance, "sourceBoundaries": [{"accountId": "fixture", "snapshotId": "gone"}]})
        capture_prediction(self.connection, episode, NOW)
        row = self.connection.execute("SELECT * FROM ontology_experiment_dataset_members").fetchone()
        self.assertEqual("historical-source-packet-unavailable", row["input_reason"])
        self.assertEqual("", row["dataset_id"])

    def test_wrong_claim_and_deployment_cannot_join_experiment(self):
        for changes in ({"claim_contract_id": "wrong"}, {"input_provenance": {**self.episode().input_provenance, "deploymentId": "active-other"}}):
            with self.assertRaises(ValueError):
                capture_prediction(self.connection, self.episode(**changes), NOW)

    def test_outcomes_are_separate_and_first_final_result_is_immutable(self):
        capture_prediction(self.connection, self.episode(), NOW)
        outcome = self.outcome()
        capture_outcome(self.connection, self.episode(), outcome, "2026-09-13T01:06:00Z")
        changed = copy.deepcopy(outcome.to_dict())
        changed["selectedHypothesisStatus"] = "contradicted"
        capture_outcome(self.connection, self.episode(), SimpleNamespace(payload=outcome.payload, to_dict=lambda: changed), "2026-09-13T01:06:00Z")
        saved = self.connection.execute("SELECT outcome_json FROM ontology_experiment_dataset_members").fetchone()
        self.assertEqual("corroborated", json.loads(saved["outcome_json"])["selectedHypothesisStatus"])

    def test_research_capture_is_scoped_and_not_repeated_each_poll(self):
        self.assertEqual([], active_observation_plans(self.connection, "other", "TEST", "candidate", NOW))
        self.assertEqual(1, len(active_observation_plans(self.connection, "fixture", "TEST", "candidate", NOW)))
        capture_prediction(self.connection, self.episode(), NOW)
        self.assertEqual([], active_observation_plans(self.connection, "fixture", "TEST", "candidate", NOW))

    def test_cleanup_is_finite_but_live_cache_retention_does_not_apply(self):
        capture_prediction(self.connection, self.episode(), NOW)
        self.assertEqual(0, sum(self.store.cleanup("2026-09-15T00:00:00Z").values()))
        result = self.store.cleanup("2027-01-01T00:00:00Z")
        self.assertEqual(3, sum(result.values()))
        self.assertEqual(1, self.connection.execute("SELECT COUNT(*) AS n FROM verified_reasoning_source_snapshots").fetchone()["n"])

    def test_legacy_episode_does_not_synthesize_lineage(self):
        restored = ShadowHypothesisObservationEpisode.from_dict({"episodeId": "legacy"})
        self.assertEqual({}, restored.input_provenance)
        capture_outcome(self.connection, restored, self.outcome(), NOW)
        delta = FactDelta.from_dict({"sourceObservedAt": START})
        self.assertEqual((), delta.source_boundaries)

    def test_request_boundaries_survive_serialization_without_secrets(self):
        boundary = {"snapshotId": "source:1", "accountId": "fixture", "generatedAt": START, "token": "never-copy"}
        delta = FactDelta.from_request(SimpleNamespace(context={"verifiedSourceSnapshots": [boundary, boundary]}, source_observed_at=START))
        self.assertEqual(1, len(delta.source_boundaries))
        self.assertNotIn("token", delta.source_boundaries[0])
        self.assertEqual(delta, FactDelta.from_dict(delta.to_dict()))

    def test_unsupported_metric_does_not_claim_a_collector_exists(self):
        plan, _ = fixture_plan([{"metric": "order-cancellations-by-venue"}])
        result = self.store.prepare(plan)
        self.assertEqual("unsupported", result["state"])
        self.assertFalse(result["automaticCollection"])
        with patch("digital_twin.modules.model_registry.infrastructure.mysql_experiment_observations.read_dataset", return_value={"source": "x" * 2_000_001}):
            oversized = self.store.prepare(self.plan)
        self.assertEqual("unsupported", oversized["state"])
        self.assertEqual("experiment-packet-size-limit", oversized["reason"])

    def test_dataset_failure_does_not_enable_legacy_comparison(self):
        runtime = OntologyEvolutionRuntime(SimpleNamespace(registry=Mock(release_artifact=Mock(return_value={
            "valid": True, "artifactFingerprint": "f", "artifact": {"evolutionPlanFingerprint": self.plan["fingerprint"]}}))), Mock(), Mock())
        result = runtime.comparison(self.plan, {"deploymentId": "candidate", "artifactFingerprint": "f"})
        self.assertEqual("frozen-experiment-inputs-required", result["reason"])
        runtime.outcomes.ontology_evolution_comparison.assert_not_called()

    def test_frequency_beyond_the_collector_is_not_an_endless_data_wait(self):
        for requirement in (
            {"metric": "price", "lookbackMinutes": 60, "minimumSamples": 60, "cadenceSeconds": 1},
            {"metric": "price", "lookbackMinutes": 0, "minimumSamples": 2},
            {"metric": "price", "lookbackMinutes": 5, "minimumSamples": 100},
        ):
            with self.subTest(requirement=requirement):
                plan, _ = fixture_plan([requirement])
                self.assertEqual("unsupported", self.store.prepare(plan)["state"])

    def test_future_outcome_cannot_be_saved_as_already_observed(self):
        capture_prediction(self.connection, self.episode(), NOW)
        with self.assertRaisesRegex(ValueError, "clock-order"):
            capture_outcome(self.connection, self.episode(), self.outcome(), NOW)

    def test_historical_samples_are_copied_without_archiving_all_old_packets(self):
        plan, _ = fixture_plan([{"metric": "price", "lookbackMinutes": 5, "minimumSamples": 2, "cadenceSeconds": 300}])
        self.connection.insert_source(source_row(99, START, "2026-09-13T00:00:02Z"))
        dataset = read_dataset(self.connection, plan, {"snapshotId": source_row()["snapshot_id"]}, NOW)
        samples = dataset["coverage"][1]["samples"]
        self.assertEqual([99, 100], [row["value"] for row in samples])
        self.assertEqual("ready", dataset["coverage"][1]["state"])
        self.connection.execute("DELETE FROM verified_reasoning_source_snapshots")
        self.assertEqual([99, 100], [row["value"] for row in validate_dataset(dataset)["coverage"][1]["samples"]])

    def test_comparator_is_observed_even_when_already_execution_eligible(self):
        from digital_twin.modules.decisions.domain.investment_reasoning import HypothesisRecord
        from digital_twin.modules.reasoning.application.investment_reasoning.episode_projection import shadow_hypothesis_observation_episodes
        hypotheses = tuple(HypothesisRecord(hypothesis_id="h:" + side, claim_contract=GraphInferenceRule.from_dict(rule).resolved_claim_contract.to_dict(),
                                           supporting_rule_ids=(rule["rule_id"],),
                                           account_id="fixture", subject_symbol="TEST", inference_generation_id="g:1")
                           for side, rule in (("candidate", self.plan["candidateRule"]), ("baseline", self.baseline)))
        candidate_set = SimpleNamespace(candidate_set_id="set:1", hypotheses=hypotheses,
                                       eligible_hypothesis_ids=tuple(row.hypothesis_id for row in hypotheses), execution_eligible_hypothesis_ids=("h:baseline",))
        subject = SimpleNamespace(candidate_set=candidate_set, account_id="fixture", symbol="TEST",
                                  synthesis=SimpleNamespace(source_abox_snapshot_id="abox:1", inference_generation_id="g:1"))
        case = SimpleNamespace(fact_delta=SimpleNamespace(source_observed_at=NOW, source_boundaries=[]), created_at=NOW, updated_at=NOW)
        episodes = shadow_hypothesis_observation_episodes(case, subject, experiment_plans=[self.plan])
        sides = [row.input_provenance.get("experimentSide") for row in episodes if row.input_provenance.get("experimentPlanFingerprint")]
        self.assertEqual(["candidate", "baseline"], sides)
