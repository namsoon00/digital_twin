import unittest
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from digital_twin.infrastructure.web import responses

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

    def test_client_timeout_samples_are_bounded_and_have_no_query_or_body(self):
        registry = ApiPerformanceRegistry(sample_limit=10)
        for _ in range(15):
            registry.record_client({"path": "/api/notification-jobs?account=private", "durationMs": 12000,
                                    "outcome": "timeout", "requestId": "web-123", "body": "must not save"})
        client = registry.snapshot()["client"]
        self.assertEqual(10, client["sampleCount"])
        self.assertEqual(10, client["timeoutCount"])
        self.assertEqual("/api/notification-jobs", client["samples"][0]["route"])
        self.assertNotIn("private", str(client))
        self.assertNotIn("must not save", str(client))
        for duration in (float("nan"), -1, 3600001, [1]):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                registry.record_client({"path": "/api/x", "outcome": "timeout", "durationMs": duration})
        registry.clear()
        self.assertEqual(0, registry.snapshot()["client"]["sampleCount"])

    def test_broken_pipe_is_not_counted_as_a_successful_200(self):
        registry = ApiPerformanceRegistry()
        handler = SimpleNamespace(
            command="GET", path_name=lambda: "/api/notification-jobs", accepts_gzip=lambda: False,
            send_response=Mock(), send_header=Mock(), end_headers=Mock(),
            wfile=SimpleNamespace(write=Mock(side_effect=BrokenPipeError())),
            _request_id="web-123", _request_started_at=time.monotonic(),
        )
        with patch.object(responses, "API_PERFORMANCE", registry), patch("builtins.print") as log:
            responses.send_payload(handler, 200, {"private": "not logged"})
        route = registry.snapshot()["routes"][0]
        self.assertEqual(200, route["lastStatus"])
        self.assertEqual(1, route["disconnectCount"])
        self.assertEqual(1, route["errorCount"])
        self.assertIn("web-123", log.call_args.args[0])
        self.assertNotIn("not logged", log.call_args.args[0])

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
