"""Regression contracts for the September evidence-flow audit (synthetic data)."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import contextmanager

from digital_twin.modules.decisions.domain.notification_ai_context_router import (
    _minimum_research_review_core, _minimum_transition_detail, _relation_facts, _external_evidence,
)
from digital_twin.modules.decisions.domain.decision_evidence_contract import temporal_evidence_summary
from digital_twin.modules.market_data.domain.crypto_market_signals import crypto_transition_targets
from digital_twin.modules.notifications.application.typedb_observation_message import reasoning_trigger_rows
from digital_twin.modules.notifications.domain.notification_narrative import (
    build_decision_core_evidence_ledger, normalize_narrative_claims,
)
from digital_twin.modules.reasoning.domain.portfolio_ontology_statistical_concepts import _feature_summary
from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_inference_materializer import grounded_inference_context
from digital_twin.modules.decisions.application.notification_decision_memory import refresh_insight_delivery_comparison
from digital_twin.modules.notifications.application.notification.workflow import NotificationQueueRunner
from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
from digital_twin.modules.notifications.domain.notifications import NotificationJob


def trigger():
    return {
        "status": "verified-material-transition", "material": True, "userObservable": True,
        "kinds": ["verified-material-source"], "changedFields": ["current_price"],
        "facts": {"bidAskImbalanceThreshold": 25, "confirmedSignalTransitions": [{
            "signalId": "price", "condition": "price-move", "observedValue": 1.36,
            "fromState": "anchored", "toState": "positive", "confirmationCount": 2,
            "requiredConfirmations": 2, "baselineValue": 100, "currentValue": 101.36,
            "baselineAt": "2026-09-20T00:00:00Z", "observedAt": "2026-09-20T00:10:00Z",
        }]},
    }


def research_core():
    facts = {"currentPrice": 101.36, "ma5Distance": 2, "ma20Distance": 3,
             "ma60Distance": 4, "btcPrice": 80000, "btcChange24h": 2.5,
             "btcChange7d": 5, "volumeRatio": 0.2, "currency": "USD"}
    ledger = build_decision_core_evidence_ledger(facts=facts, rules=[], hypotheses=[])
    ledger.insert(0, {"evidenceId": "transition:test", "kind": "decision-transition",
                      "role": "context", "value": trigger()})
    return {"subject": {"symbol": "TEST"}, "reasoningTrigger": trigger(), "facts": facts,
            "hypothesisSet": {"hypotheses": []}, "evidenceLedger": ledger}


class InsightEvidenceFlowTests(unittest.TestCase):
    def test_trigger_compaction_preserves_typed_values_and_baseline(self):
        source = trigger()
        snapshot = copy.deepcopy(source)
        compact = _minimum_transition_detail(source)
        self.assertEqual(source["facts"]["confirmedSignalTransitions"], compact["facts"]["confirmedSignalTransitions"])
        self.assertEqual(snapshot, source)

    def test_research_compaction_preserves_trigger_and_market_citations(self):
        core = _minimum_research_review_core(research_core())
        transition = next(row for row in core["evidenceLedger"] if row["kind"] == "decision-transition")
        self.assertEqual(1.36, transition["value"]["facts"]["confirmedSignalTransitions"][0]["observedValue"])
        ids = {row["evidenceId"] for row in core["evidenceLedger"]}
        for field in ("ma5Distance", "ma60Distance", "btcPrice", "btcChange24h", "volumeRatio"):
            self.assertIn("fact:" + field, ids)

    def test_fact_selection_does_not_drop_late_core_fields(self):
        facts = {"btcPrice": 80000, "btcChange24h": 2.5, "btcChange7d": 5}
        self.assertEqual(facts, _relation_facts({"relationFacts": facts}, [], []))

    def test_nested_features_are_read_without_inventing_missing_metrics(self):
        self.assertEqual({"priceReturn": 0.05}, _feature_summary({"inputFeatures": {"familyInputFeatures": {"priceReturn": 0.05}}}))
        self.assertEqual({}, _feature_summary({"inputFeatures": {"contractMatched": True}}))

    def test_btc_exposure_does_not_imply_eth_exposure(self):
        position = SimpleNamespace(symbol="MSTR", name="Strategy", sector="", is_cash=lambda: False)
        self.assertEqual(["ETH"], crypto_transition_targets([{"symbol": "ETH"}], [position]))
        self.assertEqual(["BTC", "MSTR"], crypto_transition_targets([{"symbol": "BTC"}], [position]))

    def test_source_admission_preserves_analysis_and_prioritizes_official_evidence(self):
        news = {"kind": "news", "symbol": "TEST", "publishedAt": "2026-09-20T00:00:00Z",
            "investmentJudgmentEligible": True, "alertEligible": True, "reasoningEligible": True,
            "decisionInlineEligible": True, "validationState": "verified", "dataState": "sufficient",
            "articleSummaryQuality": {"state": "ready"},
            "aiAnalysis": {"version": "fixture-v1", "status": "ok", "sourceTextHash": "fixture-hash"}}
        disclosure = {"evidenceId": "official", "symbol": "TEST", "kind": "disclosure", "reportName": "공식 실적 발표",
            "receiptDate": "2026-09-20", "documentHash": "fixture-hash", "officialDocumentState": "document-verified",
            "documentVerified": True, "analysisReady": True, "investmentJudgmentEligible": True,
            "validationState": "verified", "dataState": "sufficient"}
        brief = {"subject": {"symbol": "TEST", "referenceDate": "2026-09-20T01:00:00Z"}, "evidence": {
            "researchEvidence": [{**news, "evidenceId": str(i), "title": "검증 기사"} for i in range(4)] +
                [{**news, "evidenceId": "wrong", "symbol": "OTHER"}], "disclosure": disclosure}}
        rows, audit = _external_evidence(brief, [], [])
        self.assertEqual("official", rows[0]["evidenceId"])
        self.assertEqual(3, len(rows))
        self.assertEqual(2, audit["omittedForBudgetCount"])
        self.assertEqual(1, audit["reasonCounts"]["different-subject"])
        self.assertNotIn("aiAnalysis", rows[1])
        self.assertTrue(rows[1]["promptAdmission"]["promptEligible"])

    def test_internal_followup_code_is_not_a_customer_reason(self):
        rows = reasoning_trigger_rows({"reasoningDeliveryTrigger": {
            "material": True, "reasons": ["verified-observation-followup"], "facts": {},
        }})
        self.assertNotIn("verified-observation-followup", " ".join(rows))
        self.assertNotIn("새로", " ".join(rows))

    def test_condition_only_model_signal_cannot_verify_event_absorption(self):
        core = {"evidenceLedger": [{"evidenceId": "model:test", "kind": "model-signal",
            "role": "support", "judgementEligible": True, "value": {"strengthBand": "strong"},
            "featureSummary": {}}]}
        claims, result = normalize_narrative_claims({"_notificationAiPreparedDecisionCore": core},
            {"narrativeClaims": [{"claimId": "mechanism", "section": "mechanism",
             "text": "검증된 사건 반응 신호가 위험 이벤트 뒤 충격 흡수를 가리킵니다.",
             "evidenceIds": ["model:test"]}]}, writer_kind="ai")
        self.assertEqual([], claims)
        self.assertNotEqual("verified", result["status"])

    def test_model_window_reference_is_reported_without_substituting_unbound_history(self):
        relation = {"symbol": "TEST", "graphStoreInference": {"traces": [{"id": "trace:test", "ruleId": "test.rule",
            "matchedConditions": [{"conditionId": "model", "relationType": "HAS_MODEL_SIGNAL",
                "matchedTargetProperties": {"symbol": "TEST", "sourceFeatureSnapshotId": "snapshot:frozen",
                    "modelEvidenceIds": ["stock:TEST|HAS_TEMPORAL_WINDOW|temporal-window:TEST:1D"]}}]}]}}
        summary = temporal_evidence_summary([{"windowKey": "1D", "priceChangePct": 3}], relation)
        self.assertEqual(0, summary["matchedWindowCount"])
        self.assertEqual(["1D"], summary["referencedWindowKeys"])
        self.assertTrue(summary["unresolvedModelWindowReferences"])

    def test_model_window_requires_same_symbol_snapshot_and_knowledge_cutoff(self):
        evidence_id = "stock:TEST|HAS_TEMPORAL_WINDOW|temporal-window:TEST:1D"
        captured = {"evidenceId": evidence_id, "windowKey": "1D", "symbol": "TEST",
            "sourceFeatureSnapshotId": "frozen", "knowledgeCutoffAt": "2026-09-20T00:00:00Z", "priceChangePct": 2}
        target = {"sourceFeatureSnapshotId": "frozen", "knowledgeCutoffAt": "2026-09-20T00:00:00Z",
            "sourceTemporalWindows": [captured], "modelEvidenceIds": [evidence_id],
            "measurementBasis": "condition-coverage", "empiricalSampleCount": 0}
        stock = OntologyEntity("stock:TEST", "TEST", "stock")
        model = OntologyEntity("model:TEST", "model", "statistical-model-hypothesis-evidence", target)
        graph = PortfolioOntology("test", entities=[stock, model], relations=[
            OntologyRelation(stock.entity_id, model.entity_id, "HAS_MODEL_SIGNAL", properties={"_relationId": "model-relation"})])
        rule = SimpleNamespace(conditions=[SimpleNamespace(condition_id="model", kind="relation", relation_type="HAS_MODEL_SIGNAL")])
        proof = grounded_inference_context(graph, rule, stock, {
            "evidenceRelationIds": ["model-relation"], "matchedConditions": [{"conditionId": "model"}]})
        target = proof["matchedConditions"][0]["matchedTargetProperties"]
        self.assertEqual([captured], target["sourceTemporalWindows"])
        self.assertEqual(0, target["empiricalSampleCount"])
        self.assertEqual("condition-coverage", target["measurementBasis"])
        relation = {"subject": {"symbol": "TEST"}, "graphStoreInference": {"traces": [{"ruleId": "r", "id": "t",
            "matchedConditions": [{"relationType": "HAS_MODEL_SIGNAL", "matchedTargetProperties": target}]}]}}
        self.assertEqual(1, temporal_evidence_summary([], relation)["matchedWindowCount"])
        for field, value in [("symbol", "OTHER"), ("sourceFeatureSnapshotId", "newer"), ("knowledgeCutoffAt", "2026-09-21T00:00:00Z")]:
            with self.subTest(field=field):
                changed = copy.deepcopy(relation)
                changed["graphStoreInference"]["traces"][0]["matchedConditions"][0]["matchedTargetProperties"]["sourceTemporalWindows"][0][field] = value
                self.assertEqual(0, temporal_evidence_summary([], changed)["matchedWindowCount"])

    def test_same_evidence_can_have_opposite_roles_for_different_hypotheses(self):
        core = {"evidenceLedger": [{"evidenceId": "e", "kind": "fact", "role": "context", "value": 2,
            "judgementEligible": True, "hypothesisRoles": {"growth": "support", "risk": "counter"}}]}
        for hypothesis, section in (("growth", "support"), ("risk", "counter")):
            accepted, _ = normalize_narrative_claims({"_notificationAiPreparedDecisionCore": core},
                {"narrativeClaims": [{"hypothesisId": hypothesis, "section": section, "text": "관측값은 2입니다.", "evidenceIds": ["e"]}]}, writer_kind="ai")
            self.assertEqual(1, len(accepted))
        rejected, _ = normalize_narrative_claims({"_notificationAiPreparedDecisionCore": core},
            {"narrativeClaims": [{"hypothesisId": "invented", "section": "support", "text": "관측값은 2입니다.", "evidenceIds": ["e"]}]}, writer_kind="ai")
        self.assertEqual([], rejected)
        core["evidenceLedger"][0]["role"] = "support"
        rejected, validation = normalize_narrative_claims({"_notificationAiPreparedDecisionCore": core},
            {"narrativeClaims": [{"hypothesisId": "invented", "section": "support", "text": "관측값은 2입니다.", "evidenceIds": ["e"]}]}, writer_kind="ai")
        self.assertEqual([], rejected)
        self.assertIn("unknown-hypothesis-id", validation["validations"][0]["reasons"])

    def test_dispatch_refreshes_successful_delivery_without_rewriting_analysis_baseline(self):
        assessment = {"publishable": True, "direction": "positive", "horizon": "short-term", "conviction": "moderate", "thesisKey": "recovery"}
        store = Mock()
        store.latest_delivered_insight_episodes.return_value = [{"episodeId": "new-delivered", "accountId": "test", "symbol": "TEST",
            "insightAssessment": assessment, "notificationDelivery": {"delivered": True, "deliveredAt": "2026-09-20T00:03:00Z"}}]
        context = {"accountId": "test", "rawSymbol": "TEST", "investmentInsightDeliveryHistory": {
            "status": "not-found", "accountId": "test", "symbol": "TEST"}, "notificationAiValidatedResponse": {"insightAssessment": assessment}}
        fresh = refresh_insight_delivery_comparison(context, store)
        self.assertEqual("unchanged-insight", fresh["investmentInsightTransition"]["kind"])
        self.assertTrue(fresh["deliveryBaselineRefresh"]["changed"])
        self.assertEqual("not-found", context["investmentInsightDeliveryHistory"]["status"])
        for observed_at, expected in [("2026-09-20T00:02:00Z", False), ("2026-09-20T00:04:00Z", True)]:
            context["reasoningDeliveryTrigger"] = {**trigger(), "observedAt": observed_at}
            refreshed = refresh_insight_delivery_comparison(context, store)
            self.assertEqual(expected, refreshed["deliveryBaselineRefresh"]["newerObservedSource"])
        store.latest_delivered_insight_episodes.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            refresh_insight_delivery_comparison(context, store)

    def test_delivery_race_suppresses_repeated_observation_not_new_source_or_action(self):
        for action, newer_source, allowed in [("NO_ACTION", False, False), ("NO_ACTION", True, True), ("SELL", False, True)]:
            with self.subTest(action=action, newer_source=newer_source):
                runner = NotificationQueueRunner(Mock(), Mock(), Mock())
                job = NotificationJob.create("fixture", account_id="test", message_type="investmentInsight", context={
                    "notificationAiValidatedResponse": {"action": action},
                    "deliveryBaselineRefresh": {"changed": True, "newerObservedSource": newer_source},
                    "investmentInsightTransition": {"kind": "unchanged-insight"}})
                with patch("digital_twin.modules.notifications.application.notification.workflow.reconciled_ai_delivery_decision", return_value={"decision": "send"}):
                    self.assertEqual(allowed, runner.apply_final_ai_delivery_gate(job))

    def test_busy_subject_never_sends_and_lock_is_released(self):
        queue = Mock()
        released = []
        @contextmanager
        def busy(*args):
            try:
                yield False
            finally:
                released.append(True)
        queue.delivery_subject_lock = busy
        job = NotificationJob.create("test", account_id="test", message_type="investmentInsight", context={
            "rawSymbol": "TEST", "notificationAiValidatedResponse": {"action": "NO_ACTION"}})
        runner = NotificationQueueRunner(queue, Mock(), Mock(), delivery_comparison_refresher=Mock())
        self.assertFalse(runner.prepare_delivery_comparison(job))
        runner.delivery_scope.close()
        self.assertEqual([True], released)
        queue.mark_failed.assert_called_once()

    def test_receipt_keeps_actual_message_not_a_regenerated_template(self):
        queue, transport = Mock(), Mock()
        transport.send.return_value = SimpleNamespace(delivered=True, metadata={}, label="test", reason="")
        job = NotificationJob.create("old template", account_id="test")
        NotificationDispatchService(queue, lambda account: transport).deliver(job, {"test": object()}, "actual message")
        receipt = queue.complete_delivery_attempt.call_args.kwargs["metadata"]
        self.assertEqual("actual message", receipt["renderedMessage"])
        self.assertEqual(64, len(receipt["messageSha256"]))


if __name__ == "__main__":
    unittest.main()
