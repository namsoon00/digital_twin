import json
import unittest

from digital_twin.modules.news_intelligence.application.financial_evidence_repair_service import (
    financial_input_requires_revalidation, financial_repair_plan,
)
from digital_twin.modules.outcomes.infrastructure.financial_input_correction import quarantine_financial_input
from digital_twin.modules.market_data.application.external_data.read_model_service import ExternalSignalsReadModelService
from digital_twin.infrastructure.financial_evidence_maintenance import retire_legacy_financial_requests
from unittest.mock import Mock


class FinancialEvidenceRepairTests(unittest.TestCase):
    def test_pending_legacy_financial_ai_is_superseded_through_queue_owner(self):
        def request(identifier, rule, version=None):
            company = {"financialEvidence": {"version": version}} if version else {"latestFinancials": {"quarterly": [{"period": "2026-03-31"}]}}
            return {"request_id": identifier, "context_json": json.dumps({"ontologyRelationContext": {
                "facts": {"companyContext": company}, "activeRules": [{"rule_id": rule}]}})}
        connection, queue = Mock(), Mock()
        connection.execute.return_value.fetchall.return_value = [
            request("legacy", "graph.company.capital.dilution.risk.v1"),
            request("price", "graph.price.recovery.v1"),
            request("corrected", "graph.company.capital.dilution.risk.v1", "financial-reporting-v2"),
        ]
        self.assertEqual(["legacy"], retire_legacy_financial_requests(connection, queue, "repair", "now"))
        queue.supersede_request_with_connection.assert_not_called()
        self.assertEqual(["legacy"], retire_legacy_financial_requests(connection, queue, "repair", "now", apply=True))
        self.assertIn("FOR UPDATE", connection.execute.call_args.args[0])
        queue.supersede_request_with_connection.assert_called_once()
        self.assertEqual("legacy", queue.supersede_request_with_connection.call_args.args[1]["request_id"])

    def test_legacy_financial_case_needs_revalidation_but_price_only_does_not(self):
        company = {"latestFinancials": {"quarterly": [{"period": "2026-03-31", "operatingIncomeGrowthPct": -10}]}}
        self.assertTrue(financial_input_requires_revalidation(company, [{"ruleId": "graph.company.capital.dilution.risk.v1"}]))
        self.assertFalse(financial_input_requires_revalidation(company, [{"ruleId": "graph.price.recovery.v1"}]))

    def test_repair_is_idempotent_and_does_not_write_history(self):
        source = {"yfinanceData": {"TEST": {"quarterlyIncomeStatement": [{"metric": "Operating Income", "values": {
            "2026-06-30": 130, "2026-03-31": 100}}]}}}
        first, changes = financial_repair_plan({}, source)
        second, repeated = financial_repair_plan(first, source)
        self.assertTrue(changes)
        self.assertEqual(first, second)
        self.assertEqual([], repeated)

    def test_older_document_cannot_replace_current_official_statements(self):
        class Store:
            def list_current(self, _symbols):
                return [
                    {"datasetId": "opendart.company_facts", "payload": {"dartDisclosures": {"TEST": {
                        "financialStatementBasis": {"businessYear": "2026"}, "financialStatements": ["new"]}}}},
                    {"datasetId": "opendart.document", "payload": {"dartDisclosures": {"TEST": {
                        "financialStatementBasis": {"businessYear": "2025"}, "financialStatements": ["old"], "body": "old document"}}}},
                ]

            def provider_statuses(self):
                return []
        result = ExternalSignalsReadModelService(Store()).signals_for_subjects(["TEST"])["dartDisclosures"]["TEST"]
        self.assertEqual(["new"], result["financialStatements"])
        self.assertEqual("2026", result["financialStatementBasis"]["businessYear"])
        self.assertEqual("old document", result["body"])

    def test_quarantine_preview_does_not_write_and_apply_preserves_original_outcome(self):
        class Result:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class Connection:
            writes = []

            def execute(self, sql, params):
                if sql.startswith("SELECT"):
                    if "decision_follow_ups" in sql:
                        return Result([{"condition_id": "watch", "payload_json": '{"status":"pending"}'}])
                    if "observation_episodes" in sql:
                        return Result([{"episode_id": "ep", "payload_json": json.dumps({"outcomeContract": {"sourceRuleIds": ["graph.company.capital.dilution.risk.v1"]}})}])
                    if "observation_targets" in sql:
                        return Result([{"target_id": "target", "payload_json": '{"status":"pending"}'}])
                    return Result([{"outcome_id": "outcome", "payload_json": json.dumps({"selectedHypothesisStatus": "directionally-corroborated", "payload": {"calibrationEligibility": "eligible"}})}])
                self.writes.append((sql, params))
                return Result([])
        connection = Connection()
        case, correction = {"subject_case_id": "case", "candidate_set_id": "set"}, {"correctedAt": "2026-09-16T00:00:00Z", "reason": "source-revalidation"}
        preview = quarantine_financial_input(connection, case, correction)
        self.assertEqual(1, preview["outcomesExcluded"])
        self.assertEqual([], connection.writes)
        quarantine_financial_input(connection, case, correction, apply=True)
        self.assertEqual(3, len(connection.writes))
        outcome = json.loads(connection.writes[-1][1][0])
        self.assertEqual("directionally-corroborated", outcome["selectedHypothesisStatus"])
        self.assertEqual("eligible", outcome["payload"]["inputCorrection"]["previousCalibrationEligibility"])
        self.assertEqual("excluded-financial-input-revalidation", outcome["payload"]["calibrationEligibility"])
