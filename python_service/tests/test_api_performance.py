import unittest

from digital_twin.infrastructure.api_performance import ApiPerformanceRegistry, route_template


class ApiPerformanceTests(unittest.TestCase):
    def test_route_template_removes_high_cardinality_detail_keys(self):
        self.assertEqual(
            "/api/investment-cases/{id}/history",
            route_template("/api/investment-cases/case:default.000660/history"),
        )
        self.assertEqual(
            "/api/instruments/{id}/timeline",
            route_template("/api/instruments/005930/timeline"),
        )

    def test_snapshot_reports_latency_and_wire_size(self):
        registry = ApiPerformanceRegistry(sample_limit=10)
        for duration in [10, 20, 30, 40, 50]:
            registry.record("GET", "/api/dashboard/summary", 200, duration, 2000, 700, True)
        registry.record("GET", "/api/dashboard/summary", 500, 60, 1000, 500, True)

        payload = registry.snapshot()
        route = payload["routes"][0]

        self.assertEqual(6, route["sampleCount"])
        self.assertEqual(1, route["errorCount"])
        self.assertEqual(60, route["p95Ms"])
        self.assertEqual(60, route["maxMs"])
        self.assertGreater(route["averageRawBytes"], route["averageWireBytes"])


if __name__ == "__main__":
    unittest.main()
