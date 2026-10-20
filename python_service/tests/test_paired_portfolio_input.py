"""Real cutoff reads and frozen paired inputs, without delivery or providers."""

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from digital_twin.modules.reasoning.domain.validation_portfolio_input import (
    freeze_validation_portfolio_input, read_validation_portfolio_input,
)
from digital_twin.modules.reasoning.domain.reasoning_comparison_identity import PAIRED_VALIDATION_INPUT_VERSION
from digital_twin.modules.reasoning.domain.reasoning_source_snapshot import reasoning_source_snapshot_id
from stabilization_database import StabilizationDatabaseCase


STAMP = "2026-10-20T01:00:00Z"


class PairedPortfolioInputTests(unittest.TestCase):
    def test_frozen_portfolio_receipt_rejects_changed_source_and_survives_later_updates(self):
        row = {"portfolioId": "portfolio:fixture", "asOf": STAMP, "portfolioRiskSnapshot": {"id": "risk:old"}}
        packet = freeze_validation_portfolio_input("fixture", "source:exact", STAMP, row)
        row["portfolioRiskSnapshot"]["id"] = "risk:new"
        read = lambda value=packet, account="fixture", source="source:exact", stamp=STAMP: read_validation_portfolio_input(value, account, source, stamp)
        self.assertEqual("risk:old", read()["portfolioRiskSnapshot"]["id"])
        for kwargs in ({"account": "other"}, {"source": "new"}, {"stamp": "2026-10-20T02:00:00Z"},
                       {"value": {**packet, "sha256": "invalid"}}, {"value": {}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                read(**kwargs)
        changed = freeze_validation_portfolio_input("fixture", "source:exact", STAMP, row)
        self.assertNotEqual(packet["sha256"], changed["sha256"])
        from digital_twin.modules.reasoning.domain.reasoning_batch import reasoning_batch_key
        event = {"payload": {"candidateValidationReplay": True, "symbols": ["AAA"],
                            "validationPair": {"inputMode": PAIRED_VALIDATION_INPUT_VERSION,
                                               "portfolioContextHash": packet["sha256"]}}}
        other = deepcopy(event)
        other["payload"]["validationPair"]["portfolioContextHash"] = changed["sha256"]
        self.assertNotEqual(reasoning_batch_key(event, "GOVERNANCE"), reasoning_batch_key(other, "GOVERNANCE"))

    def test_capture_uses_the_frozen_portfolio_but_retains_independent_market_reads(self):
        from digital_twin.infrastructure import ontology_projection as api
        from digital_twin.modules.reasoning.application.projection_input.capture import capture_graph_input
        from digital_twin.modules.reasoning.domain.reasoning_shadow import unpack_projection_runtime_contexts
        from projection_input_fixture import recorder, source_snapshot, TBOX
        snapshot = source_snapshot(as_of=STAMP)
        source_id = reasoning_source_snapshot_id(snapshot.account_id, STAMP)
        row = {"portfolioId": "portfolio:" + snapshot.account_id, "asOf": STAMP, "portfolioRiskSnapshot": {"id": "risk:frozen"}}
        packet = freeze_validation_portfolio_input(snapshot.account_id, source_id, STAMP, row)
        instance = recorder(api, snapshot=snapshot)
        instance.runtime_context_overrides[snapshot.account_id]["portfolioLifecycle"] = {"id": "risk:later"}
        instance.runtime_context_overrides[snapshot.account_id]["temporalObservationWindows"] = {"AAA": {"backend": "independent"}}
        inputs = SimpleNamespace(settings=instance.settings, active_tbox_context=lambda: TBOX,
                                 runtime_context=instance.runtime_context,
                                 last_runtime_contexts=instance.last_runtime_contexts,
                                 last_runtime_context_cache_status=instance.last_runtime_context_cache_status)
        context = {"validationOnly": True, "validationInputMode": PAIRED_VALIDATION_INPUT_VERSION,
                   "projectionComputationTargetSymbols": ["AAA"],
                   "validationPortfolioContexts": {snapshot.account_id: packet}}
        result = capture_graph_input(inputs, snapshot, ["AAA"], False, context, {}, Mock())
        self.assertEqual(row, result.runtime_context["portfolioLifecycle"])
        self.assertEqual({"AAA": {"backend": "independent"}}, result.runtime_context["temporalObservationWindows"])
        saved = unpack_projection_runtime_contexts(result.runtime_context_packet)[snapshot.account_id]
        self.assertEqual(row, saved["portfolioLifecycle"])
        with self.assertRaises(ValueError):
            capture_graph_input(inputs, snapshot, ["AAA"], False, {**context, "validationPortfolioContexts": {}}, {}, Mock())
        # Live projection always supplies the source cutoff to its portfolio reader.
        instance = recorder(api, snapshot=snapshot, overrides=False)
        reader = Mock(return_value=row)
        instance.investment_domain_store = SimpleNamespace(ontology_portfolio_lifecycle_context=reader)
        instance.runtime_context(snapshot, active_tbox=TBOX, target_symbols=["AAA"])
        reader.assert_called_once_with("portfolio:" + snapshot.account_id, as_of=STAMP)

    def test_complete_subject_snapshot_retires_absent_evidence_and_preserves_other_subjects(self):
        from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
        from digital_twin.modules.reasoning.domain.ontology_scopes import apply_scoped_abox_identity, merge_target_scoped_abox_manifest
        def graph(old=False):
            nodes = [OntologyEntity("stock:" + symbol, symbol, "stock", {"ontologyBox": "ABox", "symbol": symbol}) for symbol in ("AAA", "BBB")]
            links = []
            if old:
                for symbol in ("AAA", "BBB"):
                    nodes.append(OntologyEntity("model-signal:" + symbol, symbol, "statistical-model-signal", {
                        "ontologyBox": "ABox", "symbol": symbol, "signalType": "test", "windowKey": "5d"}))
                    links.append(OntologyRelation("stock:" + symbol, "model-signal:" + symbol, "HAS_MODEL_SIGNAL",
                                                  properties={"ontologyBox": "ABox", "symbol": symbol}))
                nodes.append(OntologyEntity("statistical-model-release:old", "Historical shared release", "statisticalmodelrelease",
                                             {"ontologyBox": "ABox", "modelId": "historical"}))
            value = PortfolioOntology("fixture", entities=nodes, relations=links)
            apply_scoped_abox_identity(value, account_id="fixture")
            return value
        old, current = graph(True), graph(False)
        active = {**deepcopy(old.worldview), "status": "ok"}
        current.worldview["targetScopeRetentionMode"] = "complete-target-snapshot"
        partial = merge_target_scoped_abox_manifest(deepcopy(current), active, ["AAA"], fact_slot_plan={"enabled": False}, source_graph_complete=False)
        self.assertFalse(partial["applied"])
        self.assertEqual("complete-target-source-required", partial["fallbackReason"])
        ordinary = deepcopy(current)
        ordinary.worldview["targetScopeRetentionMode"] = "incremental-target-patch"
        result = merge_target_scoped_abox_manifest(ordinary, active, ["AAA"], fact_slot_plan={"enabled": False}, source_graph_complete=True)
        self.assertEqual([], result["retiredScopeIds"])
        protected = deepcopy(active)
        target_scope = next(item["scopeId"] for item in protected["scopePlan"] if "model-signal:AAA" in (item.get("nodeIds") or []))
        shared = next(item for item in protected["scopePlan"] if "statistical-model-release:old" in (item.get("nodeIds") or []))
        shared["dependencyScopeIds"] = [target_scope]
        result = merge_target_scoped_abox_manifest(deepcopy(current), protected, ["AAA"], fact_slot_plan={"enabled": False}, source_graph_complete=True)
        self.assertEqual("skipped-retired-scope-still-referenced", result["status"])
        result = merge_target_scoped_abox_manifest(current, active, ["AAA"], fact_slot_plan={"enabled": False}, source_graph_complete=True)
        self.assertTrue(result["applied"], result)
        self.assertTrue(result["retiredScopeIds"], result)
        self.assertTrue(all("symbol:AAA:" in key for key in result["retiredScopeIds"]))
        after = {item["scopeId"]: item for item in current.worldview["scopePlan"]}
        for item in active["scopePlan"]:
            nodes = item.get("nodeIds") or []
            if "model-signal:BBB" in nodes or "statistical-model-release:old" in nodes:
                self.assertEqual(item["generationId"], after[item["scopeId"]]["generationId"])
            if "model-signal:AAA" in nodes:
                self.assertNotIn(item["scopeId"], after)


class PortfolioCutoffReadTests(StabilizationDatabaseCase):
    def test_future_and_revised_heads_are_not_relabelled_as_past_portfolio_facts(self):
        from digital_twin.infrastructure.transactions.portfolio import MySQLInvestmentDomainStore
        store = MySQLInvestmentDomainStore(self.settings)
        key = "portfolio:cutoff-fixture"
        self.addCleanup(self.sql, "DELETE FROM portfolio_risk_snapshots WHERE portfolio_id=%s", (key,))
        self.addCleanup(self.sql, "DELETE FROM portfolio_snapshot_checkpoints WHERE portfolio_id=%s", (key,))
        for risk_id, observed, updated in (
            ("known", "2026-10-20T00:50:00Z", "2026-10-20T00:51:00Z"),
            ("future", "2026-10-20T01:10:00Z", "2026-10-20T01:11:00Z"),
            ("revised", "2026-10-20T00:59:00Z", "2026-10-20T01:11:00Z"),
        ):
            self.sql("INSERT INTO portfolio_risk_snapshots (risk_snapshot_id, portfolio_id, observed_at, payload_json, created_at, updated_at) "
                     "VALUES (%s,%s,%s,%s,%s,%s)", (risk_id, key, observed, json.dumps({"id": risk_id}), observed, updated))
        self.sql("INSERT INTO portfolio_snapshot_checkpoints (portfolio_id,account_id,account_fingerprint,observed_at,"
                 "balance_fingerprint,position_count,payload_json,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s)",
                 (key, "cutoff-fixture", "fixture", "2026-10-20T00:50:00Z", "fixture", '{"id":"revised-head"}',
                  "2026-10-20T00:50:00Z", "2026-10-20T01:11:00Z"))
        historical = store.ontology_portfolio_lifecycle_context(key, as_of=STAMP)
        self.assertEqual({"id": "known"}, historical["portfolioRiskSnapshot"])
        self.assertEqual({}, historical["snapshotCheckpoint"])
        self.assertEqual(STAMP, historical["asOf"])
        self.assertEqual({"id": "future"}, store.ontology_portfolio_lifecycle_context(key)["portfolioRiskSnapshot"])
