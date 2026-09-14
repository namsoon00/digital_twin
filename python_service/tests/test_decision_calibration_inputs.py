"""Compact history projection parity and transactional failure contracts."""

import unittest
import uuid
from unittest.mock import patch

from stabilization_database import StabilizationDatabaseCase
from test_internal_decision_history import episode, outcome, NOW
from digital_twin.modules.decisions.contracts import DecisionEpisode
from digital_twin.modules.outcomes.domain.decision_calibration_input import calibration_hypotheses
from digital_twin.modules.outcomes.contracts import evaluate_decision_performance
from digital_twin.modules.outcomes.infrastructure.mysql_decision_calibration_inputs import MySQLDecisionCalibrationInputStore
from digital_twin.modules.outcomes.infrastructure import transaction_writes


class CalibrationInputShapeTests(unittest.TestCase):
    def test_calibration_projection_keeps_claims_order_and_never_audit_payload(self):
        source = {"hypothesisSet": {"hypotheses": [
            {"hypothesisId": "a", "claimContract": {"revision": 2}, "trace": "large-audit"},
            {"hypothesisId": "a", "claimContract": {"revision": 1}}, None,
        ]}, "factsAtDecision": {"private": "not-calibration"}}
        result = calibration_hypotheses(source)
        self.assertEqual([{"hypothesisId": "a", "claimContract": {"revision": 2}},
                          {"hypothesisId": "a", "claimContract": {"revision": 1}}], result)
        result[0]["claimContract"]["revision"] = 9
        self.assertEqual(2, source["hypothesisSet"]["hypotheses"][0]["claimContract"]["revision"])

    def test_calibration_projection_preserves_missing_contract_semantics(self):
        for payload in ({}, {"hypothesisSet": None}, {"hypothesisSet": {"hypotheses": 7}}):
            self.assertEqual([], calibration_hypotheses(payload))
        self.assertEqual([{"hypothesisId": "a", "claimContract": {}}],
                         calibration_hypotheses({"hypothesisSet": {"hypotheses": [{"hypothesisId": "a"}]}}))


class CalibrationInputStorageTests(StabilizationDatabaseCase):
    def create_history(self):
        value = episode("calibration:" + uuid.uuid4().hex)
        payload = value.to_dict()
        payload["factsAtDecision"]["auditOnly"] = "x" * 50000
        payload["hypothesisSet"]["hypotheses"][0]["claimContract"] = {"claimContractId": "claim:exact", "revision": 3}
        value = DecisionEpisode.from_dict(payload)
        self.decisions.save(value)
        observed = outcome("outcome:" + uuid.uuid4().hex)
        observed.episode_id = value.episode_id
        self.decisions.save_outcome(value, observed)
        return value

    def test_calibration_sql_matches_legacy_and_excludes_future_and_other_accounts(self):
        value = self.create_history()
        def read(cutoff=NOW):
            return self.decisions.performance_episodes(account_id=value.account_id, limit=2000, as_of=cutoff)
        compact = read()
        expected = next(row for row in compact if row["episodeId"] == value.episode_id)
        self.assertEqual("claim:exact", expected["hypothesisSet"]["hypotheses"][0]["claimContract"]["claimContractId"])
        self.sql("DELETE FROM investment_decision_calibration_inputs WHERE episode_id=%s", (value.episode_id,))
        self.assertEqual(compact, read())
        self.assertEqual(evaluate_decision_performance(compact), evaluate_decision_performance(read()))
        self.assertNotIn(value.episode_id, [r["episodeId"] for r in read("2026-09-10T14:30:00Z")])
        self.assertEqual([], self.decisions.performance_episodes(account_id="other:" + uuid.uuid4().hex, as_of=NOW))
        repaired = MySQLDecisionCalibrationInputStore(self.settings).repair()
        self.assertGreaterEqual(repaired["repairedCount"], 1)
        self.assertEqual(compact, read())

    def test_stale_calibration_version_falls_back_and_repair_removes_orphans(self):
        value = self.create_history()
        before = self.decisions.performance_episodes(account_id=value.account_id, as_of=NOW)
        for column in ("format_version", "source_updated_at"):
            self.sql("UPDATE investment_decision_calibration_inputs SET " + column + "='invalid', hypotheses_json='[]' WHERE episode_id=%s", (value.episode_id,))
            self.assertEqual(before, self.decisions.performance_episodes(account_id=value.account_id, as_of=NOW))
            MySQLDecisionCalibrationInputStore(self.settings).repair()
        self.sql("DELETE FROM investment_decision_episodes WHERE episode_id=%s", (value.episode_id,))
        MySQLDecisionCalibrationInputStore(self.settings).repair()
        self.assertIsNone(self.sql("SELECT episode_id FROM investment_decision_calibration_inputs WHERE episode_id=%s", (value.episode_id,)))

    def test_calibration_write_failure_rolls_back_decision_and_projection_together(self):
        value = self.create_history()
        original = self.sql("SELECT payload_json FROM investment_decision_episodes WHERE episode_id=%s", (value.episode_id,))
        projection = self.sql("SELECT * FROM investment_decision_calibration_inputs WHERE episode_id=%s", (value.episode_id,))
        value.status = "changed"
        write = transaction_writes.upsert_decision_calibration_input
        def fail_after_write(*args, **kwargs):
            write(*args, **kwargs)
            raise RuntimeError("fixture-calibration-failure")
        with patch.object(transaction_writes, "upsert_decision_calibration_input", side_effect=fail_after_write):
            with self.assertRaisesRegex(RuntimeError, "fixture-calibration-failure"):
                self.decisions.save(value)
        self.assertEqual(original, self.sql("SELECT payload_json FROM investment_decision_episodes WHERE episode_id=%s", (value.episode_id,)))
        self.assertEqual(projection, self.sql("SELECT * FROM investment_decision_calibration_inputs WHERE episode_id=%s", (value.episode_id,)))

    def test_calibration_repair_skips_locked_episode_without_changing_its_source(self):
        value = self.create_history()
        self.sql("DELETE FROM investment_decision_calibration_inputs WHERE episode_id=%s", (value.episode_id,))
        with self.decisions.transaction() as connection:
            connection.execute("SELECT episode_id FROM investment_decision_episodes WHERE episode_id=%s FOR UPDATE", (value.episode_id,)).fetchone()
            result = MySQLDecisionCalibrationInputStore(self.settings).repair()
            self.assertEqual(0, result["repairedCount"])
            self.assertEqual("locked", result["status"])
        self.assertEqual(1, MySQLDecisionCalibrationInputStore(self.settings).repair()["repairedCount"])
