import unittest

from digital_twin.application.psychology_shadow_service import PsychologyShadowService
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.market_psychology import market_psychology_snapshot
from digital_twin.domain.ontology_inference_context import (
    is_primary_inference_relation,
    is_shadow_inference_relation,
    matches_from_inference,
)
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder


OBSERVED_AT = "2026-07-20T01:00:00Z"


def fresh_profiles():
    return {
        "quote": {
            "freshnessStatus": "fresh",
            "judgementEvidenceUsable": True,
            "sourceReliability": 90,
            "observationSource": "KIS Open API",
            "sourceAsOf": "2026-07-20T00:59:00Z",
        },
        "flow": {
            "freshnessStatus": "fresh",
            "judgementEvidenceUsable": True,
            "sourceReliability": 88,
            "observationSource": "KIS Open API",
            "sourceAsOf": "2026-07-20T00:59:00Z",
        },
    }


def psychology_position(**overrides):
    payload = {
        "symbol": "005930",
        "name": "삼성전자",
        "market": "KR",
        "currency": "KRW",
        "quantity": 10,
        "currentPrice": 82000,
        "averagePrice": 78000,
        "marketValue": 820000,
        "changeRate": 3.5,
        "ma20": 79000,
        "ma20Distance": 3.8,
        "volumeRatio": 1.8,
        "tradeStrength": 118,
        "bidAskImbalance": 12,
        "foreignNetVolume": 300000,
        "institutionNetVolume": 180000,
        "individualNetVolume": -420000,
        "sourceAsOf": "2026-07-20T00:59:00Z",
        "sourceFetchedAt": "2026-07-20T00:59:30Z",
        "quoteSource": "KIS Open API",
        "dataQuality": "actual",
        "marketSignalCoverage": {
            "investor": {
                "status": "available",
                "judgementEvidenceUsable": True,
                "sourceAsOf": "2026-07-20T00:58:00Z",
            }
        },
    }
    payload.update(overrides)
    return normalize_position(payload)


class MarketPsychologyTests(unittest.TestCase):
    def test_fresh_behavior_and_investor_flow_create_shadow_state_without_decision_impact(self):
        result = market_psychology_snapshot(
            psychology_position(),
            observation_profiles=fresh_profiles(),
            observed_at=OBSERVED_AT,
        ).to_dict()

        self.assertNotEqual("insufficient", result["state"])
        self.assertEqual("sufficient", result["dataState"])
        self.assertEqual("support-only", result["conflictState"])
        self.assertTrue(result["shadowOnly"])
        components = {item["key"]: item for item in result["components"]}
        self.assertTrue(components["behavior"]["available"])
        self.assertTrue(components["investorFlow"]["available"])
        self.assertFalse(components["crowd"]["available"])

    def test_stale_quote_and_stale_options_are_excluded(self):
        profiles = fresh_profiles()
        profiles["quote"] = {
            "freshnessStatus": "stale",
            "judgementEvidenceUsable": False,
            "freshnessGateReason": "기준 3분 초과",
            "sourceAsOf": "2026-07-19T00:00:00Z",
        }
        external_signals = {
            "yfinanceData": {
                "005930": {
                    "collectedAt": "2026-07-19T00:00:00Z",
                    "optionChains": [{"summary": {"putCallOpenInterestRatio": 0.4}}],
                    "moduleFreshness": {"optionChains": {"status": "stale"}},
                }
            }
        }

        result = market_psychology_snapshot(
            psychology_position(),
            external_signals=external_signals,
            observation_profiles=profiles,
            observed_at=OBSERVED_AT,
        ).to_dict()
        components = {item["key"]: item for item in result["components"]}

        self.assertFalse(components["behavior"]["available"])
        self.assertFalse(components["positioning"]["available"])
        self.assertEqual("insufficient", result["state"])

    def test_strong_price_and_flow_support_is_categorical_not_a_score(self):
        result = market_psychology_snapshot(
            psychology_position(changeRate=10, ma20Distance=14, tradeStrength=150, bidAskImbalance=35),
            observation_profiles=fresh_profiles(),
            observed_at=OBSERVED_AT,
        ).to_dict()

        self.assertEqual("optimistic", result["state"])
        self.assertEqual("support-only", result["conflictState"])
        self.assertNotIn("score", result)
        self.assertNotIn("candidateRiskImpact", result)

    def test_ontology_projects_psychology_abox_and_shadow_rule(self):
        position = psychology_position()
        portfolio = portfolio_summary([position], account_cash=100000)
        graph = build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="psychology-test",
            runtime_context={
                "asOf": OBSERVED_AT,
                "settings": {"psychologyMinimumComponentCount": "2"},
            },
        )

        state = next(item for item in graph.entities if item.kind == "market-psychology-state")
        relation_types = {item.relation_type for item in graph.relations}
        rule_ids = {item.rule_id for item in default_graph_inference_rules()}

        self.assertTrue(state.properties["shadowOnly"])
        self.assertFalse(state.properties["decisionImpactApplied"])
        self.assertNotIn("psychologyScore", state.properties)
        self.assertIn("HAS_MARKET_PSYCHOLOGY_STATE", relation_types)
        self.assertIn("shadow.market_psychology.state.v1", rule_ids)

    def test_shadow_inference_never_becomes_active_investment_match(self):
        relation = {
            "type": "HAS_PSYCHOLOGY_SHADOW",
            "source": "stock:005930",
            "target": "psychology-shadow:005930",
            "symbol": "005930",
            "ruleId": "shadow.market_psychology.state.v1",
            "derivationIndex": 0,
            "reviewLevel": "observe",
            "dataState": "sufficient",
            "evidenceRole": "context",
        }

        self.assertTrue(is_shadow_inference_relation(relation))
        self.assertFalse(is_primary_inference_relation(relation))
        self.assertEqual([], matches_from_inference([relation], [], facts={}))

    def test_shadow_service_records_comparison_but_not_dispatch(self):
        position = psychology_position()
        portfolio = portfolio_summary([position], account_cash=100000)
        snapshot = AccountSnapshot(
            account_id="main",
            account_label="기본 계정",
            provider="toss",
            mode="live",
            status="ok",
            generated_at=OBSERVED_AT,
            portfolio=portfolio,
            positions=[position],
            metadata={"previousMonitorState": {}},
        )

        events = PsychologyShadowService({"psychologyMinimumComponentCount": "2"}).evaluate(snapshot)
        row = snapshot.metadata["psychologyShadow"]["symbols"]["005930"]

        self.assertEqual(1, len(events))
        self.assertFalse(row["comparison"]["decisionImpactApplied"])
        self.assertFalse(row["comparison"]["dispatchEligible"])
        self.assertFalse(events[0].payload["decisionImpactApplied"])
        self.assertFalse(events[0].payload["dispatchEligible"])

    def test_existing_typedb_rulebox_adds_only_missing_shadow_rule(self):
        original_rule = default_graph_inference_rules()[0].to_dict()

        class Repository:
            def __init__(self):
                self.rules = [original_rule]
                self.saved_payloads = []

            def rulebox_snapshot(self):
                return {
                    "configured": True,
                    "status": "ok",
                    "ruleCount": len(self.rules),
                    "rules": list(self.rules),
                }

            def save_rulebox(self, payload):
                self.saved_payloads.append(dict(payload or {}))
                self.rules = list((payload or {}).get("rules") or [])
                return {"saved": True, "status": "ok", "ruleCount": len(self.rules), "rules": list(self.rules)}

        repository = Repository()
        result = PortfolioOntologyProjectionRecorder(repository, settings={"psychologyShadowEnabled": "1"}).ensure_rulebox_ready()
        rule_ids = [str(item.get("rule_id") or item.get("ruleId") or "") for item in repository.rules]

        self.assertEqual(1, len(repository.saved_payloads))
        self.assertEqual(original_rule["rule_id"], rule_ids[0])
        self.assertEqual(1, rule_ids.count("shadow.market_psychology.state.v1"))
        self.assertEqual("migrated", result["additiveRuleMigration"]["status"])


if __name__ == "__main__":
    unittest.main()
