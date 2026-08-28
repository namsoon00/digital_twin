import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from digital_twin.domain.hypothesis_outcome_contract import (
    HYPOTHESIS_OUTCOME_CONTRACT_VERSION,
    outcome_contract_fingerprint,
)
from digital_twin.infrastructure.mysql_investment_decision_episodes import (
    MySQLInvestmentDecisionEpisodeStore,
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
            "horizonMinutes": 60,
            "required": True,
            "requiredObservationDomains": ["quote"],
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


if __name__ == "__main__":
    unittest.main()
