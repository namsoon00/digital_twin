"""The small live read model must reproduce the original ABox, not a summary."""

from copy import deepcopy
import json
import unittest
import uuid
from unittest.mock import patch

from stabilization_database import StabilizationDatabaseCase
from test_internal_decision_history import episode, outcome, NOW
from digital_twin.modules.decisions.contracts import DecisionEpisode, decision_episode_ontology_context
from digital_twin.modules.decisions.domain.decision_projection_input import decision_projection_input
from digital_twin.modules.decisions.infrastructure import transaction_writes
from digital_twin.modules.decisions.infrastructure.mysql_projection_inputs import MySQLDecisionProjectionInputStore


class DecisionProjectionShapeTests(unittest.TestCase):
    def test_projection_preserves_every_context_limit_without_copying_audit(self):
        payload = episode().to_dict()
        payload["factsAtDecision"]["auditOnly"] = "large" * 200000
        payload["researchAudit"] = {"text": "research" * 200000}
        hypothesis = payload["hypothesisSet"]["hypotheses"][0]
        payload["hypothesisSet"]["hypotheses"] = [
            {**deepcopy(hypothesis), "hypothesisId": "h:" + str(i), "claim": "claim " + str(i)}
            for i in range(24)
        ]
        payload["selectedHypothesisId"] = "h:20"
        original, compact = DecisionEpisode.from_dict(payload), DecisionEpisode.from_dict(decision_projection_input(payload))
        for limit in (1, 3, 16):
            self.assertEqual(decision_episode_ontology_context(original, limit),
                             decision_episode_ontology_context(compact, limit))
        small = decision_projection_input(payload)
        self.assertLess(len(json.dumps(small)), len(json.dumps(payload)) / 50)
        small["hypothesisSet"]["hypotheses"][0]["claim"] = "changed"
        self.assertEqual("claim 0", payload["hypothesisSet"]["hypotheses"][0]["claim"])


class DecisionProjectionStorageTests(StabilizationDatabaseCase):
    def create_history(self):
        value = episode("projection:" + uuid.uuid4().hex)
        value.facts_at_decision["auditOnly"] = "x" * 50000
        self.decisions.save(value)
        observed = outcome("outcome:" + uuid.uuid4().hex)
        observed.episode_id = value.episode_id
        self.decisions.save_outcome(value, observed)
        return value

    def contexts(self, value, compact=True, cutoff=NOW):
        reader = self.decisions.list_projection_for_symbols if compact else self.decisions.list_for_symbols
        return [decision_episode_ontology_context(item) for item in reader(
            [value.symbol], account_id=value.account_id, as_of=cutoff, limit_per_symbol=3)]

    def test_compact_read_preserves_native_input_outcomes_cutoff_and_subject(self):
        value = self.create_history()
        for cutoff in (NOW, "2026-09-10T14:30:00Z", "2026-09-10T13:00:00Z"):
            self.assertEqual(self.contexts(value, False, cutoff), self.contexts(value, True, cutoff))
        self.assertEqual([], self.decisions.list_projection_for_symbols(["MSFT"], account_id=value.account_id, as_of=NOW))
        self.assertEqual([], self.decisions.list_projection_for_symbols([value.symbol], account_id="other", as_of=NOW))
        row = self.sql("SELECT payload_json FROM investment_decision_projection_inputs WHERE episode_id=%s", (value.episode_id,))
        self.assertNotIn("auditOnly", row["payload_json"])

    def test_missing_stale_and_old_version_repair_without_changing_decision(self):
        value = self.create_history()
        expected = self.contexts(value, False)
        original = self.sql("SELECT payload_json,updated_at FROM investment_decision_episodes WHERE episode_id=%s", (value.episode_id,))
        for column in ("source_updated_at", "format_version"):
            self.sql("UPDATE investment_decision_projection_inputs SET " + column + "='old',payload_json='{}' WHERE episode_id=%s", (value.episode_id,))
            self.assertEqual(expected, self.contexts(value))
            MySQLDecisionProjectionInputStore(self.settings).repair()
        self.sql("DELETE FROM investment_decision_projection_inputs WHERE episode_id=%s", (value.episode_id,))
        self.assertEqual(expected, self.contexts(value))
        self.assertGreaterEqual(MySQLDecisionProjectionInputStore(self.settings).repair()["repairedCount"], 1)
        self.assertEqual(expected, self.contexts(value))
        self.assertEqual(original, self.sql("SELECT payload_json,updated_at FROM investment_decision_episodes WHERE episode_id=%s", (value.episode_id,)))

    def test_projection_failure_rolls_back_the_original_decision(self):
        value = self.create_history()
        before = self.contexts(value, False)
        value.status = "changed"
        write = transaction_writes.upsert_decision_projection_input
        def fail(*args):
            write(*args)
            raise RuntimeError("projection-write-failed")
        with patch.object(transaction_writes, "upsert_decision_projection_input", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "projection-write-failed"):
                self.decisions.save(value)
        self.assertEqual(before, self.contexts(value, False))
        self.assertEqual(before, self.contexts(value))

    def test_repair_skips_busy_source_and_reclaims_only_orphans(self):
        value = self.create_history()
        self.sql("DELETE FROM investment_decision_projection_inputs WHERE episode_id=%s", (value.episode_id,))
        with self.decisions.transaction() as connection:
            connection.execute("SELECT episode_id FROM investment_decision_episodes WHERE episode_id=%s FOR UPDATE", (value.episode_id,)).fetchone()
            self.assertEqual("locked", MySQLDecisionProjectionInputStore(self.settings).repair()["status"])
        MySQLDecisionProjectionInputStore(self.settings).repair()
        self.sql("DELETE FROM investment_decision_episodes WHERE episode_id=%s", (value.episode_id,))
        self.assertGreaterEqual(MySQLDecisionProjectionInputStore(self.settings).repair()["orphanCount"], 1)
        self.assertIsNone(self.sql("SELECT episode_id FROM investment_decision_projection_inputs WHERE episode_id=%s", (value.episode_id,)))
