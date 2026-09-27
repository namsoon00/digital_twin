"""Boundary tests for research inputs, valuation measurement and cohort selection."""
import copy
import unittest
from datetime import timedelta
from types import SimpleNamespace

from test_ontology_evolution import case, pairs, START
from digital_twin.modules.model_registry.domain.ontology_evolution import create_plan, evaluate_comparison
from digital_twin.modules.model_registry.infrastructure.evolution_policy import evolution_policy
from digital_twin.modules.model_registry.domain.hypothesis_study import study_readiness, development_readiness
from digital_twin.modules.model_registry.application.hypothesis_research_context import research_context
from digital_twin.modules.outcomes.domain.hypothesis_outcome_facts import valuation_observation_facts, freeze_outcome_baseline
from digital_twin.modules.outcomes.domain.investment_assistant_quality import evaluate_investment_assistant_quality
from digital_twin.modules.model_registry.domain.hypothesis_authoring import authoring_catalog
from digital_twin.modules.model_registry.contracts import default_graph_inference_rules


def valuation(day, value=120, price=100):
    return {"bundleId": "bundle:" + str(day), "assessmentId": "assessment:" + str(day),
            "modelId": "dcf", "modelVersion": "1", "assumptionVersion": "1", "currency": "USD",
            "observedAt": (START+timedelta(days=day)).isoformat(), "knownAt": (START+timedelta(days=day)).isoformat(),
            "reproducibilityState": "reproducible", "fairValue": value, "price": price}


class ResearchPipelineTests(unittest.TestCase):
    def test_registered_numeric_subject_condition_can_be_varied(self):
        from test_hypothesis_authoring import RegisteredHypothesisAuthoringTests
        from digital_twin.modules.model_registry.domain.hypothesis_authoring import assemble_hypothesis_design
        fixture = RegisteredHypothesisAuthoringTests()
        fixture.setUpClass()
        context, candidate = fixture.context(), fixture.candidate()
        numeric = next(row for row in context["authoringContract"]["conditions"]
                       if row["condition"]["kind"] == "subject_property"
                       and type(row["condition"].get("value")) in {int, float}
                       and row["condition"].get("operator") in {">", ">=", "<", "<="})
        candidate["hypothesisDesign"]["conditionRefs"] = [{"ruleId":numeric["ruleId"], "conditionId":numeric["conditionId"],
                                                          "operator":">=", "value":1.25}]
        result = assemble_hypothesis_design(candidate, context)["proposedRule"]
        condition = result["conditions"][-1]
        self.assertEqual("subject_property", condition["kind"])
        self.assertEqual(numeric["condition"]["field"], condition["field"])
        self.assertEqual(1.25, condition["value"])
        candidate["hypothesisDesign"]["conditionRefs"][0]["value"] = float("nan")
        with self.assertRaisesRegex(ValueError, "Numeric variants"):
            assemble_hypothesis_design(candidate, context)

    def test_native_negative_receipt_requires_inputs_and_survives_compaction(self):
        from contextlib import nullcontext
        from unittest.mock import Mock
        import time
        from digital_twin.modules.reasoning.infrastructure.native_execution.selection_receipts import read_selection_receipts, selection_input_rule
        from digital_twin.modules.reasoning.application.independent_reasoning_engine import compact_projection_result
        from digital_twin.modules.decisions.contracts import rule_evaluation_records_from_projection_results
        from digital_twin.modules.reasoning.application.investment_reasoning.episode_projection import selection_provenance
        rule = {"rule_id":"candidate", "source_kind":"stock", "conditions":[
            {"kind":"subject_property", "field":"currentPrice", "operator":">", "value":100}],
            "model_input_contract":{"researchDesign":{"contract":"registered-hypothesis-design-v2", "modelRuleId":"base", "comparisonRuleId":"base"}}}
        presence = selection_input_rule(rule)
        self.assertEqual("exists", presence["conditions"][0]["operator"])
        self.assertEqual(">", rule["conditions"][0]["operator"])
        store = SimpleNamespace(database="test", read_transaction_options=lambda _: {}, read_rows_in_transaction=Mock(return_value=[{"sourceId":"stock:AAA"}]))
        driver = SimpleNamespace(transaction=lambda *args: nullcontext(object()))
        def read():
            return read_selection_receipts(store, driver, SimpleNamespace(READ="READ"), rule, ["AAA", "BBB"], [], "world", True, {}, time.monotonic()+10)
        receipts = read()
        self.assertEqual(["stock:AAA"], [row["subjectId"] for row in receipts])
        query = store.read_rows_in_transaction.call_args.args[1]
        self.assertNotIn("> 100", query)
        projection = {"accountId":"account", "sourceAboxSnapshotId":"snapshot", "inferenceGenerationId":"generation",
            "ruleboxExecution":{"nativeMatchResult":{"executedRules":[{"selectionEvaluations":receipts}]}}}
        compact = compact_projection_result(projection)
        records = rule_evaluation_records_from_projection_results({"account":compact})
        subject = SimpleNamespace(account_id="account", symbol="AAA", source_abox_snapshot_id="snapshot", inference_generation_id="generation")
        reasoning = SimpleNamespace(inference_result=SimpleNamespace(rule_evaluations=records))
        self.assertEqual("skipped", selection_provenance({"candidateRuleId":"candidate"}, reasoning, subject)["selectionState"])
        subject.symbol = "BBB"
        self.assertEqual("unknown", selection_provenance({"candidateRuleId":"candidate"}, reasoning, subject)["selectionState"])
        store.read_rows_in_transaction.return_value = []
        self.assertEqual([], read())
        store.read_rows_in_transaction.side_effect = RuntimeError("unavailable")
        self.assertEqual([], read())
        rule["conditions"][0]["role"] = "not"
        self.assertIsNone(selection_input_rule(rule))

    def selection(self):
        rule = {"rule_id": "candidate", "model_input_contract": {"researchDesign": {
            "contract": "registered-hypothesis-design-v2", "modelRuleId": "base", "comparisonRuleId": "base"}}}
        plan = create_plan(case(), rule, {"deploymentId": "base", "artifactFingerprint": "frozen", "comparisonHorizonMinutes": 60}, evolution_policy(), START.isoformat())
        rows = pairs(plan, candidate_wins=10, baseline_wins=10)
        for index, row in enumerate(rows):
            row.update(datasetFingerprint="dataset:"+str(index), inputState="ready", selectionState="selected" if index<10 else "skipped")
        return plan, rows

    def test_skipped_opportunity_round_trips_through_existing_experiment_ledger(self):
        import test_experiment_observations as fixture
        from unittest.mock import patch
        from dataclasses import replace
        from digital_twin.modules.model_registry.domain.ontology_evolution import fingerprint
        from digital_twin.modules.model_registry.infrastructure.experiment_observation_writes import capture_prediction, capture_outcome
        setup = fixture.ExperimentObservationTests()
        setup.setUp()
        try:
            setup.plan["selectionContract"] = {"version":"conditional-selection-v1", "minimumSelected":5}
            setup.plan["fingerprint"] = fingerprint({k:v for k,v in setup.plan.items() if k != "fingerprint"})
            setup.store.register(setup.plan, "candidate")
            episode = setup.episode("baseline")
            episode = replace(episode, input_provenance={**episode.input_provenance, "selectionState":"skipped"})
            capture_prediction(setup.connection, episode, fixture.NOW)
            capture_prediction(setup.connection, episode, fixture.NOW)
            capture_outcome(setup.connection, episode, setup.outcome("baseline"), "2026-09-13T01:06:00Z")
            with patch("digital_twin.modules.model_registry.infrastructure.mysql_experiment_observations.utc_now_iso", return_value="2026-09-13T01:07:00Z"):
                result = setup.store.comparison(setup.plan)
            self.assertEqual(1, len(result["pairs"]))
            self.assertTrue(result["pairs"][0]["eligible"])
            self.assertEqual("skipped", result["pairs"][0]["selectionState"])
            self.assertEqual("contradicted", result["pairs"][0]["baselineOutcome"])
            count = setup.connection.execute("SELECT COUNT(*) AS n FROM ontology_experiment_dataset_members WHERE plan_fingerprint = %s", (setup.plan["fingerprint"],)).fetchone()["n"]
            self.assertEqual(2, count)
        finally:
            setup.tearDown()

    def test_selection_captures_skips_without_claiming_trading_returns(self):
        plan, rows = self.selection()
        result = evaluate_comparison(plan, {"status":"ok", "pairs":rows}, now=(START+timedelta(days=30)).isoformat())
        self.assertEqual("qualified", result["status"])
        self.assertEqual(10, result["avoidedAdverseCount"])
        self.assertEqual(0, result["missedOpportunityCount"])
        self.assertIsNone(result["financialReturn"])
        self.assertFalse(result["automaticDeployment"])

    def test_selection_missing_first_receipt_is_not_replaced_by_later_winner(self):
        plan, rows = self.selection()
        rows[0]["selectionState"] = "unknown"
        later = {**rows[1], "id":"later", "independenceKey":"later", "observedFromAt":(START+timedelta(days=21)).isoformat(), "observedAt":(START+timedelta(days=21, hours=1)).isoformat()}
        result = evaluate_comparison(plan, {"status":"ok", "pairs":rows+[later]}, now=(START+timedelta(days=30)).isoformat())
        self.assertEqual("needs-data", result["status"])
        self.assertEqual(20, result["frozenOpportunityCount"])
        self.assertEqual(19, result["independentPairCount"])
        self.assertNotIn("later", result["evidenceIds"])

    def test_identical_predictions_without_selection_gain_do_not_win(self):
        plan, rows = self.selection()
        for row in rows:
            row["selectionState"] = "selected"
        result = evaluate_comparison(plan, {"status":"ok", "pairs":rows}, now=(START+timedelta(days=30)).isoformat())
        self.assertEqual("not-better", result["status"])
        self.assertEqual(1, result["pairedTestPValue"])

    def test_delayed_first_outcome_reserves_actual_interval(self):
        plan, rows = self.selection()
        rows[0]["observedAt"] = rows[1]["observedAt"]
        rows[0]["eligible"] = False
        result = evaluate_comparison(plan, {"status":"ok", "pairs":rows}, now=(START+timedelta(days=30)).isoformat())
        self.assertEqual("needs-data", result["status"])
        self.assertNotIn(rows[1]["id"], result["evidenceIds"])
        self.assertTrue(any(item["id"] == rows[1]["id"] and item["reason"] == "overlapping-or-duplicate-opportunity" for item in result["exclusions"]))

    def test_missed_opportunities_count_against_filter(self):
        plan, rows = self.selection()
        for row in rows[10:]:
            row["baselineOutcome"] = "corroborated"
        result = evaluate_comparison(plan, {"status":"ok", "pairs":rows}, now=(START+timedelta(days=30)).isoformat())
        self.assertEqual(10, result["missedOpportunityCount"])
        self.assertLess(result["improvement"], 0)

    def test_valuation_separates_price_from_value_change(self):
        result = valuation_observation_facts(valuation(0), valuation(7, value=150, price=110))
        self.assertEqual("measured", result["valuationMeasurementState"])
        self.assertGreater(result["valuationPriceContributionPp"], 0)
        self.assertLess(result["valuationGapReductionPp"], 0)
        self.assertAlmostEqual(result["valuationGapReductionPp"], result["valuationPriceContributionPp"]+result["valuationValueContributionPp"])

    def test_valuation_contract_rejects_price_rise_when_value_gap_widens(self):
        from digital_twin.modules.outcomes.domain.hypothesis_outcome_evaluation import evaluate_hypothesis_outcome
        facts = valuation_observation_facts(valuation(0), valuation(7, value=150, price=110))
        contract = {"outcomeHorizonMinutes":[10080], "requiredObservationDomains":["valuation"], "criteria":[
            {"criterionId":"gap", "role":"cause", "metric":"valuationGapReductionPp", "operator":">", "threshold":0, "required":True},
            {"criterionId":"price", "role":"result", "metric":"instrumentReturnPct", "operator":">", "threshold":0, "required":True}]}
        result = evaluate_hypothesis_outcome(contract, "support", facts, 10, 10080)
        self.assertEqual("directionally-contradicted", result["selectedHypothesisStatus"])
        self.assertEqual("not-established", result["causalAttribution"])

    def test_comparison_receipts_reject_future_reports_and_revision_tampering(self):
        import json
        from digital_twin.modules.model_registry.infrastructure.qualification_reads import qualification_receipts
        from digital_twin.modules.outcomes.contracts import claim_validation_fingerprint
        from digital_twin.modules.model_registry.domain.ontology_evolution import fingerprint
        plan, rows = self.selection()
        rule = next(row.to_dict() for row in default_graph_inference_rules() if row.resolved_claim_contract.is_predictive)
        plan["candidateRule"]["claim_contract"] = rule["claim_contract"]
        plan["baseline"]["candidateClaim"] = {"validationFingerprint":claim_validation_fingerprint(rule["claim_contract"])}
        plan["fingerprint"] = fingerprint({key:value for key,value in plan.items() if key != "fingerprint"})
        for row in rows: row["candidateFingerprint"] = plan["fingerprint"]
        report = evaluate_comparison(plan, {"status":"ok", "pairs":rows}, now=(START+timedelta(days=30)).isoformat())
        payload = {"evolution":{"plan":plan, "assessment":report}}
        connection = SimpleNamespace(execute=lambda *args:SimpleNamespace(fetchall=lambda:[{"payload_json":json.dumps(payload)}]))
        self.assertFalse(qualification_receipts(connection, {("test","TEST")}, START.isoformat()))
        receipts = qualification_receipts(connection, {("test","TEST")}, (START+timedelta(days=31)).isoformat())
        self.assertEqual(1, len(receipts))
        payload["evolution"]["plan"]["candidateRule"]["rule_id"] = "changed"
        self.assertFalse(qualification_receipts(connection, {("test","TEST")}, (START+timedelta(days=31)).isoformat()))

    def test_valuation_missing_changed_unit_and_future_known_data_are_not_results(self):
        for after in ({}, {**valuation(7), "currency":"KRW"}, {**valuation(7), "knownAt":valuation(8)["knownAt"]}, {**valuation(7), "reproducibilityState":"partial"}):
            with self.subTest(after=after):
                self.assertNotIn("valuationGapReductionPp", valuation_observation_facts(valuation(0), after))

    def test_long_study_is_not_squeezed_into_short_auto_experiment(self):
        rule = {"claim_contract":{"predictionTarget":"business-continuity", "outcomeContract":{"outcomeHorizonMinutes":[129600,259200], "criteria":[{"role":"cause", "metric":"revenueGrowthPct"}]}}}
        report = study_readiness(rule, evolution_policy())
        self.assertEqual("fundamental-continuity", report["family"])
        self.assertEqual(1800, report["minimumAcquisitionDays"])
        self.assertEqual("blocked", report["state"])
        self.assertTrue(report["minimumRetentionDays"] > 3600)

    def test_full_registered_catalog_is_discoverable(self):
        rules = [rule.to_dict() for rule in default_graph_inference_rules()]
        catalog = authoring_catalog({"ruleBox":{"rules":rules}})
        self.assertGreater(len(catalog["baselines"]), 16)
        self.assertGreater(len(catalog["conditions"]), 32)
        self.assertEqual(0, catalog["coverage"]["omittedModels"])

    def test_unlabelled_quality_metrics_are_unmeasured_not_failures(self):
        result = evaluate_investment_assistant_quality([{"episodeId":"e", "sourceTraceComplete":True}], minimum_independent_episodes=1,
            maximum_unsupported_claim_rate=0, maximum_duplicate_delivery_rate=0, minimum_reproducibility_rate=1)
        self.assertEqual(1, result["metrics"]["sourceTraceRate"])
        self.assertIsNone(result["metrics"]["calculationReproducibilityRate"])
        self.assertIsNone(result["metrics"]["unsupportedClaimRate"])

    def research_repository(self):
        return SimpleNamespace(inferencebox_recovery_metadata=lambda **kw:{"status":"ok", "sourceAboxSnapshotId":"a", "inferenceGenerationId":"g", "targetSymbols":["TEST"]},
            active_abox_snapshot_id=lambda **kw:"a", active_tbox_metadata=lambda:{"fingerprint":"t"},
            read_entity_rows_by_ids=lambda ids, **kw:[{"id":"e", "properties":{"symbol":"TEST", "accountId":"account", "sourceUrl":"https://example.test/source", "statement":"counter"}}])

    def test_current_graph_research_preserves_counter_source_and_missing_state(self):
        result = research_context(self.research_repository(), {"counterEvidenceIds":["e"], "supportingEvidenceIds":["missing"]}, account_id="account", symbol="TEST", world_id="world")
        self.assertEqual("partial", result["status"])
        self.assertEqual("observed", result["counterEvidenceState"])
        self.assertEqual(["missing"], result["missingEvidenceIds"])
        self.assertEqual("current-research-not-historical-replay", result["purpose"])

    def test_graph_snapshot_change_discards_facts(self):
        repository = self.research_repository()
        stamps = iter(["a", "b"])
        repository.active_abox_snapshot_id = lambda **kw:next(stamps)
        result = research_context(repository, {"counterEvidenceIds":["e"]}, account_id="account", symbol="TEST", world_id="world")
        self.assertEqual("not-aligned", result["status"])
        self.assertEqual([], result["counterEvidence"])

    def test_cross_account_facts_are_rejected(self):
        result = research_context(self.research_repository(), {"supportingEvidenceIds":["e"]}, account_id="other", symbol="TEST", world_id="world")
        self.assertEqual("query-failed", result["status"])
        self.assertEqual([], result["facts"])

if __name__ == "__main__":
    unittest.main()
