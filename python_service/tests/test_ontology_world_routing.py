import unittest

from digital_twin.domain.ontology_world_routing import route_world_impact


class OntologyWorldRoutingTests(unittest.TestCase):
    def test_position_only_change_stays_inside_portfolio_world(self):
        route = route_world_impact({"changedScopeFamilies": ["position"]})

        self.assertTrue(route["portfolio"]["required"])
        self.assertFalse(route["market"]["required"])
        self.assertFalse(route["knowledge"]["required"])

    def test_quote_and_flow_change_updates_market_not_durable_knowledge(self):
        route = route_world_impact({
            "changedScopeFamilies": ["market", "flow"],
            "explicitTargetSymbols": ["AAPL"],
        })

        self.assertTrue(route["market"]["required"])
        self.assertFalse(route["knowledge"]["required"])
        self.assertTrue(route["partitions"]["instrumentPremise"]["required"])
        self.assertEqual(
            ["premise:shared:global:instrument:AAPL"],
            route["partitions"]["instrumentPremise"]["partitionIds"],
        )
        account_work = route["durableHandoff"]["workItems"][-1]
        self.assertEqual("portfolio-overlay", account_work["owner"])
        self.assertEqual(
            ["project:premise:shared:global:instrument:AAPL"],
            account_work["dependsOn"],
        )

    def test_macro_change_uses_macro_partition_before_account_overlay(self):
        route = route_world_impact({
            "changedScopeFamilies": ["macro-rates"],
            "explicitTargetSymbols": ["NVDA"],
        })

        self.assertTrue(route["partitions"]["macroContext"]["required"])
        self.assertEqual(
            ["premise:shared:global:macro:macro-rates"],
            route["partitions"]["macroContext"]["partitionIds"],
        )
        self.assertIn(
            "project:premise:shared:global:macro:macro-rates",
            route["durableHandoff"]["workItems"][-1]["dependsOn"],
        )

    def test_exposure_change_updates_shared_knowledge_world(self):
        route = route_world_impact({"changedScopeFamilies": ["exposure"]})

        self.assertFalse(route["market"]["required"])
        self.assertTrue(route["knowledge"]["required"])

    def test_initial_projection_establishes_both_shared_worlds(self):
        route = route_world_impact({}, initial_projection=True)

        self.assertTrue(route["market"]["required"])
        self.assertTrue(route["knowledge"]["required"])


if __name__ == "__main__":
    unittest.main()
