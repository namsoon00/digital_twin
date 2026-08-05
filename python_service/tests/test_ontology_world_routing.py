import unittest

from digital_twin.domain.ontology_world_routing import route_world_impact


class OntologyWorldRoutingTests(unittest.TestCase):
    def test_position_only_change_stays_inside_portfolio_world(self):
        route = route_world_impact({"changedScopeFamilies": ["position"]})

        self.assertTrue(route["portfolio"]["required"])
        self.assertFalse(route["market"]["required"])
        self.assertFalse(route["knowledge"]["required"])

    def test_quote_and_flow_change_updates_market_not_durable_knowledge(self):
        route = route_world_impact({"changedScopeFamilies": ["market", "flow"]})

        self.assertTrue(route["market"]["required"])
        self.assertFalse(route["knowledge"]["required"])

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
