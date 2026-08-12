import unittest

from digital_twin.domain.investment_analysis import build_investment_analysis


class InvestmentAnalysisReadModelTests(unittest.TestCase):
    def test_action_queue_exposes_stable_decision_link_and_data_provenance(self):
        snapshot = {
            "generatedAt": "2026-08-13T07:00:00Z",
            "toss": {
                "mode": "live",
                "account": {"accountId": "account-1"},
                "positions": [{
                    "symbol": "AAPL",
                    "name": "Apple",
                    "source": "Toss Securities",
                    "quoteSource": "Toss Open API",
                    "currentPrice": 221.4,
                    "dataQuality": "actual",
                    "updatedAt": "2026-08-13T06:59:00Z",
                }],
            },
            "tossDecision": {
                "items": [{
                    "symbol": "AAPL",
                    "decision": "소액 진입 검토",
                    "actionCode": "BUY",
                    "investmentDecisionEpisodeId": "episode-aapl-1",
                    "ontologyRelationContext": {
                        "graphStoreUsed": True,
                        "decision": {"reviewLevel": "act", "dataState": "sufficient", "validationState": "ready"},
                    },
                }],
            },
        }

        payload = build_investment_analysis(snapshot)
        row = payload["actionQueue"][0]

        self.assertEqual("account-1", payload["accountFocus"]["accountId"])
        self.assertEqual("episode-aapl-1", row["decisionEpisodeId"])
        self.assertTrue(row["decisionKey"].startswith("decision:"))
        self.assertEqual("BUY", row["actionCode"])
        self.assertEqual("holding", row["portfolioRole"])
        self.assertEqual("Apple", row["name"])
        self.assertEqual("Toss Open API", row["apiSource"])
        self.assertFalse(row["isMock"])


if __name__ == "__main__":
    unittest.main()
