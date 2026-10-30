"""Real-MySQL cache validity, historical cutoff and rollback rehearsals."""

import json
import unittest
import uuid
from unittest.mock import patch

from stabilization_database import StabilizationDatabaseCase
from test_internal_decision_history import episode, outcome, NOW
from digital_twin.infrastructure.transactions.decision_history import MySQLInvestmentDecisionEpisodeStore
from digital_twin.modules.outcomes.infrastructure import prepared_inputs as cache
from digital_twin.modules.outcomes.infrastructure.prepared_input_schema import ensure_prepared_input_schema


class PreparedOutcomeInputTests(StabilizationDatabaseCase):
    def setUp(self):
        super().setUp()
        self.account = "prepared:" + uuid.uuid4().hex
        self.addCleanup(self.sql, "DELETE FROM hypothesis_development_cases WHERE account_id=%s", (self.account,))
        self.value = episode("prepared-episode:" + uuid.uuid4().hex)
        self.value.account_id = self.account
        self.decisions.save(self.value)
        self.observed = outcome("prepared-outcome:" + uuid.uuid4().hex)
        self.observed.episode_id = self.value.episode_id
        self.decisions.save_outcome(self.value, self.observed)

    def history(self, stamp=NOW):
        return self.decisions.performance_episodes(account_id=self.account, symbol="AAPL", as_of=stamp)

    def report(self, stamp=NOW, **kwargs):
        return self.decisions.performance(account_id=self.account, as_of=stamp, **kwargs)

    def uncached_report(self, stamp=NOW, **kwargs):
        store = MySQLInvestmentDecisionEpisodeStore({**self.settings, "outcomePreparedInputsEnabled": "false"})
        return store.performance(account_id=self.account, as_of=stamp, **kwargs)

    def mutate_outcome(self, connection=None):
        query = "UPDATE investment_decision_outcomes SET payload_json=JSON_SET(payload_json, '$.priceChangeFromDecisionPct', -7) WHERE outcome_id=%s"
        if connection:
            connection.execute(query, (self.observed.outcome_id,))
        else:
            self.sql(query, (self.observed.outcome_id,))

    def test_report_reuse_crosses_process_objects_and_skips_history_before_read(self):
        expected = self.uncached_report()
        self.assertEqual(expected, self.report())

        self.assertEqual("miss", self.decisions.last_prepared_outcome_input["status"])
        another = MySQLInvestmentDecisionEpisodeStore(self.settings)
        with patch.object(another, "performance_episodes", side_effect=AssertionError("history must not be read on a hit")):
            actual = another.performance(account_id=self.account, as_of="2026-09-10T15:02:00Z")
        self.assertEqual(expected, actual)
        self.assertEqual("hit", another.last_prepared_outcome_input["status"])
        self.assertFalse(another.last_prepared_outcome_input["historyRead"])
        actual["episodeCount"] = -1
        self.assertEqual(expected, self.report())

    def test_cold_history_identity_selection_can_use_only_the_covering_index(self):
        with self.decisions.connect() as connection:
            row = connection.execute("EXPLAIN FORMAT=JSON SELECT episode_id, MAX(observed_at) AS latest_observed_at "
                       "FROM investment_decision_outcomes FORCE INDEX (idx_outcome_calibration_selection) WHERE account_id=%s AND observed_at<=%s "
                       "GROUP BY episode_id ORDER BY latest_observed_at DESC LIMIT 2000", (self.account, NOW)).fetchone()
        plan = json.loads(row["EXPLAIN"])
        def tables(value):
            if isinstance(value, dict):
                if value.get("table_name") == "investment_decision_outcomes":
                    yield value
                for child in value.values():
                    yield from tables(child)
            elif isinstance(value, list):
                for child in value:
                    yield from tables(child)
        access = list(tables(plan))
        self.assertTrue(access)
        self.assertTrue(all(item.get("using_index") or item.get("covering") for item in access), plan)

    def test_future_outcomes_and_backwards_replay_do_not_reuse_the_wrong_window(self):
        before = "2026-09-10T14:59:00Z"
        self.assertEqual([], self.history(before))
        rows = self.history(NOW)
        self.assertEqual(1, len(rows))
        self.assertEqual("miss", self.decisions.last_prepared_outcome_input["status"])
        self.assertEqual([], self.history(before))
        self.assertEqual(self.uncached_report(before), self.report(before))
        self.assertEqual(self.uncached_report(), self.report())

    def test_same_timestamp_correction_delete_and_backfill_invalidate(self):
        original = self.history()
        self.mutate_outcome()
        changed = self.history()
        self.assertNotEqual(original, changed)
        self.assertEqual("miss", self.decisions.last_prepared_outcome_input["status"])
        self.sql("DELETE FROM investment_decision_outcomes WHERE outcome_id=%s", (self.observed.outcome_id,))
        self.assertEqual([], self.history())
        self.decisions.save_outcome(self.value, self.observed)
        self.assertEqual(original, self.history())

    def test_observed_episode_and_compact_projection_repairs_invalidate(self):
        self.history()
        token = self.decisions.outcome_input_revision()
        self.sql("UPDATE investment_decision_calibration_inputs SET hypotheses_json=JSON_SET(hypotheses_json, '$.hypotheses[0].claimContract.revision', 42) WHERE episode_id=%s", (self.value.episode_id,))
        self.assertNotEqual(token, self.decisions.outcome_input_revision())
        self.assertEqual(42, self.history()[0]["hypothesisSet"]["hypotheses"][0]["claimContract"]["revision"])
        self.sql("DELETE FROM investment_decision_calibration_inputs WHERE episode_id=%s", (self.value.episode_id,))
        self.assertNotIn("revision", self.history()[0]["hypothesisSet"]["hypotheses"][0]["claimContract"])
        self.sql("UPDATE investment_decision_episodes SET subject_name='Corrected' WHERE episode_id=%s", (self.value.episode_id,))
        self.assertEqual("Corrected", self.history()[0]["subjectName"])
        self.sql("DELETE FROM investment_decision_episodes WHERE episode_id=%s", (self.value.episode_id,))
        self.assertEqual([], self.history())

    def test_pending_episode_does_not_flush_calibration_but_archive_count_stays_current(self):
        old = self.report()
        token = self.decisions.outcome_input_revision()
        pending = episode("pending:" + uuid.uuid4().hex)
        pending.account_id = self.account
        self.decisions.save(pending)
        self.assertEqual(token, self.decisions.outcome_input_revision())
        result = self.report()
        self.assertEqual("hit", self.decisions.last_prepared_outcome_input["status"])
        self.assertEqual(old["episodeCount"] + 1, result["episodeCount"])
        self.assertEqual(self.uncached_report(), result)

    def test_failed_transaction_rolls_back_both_change_and_invalidation(self):
        expected = self.history()
        token = self.decisions.outcome_input_revision()
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.decisions.transaction() as connection:
                self.mutate_outcome(connection)
                raise RuntimeError("rollback")
        self.assertEqual(token, self.decisions.outcome_input_revision())
        self.assertEqual(expected, self.history())
        self.assertEqual("hit", self.decisions.last_prepared_outcome_input["status"])

    def test_coverage_deadline_is_recomputed_even_when_calibration_is_reused(self):
        pending = episode("pending:" + uuid.uuid4().hex)
        pending.account_id = self.account
        self.decisions.save(pending)
        self.sql("UPDATE investment_decision_outcome_targets SET target_at='2026-09-10T15:01:00Z' WHERE episode_id=%s", (pending.episode_id,))
        before = self.report()
        after = self.report("2026-09-10T15:02:00Z")
        self.assertEqual("hit", self.decisions.last_prepared_outcome_input["status"])
        self.assertGreater(after["outcomeCoverageDueUnobservedEpisodeCount"], before["outcomeCoverageDueUnobservedEpisodeCount"])
        self.assertEqual(self.uncached_report("2026-09-10T15:02:00Z"), after)

    def test_qualification_adoption_revoke_and_future_offset_clock_invalidate(self):
        self.report()
        identity = uuid.uuid4().hex
        self.sql("INSERT INTO hypothesis_development_cases (case_id, fingerprint, account_id, symbol, status, payload_json, created_at, updated_at) VALUES (%s,%s,%s,'AAPL','evolution-monitoring',%s,%s,%s)",
                 (identity, identity, self.account, json.dumps({"evolution": {"assessment": {"evaluatedAt": "2026-09-11T00:01:00+09:00"}}}), NOW, NOW))
        self.report()
        self.assertEqual("miss", self.decisions.last_prepared_outcome_input["status"])
        self.report("2026-09-10T15:00:30Z")
        self.assertEqual("hit", self.decisions.last_prepared_outcome_input["status"])
        self.report("2026-09-10T15:01:00Z")
        self.assertEqual("miss", self.decisions.last_prepared_outcome_input["status"])
        self.sql("UPDATE hypothesis_development_cases SET status='blocked' WHERE case_id=%s", (identity,))
        self.report()
        self.assertEqual("miss", self.decisions.last_prepared_outcome_input["status"])

    def test_account_symbol_limit_and_qualification_threshold_are_separate(self):
        self.report()
        other = self.decisions.performance(account_id="other:" + self.account, as_of=NOW)
        self.assertEqual(0, other["episodeCount"])
        self.assertEqual([], self.decisions.performance_episodes(account_id=self.account, symbol="MSFT", as_of=NOW))
        for limit in (1, 50):
            self.assertEqual(self.uncached_report(limit=limit), self.report(limit=limit))
            self.assertEqual("miss", self.decisions.last_prepared_outcome_input["status"])
        store = MySQLInvestmentDecisionEpisodeStore({**self.settings, "investmentBrainPerformanceMinimumSamples": "17"})
        expected = MySQLInvestmentDecisionEpisodeStore({**store.runtime_settings, "outcomePreparedInputsEnabled": "false"}).performance(account_id=self.account, as_of=NOW)
        self.assertEqual(expected, store.performance(account_id=self.account, as_of=NOW))
        self.assertEqual("miss", store.last_prepared_outcome_input["status"])

    def test_concurrent_commit_during_cold_read_does_not_publish_a_stale_cache(self):
        def load():
            self.mutate_outcome()
            return {"sample": "before-concurrent-commit"}
        value, receipt = cache.prepared_input(connect=self.decisions.connect, account_id=self.account,
            symbol="", as_of=NOW, kind="race-fixture", parameters={}, load=load)
        self.assertEqual("changed-during-read", receipt["status"])
        self.assertEqual({"sample": "before-concurrent-commit"}, value)
        _, receipt = cache.prepared_input(connect=self.decisions.connect, account_id=self.account,
            symbol="", as_of=NOW, kind="race-fixture", parameters={}, load=lambda: {"sample": "new"})
        self.assertEqual("miss", receipt["status"])

    def test_read_failure_and_invalid_payload_fall_back_but_loader_failure_propagates(self):
        expected = self.history()
        self.sql("UPDATE prepared_outcome_inputs SET payload_json='broken'")
        self.assertEqual(expected, self.history())
        self.assertEqual("unavailable", self.decisions.last_prepared_outcome_input["status"])
        with patch.object(cache, "revision_token", side_effect=RuntimeError("cache unavailable")):
            self.assertEqual(expected, self.history())
            with self.assertRaisesRegex(RuntimeError, "authoritative read failed"):
                cache.prepared_input(connect=self.decisions.connect, account_id=self.account,
                    symbol="", as_of=NOW, kind="failure", parameters={},
                    load=lambda: (_ for _ in ()).throw(RuntimeError("authoritative read failed")))
        for enabled in ("true", "false"):
            with patch.dict(self.decisions.runtime_settings, {"outcomePreparedInputsEnabled": enabled}):
                self.decisions.last_prepared_outcome_input = {"status": "hit", "historyRead": False}
                with self.assertRaisesRegex(RuntimeError, "authoritative read failed"):
                    self.decisions.prepared_outcome_input("failure", self.account, "", NOW, {},
                        lambda: (_ for _ in ()).throw(RuntimeError("authoritative read failed")))
                self.assertEqual({"status": "failed", "kind": "failure", "historyRead": True},
                                 self.decisions.last_prepared_outcome_input)

    def test_concurrent_commit_between_cache_lookup_and_validation_reloads_history(self):
        old = self.history()
        original = cache.revision_token
        calls = []
        def read(connection):
            calls.append(1)
            if len(calls) == 2:
                self.mutate_outcome()
            return original(connection)
        with patch.object(cache, "revision_token", side_effect=read):
            changed = self.history()
        self.assertNotEqual(old, changed)
        self.assertTrue(self.decisions.last_prepared_outcome_input["historyRead"])

    def test_shadow_history_insert_repair_and_delete_invalidate(self):
        original = self.history()
        identity = uuid.uuid4().hex
        payload = {"episodeId": identity, "accountId": self.account, "symbol": "AAPL", "hypothesisId": "shadow",
                   "observedFromAt": "2026-09-10T14:00:00Z", "hypothesis": {"hypothesisId": "shadow"}}
        self.sql("INSERT INTO investment_hypothesis_observation_episodes (episode_id, account_id, symbol, claim_identity, independence_bucket, payload_json, created_at, updated_at) VALUES (%s,%s,'AAPL',%s,%s,%s,%s,%s)",
                 (identity, self.account, identity, identity, json.dumps(payload), NOW, NOW))
        self.sql("INSERT INTO investment_hypothesis_observation_outcomes (outcome_id, observation_episode_id, account_id, symbol, observed_at, payload_json, created_at) VALUES (%s,%s,%s,'AAPL',%s,%s,%s)",
                 (identity, identity, self.account, NOW, json.dumps({"observedAt": NOW, "price": 100}), NOW))
        self.assertEqual(len(original) + 1, len(self.history()))
        self.sql("UPDATE investment_hypothesis_observation_episodes SET payload_json=JSON_SET(payload_json, '$.candidateAction', 'REDUCE') WHERE episode_id=%s", (identity,))
        self.assertEqual("REDUCE", next(row for row in self.history() if row["episodeId"] == identity)["action"])
        self.sql("UPDATE investment_hypothesis_observation_outcomes SET payload_json=JSON_SET(payload_json, '$.price', 123) WHERE outcome_id=%s", (identity,))
        self.assertEqual(123, next(row for row in self.history() if row["episodeId"] == identity)["outcomes"][0]["price"])
        self.sql("DELETE FROM investment_hypothesis_observation_outcomes WHERE outcome_id=%s", (identity,))
        self.assertEqual(original, self.history())

    def test_fractional_clock_and_qualification_boundaries_keep_uncached_semantics(self):
        identity = uuid.uuid4().hex
        self.sql("INSERT INTO hypothesis_development_cases (case_id, fingerprint, account_id, symbol, status, payload_json, created_at, updated_at) VALUES (%s,%s,%s,'AAPL','strengthened',%s,%s,%s)",
                 (identity, identity, self.account, json.dumps({"evolution": {"assessment": {"evaluatedAt": "2026-09-11T00:00:00.500000+09:00"}}}), NOW, NOW))
        for stamp in (NOW, "2026-09-10T15:00:00.100000Z", "2026-09-10T15:00:00.600000Z", "2026-09-10T15:01:00Z", NOW):
            self.assertEqual(self.uncached_report(stamp), self.report(stamp))

    def test_schema_repair_invalidates_existing_prepared_rows(self):
        self.report()
        token = self.decisions.outcome_input_revision()
        self.sql("DROP TRIGGER outcome_input_v1_do_update")
        with self.decisions.transaction() as connection:
            ensure_prepared_input_schema(connection)
        self.assertNotEqual(token, self.decisions.outcome_input_revision())
        self.report()
        self.assertEqual("miss", self.decisions.last_prepared_outcome_input["status"])


if __name__ == "__main__":
    unittest.main()
