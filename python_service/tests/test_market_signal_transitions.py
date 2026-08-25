import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.market_signal_transitions import (
    MARKET_SIGNAL_TRANSITION_RESULTS_KEY,
    MARKET_SIGNAL_TRANSITION_STATE_KEY,
    evaluate_market_signal_transitions,
    prepare_market_signal_transition_metadata,
)
from digital_twin.domain.materiality import market_change_materiality
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.domain.portfolio_ontology_runtime_concepts import (
    add_operational_world_concepts,
    add_runtime_metadata_concepts,
)


def market_row(price=100.0, imbalance=0.0, trade_strength=100.0, volume_ratio=1.0, ma20=0.0, ma60=0.0):
    return {
        "symbol": "005930",
        "current_price": price,
        "bid_ask_imbalance": imbalance,
        "trade_strength": trade_strength,
        "volume_ratio": volume_ratio,
        "ma20_distance": ma20,
        "ma60_distance": ma60,
        "data_quality": "good",
        "freshness_status": "fresh",
        "source_timestamp_state": "current",
        "latency_status": "normal",
        "market_session": "regular",
        "real_time": True,
    }


class MarketSignalTransitionTests(unittest.TestCase):
    def baseline(self):
        return evaluate_market_signal_transitions({}, market_row(), {}, {}, "2026-08-25T00:00:00Z")

    def test_orderbook_requires_two_observations_and_does_not_repeat(self):
        baseline = self.baseline()
        first = evaluate_market_signal_transitions(
            market_row(), market_row(imbalance=30), baseline["state"], {}, "2026-08-25T00:01:00Z"
        )
        second = evaluate_market_signal_transitions(
            market_row(imbalance=30), market_row(imbalance=31), first["state"], {}, "2026-08-25T00:02:00Z"
        )
        unchanged = evaluate_market_signal_transitions(
            market_row(imbalance=31), market_row(imbalance=32), second["state"], {}, "2026-08-25T00:03:00Z"
        )

        self.assertEqual([], first["confirmedTransitions"])
        self.assertEqual("pending", first["pendingTransitions"][0]["transition"])
        self.assertEqual(["orderbook-imbalance"], second["confirmedConditions"])
        self.assertEqual([], unchanged["confirmedTransitions"])

    def test_orderbook_hysteresis_requires_clear_boundary_and_persistence(self):
        baseline = self.baseline()
        first = evaluate_market_signal_transitions(market_row(), market_row(imbalance=30), baseline["state"], {}, "t1")
        entered = evaluate_market_signal_transitions(market_row(imbalance=30), market_row(imbalance=31), first["state"], {}, "t2")
        inside_hysteresis = evaluate_market_signal_transitions(
            market_row(imbalance=31), market_row(imbalance=20), entered["state"], {}, "t3"
        )
        clear_candidate = evaluate_market_signal_transitions(
            market_row(imbalance=20), market_row(imbalance=14), inside_hysteresis["state"], {}, "t4"
        )
        cleared = evaluate_market_signal_transitions(
            market_row(imbalance=14), market_row(imbalance=13), clear_candidate["state"], {}, "t5"
        )

        self.assertEqual("positive", inside_hysteresis["state"]["signals"]["orderbook"]["confirmedState"])
        self.assertEqual([], clear_candidate["confirmedTransitions"])
        self.assertEqual(["orderbook-imbalance-cleared"], cleared["confirmedConditions"])

    def test_critical_cumulative_price_move_is_immediate_and_reanchors(self):
        baseline = self.baseline()
        immediate = evaluate_market_signal_transitions(
            market_row(), market_row(price=103.1), baseline["state"], {}, "t1"
        )
        next_quote = evaluate_market_signal_transitions(
            market_row(price=103.1), market_row(price=103.1), immediate["state"], {}, "t2"
        )

        self.assertTrue(immediate["immediate"])
        self.assertEqual(["price-move-immediate"], immediate["confirmedConditions"])
        self.assertEqual("anchored", immediate["state"]["signals"]["price"]["confirmedState"])
        self.assertEqual([], next_quote["confirmedTransitions"])

    def test_critical_price_move_bypasses_warmup_when_previous_quote_exists(self):
        immediate = evaluate_market_signal_transitions(
            market_row(), market_row(price=103.1), {}, {}, "t1"
        )

        self.assertTrue(immediate["immediate"])
        self.assertEqual(["price-move-immediate"], immediate["confirmedConditions"])

    def test_pending_transition_does_not_pass_materiality_gate(self):
        baseline = self.baseline()
        first = evaluate_market_signal_transitions(
            market_row(), market_row(imbalance=30), baseline["state"], {}, "t1"
        )
        assessment = market_change_materiality(
            "005930",
            market_row(),
            market_row(imbalance=30),
            {"fields": ["bid_ask_imbalance"]},
            {},
            signal_transition_result=first,
        )

        self.assertFalse(assessment.passed)
        self.assertEqual(1, assessment.facts["pendingSignalTransitionCount"])
        self.assertEqual([], assessment.matched_conditions)

    def test_snapshot_metadata_persists_state_and_exposes_only_results(self):
        snapshot = AccountSnapshot(
            account_id="main",
            account_label="Main",
            provider="toss",
            mode="live",
            status="ok",
            generated_at="2026-08-25T00:00:00Z",
            portfolio=PortfolioSummary(1000, 1000, 0, [], [], 100),
            positions=[Position(symbol="005930", name="삼성전자", current_price=100, quantity=1)],
        )

        prepare_market_signal_transition_metadata(snapshot, {}, {})

        self.assertIn("005930", snapshot.metadata[MARKET_SIGNAL_TRANSITION_STATE_KEY])
        self.assertIn("005930", snapshot.metadata[MARKET_SIGNAL_TRANSITION_RESULTS_KEY])
        self.assertEqual([], snapshot.metadata[MARKET_SIGNAL_TRANSITION_RESULTS_KEY]["005930"]["confirmedTransitions"])

    def test_abox_projects_confirmed_state_and_transition_but_not_candidate(self):
        baseline = self.baseline()
        first = evaluate_market_signal_transitions(
            market_row(), market_row(imbalance=30), baseline["state"], {}, "t1"
        )
        confirmed = evaluate_market_signal_transitions(
            market_row(imbalance=30), market_row(imbalance=31), first["state"], {}, "t2"
        )
        graph = PortfolioOntology("portfolio:main")
        portfolio_id = add_entity(graph, "portfolio", "portfolio:main", "Main", {"tboxClass": "Portfolio"})
        add_entity(graph, "stock", "005930", "삼성전자", {"tboxClass": "Stock", "symbol": "005930"})
        runtime_context = {
            "settings": {},
            "metadata": {
                MARKET_SIGNAL_TRANSITION_STATE_KEY: {"005930": confirmed["state"]},
                MARKET_SIGNAL_TRANSITION_RESULTS_KEY: {
                    "005930": {key: value for key, value in confirmed.items() if key != "state"}
                },
            },
        }

        add_runtime_metadata_concepts(graph, portfolio_id, runtime_context)
        add_operational_world_concepts(graph, portfolio_id, runtime_context, [])

        classes = [str(item.properties.get("tboxClass") or "") for item in graph.entities]
        relation_types = [item.relation_type for item in graph.relations]
        self.assertIn("SignalState", classes)
        self.assertIn("ConfirmedSignalTransition", classes)
        self.assertIn("SignalTransitionPolicy", classes)
        self.assertNotIn("CandidateSignalTransition", classes)
        self.assertIn("HAS_SIGNAL_TRANSITION", relation_types)
        self.assertIn("GOVERNED_BY_SIGNAL_POLICY", relation_types)


if __name__ == "__main__":
    unittest.main()
