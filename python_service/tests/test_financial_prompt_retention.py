import copy
import json
import unittest
from unittest.mock import patch

from digital_twin.modules.decisions.domain.notification_ai_context_router import (
    fit_notification_ai_decision_core,
)
from digital_twin.modules.decisions.domain.notification_ai_decision_brief import (
    build_notification_ai_prompt_bundle,
)
from digital_twin.modules.notifications.domain.notification_narrative import (
    build_decision_core_evidence_ledger,
    resolved_narrative_claim_evidence_contract,
)


def financial_company():
    return {
        "symbol": "TEST",
        "financialIntegrity": {"status": "checked", "issues": []},
        "financialEvidence": {
            "version": "financial-reporting-v2",
            "fingerprint": "test-financial-packet",
            "period": "2026-06-30",
            "eventSemantics": "reporting-period-evidence-not-new-filing",
            "comparisons": [
                {
                    "metric": "revenue", "growthMetric": "revenueGrowthPct",
                    "currentValue": 120, "previousValue": 100, "changePct": 20,
                    "currentPeriod": "2026-06-30", "previousPeriod": "2025-06-30",
                    "comparisonBasis": "year-over-year", "durationBasis": "quarterly",
                    "provider": "OpenDART", "currency": "KRW", "scope": "CFS",
                    "sourceUrl": "https://example.test/filing", "receiptNo": "test-receipt",
                    "status": "verified-comparable",
                },
                {
                    "metric": "sharesOutstanding", "status": "excluded",
                    "reason": "share-count-discontinuity-unverified",
                    "currentValue": 160, "previousValue": 100,
                },
            ],
            "ratios": [{
                "metric": "freeCashFlowMarginPct", "value": 25,
                "numerator": {"metric": "freeCashFlow", "value": 50},
                "denominator": {"metric": "revenue", "value": 200},
                "formula": "freeCashFlow / revenue * 100",
                "period": "2026-06-30", "durationBasis": "quarterly",
                "provider": "yfinance", "currency": "KRW", "official": False,
                "status": "verified-comparable",
            }],
            "issues": [{"metric": "sharesOutstanding", "reason": "unverified-comparison"}],
        },
    }


def research_core():
    company = financial_company()
    ledger = build_decision_core_evidence_ledger(
        facts={"currentPrice": 150, "ma20Distance": 1.2, **{"other" + str(i): i for i in range(12)}},
        rules=[], hypotheses=[], company_evidence=company,
    )
    ledger.insert(0, {
        "evidenceId": "assertion:financial", "kind": "ontology-assertion",
        "source": "TypeDB", "judgementEligible": True, "role": "support",
        "value": "confirmed", "label": "Financial confirmation",
    })
    return {
        "reviewMode": "context-narrative", "notificationIntent": "context-observation",
        "subject": {"symbol": "TEST"},
        "decision": {"actionEnvelope": {"executionAction": "NO_ACTION", "allowedActions": []}},
        "facts": {"currentPrice": 150, "ma20Distance": 1.2},
        "companyEvidence": company,
        "hypothesisSet": {"comparisonMode": "research-only", "hypotheses": [{
            "hypothesisId": "hypothesis:test", "researchOnly": True,
            "supportingRuleIds": ["graph.company.market.fundamental_confirmation.support.v1"],
            "supportingEvidenceIds": ["assertion:financial"], "counterEvidenceIds": [],
        }]},
        "evidenceLedger": ledger,
        "background": {"audit": "x" * 100000},
    }


class FinancialPromptRetentionTests(unittest.TestCase):
    def assert_financial_packet(self, core):
        self.assertEqual(financial_company()["financialEvidence"], core["companyEvidence"]["financialEvidence"])
        rows = {r["evidenceId"]: r for r in core["evidenceLedger"]}
        self.assertEqual(120, rows["financial:revenue:current"]["value"])
        self.assertEqual("2025-06-30", rows["financial:revenue:previous"]["sourceAsOf"])
        self.assertEqual("OpenDART", rows["financial:revenue:change"]["source"])
        self.assertEqual(25, rows["financial:freeCashFlowMarginPct:ratio"]["value"])
        self.assertFalse(any(key.startswith("financial:sharesOutstanding:") for key in rows))
        contract = resolved_narrative_claim_evidence_contract(core.get("narrativeClaimContract"), rows.values())
        self.assertIn("financial:revenue:current", json.dumps(contract))
        self.assertIn("financial:freeCashFlowMarginPct:ratio", json.dumps(contract))

    def test_research_only_compression_preserves_comparisons_ratios_and_citations(self):
        original = research_core()
        snapshot = copy.deepcopy(original)
        fitted = fit_notification_ai_decision_core(original, 16384)
        self.assertEqual("minimum-research-review-contract", fitted["routingAudit"]["status"])
        self.assert_financial_packet(fitted)
        self.assertEqual(original, snapshot)
        self.assertEqual([], fitted["decision"]["actionEnvelope"].get("allowedActions", []))
        self.assertEqual("NO_ACTION", fitted["decision"]["actionEnvelope"]["executionAction"])

    def test_action_and_emergency_compression_preserve_the_same_packet(self):
        core = research_core()
        core["reviewMode"] = "decision"
        core["notificationIntent"] = "investment-decision"
        core["hypothesisSet"]["comparisonMode"] = "decision"
        core["companyEvidence"]["latestFinancials"] = {"audit": "x" * 100000}
        core["temporalEvidence"] = {"windows": [{"audit": "x" * 50000}]}
        fitted = fit_notification_ai_decision_core(core, 16384)
        self.assertEqual("minimum-decision-contract", fitted["routingAudit"]["status"])
        self.assert_financial_packet(fitted)

    def test_crowded_ledger_does_not_drop_financial_evidence_before_compression(self):
        rows = build_decision_core_evidence_ledger(
            facts={"field" + str(i): i for i in range(100)},
            rules=[], hypotheses=[], company_evidence=financial_company(),
        )
        self.assertLessEqual(len(rows), 64)
        self.assertEqual(4, sum(r["evidenceId"].startswith("financial:") for r in rows))

    def test_final_rendered_prompt_contains_the_packet_not_only_the_instruction(self):
        brief = {
            "reviewMode": "context-narrative", "notificationIntent": "context-observation",
            "subject": {"symbol": "TEST"},
            "currentSituation": {"companyContext": financial_company(), "relationFacts": {"ma20Distance": 1.2}},
            "decisionState": {"actionEnvelope": {"executionAction": "NO_ACTION", "allowedActions": []}},
            "inference": {
                "activeRules": [{"ruleId": "graph.company.market.fundamental_confirmation.support.v1"}],
                "hypothesisSet": research_core()["hypothesisSet"],
            },
        }
        brief["currentSituation"]["companyContext"]["financialIntegrity"]["audit"] = "x" * 100000
        # A large, source-bound event exercises the actual production fitter.
        brief["currentSituation"]["reasoningDeliveryTrigger"] = {"status": "verified", "audit": "x" * 100000}
        for budget in (32768, 49152):
            with self.subTest(budget=budget):
                bundle = build_notification_ai_prompt_bundle({}, decision_brief=brief, max_prompt_bytes=budget)
                rendered = json.loads(bundle["prompt"].split("DecisionCore:\n", 1)[1])
                self.assert_financial_packet(rendered)
                self.assertEqual(bundle["decisionCore"], rendered)
                self.assertLessEqual(len(bundle["prompt"].encode()), budget)

    def test_insufficient_budget_does_not_turn_present_financials_into_missing_data(self):
        with self.assertRaises(ValueError):
            fit_notification_ai_decision_core(research_core(), 1000)

    def test_retention_check_rejects_future_compactor_regressions(self):
        for mutation in ("packet", "citation", "source", "value"):
            with self.subTest(mutation=mutation):
                result = fit_notification_ai_decision_core(research_core(), 16384)
                if mutation == "packet":
                    result.pop("companyEvidence")
                else:
                    row = next(r for r in result["evidenceLedger"] if r["evidenceId"] == "financial:revenue:current")
                    if mutation == "citation":
                        result["evidenceLedger"].remove(row)
                    else:
                        row[mutation] = "altered"
                with patch("digital_twin.modules.decisions.domain.notification_ai_context_router._fit_notification_ai_decision_core", return_value=result):
                    with self.assertRaisesRegex(ValueError, "financial evidence") as raised:
                        fit_notification_ai_decision_core(research_core(), 16384)
                from digital_twin.modules.decisions.application.ai_inference_queue_service import ai_failure_diagnostic
                diagnostic = ai_failure_diagnostic(raised.exception, "ai-preparation")
                self.assertEqual("contract-invalid", diagnostic["category"])
                self.assertFalse(diagnostic["retryable"])

    def test_no_company_evidence_is_not_filled_with_invented_financials(self):
        core = research_core()
        core.pop("companyEvidence")
        core["evidenceLedger"] = [r for r in core["evidenceLedger"] if not r["evidenceId"].startswith("financial:")]
        result = fit_notification_ai_decision_core(core, 16384)
        self.assertFalse(result.get("companyEvidence"))
        self.assertFalse(any(r["evidenceId"].startswith("financial:") for r in result["evidenceLedger"]))


if __name__ == "__main__":
    unittest.main()
