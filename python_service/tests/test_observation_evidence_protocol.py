"""Cross-boundary regressions for omitted facts, change detection and replay."""
import copy
import json
import unittest
from unittest.mock import Mock

from digital_twin.modules.reasoning.domain.observation_evidence import (
    select_evidence, validate_evidence_packet, evidence_change_identity,
)
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
from digital_twin.modules.ai_orchestration.infrastructure.observation_reader import GraphObservationReader
from test_ai_control import PACKET, SUBJECT
import test_ai_control as control_helpers


class ObservationEvidenceProtocolTests(unittest.TestCase):
    def test_subject_inventory_delivers_financial_and_shared_evidence_without_rules(self):
        repository = Mock()
        repository.active_abox_metadata.return_value = {"status": "ok", "aboxSnapshotId": "snapshot-1", "accountId": SUBJECT["accountId"]}
        repository.active_abox_snapshot_id.return_value = "snapshot-1"
        values = [{"id": "q", "kind": "stock", "symbol": "TEST", "currentPrice": 100},
                  {"id": "eps", "kind": "earnings-scenario-observation", "symbol": "TEST", "payload": {"base": 10}},
                  {"id": "dcf", "kind": "valuation-input-bundle", "symbol": "TEST", "payload": {"sourceReferences": ["annual-report"]}},
                  {"id": "rate", "kind": "interest-rate", "rate": 4.0}]
        repository.read_rows.side_effect = [[{"id": x["id"], "kind": x["kind"], "label": x["id"], "json": json.dumps(x)} for x in values[:-1]],
                                            [{"id": "rate", "kind": "interest-rate", "label": "rate", "json": json.dumps(values[-1])}], []]
        repository.active_abox_members_clause.return_value = ""
        packet = GraphObservationReader(repository)(SUBJECT)
        validate_evidence_packet(packet)
        self.assertEqual(4, packet["includedFactCount"])
        self.assertEqual(2, packet["coverage"]["valuation"]["included"])
        self.assertEqual(1, packet["coverage"]["macro"]["included"])
        repository.active_abox_rule_context.assert_not_called()

    def test_coverage_accounts_for_whole_fact_exclusions_and_unknown_kinds(self):
        original = copy.deepcopy(PACKET["facts"])
        oversized = {**original[0], "id": "large", "kind": "valuation-input-bundle", "payload": "x" * 40000}
        unsupported = {**original[0], "id": "new", "kind": "future-kind", "newMetric": 1}
        facts, coverage = select_evidence(original + [oversized, unsupported])
        packet = {**PACKET, "facts": facts, "coverage": coverage}
        validate_evidence_packet(packet)
        self.assertEqual({"context-budget": 1}, coverage["valuation"]["exclusionReasons"])
        self.assertEqual("missing", coverage["company"]["status"])
        self.assertEqual("unsupported", coverage["unclassified"]["status"])
        before = evidence_change_identity(packet, [])
        unsupported["newMetric"] = 2
        _, coverage2 = select_evidence(original + [oversized, unsupported])
        self.assertNotEqual(before, evidence_change_identity({**packet, "coverage": coverage2}, []))
        broken = copy.deepcopy(packet); broken["coverage"]["quote"]["included"] = 0
        with self.assertRaises(ValueError):
            validate_evidence_packet(broken)
        old = {**oversized, "id": "a-old", "sourceAsOf": "2025-01-01", "payload": "x" * 18000}
        new = {**old, "id": "z-new", "sourceAsOf": "2026-01-01"}
        selected, _ = select_evidence(original + [old, new])
        self.assertIn("z-new", {fact["id"] for fact in selected})
        self.assertNotIn("a-old", {fact["id"] for fact in selected})

    def test_all_new_value_quality_and_nested_revision_fields_are_material(self):
        packet = copy.deepcopy(PACKET)
        base = evidence_change_identity(packet, [])
        for key, value in (("bidAskImbalance", 0.5), ("investorFlowComplete", False), ("judgementEvidenceUsable", False),
                           ("newlyAddedMetric", 12), ("payload", {"sourceRevision": "new", "valuationDecisionEligible": False})):
            changed = copy.deepcopy(packet); changed["facts"][0][key] = value
            self.assertNotEqual(base, evidence_change_identity(changed, []), key)
        for key in ("projectionRunId", "updatedAt", "sourceSnapshotId"):
            changed = copy.deepcopy(packet); changed["facts"][0][key] = "later-poll"
            self.assertEqual(base, evidence_change_identity(changed, []), key)

    def test_frozen_execution_owns_actual_memories_and_detects_prompt_tampering(self):
        history, research = [{"summary": "prior", "previousFacts": [{"currentPrice": 90}]}], [{"runId": "research-v1"}]
        envelope = freeze_execution_input(PACKET, history, research)
        fingerprint = validate_execution_input(envelope)
        history[0]["summary"] = "later"; research[0]["runId"] = "later"
        self.assertEqual(fingerprint, validate_execution_input(envelope))
        envelope["researchResults"][0]["runId"] = "tampered"
        with self.assertRaises(ValueError):
            validate_execution_input(envelope)
        bad = {**PACKET, "protocolVersion": "unrecognized"}
        with self.assertRaises(ValueError):
            freeze_execution_input(bad, [], [])

    def test_failed_capture_or_lost_input_lease_cannot_invoke_model(self):
        helper = control_helpers.AIControlTests()
        service, store, planner = helper.runner()
        store.save_execution_input.return_value = ""
        self.assertEqual("lease-lost", service.run_once()["status"])
        planner.assert_not_called()
        store.complete.assert_not_called()
