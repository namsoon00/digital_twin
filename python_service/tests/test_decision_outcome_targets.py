import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from digital_twin.domain.hypothesis_outcome_contract import (
    HYPOTHESIS_OUTCOME_CONTRACT_VERSION,
    outcome_contract_fingerprint,
)
from digital_twin.domain.decision_follow_up import FOLLOW_UP_CONDITION_VERSION
from digital_twin.application.investment_reasoning.episode_projection import (
    decision_episode_outcome_contract_readiness,
    hypothesis_coverage_gap_request_from_subject_case,
    shadow_hypothesis_observation_episodes,
)
from digital_twin.application.investment_outcome_observation_service import (
    InvestmentOutcomeObservationService,
)
from digital_twin.domain.hypothesis_observation import (
    ShadowHypothesisObservationEpisode,
)
from digital_twin.domain.investment_reasoning import (
    ConditionEvidence,
    DataGap,
    HypothesisRecord,
    RuleEvaluationRecord,
    RuleMatchProof,
)
from digital_twin.domain.hypothesis_outcome_evaluation import (
    evaluate_hypothesis_outcome,
)
from digital_twin.infrastructure.mysql_investment_decision_episodes import (
    MySQLInvestmentDecisionEpisodeStore,
    outcome_target_at,
)
from digital_twin.infrastructure.mysql_schema_tuning import (
    MYSQL_OPERATIONAL_COLUMN_WIDTHS,
    ensure_mysql_column_widths,
)


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), tuple(params)))
        return SimpleNamespace(rowcount=1)


class EmptyMigrationConnection(RecordingConnection):
    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), tuple(params)))
        return SimpleNamespace(rowcount=0, fetchall=lambda: [])


class QueryRowsConnection(RecordingConnection):
    def __init__(self, rows):
        super().__init__()
        self.rows = list(rows or [])

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), tuple(params)))
        return SimpleNamespace(fetchall=lambda: list(self.rows))


class PendingRepairConnection(RecordingConnection):
    def __init__(self, decision_rows=None, shadow_rows=None):
        super().__init__()
        self.decision_rows = list(decision_rows or [])
        self.shadow_rows = list(shadow_rows or [])

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, tuple(params)))
        if normalized.startswith("SELECT targets.target_id"):
            rows = (
                self.shadow_rows
                if "investment_hypothesis_observation_targets" in normalized
                else self.decision_rows
            )
            return SimpleNamespace(fetchall=lambda: list(rows))
        return SimpleNamespace(rowcount=1)


class FollowUpConnection(RecordingConnection):
    def __init__(self, payload):
        super().__init__()
        self.payload = dict(payload or {})

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, tuple(params)))
        if normalized.startswith("SELECT follow_up.condition_id"):
            return SimpleNamespace(fetchall=lambda: [{
                "condition_id": "decision-follow-up:1",
                "episode_id": "decision-episode:1",
                "account_id": "account:1",
                "symbol": "NVDA",
                "payload_json": json.dumps(self.payload),
            }])
        return SimpleNamespace(rowcount=1)


class SupersedeConnection(RecordingConnection):
    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, tuple(params)))
        if normalized.startswith("SELECT decision_episode_id"):
            return SimpleNamespace(fetchone=lambda: {
                "decision_episode_id": "decision-episode:new",
            })
        if normalized.startswith("SELECT condition_id"):
            return SimpleNamespace(fetchall=lambda: [{
                "condition_id": "decision-follow-up:old",
                "payload_json": json.dumps({"status": "pending"}),
            }])
        return SimpleNamespace(rowcount=1)


def predictive_contract():
    payload = {
        "contractVersion": HYPOTHESIS_OUTCOME_CONTRACT_VERSION,
        "criteriaOrigin": "rulebox",
        "selectedHypothesisId": "hypothesis:trend:1",
        "sourceRuleIds": ["graph.trend.test.v1"],
        "inferenceGenerationId": "generation:1",
        "outcomeHorizonMinutes": [60, 1440],
        "requiredObservationDomains": ["quote"],
        "minimumIndependentEpisodes": 5,
        "maximumObservationDelayMinutes": 180,
        "predictionTarget": "price-path",
        "expectedDirection": "support",
        "expectedOutcome": "positive return",
        "outcomeMetric": "instrumentReturnPct",
        "falsificationContract": "opposite return",
        "criteria": [{
            "criterionId": "trend:result",
            "label": "positive return",
            "role": "result",
            "metric": "instrumentReturnPct",
            "operator": ">=",
            "threshold": 0.5,
            "horizonMinutes": 0,
            "required": True,
            "requiredObservationDomains": ["quote"],
            "sourcePolicy": ["point-in-time-market-observation"],
        }],
    }
    payload["contractFingerprint"] = outcome_contract_fingerprint(payload)
    return payload


def episode(contract=None, eligible=None):
    eligible = bool(contract) if eligible is None else bool(eligible)
    return SimpleNamespace(
        episode_id="episode:1",
        account_id="account:1",
        symbol="NVDA",
        subject_name="NVIDIA",
        selected_hypothesis_id="hypothesis:trend:1" if contract else "",
        decided_at="2026-08-25T00:00:00Z",
        facts_at_decision={
            **({"hypothesisOutcomeContract": contract} if contract else {}),
            "factDelta": {"source_observed_at": "2026-08-24T23:58:00Z"},
            "calibrationPolicy": {
                "eligible": eligible,
                "reason": "selected-predictive-rulebox-contract-frozen" if eligible else "no-selected-hypothesis",
            },
        },
    )


class DecisionOutcomeTargetTests(unittest.TestCase):
    def store(self):
        store = object.__new__(MySQLInvestmentDecisionEpisodeStore)
        store.runtime_settings = {}
        return store

    def test_schema_tuning_widens_legacy_contract_fingerprint_column(self):
        class Cursor:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def __init__(self):
                self.statements = []

            def execute(self, sql, params=()):
                self.statements.append((sql, params))
                if sql.startswith("SHOW COLUMNS"):
                    return Cursor({"Field": "contract_fingerprint", "Type": "varchar(64)"})
                return Cursor()

        connection = Connection()

        widened = ensure_mysql_column_widths(connection, MYSQL_OPERATIONAL_COLUMN_WIDTHS)

        self.assertEqual(
            [
                "investment_decision_outcome_targets.contract_fingerprint",
                "investment_hypothesis_observation_targets.contract_fingerprint",
            ],
            widened,
        )
        self.assertIn(
            "MODIFY COLUMN `contract_fingerprint` VARCHAR(96)",
            connection.statements[-1][0],
        )
        self.assertEqual(71, len(outcome_contract_fingerprint(predictive_contract())))

        payload = {
            "version": FOLLOW_UP_CONDITION_VERSION,
            "conditionId": "decision-follow-up:1",
            "field": "ma20Distance",
            "operator": ">=",
            "threshold": 0,
            "purpose": "strengthen",
            "status": "pending",
            "observable": True,
            "armed": True,
            "previousMatched": False,
            "currentMatched": False,
            "currentValue": -0.2,
        }
        connection = FollowUpConnection(payload)
        store = self.store()

        @contextmanager
        def connect():
            yield connection

        store.connect = connect
        transitions = store.evaluate_follow_up_observation(
            "account:1",
            "NVDA",
            {"ma20Distance": 0.3},
            "2026-08-25T01:00:00Z",
        )

        self.assertEqual(1, len(transitions))
        self.assertEqual("decision-episode:1", transitions[0]["episodeId"])
        self.assertEqual("account:1", transitions[0]["accountId"])
        self.assertEqual("NVDA", transitions[0]["symbol"])
        self.assertEqual("pending", transitions[0]["previousStatus"])
        self.assertEqual("condition-reached", transitions[0]["trackingStatus"])
        self.assertEqual("system", transitions[0]["trackingOwner"])
        self.assertEqual("each-live-snapshot", transitions[0]["trackingCadence"])
        self.assertTrue(transitions[0]["notificationOnTransition"])
        self.assertTrue(transitions[0]["transitionVerified"])
        self.assertIn("JOIN investment_flow_current", connection.statements[0][0])

        supersede_connection = SupersedeConnection()
        changed = store.supersede_prior_follow_ups_for_current(
            supersede_connection,
            "account:1",
            "NVDA",
            "decision-episode:new",
            "2026-08-25T01:01:00Z",
        )
        self.assertEqual(1, changed)
        update_params = next(
            params for sql, params in supersede_connection.statements
            if sql.startswith("UPDATE investment_decision_follow_ups")
        )
        superseded_payload = json.loads(update_params[0])
        self.assertEqual("superseded", superseded_payload["status"])
        self.assertEqual("decision-episode:new", superseded_payload["supersededByEpisodeId"])

    def test_predictive_decision_creates_one_durable_target_per_horizon(self):
        connection = RecordingConnection()

        result = self.store().sync_outcome_targets(
            connection,
            episode(predictive_contract()),
            "2026-08-25T00:01:00Z",
        )

        inserts = [sql for sql, _params in connection.statements if sql.startswith("INSERT INTO investment_decision_outcome_targets")]
        self.assertEqual("scheduled", result["status"])
        self.assertEqual(2, result["targetCount"])
        self.assertEqual(2, len(inserts))
        target_payload = json.loads(next(
            params[10]
            for sql, params in connection.statements
            if sql.startswith("INSERT INTO investment_decision_outcome_targets")
        ))
        self.assertEqual("decision", target_payload["episodeKind"])
        self.assertTrue(target_payload["requiresInstrumentBaseline"])
        self.assertEqual("2026-08-24T23:58:00Z", target_payload["baselineAt"])

    def test_contract_rejects_a_scheduled_horizon_without_a_criterion(self):
        contract = predictive_contract()
        contract["criteria"][0]["horizonMinutes"] = 60
        contract["contractFingerprint"] = outcome_contract_fingerprint(contract)

        readiness = decision_episode_outcome_contract_readiness(episode(contract))

        self.assertFalse(readiness["ready"])
        self.assertIn("criterion-horizon-coverage", readiness["missing"])

    def test_observation_loads_the_price_at_the_source_baseline(self):
        target = {
            "requestId": "target:1",
            "episodeId": "episode:1",
            "episodeKind": "decision",
            "symbol": "NVDA",
            "horizonMinutes": 60,
            "decidedAt": "2026-08-25T00:00:00Z",
            "baselineAt": "2026-08-24T23:58:00Z",
            "targetAt": "2026-08-25T01:00:00Z",
            "maximumObservationDelayMinutes": 180,
            "requiresInstrumentBaseline": True,
        }

        class Store:
            def __init__(self):
                self.records = []

            def pending_outcome_targets(self, *_args, **_kwargs):
                return [target]

            def record_outcome_observations(self, _account_id, records):
                self.records = list(records)
                return []

        class TimeSeries:
            def __init__(self):
                self.requests = []

            def load_outcome_observations(self, _account_id, requests, **_kwargs):
                rows = list(requests)
                self.requests.append(rows)
                result = {}
                for request in rows:
                    request_id = request["requestId"]
                    baseline = request_id.endswith(":instrument-start")
                    result[request_id] = {
                        "currentPrice": 100 if baseline else 110,
                        "sourceAsOf": request["targetAt"],
                        "dataQuality": "fresh",
                        "observationBasis": "historical-market-time-series",
                    }
                return result

        store = Store()
        time_series = TimeSeries()
        service = InvestmentOutcomeObservationService(store, time_series)
        snapshot = SimpleNamespace(
            account_id="account:1",
            generated_at="2026-08-25T01:01:00Z",
            positions=[],
            watchlist=[],
            has_live_account_data=lambda: True,
        )

        service.observe_snapshot(snapshot)

        baseline_request = time_series.requests[1][0]
        self.assertEqual("2026-08-24T23:58:00Z", baseline_request["targetAt"])
        self.assertEqual(100.0, store.records[0]["facts"]["decisionPrice"])
        self.assertEqual(
            "2026-08-24T23:58:00Z",
            store.records[0]["facts"]["decisionPriceSourceAsOf"],
        )

    def test_shadow_hypothesis_projection_deduplicates_repeated_snapshot_generation(self):
        claim_contract = {
            "claimContractId": "claim:trend:1",
            "ruleId": "graph.trend.test.v1",
            "claimType": "market-hypothesis",
            "decisionAuthority": "conditional-investment-evidence",
            "evidenceIndependenceKey": "trend-price-path",
            "predictionTarget": "price-path",
            "expectedDirection": "support",
            "expectedOutcome": "positive return",
            "outcomeMetric": "instrumentReturnPct",
            "falsificationContract": "opposite return",
            "outcomeContract": predictive_contract(),
        }
        hypothesis = HypothesisRecord(
            hypothesis_id="hypothesis:trend:1",
            family_id="trend-continuation",
            label="추세가 이어진다",
            candidate_action="ADD",
            supporting_rule_ids=("graph.trend.test.v1",),
            claim_contract=claim_contract,
            qualification={"status": "shadow"},
            account_id="account:1",
            subject_symbol="NVDA",
            inference_generation_id="generation:1",
        )

        def projection(observed_at, generation_id):
            candidate_set = SimpleNamespace(
                candidate_set_id="candidate:" + generation_id,
                hypotheses=(hypothesis,),
                eligible_hypothesis_ids=(hypothesis.hypothesis_id,),
                execution_eligible_hypothesis_ids=(),
            )
            subject_case = SimpleNamespace(
                account_id="account:1",
                symbol="NVDA",
                candidate_set=candidate_set,
                synthesis=SimpleNamespace(
                    source_abox_snapshot_id="abox:" + generation_id,
                    inference_generation_id=generation_id,
                ),
            )
            reasoning_case = SimpleNamespace(
                fact_delta=SimpleNamespace(source_observed_at=observed_at),
                updated_at=observed_at,
                created_at=observed_at,
            )
            return shadow_hypothesis_observation_episodes(
                reasoning_case,
                subject_case,
            )[0]

        first = projection("2026-08-25T00:01:00Z", "generation:1")
        repeated = projection("2026-08-25T00:59:00Z", "generation:2")

        self.assertTrue(first.observation_eligible)
        self.assertEqual("scheduled", first.status)
        self.assertEqual(first.episode_id, repeated.episode_id)
        self.assertEqual(first.independence_bucket, repeated.independence_bucket)
        self.assertEqual(first.market_independence_key, repeated.market_independence_key)

    def test_shadow_hypothesis_target_and_outcome_are_research_only(self):
        contract = predictive_contract()
        contract["marketIndependenceKey"] = "market-event:1"
        contract["accountIndependenceKey"] = "account-event:1"
        episode_value = ShadowHypothesisObservationEpisode(
            episode_id="shadow:1",
            candidate_set_id="candidate:1",
            account_id="account:1",
            symbol="BTC",
            hypothesis_id="hypothesis:trend:1",
            claim_identity="claim:trend:1",
            family_id="trend-continuation",
            claim_contract_id="claim:trend:1",
            observed_from_at="2026-08-25T00:00:00Z",
            independence_bucket="2026-08-25T00:00:00Z/60m",
            market_independence_key="market-event:1",
            account_independence_key="account-event:1",
            candidate_action="ADD",
            stance="support",
            market="CRYPTO",
            outcome_contract=contract,
            hypothesis={
                "hypothesisId": "hypothesis:trend:1",
                "familyId": "trend-continuation",
                "stance": "support",
            },
            readiness={"eligible": True},
        )
        store = self.store()
        connection = RecordingConnection()

        schedule = store.sync_shadow_hypothesis_observation_targets(
            connection,
            episode_value,
            "2026-08-25T00:01:00Z",
        )

        self.assertEqual("scheduled", schedule["status"])
        self.assertEqual(2, schedule["targetCount"])
        self.assertEqual(2, len([
            sql for sql, _params in connection.statements
            if sql.startswith("INSERT INTO investment_hypothesis_observation_targets")
        ]))

        @contextmanager
        def transaction():
            yield connection

        store.transaction = transaction
        store.shadow_observation_episodes_by_ids = lambda _ids: {
            episode_value.episode_id: episode_value
        }
        outcomes = store.record_shadow_hypothesis_outcome_observations(
            "account:1",
            [{
                "episodeId": episode_value.episode_id,
                "horizonMinutes": 60,
                "observedAt": "2026-08-25T01:05:00Z",
                "facts": {
                    "currentPrice": 101,
                    "decisionPrice": 100,
                    "sourceAsOf": "2026-08-25T01:05:00Z",
                    "observationBasis": "historical-market-time-series",
                    "dataQuality": "fresh",
                },
            }],
        )

        self.assertEqual(1, len(outcomes))
        self.assertEqual(
            "directionally-corroborated",
            outcomes[0].selected_hypothesis_status,
        )
        self.assertEqual(
            "shadow-hypothesis",
            outcomes[0].payload["episodeKind"],
        )
        self.assertTrue(any(
            sql.startswith("INSERT INTO investment_hypothesis_observation_outcomes")
            for sql, _params in connection.statements
        ))

    def test_unproven_rule_without_hypothesis_does_not_create_research_request(self):
        synthesis = SimpleNamespace(
            disposition_code="RULE_COVERAGE_GAP_CANDIDATE",
            selected_rule_id="graph.trend.coverage.test.v1",
            investment_view_action="ADD",
        )
        subject_case = SimpleNamespace(
            subject_case_id="subject:generation:1",
            account_id="account:1",
            symbol="NVDA",
            inference_generation_id="generation:1",
            created_at="2026-08-25T00:00:00Z",
            synthesis=synthesis,
            candidate_set=SimpleNamespace(
                candidate_set_id="candidate:generation:1",
                data_gaps=(DataGap("investorFlow", state="missing"),),
            ),
        )

        request = hypothesis_coverage_gap_request_from_subject_case(
            SimpleNamespace(
                inference_result=SimpleNamespace(rule_evaluations=()),
            ),
            subject_case,
        )

        self.assertEqual({}, request)

    def test_evidence_backed_coverage_gap_creates_deduplicated_research_request(self):
        def request_for(generation_id):
            synthesis = SimpleNamespace(
                disposition_code="RULE_COVERAGE_GAP_CANDIDATE",
                selected_rule_id="graph.trend.coverage.test.v1",
                investment_view_action="ADD",
            )
            candidate_set = SimpleNamespace(
                candidate_set_id="candidate:" + generation_id,
                data_gaps=(DataGap("investorFlow", state="missing"),),
            )
            subject_case = SimpleNamespace(
                subject_case_id="subject:" + generation_id,
                account_id="account:1",
                symbol="NVDA",
                inference_generation_id=generation_id,
                created_at="2026-08-25T00:00:00Z",
                synthesis=synthesis,
                candidate_set=candidate_set,
            )
            proof = RuleMatchProof(
                proof_id="proof:" + generation_id,
                rule_id="graph.trend.coverage.test.v1",
                trace_id="trace:" + generation_id,
                subject_id="NVDA",
                matched=True,
                conditions=(ConditionEvidence(
                    condition_id="ma20-positive",
                    field="ma20GapPct",
                    operator=">",
                    expected_value=0,
                    observed_value=8.6,
                    relation_id="relation:price-recovery",
                    source="KIS",
                    source_as_of="2026-08-25T00:00:00Z",
                    freshness="fresh",
                    evidence_ids=("relation:price-recovery",),
                ),),
                evidence_ids=("relation:price-recovery",),
                status="available",
            )
            reasoning_case = SimpleNamespace(
                inference_result=SimpleNamespace(rule_evaluations=(
                    RuleEvaluationRecord(
                        evaluation_id="evaluation:" + generation_id,
                        account_id="account:1",
                        rule_id="graph.trend.coverage.test.v1",
                        source_abox_snapshot_id="abox:" + generation_id,
                        inference_generation_id=generation_id,
                        matched=True,
                        selected=True,
                        proof=proof,
                    ),
                )),
            )
            return hypothesis_coverage_gap_request_from_subject_case(
                reasoning_case,
                subject_case,
            )

        first = request_for("generation:1")
        repeated = request_for("generation:2")

        self.assertTrue(first)
        self.assertEqual(first["requestId"], repeated["requestId"])
        self.assertEqual(first["gapFingerprint"], repeated["gapFingerprint"])
        self.assertEqual([], first["hypothesisSet"]["hypotheses"])
        self.assertEqual(
            "RULE_CONDITION_EVIDENCE",
            first["relationContext"]["graphStoreInference"]["relations"][0]["type"],
        )
        self.assertEqual(
            "verified-rule-match-proof",
            first["evidenceReadiness"]["status"],
        )
        self.assertEqual(
            "review-required-no-automatic-rulebox-deployment",
            first["governance"],
        )

    def test_final_decision_contract_readiness_requires_exact_fingerprint(self):
        contract = predictive_contract()

        ready = decision_episode_outcome_contract_readiness(episode(contract))
        contract["contractFingerprint"] = "sha256:tampered"
        rejected = decision_episode_outcome_contract_readiness(episode(contract))

        self.assertTrue(ready["ready"])
        self.assertFalse(rejected["ready"])
        self.assertIn("contract-fingerprint-mismatch", rejected["missing"])

    def test_incomplete_legacy_decision_is_audited_without_contract_reconstruction(self):
        connection = RecordingConnection()

        result = self.store().sync_outcome_targets(
            connection,
            episode(),
            "2026-08-25T00:01:00Z",
        )

        self.assertEqual("excluded", result["status"])
        self.assertEqual("no-selected-hypothesis", result["reason"])
        insert_params = next(
            params for sql, params in connection.statements
            if sql.startswith("INSERT INTO investment_decision_outcome_targets")
        )
        self.assertEqual(0, insert_params[4])
        self.assertEqual("excluded", insert_params[8])

    def test_backfill_steady_state_is_index_only_and_selects_only_missing_targets(self):
        store = self.store()
        connection = EmptyMigrationConnection()

        @contextmanager
        def transaction():
            yield connection

        store.transaction = transaction

        result = store.backfill_outcome_targets("account:1")

        self.assertEqual("already-initialized", result["status"])
        query = connection.statements[0][0]
        self.assertIn("NOT EXISTS", query)
        self.assertIn("investment_decision_outcome_targets", query)
        self.assertNotIn("payload_json", query)

        backfill_calls = []
        store.backfill_outcome_targets = lambda account_id: (
            backfill_calls.append(account_id) or {"status": "already-initialized"}
        )
        store.outcome_batch_size = lambda: 100

        @contextmanager
        def connect():
            yield EmptyMigrationConnection()

        store.connect = connect
        store.pending_outcome_targets("account:1")
        store.pending_outcome_targets("account:1")
        self.assertEqual(["account:1"], backfill_calls)

    def test_pending_target_with_uncovered_horizon_is_retired(self):
        contract = predictive_contract()
        contract["criteria"][0]["horizonMinutes"] = 60
        contract["contractFingerprint"] = outcome_contract_fingerprint(contract)
        episode_payload = {
            "episodeId": "episode:legacy-gap",
            "accountId": "account:1",
            "symbol": "NVDA",
            "selectedHypothesisId": "hypothesis:trend:1",
            "decidedAt": "2026-08-25T00:00:00Z",
            "factsAtDecision": {
                "hypothesisOutcomeContract": contract,
                "calibrationPolicy": {"eligible": True},
            },
        }
        connection = PendingRepairConnection(decision_rows=[{
            "target_id": "target:legacy-gap",
            "horizon_minutes": 1440,
            "target_at": "2026-08-26T00:00:00Z",
            "target_json": json.dumps({
                "requestId": "target:legacy-gap",
                "episodeId": "episode:legacy-gap",
            }),
            "episode_json": json.dumps(episode_payload),
            "episode_status": "active",
            "decided_at": "2026-08-25T00:00:00Z",
        }])
        store = self.store()

        @contextmanager
        def transaction():
            yield connection

        store.transaction = transaction

        result = store.repair_pending_outcome_target_schedules("account:1")

        self.assertEqual("repaired", result["status"])
        self.assertEqual(1, result["excludedCount"])
        update = next(
            (sql, params)
            for sql, params in connection.statements
            if sql.startswith("UPDATE investment_decision_outcome_targets")
        )
        self.assertIn("status = 'excluded'", update[0])
        self.assertEqual("outcome-contract-incomplete", update[1][0])
        shadow_select = next(
            sql
            for sql, _params in connection.statements
            if sql.startswith("SELECT targets.target_id")
            and "investment_hypothesis_observation_targets" in sql
        )
        self.assertIn(
            "episodes.episode_id = targets.observation_episode_id",
            shadow_select,
        )

    def test_market_symbol_without_metadata_aligns_outcome_to_next_session(self):
        value = episode(predictive_contract())
        value.symbol = "035420"
        value.decided_at = "2026-09-07T15:01:00Z"
        value.facts_at_decision.pop("market", None)
        value.facts_at_decision.pop("currency", None)

        target = outcome_target_at(value, 60)

        self.assertEqual("2026-09-08T00:00:00Z", target)

    def test_historical_quote_satisfies_point_in_time_source_policy(self):
        result = evaluate_hypothesis_outcome(
            predictive_contract(),
            "support",
            {
                "currentPrice": 101.0,
                "observationBasis": "historical-market-time-series",
                "sourceAsOf": "2026-08-25T01:00:05Z",
            },
            1.0,
            60,
        )

        self.assertEqual("directionally-corroborated", result["selectedHypothesisStatus"])
        self.assertEqual([], result["missingRequiredMetricIds"])

    def test_performance_loader_keeps_observed_episodes_outside_recent_window(self):
        store = self.store()
        connection = QueryRowsConnection([{
            "episode_id": "episode:historical",
            "account_id": "account:1",
            "symbol": "NVDA",
            "action": "HOLD",
            "selected_hypothesis_id": "hypothesis:trend:1",
            "observed_at": "2026-08-25T01:00:00Z",
            "outcome_json": '{"outcomeId":"outcome:1","observedAt":"2026-08-25T01:00:00Z","payload":{}}',
        }])

        @contextmanager
        def connect():
            yield connection

        store.connect = connect

        rows = store.performance_episodes(
            "account:1",
            limit=1,
            as_of="2026-08-25T01:30:00Z",
        )

        self.assertEqual(["episode:historical"], [item["episodeId"] for item in rows])
        self.assertIn("SELECT episode_id, MAX(observed_at)", connection.statements[0][0])
        self.assertEqual(2, connection.statements[0][0].count("observed_at <= %s"))
        self.assertEqual(
            ("account:1", "2026-08-25T01:30:00Z", 1, "2026-08-25T01:30:00Z"),
            connection.statements[0][1],
        )


if __name__ == "__main__":
    unittest.main()
