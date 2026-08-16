import json
import unittest
from types import SimpleNamespace

from digital_twin.application.shared_instrument_inference_service import (
    SharedInstrumentInferenceService,
)
from digital_twin.domain.shared_instrument_inference import (
    build_shared_instrument_inference,
)


def projection(account_id, market_observed=226.17):
    market_trace = {
        "id": "trace:market:" + account_id,
        "symbol": "NVDA",
        "ruleId": "graph.market.recovery.v1",
        "semanticRuleId": "graph.market.recovery.v1",
        "ruleConditionShapes": [{
            "conditionId": "market-price",
            "kind": "field",
            "field": "currentPrice",
            "operator": ">",
            "value": 200,
            "hypothesisScope": "market",
        }],
        "matchedConditions": [{
            "conditionId": "market-price",
            "kind": "field",
            "field": "currentPrice",
            "operator": ">",
            "value": 200,
            "observedValue": market_observed,
            "hypothesisScope": "market",
        }],
        "matchedConditionIds": ["market-price"],
        "freshnessStatus": "fresh",
        "validationState": "verified",
    }
    account_trace = {
        "id": "trace:account:" + account_id,
        "symbol": "NVDA",
        "ruleId": "graph.portfolio.concentration.v1",
        "semanticRuleId": "graph.portfolio.concentration.v1",
        "ruleConditionShapes": [{
            "conditionId": "account-weight",
            "kind": "field",
            "field": "positionWeightPct",
            "operator": ">",
            "value": 25,
            "hypothesisScope": "account",
        }],
        "matchedConditions": [{
            "conditionId": "account-weight",
            "kind": "field",
            "field": "positionWeightPct",
            "operator": ">",
            "value": 25,
            "observedValue": 31,
            "hypothesisScope": "account",
        }],
        "matchedConditionIds": ["account-weight"],
    }
    relations = [
        {
            "type": "HAS_MARKET_RECOVERY",
            "source": "stock:NVDA",
            "target": "inference:market:NVDA",
            "ruleId": market_trace["ruleId"],
            "inferenceTraceId": market_trace["id"],
            "reviewLevel": "check",
        },
        {
            "type": "HAS_CONCENTRATION_RISK",
            "source": "stock:NVDA",
            "target": "portfolio:" + account_id,
            "ruleId": account_trace["ruleId"],
            "inferenceTraceId": account_trace["id"],
            "reviewLevel": "check",
        },
    ]
    return {
        "status": "ok",
        "inferenceBox": {
            "status": "ok",
            "graphStore": "typedb",
            "nativeTypeDbReasoningCompleted": True,
            "generationAligned": True,
            "sourceAboxSnapshotId": "abox:" + account_id,
            "inferenceGenerationId": "generation:" + account_id,
            "inferenceGenerationAt": "2026-08-17T01:00:00Z",
            "ruleboxRulesHash": "rules-1",
            "symbols": ["NVDA"],
            "relations": relations,
            "traces": [market_trace, account_trace],
        },
    }


class FakeStore:
    def __init__(self):
        self.report = None
        self.reconciled = []

    def publish(self, report):
        self.report = report
        return {
            "status": report["status"],
            "snapshotCount": len(report["snapshots"]),
            "overlayCount": len(report["overlays"]),
            "headUpdateCount": len(report["snapshots"]),
            "sharedSymbolCount": report["sharedSymbolCount"],
            "verifiedAccountCount": report["verifiedAccountCount"],
            "consistencyBySymbol": report["consistencyBySymbol"],
        }

    def reconcile_subscriptions(self, account_id, holdings, watchlist, **kwargs):
        self.reconciled.append((account_id, list(holdings), list(watchlist), kwargs))
        return {"activeCount": len(set(holdings) | set(watchlist))}


class SharedInstrumentInferenceTest(unittest.TestCase):
    def test_equivalent_market_inference_is_shared_without_account_data(self):
        report = build_shared_instrument_inference(
            {"a-1": projection("a-1"), "a-2": projection("a-2")},
            ["NVDA"],
            deployment_id="ontology-v2-shadow",
            release_fingerprint="release-1",
            observed_at="2026-08-17T01:01:00Z",
        )

        self.assertEqual("ready", report["status"])
        self.assertEqual({"NVDA": "equivalent"}, report["consistencyBySymbol"])
        self.assertEqual(1, len(report["snapshots"]))
        self.assertEqual(2, report["snapshots"][0].source_account_count)
        self.assertEqual(2, len(report["overlays"]))
        self.assertTrue(all(item.status == "ready" for item in report["overlays"]))
        shared_json = json.dumps(report["snapshots"][0].to_dict(), ensure_ascii=False)
        self.assertNotIn("a-1", shared_json)
        self.assertNotIn("a-2", shared_json)
        self.assertNotIn("positionWeightPct", shared_json)
        self.assertIn("graph.market.recovery.v1", shared_json)

    def test_conflicting_market_fingerprints_fail_closed(self):
        report = build_shared_instrument_inference(
            {"a-1": projection("a-1", 226.17), "a-2": projection("a-2", 227.0)},
            ["NVDA"],
            deployment_id="ontology-v2-shadow",
        )

        self.assertEqual("conflict", report["status"])
        self.assertEqual("conflict", report["consistencyBySymbol"]["NVDA"])
        self.assertEqual(2, len(report["snapshots"]))
        self.assertTrue(all(item.status == "conflict" for item in report["overlays"]))
        self.assertTrue(all(not item.shared_snapshot_ids for item in report["overlays"]))

    def test_service_attaches_bounded_lineage_and_reconciles_subscriptions(self):
        store = FakeStore()
        service = SharedInstrumentInferenceService(
            store,
            "ontology-v2-shadow",
            "release-1",
        )
        projection_results = {"a-1": projection("a-1")}
        holding = SimpleNamespace(symbol="NVDA", is_cash=lambda: False)
        cash = SimpleNamespace(symbol="CASH", is_cash=lambda: True)
        watch = SimpleNamespace(symbol="TSLA", is_cash=lambda: False)
        snapshot = SimpleNamespace(
            account_id="a-1",
            generated_at="2026-08-17T01:00:00Z",
            positions=[holding, cash],
            watchlist=[watch],
            metadata={"ontology": {"projection": projection_results["a-1"]}},
        )

        receipt = service.publish_verified_results(
            projection_results,
            ["NVDA"],
            snapshots=[snapshot],
            observed_at="2026-08-17T01:01:00Z",
        )

        self.assertEqual("ready", receipt["status"])
        self.assertEqual(("a-1", ["NVDA"], ["TSLA"]), store.reconciled[0][:3])
        shared = projection_results["a-1"]["inferenceBox"]["sharedInstrumentInference"]
        self.assertEqual("none", shared["decisionAuthority"])
        self.assertTrue(shared["symbols"]["NVDA"]["reuseEligible"])
        self.assertEqual(
            shared,
            snapshot.metadata["ontology"]["projection"]["sharedInstrumentInference"],
        )

    def test_warm_worker_repairs_subscription_index_from_current_states_once(self):
        store = FakeStore()
        service = SharedInstrumentInferenceService(store, "ontology-v2-shadow")
        accounts = [SimpleNamespace(account_id="a-1", watchlist_symbols=["TSLA"])]
        states = {
            "a-1": {
                "generatedAt": "2026-08-17T01:00:00Z",
                "positions": {
                    "NVDA": {"symbol": "NVDA"},
                    "CASH": {"symbol": "CASH", "isCash": True},
                },
                "watchlist": {},
            }
        }

        first = service.ensure_subscription_index(accounts, states)
        second = service.ensure_subscription_index(accounts, states)

        self.assertEqual("ready", first["status"])
        self.assertEqual("already-reconciled", second["status"])
        self.assertEqual(("a-1", ["NVDA"], ["TSLA"]), store.reconciled[0][:3])

    def test_v1_publication_refreshes_subscriptions_from_consumed_immutable_state(self):
        store = FakeStore()
        service = SharedInstrumentInferenceService(store, "ontology-v1-active")

        receipt = service.publish_verified_results(
            {"a-1": projection("a-1")},
            ["NVDA"],
            states={
                "a-1": {
                    "generatedAt": "2026-08-17T01:00:00Z",
                    "positions": {"NVDA": {"symbol": "NVDA"}},
                    "watchlist": {"TSLA": {"symbol": "TSLA"}},
                },
            },
        )

        self.assertEqual("immutable-reasoning-states", receipt["subscriptionIndex"]["source"])
        self.assertEqual(1, receipt["subscriptionIndex"]["accountCount"])
        self.assertEqual(("a-1", ["NVDA"], ["TSLA"]), store.reconciled[0][:3])


if __name__ == "__main__":
    unittest.main()
