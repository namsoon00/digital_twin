"""Coordinator faults on an isolated execute-only ledger, never a live DB."""

import ast
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.infrastructure.transactions import decision_history as api
from digital_twin.infrastructure.transactions.decision_history_parts import (
    decision_write,
    follow_ups,
    target_queries,
)
from digital_twin.modules.decisions.domain.investment_brain import DecisionEpisode, ObservedOutcome
from digital_twin.modules.outcomes.domain.hypothesis_observation import (
    ShadowHypothesisObservationEpisode,
)
from test_decision_outcome_targets import predictive_contract


NOW = "2026-09-10T15:00:00Z"
DECIDED = "2026-09-10T14:00:00Z"


def episode(identity="episode:fixture"):
    return DecisionEpisode.from_dict(
        {
            "episodeId": identity,
            "accountId": "fixture",
            "symbol": "AAPL",
            "action": "HOLD",
            "source": "storage-fixture",
            "decidedAt": DECIDED,
            "selectedHypothesisId": "hypothesis:trend:1",
            "sourceAboxSnapshotId": "abox:source",
            "inferenceGenerationId": "generation:frozen",
            "factsAtDecision": {
                "currentPrice": 100,
                "market": "US",
                "currency": "USD",
                "sourceAsOf": DECIDED,
                "calibrationPolicy": {"eligible": True},
                "hypothesisOutcomeContract": predictive_contract(),
            },
            "hypothesisSet": {
                "createdAt": DECIDED,
                "hypotheses": [
                    {
                        "hypothesisId": "hypothesis:trend:1",
                        "stance": "support",
                    }
                ],
            },
            "followUpConditions": [
                {
                    "conditionId": "condition:fixture",
                    "field": "currentPrice",
                    "operator": ">",
                    "threshold": 110,
                }
            ],
        }
    )


def outcome(identity="outcome:fixture"):
    return ObservedOutcome(
        outcome_id=identity,
        episode_id="episode:fixture",
        observed_at=NOW,
        price=101,
        price_change_from_decision_pct=1,
        payload={"horizonMinutes": 60, "contractFingerprint": "contract:frozen"},
    )


class AtomicLedger:
    """Model only connection atomicity; retain exact SQL/parameters for audit."""

    def __init__(self, fail_write=0, after_write=False, commit_failure=""):
        self.fail_write = fail_write
        self.after_write = after_write
        self.commit_failure = commit_failure
        self.committed = []
        self.pending = None
        self.statements = []
        self.write_count = 0
        self.begins = 0
        self.select_rows = []
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        if sql.lstrip().startswith("SELECT"):
            return SimpleNamespace(
                fetchone=lambda: self.select_rows[0] if self.select_rows else None,
                fetchall=lambda: deepcopy(self.select_rows),
                rowcount=0,
            )
        self.write_count += 1
        if self.write_count == self.fail_write and not self.after_write:
            raise RuntimeError("injected write failure")
        destination = self.pending if self.pending is not None else self.committed
        destination.append((sql, tuple(params)))
        if self.write_count == self.fail_write and self.after_write:
            raise RuntimeError("injected write failure")
        return SimpleNamespace(rowcount=self.rowcount)

    @contextmanager
    def transaction(self):
        if self.pending is not None:
            raise AssertionError("unexpected nested transaction")
        self.begins += 1
        self.pending = []
        try:
            yield self
            if self.commit_failure == "before":
                raise RuntimeError("injected commit failure")
            self.committed.extend(self.pending)
            if self.commit_failure == "after":
                raise RuntimeError("injected lost acknowledgement")
        finally:
            self.pending = None

    @contextmanager
    def connect(self):
        yield self


def store_for(ledger):
    store = object.__new__(api.MySQLInvestmentDecisionEpisodeStore)
    store.runtime_settings = {}
    store.transaction = ledger.transaction
    store.connect = ledger.connect
    return store


def record_event(connection, event):
    connection.execute("EVENT " + event.name, (event.aggregate_id,))


class InternalDecisionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(api, "utc_now_iso", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.events = patch.object(api, "insert_domain_event_with_connection", record_event)
        self.events.start()
        self.addCleanup(self.events.stop)

    def test_prepared_packet_and_pending_read_are_frozen(self):
        value = episode()
        prepared = decision_write.prepare_decision(value, utc_now_iso=lambda: NOW)
        self.assertIs(value, prepared.episode)
        self.assertEqual(DECIDED, prepared.payload["decidedAt"])
        self.assertEqual("generation:frozen", prepared.payload["inferenceGenerationId"])
        self.assertEqual("abox:source", prepared.payload["sourceAboxSnapshotId"])
        self.assertEqual(NOW, prepared.stamp)
        with self.assertRaises(FrozenInstanceError):
            prepared.stamp = "new-clock"
        request = target_queries.PendingTargetRead("fixture", NOW, 12)
        with self.assertRaises(FrozenInstanceError):
            request.observed_at = "new-clock"

    def test_save_faults_before_and_after_every_owner_write_rollback_everything(self):
        complete = AtomicLedger()
        store_for(complete).save(episode())
        self.assertEqual(1, complete.begins)
        self.assertGreaterEqual(complete.write_count, 8)
        self.assertTrue(complete.committed[-1][0].startswith("EVENT "))
        for index in range(1, complete.write_count + 1):
            for after in (False, True):
                with self.subTest(write=index, after=after):
                    ledger = AtomicLedger(index, after)
                    with self.assertRaisesRegex(RuntimeError, "injected write"):
                        store_for(ledger).save(episode())
                    self.assertEqual([], ledger.committed)
                    self.assertEqual(1, ledger.begins)

    def test_external_transaction_is_the_only_commit_owner(self):
        ledger = AtomicLedger()
        store = store_for(ledger)
        store.transaction = Mock(side_effect=AssertionError("must use caller transaction"))
        with self.assertRaisesRegex(RuntimeError, "caller failure"):
            with ledger.transaction() as connection:
                store.save(episode(), connection=connection)
                self.assertEqual([], ledger.committed)
                raise RuntimeError("caller failure after all decision writes")
        self.assertEqual([], ledger.committed)
        store.transaction.assert_not_called()

    def test_commit_failure_and_lost_acknowledgement_are_not_disguised_as_success(self):
        for stage in ("before", "after"):
            ledger = AtomicLedger(commit_failure=stage)
            with self.assertRaises(RuntimeError):
                store_for(ledger).save(episode())
            self.assertEqual(stage == "after", bool(ledger.committed))
            self.assertEqual(1, ledger.begins)

    def test_outcome_partial_writes_keep_the_previous_committed_history(self):
        for shadow in (False, True):
            for index in range(1, 4):
                for after in (False, True):
                    with self.subTest(shadow=shadow, write=index, after=after):
                        ledger = AtomicLedger(index, after)
                        previous = ("previous-generation", ("generation:old",))
                        ledger.committed = [previous]
                        store = store_for(ledger)
                        with self.assertRaisesRegex(RuntimeError, "injected write"):
                            if shadow:
                                store.save_shadow_hypothesis_outcome(
                                    SimpleNamespace(
                                        episode_id="shadow:1", account_id="fixture", symbol="AAPL"
                                    ),
                                    outcome(),
                                )
                            else:
                                store.save_outcome(episode(), outcome())
                        self.assertEqual([previous], ledger.committed)

    def test_outcome_save_keeps_exact_clock_and_contract_target_identity(self):
        ledger = AtomicLedger()
        store_for(ledger).save_outcome(episode(), outcome())
        self.assertEqual(1, ledger.begins)
        self.assertEqual(3, len(ledger.committed))
        sql, params = ledger.committed[-1]
        self.assertIn("AND contract_fingerprint = %s", sql)
        self.assertEqual(
            ("outcome:fixture", NOW, NOW, "episode:fixture", 60, "contract:frozen"), params
        )
        self.assertEqual(DECIDED, ledger.committed[1][1][1])

    def test_batch_failure_does_not_undo_earlier_per_outcome_commit_or_run_learning(self):
        ledger = AtomicLedger(fail_write=4, after_write=True)
        store = store_for(ledger)
        first, second = episode("episode:first"), episode("episode:second")
        store.episodes_by_ids = lambda _ids: {v.episode_id: v for v in (first, second)}
        store.propose_learning_from_outcomes = Mock()
        observations = [
            {
                "episodeId": item.episode_id,
                "horizonMinutes": 60,
                "observedAt": NOW,
                "facts": {
                    "currentPrice": 101,
                    "decisionPrice": 100,
                    "sourceAsOf": NOW,
                    "dataQuality": "live",
                    "observationBasis": "historical-market-time-series",
                },
            }
            for item in (first, second)
        ]
        with self.assertRaisesRegex(RuntimeError, "injected write"):
            store.record_outcome_observations("fixture", observations)
        self.assertEqual(2, ledger.begins)
        self.assertEqual(3, len(ledger.committed))
        self.assertEqual("episode:first", ledger.committed[-1][1][3])
        store.propose_learning_from_outcomes.assert_not_called()

    def test_shadow_batch_scheduling_is_atomic_and_has_no_decision_or_delivery_writes(self):
        def shadow(identity):
            return ShadowHypothesisObservationEpisode.from_dict(
                {
                    "episodeId": identity,
                    "accountId": "fixture",
                    "symbol": "AAPL",
                    "observedFromAt": DECIDED,
                    "hypothesisId": "hypothesis:trend:1",
                    "claimIdentity": "claim:fixture",
                    "independenceBucket": DECIDED + "/60m",
                    "market": "US",
                    "currency": "USD",
                    "readiness": {"eligible": True},
                    "outcomeContract": predictive_contract(),
                }
            )

        complete = AtomicLedger()
        store_for(complete).save_shadow_hypothesis_observations([shadow("a"), shadow("b")])
        self.assertEqual(6, complete.write_count)
        for index in range(1, complete.write_count + 1):
            ledger = AtomicLedger(index, after_write=True)
            with self.assertRaises(RuntimeError):
                store_for(ledger).save_shadow_hypothesis_observations([shadow("a"), shadow("b")])
            self.assertEqual([], ledger.committed)
        self.assertTrue(
            all("investment_hypothesis_observation_" in sql for sql, _ in complete.committed)
        )
        self.assertEqual(1, complete.begins)

    def test_schedule_repair_failure_rolls_back_decision_and_shadow_targets_together(self):
        class RepairLedger(AtomicLedger):
            def execute(self, sql, params=()):
                if sql.startswith("SELECT targets.target_id"):
                    shadow = "investment_hypothesis_observation_targets" in sql
                    value = episode().to_dict()
                    if shadow:
                        value = {
                            "episodeId": "shadow:fixture",
                            "accountId": "fixture",
                            "symbol": "AAPL",
                            "observedFromAt": DECIDED,
                            "hypothesisId": "hypothesis:trend:1",
                            "claimIdentity": "claim:fixture",
                            "independenceBucket": DECIDED + "/60m",
                            "market": "US",
                            "currency": "USD",
                            "readiness": {"eligible": True},
                            "outcomeContract": predictive_contract(),
                        }
                    self.select_rows = [
                        {
                            "target_id": "shadow:target" if shadow else "decision:target",
                            "horizon_minutes": 60,
                            "target_at": DECIDED,
                            "target_json": "{}",
                            "episode_json": json.dumps(value),
                            "decided_at": DECIDED,
                        }
                    ]
                return super().execute(sql, params)

        complete = RepairLedger()
        result = store_for(complete).repair_pending_outcome_target_schedules("fixture")
        self.assertEqual(2, result["repairedCount"])
        self.assertEqual(1, complete.begins)
        for index in (1, 2):
            for after in (False, True):
                ledger = RepairLedger(index, after_write=after)
                with self.assertRaises(RuntimeError):
                    store_for(ledger).repair_pending_outcome_target_schedules("fixture")
                self.assertEqual([], ledger.committed)

    def test_pending_maintenance_failure_is_not_cached_as_completed(self):
        store = store_for(AtomicLedger())
        store.backfill_outcome_targets = Mock(return_value={"status": "already-initialized"})
        store.repair_pending_outcome_target_schedules = Mock(
            side_effect=[RuntimeError("repair fault"), {}]
        )
        with self.assertRaisesRegex(RuntimeError, "repair fault"):
            store.pending_outcome_targets("fixture", NOW)
        self.assertNotIn(
            "fixture", getattr(store, "_outcome_target_schedule_repair_completed_accounts", set())
        )
        self.assertEqual([], store.pending_outcome_targets("fixture", NOW))
        self.assertEqual(2, store.repair_pending_outcome_target_schedules.call_count)
        self.assertEqual(1, store.backfill_outcome_targets.call_count)

    def test_stale_follow_up_compare_and_set_cannot_report_a_transition(self):
        ledger = AtomicLedger()
        ledger.select_rows = [
            {
                "condition_id": "condition:old",
                "episode_id": "episode:old",
                "payload_json": json.dumps({"status": "pending"}),
            }
        ]
        ledger.rowcount = 0
        with patch.object(
            follow_ups,
            "evaluate_follow_up_conditions",
            return_value=(
                [{"status": "satisfied", "transitionAt": NOW}],
                True,
            ),
        ):
            result = store_for(ledger).evaluate_follow_up_observation("fixture", "AAPL", {}, NOW)
        self.assertEqual([], result)
        self.assertIn("AND status = 'pending'", ledger.statements[-1][0])

    def test_invalid_decisions_fail_before_acquiring_a_transaction(self):
        for action, source, selected in (
            ("NO_ACTION", "fixture", "h"),
            ("HOLD", "v2-reasoning-case", ""),
        ):
            value = episode()
            value.action, value.source, value.selected_hypothesis_id = action, source, selected
            ledger = AtomicLedger()
            with self.assertRaises(ValueError):
                store_for(ledger).save(value)
            self.assertEqual(0, ledger.begins)

    def test_private_helpers_do_not_receive_a_repository_or_control_transactions(self):
        parts = Path(decision_write.__file__).parent
        for path in parts.glob("*.py"):
            tree = ast.parse(path.read_text())
            self.assertFalse(
                any(isinstance(node, ast.Name) and node.id == "self" for node in ast.walk(tree)),
                path,
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(
                        node.func.attr, {"commit", "rollback", "connect", "transaction"}, path
                    )


if __name__ == "__main__":
    unittest.main()
