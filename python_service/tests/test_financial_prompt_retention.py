import copy
import json
import unittest
from unittest.mock import patch

from digital_twin.modules.decisions.domain.notification_ai_context_router import (
    fit_notification_ai_decision_core,
    route_notification_ai_decision_context,
)
from digital_twin.modules.decisions.domain.notification_ai_decision_brief import (
    build_notification_ai_prompt_bundle,
    notification_ai_decision_brief,
)
from digital_twin.modules.notifications.domain.notification_narrative import (
    build_decision_core_evidence_ledger,
    normalize_narrative_claims,
    resolved_narrative_claim_evidence_contract,
)
from digital_twin.modules.news_intelligence.contracts import financial_evidence_use


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
    company["financialEvidenceUse"] = financial_evidence_use(company["financialEvidence"], company["financialEvidence"])
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
        self.assertNotIn("featureSummary", rows["financial:revenue:current"])
        self.assertNotIn("featureSummary", rows["financial:freeCashFlowMarginPct:ratio"])
        self.assertFalse(any(key.startswith("financial:sharesOutstanding:") for key in rows))
        contract = resolved_narrative_claim_evidence_contract(core.get("narrativeClaimContract"), rows.values())
        self.assertIn("financial:revenue:current", json.dumps(contract))
        self.assertIn("financial:freeCashFlowMarginPct:ratio", json.dumps(contract))
        self.assertIn("financialEvidenceUse", core["companyEvidence"])

    def test_research_only_compression_preserves_comparisons_ratios_and_citations(self):
        original = research_core()
        snapshot = copy.deepcopy(original)
        fitted = fit_notification_ai_decision_core(original, 16384)
        self.assertEqual("minimum-research-review-contract", fitted["routingAudit"]["status"])
        self.assert_financial_packet(fitted)
        self.assertEqual(original, snapshot)
        self.assertEqual([], fitted["decision"]["actionEnvelope"].get("allowedActions", []))
        self.assertEqual("NO_ACTION", fitted["decision"]["actionEnvelope"]["executionAction"])
        self.assertEqual(original["companyEvidence"]["financialEvidenceUse"], fitted["companyEvidence"]["financialEvidenceUse"])

    def test_financial_continuity_distinguishes_reuse_revision_and_report_period(self):
        packet = financial_company()["financialEvidence"]
        for previous, expected in (({}, "first-observed"), (packet, "reused"),
                                   ({"fingerprint": "old", "period": "2026-06-30"}, "revised"),
                                   ({"fingerprint": "old", "period": "2026-03-31"}, "new-period")):
            with self.subTest(expected=expected):
                use = financial_evidence_use(packet, previous)
                self.assertEqual(expected, use["state"])
                self.assertEqual("2026-06-30", use["reportingPeriod"])
                self.assertEqual("not-established-by-financial-packet", use["filingPublication"])
                json.dumps(use)
        self.assertEqual({}, financial_evidence_use({}))
        self.assertEqual("first-observed", financial_evidence_use({"period": "2026-06-30"}, {"period": "2026-06-30"})["state"])

    def test_router_separates_prior_financials_from_the_market_trigger(self):
        company = financial_company()
        trigger = {"status": "verified-material-transition", "reasons": ["orderbook-imbalance-cleared"]}
        brief = {"currentSituation": {"companyContext": company, "reasoningDeliveryTrigger": trigger},
                 "decisionState": {"previousInvestmentInsight": {"financialEvidence": company["financialEvidence"]}},
                 "inference": {"activeRules": [{"ruleId": "graph.company.market.fundamental_confirmation.support.v1"}]}}
        original = copy.deepcopy(brief)
        core, _ = route_notification_ai_decision_context(brief)
        self.assertEqual("reused", core["companyEvidence"]["financialEvidenceUse"]["state"])
        self.assertEqual(trigger, core["reasoningTrigger"])
        self.assertEqual(original, brief)

    def test_production_brief_prefers_captured_delivered_financials_over_undelivered_analysis(self):
        company = financial_company()
        context = {"symbol": "TEST", "ontologyRelationContext": {"facts": {"companyContext": company}},
                   "previousDeliveredInvestmentAIInsightEpisode": {"insight": {"financialEvidence": company["financialEvidence"]}},
                   "previousInvestmentAIInsightEpisode": {"financialEvidence": {"fingerprint": "other", "period": "2026-03-31"}}}
        original = copy.deepcopy(context)
        brief = notification_ai_decision_brief(context)
        self.assertEqual("reused", brief["currentSituation"]["companyContext"]["financialEvidenceUse"]["state"])
        self.assertEqual(original, context)

    def test_financial_price_attribution_is_repaired_not_replaced_with_a_bearish_claim(self):
        core = research_core()
        context = {"_notificationAiPreparedDecisionCore": core}
        def review(text):
            return normalize_narrative_claims(context, {"narrativeClaims": [{
                "claimId": "mechanism", "section": "mechanism", "text": text,
                "evidenceIds": ["financial:revenue:change", "fact:ma20Distance"],
            }]}, writer_kind="ai")
        claims, audit = review("매출 확대가 이익 개선으로 이어졌고, 이 변화가 시장 가격에 일부 반영됐습니다.")
        self.assertEqual([], claims)
        self.assertIn("financial-price-causation-unproven", audit["validations"][0]["reasons"])
        for text in ("기존 매출 개선과 평균 위 가격이 함께 관찰돼 긍정적인 해석을 뒷받침합니다.",
                     "실적 변화가 가격에 반영됐을 가능성이 있지만 원인으로 단정할 수 없습니다."):
            claims, audit = review(text)
            self.assertEqual("verified", audit["status"])
            self.assertEqual(text, claims[0]["text"])
        _, audit = review("새 실적이 발표됐고 긍정적 호재입니다.")
        self.assertIn("reused-financials-presented-as-new-filing", audit["validations"][0]["reasons"])
        claims, audit = review("일회성 손익을 제외한 정상 이익이 개선됐습니다.")
        self.assertEqual([], claims)
        self.assertIn("earnings-quality-not-assessed", audit["validations"][0]["reasons"])
        claims, audit = review("정상 이익의 개선 여부는 추가 확인이 필요합니다.")
        self.assertEqual("verified", audit["status"])
        core["companyEvidence"]["financialEvidence"]["earningsQuality"] = {"normalizedEarningsAvailable": True}
        claims, audit = review("정상 이익이 개선됐습니다.")
        self.assertNotIn("earnings-quality-not-assessed", audit["validations"][0]["reasons"])

    def test_ai_document_separates_financial_baseline_market_change_and_interpretation(self):
        from digital_twin.modules.decisions.contracts import NotificationAIValidatedResponse
        from digital_twin.modules.notifications.application.notification_ai_gate_message import research_narrative_telegram_message
        company = financial_company()
        original = company["financialEvidence"]["comparisons"][0]
        company["financialEvidence"]["comparisons"].extend([
            {**original, "metric": "operatingIncome", "changePct": 30},
            {**original, "metric": "freeCashFlow", "changePct": 10, "provider": "yfinance", "comparisonBasis": "quarter-over-quarter"},
        ])
        context = {"displayTarget": "테스트 / TEST", "notificationAiReviewMode": "context-narrative",
                   "ontologyRelationContext": {"facts": {"companyContext": company},
                                              "activeRules": [{"ruleId": "graph.company.market.fundamental_confirmation.support.v1"}]},
                   "previousDeliveredInvestmentAIInsightEpisode": {"financialEvidence": company["financialEvidence"]},
                   "reasoningDeliveryTrigger": {"status": "verified-material-transition", "reasons": ["orderbook-imbalance-cleared"]}}
        response = NotificationAIValidatedResponse(action="NO_ACTION", source="test AI", insight_assessment={
            "dominantThesis": "기존 매출 개선과 가격 회복이 긍정적인 관점을 뒷받침합니다.",
            "causalMechanism": "기존 실적의 개선 방향을 유지하면서 이번 거래 흐름을 함께 비교했습니다.",
        })
        rendered = research_narrative_telegram_message(context, response)
        sections = context["customerInvestmentDocument"]["sections"]
        keys = [s["key"] for s in sections]
        self.assertLess(keys.index("change"), keys.index("financial-evidence"))
        self.assertLess(keys.index("change"), keys.index("reasons"))
        self.assertIn("기존 전제 · 재무", rendered)
        self.assertIn("판단 연결", rendered)
        self.assertNotIn("OpenDART 공시", rendered)
        self.assertNotIn("yfinance 집계", rendered)
        financial = next(s for s in sections if s["key"] == "financial-evidence")
        self.assertEqual(1, len(financial["rows"]))
        self.assertIn("직전 알림과 같습니다", financial["rows"][0])
        self.assertNotIn("전년 동기 대비", financial["rows"][0])
        self.assertNotIn("전분기 대비", financial["rows"][0])
        change = next(s for s in sections if s["key"] == "change")
        self.assertIn("관찰 기준", " ".join(change["rows"]))
        self.assertNotIn("매출", " ".join(change["rows"]))

    def test_action_and_emergency_compression_preserve_the_same_packet(self):
        core = research_core()
        core["reviewMode"] = "decision"
        core["notificationIntent"] = "investment-decision"
        core["hypothesisSet"]["comparisonMode"] = "decision"
        core["companyEvidence"]["latestFinancials"] = {"audit": "x" * 100000}
        core["temporalEvidence"] = {"windows": [{"windowKey": "price-1h", "returnPct": 1.3, "sourceFeatureSnapshotId": "fixture-snapshot"}]}
        core["background"] = {"audit": "x" * 50000}
        fitted = fit_notification_ai_decision_core(core, 16384)
        self.assertEqual("minimum-decision-contract", fitted["routingAudit"]["status"])
        self.assert_financial_packet(fitted)
        self.assertEqual(core["temporalEvidence"], fitted["temporalEvidence"])
        core["temporalEvidence"]["windows"] *= 300
        with self.assertRaises(ValueError):
            fit_notification_ai_decision_core(core, 16384)

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
