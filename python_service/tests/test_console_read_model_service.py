import unittest

from digital_twin.application.console_read_model_service import ConsoleReadModelService


class Symbol:
    def __init__(self, symbol, name, market="KR"):
        self.symbol = symbol
        self.name = name
        self.market = market

    def to_dict(self):
        return {"symbol": self.symbol, "name": self.name, "market": self.market}


class SymbolRepository:
    def __init__(self):
        self.values = {
            "066570": Symbol("066570", "LG전자"),
            "MSTR": Symbol("MSTR", "스트래티지", "US"),
        }

    def get(self, symbol):
        return self.values.get(symbol)


class ConsoleReadModelServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = ConsoleReadModelService(SymbolRepository())

    def lifecycle(self):
        return {
            "status": "ready",
            "portfolioId": "portfolio:default",
            "snapshotCheckpoint": {
                "portfolioTotal": "1000000",
                "cashBalance": "100000",
                "observedAt": "2026-08-24T00:00:00Z",
            },
            "portfolioState": {
                "cashWeightPct": 10,
                "positions": [{
                    "symbol": "066570",
                    "name": "066570",
                    "currentWeightPct": 90,
                    "marketValueKrw": 900000,
                }],
            },
            "exposureSnapshot": {
                "metrics": [{"exposure_type": "position", "key": "066570", "policyDeltaPct": 45}],
            },
            "portfolioRiskSnapshot": {
                "periodReturnPct": 3,
                "annualizedVolatilityPct": 25,
                "maximumDrawdownPct": -12,
                "sampleCount": 100,
                "dataState": "complete",
            },
            "rebalanceProposal": {
                "status": "review-required",
                "proposalId": "proposal:1",
                "scenarios": [{"scenario_id": "keep"}, {"scenario_id": "reduce"}],
            },
            "portfolioDecisionCycle": {"candidates": [{"candidate_id": "candidate:1"}]},
            "ledgerEntries": [{"entryId": "ledger:1"}],
            "recentActivityEpisodes": [{"episodeId": "activity:1"}],
            "actionPlans": [{"planId": "plan:1"}],
            "decisionReviews": [{"reviewId": "review:1"}],
            "reconciliation": {"status": "matched"},
        }

    def test_dashboard_keeps_actions_bounded_and_groups_blockers(self):
        cases = {
            "status": "ok",
            "items": [
                {
                    "caseId": "case:1",
                    "symbol": "066570",
                    "name": "066570",
                    "decision": {"action": "BUY"},
                    "attention": {"userActionable": True, "state": "action"},
                    "headline": "진입 조건 확인",
                    "updatedAt": "2026-08-24T01:00:00Z",
                    "readinessState": "warning",
                    "statusDimensions": [
                        {"id": "data", "state": "warning", "reason": "수급 지연", "effect": "판단 강도 제한"},
                    ],
                },
                {
                    "caseId": "case:2",
                    "symbol": "MSTR",
                    "decision": {"action": "HOLD"},
                    "attention": {"userActionable": False, "state": "review"},
                    "updatedAt": "2026-08-24T00:00:00Z",
                    "readinessState": "warning",
                    "statusDimensions": [
                        {"id": "data", "state": "warning", "reason": "수급 지연", "effect": "판단 강도 제한"},
                    ],
                },
            ],
        }
        payload = self.service.dashboard_summary(
            {"generatedAt": "2026-08-24T01:00:00Z"},
            self.lifecycle(),
            cases,
            {"events": [{"eventId": "future", "startsAt": "2099-01-01T00:00:00Z", "status": "active"}]},
        )
        self.assertEqual(1, len(payload["tasks"]))
        self.assertEqual("LG전자", payload["tasks"][0]["name"])
        self.assertEqual(1, len(payload["blockerGroups"]))
        self.assertEqual(2, payload["blockerGroups"][0]["count"])
        self.assertEqual(["future"], [item["eventId"] for item in payload["upcomingEvents"]])

    def test_portfolio_views_do_not_return_full_lifecycle_history(self):
        summary = self.service.portfolio(self.lifecycle(), "summary")
        positions = self.service.portfolio(self.lifecycle(), "positions")
        activity = self.service.portfolio(self.lifecycle(), "activity")
        self.assertEqual("LG전자", summary["positions"][0]["name"])
        self.assertEqual(1, summary["summary"]["policyBreachCount"])
        self.assertEqual(1, positions["count"])
        self.assertNotIn("performanceAttributions", activity)
        self.assertEqual(1, activity["counts"]["ledger"])
        self.assertNotIn("payload", activity["ledgerEntries"][0])
        self.assertNotIn("envelope", activity["actionPlans"][0])

    def test_decision_heads_keep_list_contract_and_drop_full_trace_payloads(self):
        payload = self.service.decision_heads({
            "version": "investment-case-v4",
            "status": "ok",
            "items": [{
                "caseId": "case:1",
                "symbol": "066570",
                "name": "066570",
                "headline": "회복 조건 확인",
                "decision": {"action": "BUY"},
                "facts": {"dataState": "sufficient", "sourceSnapshot": {"large": "value"}},
                "integrity": {"trace": [1, 2, 3]},
                "explanation": {"changeConditions": ["조건 1", "조건 2", "조건 3", "조건 4"]},
            }],
        })
        self.assertEqual("LG전자", payload["items"][0]["name"])
        self.assertEqual({"dataState": "sufficient"}, payload["items"][0]["facts"])
        self.assertNotIn("integrity", payload["items"][0])
        self.assertNotIn("changeConditions", payload["items"][0]["explanation"])

    def test_market_instruments_deduplicate_holding_and_watchlist(self):
        payload = self.service.market_instruments({
            "generatedAt": "2026-08-24T00:00:00Z",
            "toss": {
                "positions": [{
                    "symbol": "066570",
                    "name": "066570",
                    "market": "KR",
                    "source": "holding",
                    "currentPrice": 100,
                    "foreignBuyVolume": 45,
                    "foreignSellVolume": 45,
                    "foreignNetVolume": 0,
                    "institutionBuyVolume": 220,
                    "institutionSellVolume": 100,
                    "institutionNetVolume": 120,
                    "marketSignalCoverage": {
                        "investor": {
                            "status": "available",
                            "observedFields": ["foreignNetVolume", "institutionNetVolume"],
                        },
                    },
                }],
                "watchlist": [{"symbol": "066570", "market": "KR", "source": "watchlist", "currentPrice": 99}],
                "watchlistQuotes": [{"symbol": "MSTR", "market": "US", "currentPrice": 120}],
            },
        })
        self.assertEqual(2, payload["summary"]["total"])
        self.assertEqual("LG전자", payload["items"][0]["name"])
        self.assertEqual("holding", payload["items"][0]["source"])
        self.assertEqual(45, payload["items"][0]["foreignBuyVolume"])
        self.assertEqual(45, payload["items"][0]["foreignSellVolume"])
        self.assertEqual(220, payload["items"][0]["institutionBuyVolume"])
        self.assertEqual(100, payload["items"][0]["institutionSellVolume"])
        self.assertEqual(
            ["foreignNetVolume", "institutionNetVolume"],
            payload["items"][0]["marketSignalCoverage"]["investor"]["observedFields"],
        )

    def test_market_evidence_filters_before_applying_page_limit(self):
        payload = self.service.market_evidence({
            "total": 4,
            "items": [
                {"evidenceId": "hidden", "kind": "news", "displayEligible": False, "publishedAt": "2026-08-24T04:00:00Z"},
                {"evidenceId": "generic", "kind": "financial-fact", "url": "", "publishedAt": "2026-08-24T03:00:00Z"},
                {"evidenceId": "news", "kind": "news", "displayEligible": True, "publishedAt": "2026-08-24T02:00:00Z"},
                {"evidenceId": "disclosure", "kind": "disclosure", "displayEligible": True, "publishedAt": "2026-08-24T01:00:00Z"},
            ],
        }, limit=1)
        self.assertEqual(["news"], [item["evidenceId"] for item in payload["items"]])
        self.assertEqual(2, payload["totalEligible"])

    def test_operations_health_uses_queue_status_not_compact_abox_rows(self):
        payload = self.service.operations_health({
            "realtime": {"monitoring": {"snapshot": {"occurredAt": "2026-08-24T00:00:00Z"}}, "notificationJobs": {}, "aiInferenceQueue": {}},
            "external": {"providers": [{"state": "healthy", "updatedAt": "2026-08-24T00:00:00Z"}]},
            "reasoning": {"status": "idle", "effectivePendingCount": 0, "processingCount": 0, "deploymentId": "v2"},
            "engine": {"status": "ready"},
            "timeSeries": {"status": "ready"},
        })
        reasoning = next(item for item in payload["components"] if item["id"] == "reasoning")
        self.assertEqual("healthy", reasoning["state"])
        self.assertNotIn("ABox", reasoning["detail"])
        self.assertEqual("healthy", next(item for item in payload["components"] if item["id"] == "time-series")["state"])

    def test_operations_health_distinguishes_active_notification_work_from_history(self):
        payload = self.service.operations_health({
            "realtime": {
                "monitoring": {},
                "notificationJobs": {
                    "pending": 2,
                    "awaiting_ai": 1,
                    "processing": 1,
                    "failed": 4,
                    "done": 20,
                    "suppressed": 8,
                },
                "aiInferenceQueue": {},
            },
            "external": {},
            "reasoning": {},
            "engine": {},
            "timeSeries": {},
        })
        notifications = next(item for item in payload["components"] if item["id"] == "notifications")
        self.assertEqual("warning", notifications["state"])
        self.assertIn("현재 대기 3건", notifications["detail"])
        self.assertIn("미처리 실패 4건", notifications["detail"])
        self.assertIn("누적 완료 20건", notifications["detail"])


if __name__ == "__main__":
    unittest.main()
