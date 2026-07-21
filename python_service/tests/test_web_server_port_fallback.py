import errno
import os
import unittest
from time import sleep
from unittest import mock

from digital_twin.infrastructure.web_server import bind_web_server, port_fallback_enabled
from digital_twin.infrastructure.flow_lens_read_model import FlowLensReadModel


class WebServerPortFallbackTests(unittest.TestCase):
    def test_bind_web_server_uses_next_port_when_requested_port_is_occupied(self):
        attempts = []
        expected_server = object()

        def server_factory(address, _handler):
            attempts.append(address)
            if address[1] == 3000:
                raise OSError(errno.EADDRINUSE, "Address already in use")
            return expected_server

        server, selected_port = bind_web_server(
            "127.0.0.1",
            3000,
            allow_port_fallback=True,
            server_factory=server_factory,
        )

        self.assertIs(expected_server, server)
        self.assertEqual(3001, selected_port)
        self.assertEqual([("127.0.0.1", 3000), ("127.0.0.1", 3001)], attempts)

    def test_bind_web_server_raises_when_fallback_is_disabled(self):
        def server_factory(_address, _handler):
            raise OSError(errno.EADDRINUSE, "Address already in use")

        with self.assertRaisesRegex(OSError, "ports 3000-3000"):
            bind_web_server(
                "127.0.0.1",
                3000,
                allow_port_fallback=False,
                server_factory=server_factory,
            )

    def test_port_fallback_is_enabled_unless_explicitly_disabled(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(port_fallback_enabled())
        self.assertFalse(port_fallback_enabled("0"))
        self.assertFalse(port_fallback_enabled("false"))

    def test_flow_lens_read_model_serves_persisted_snapshot_without_live_collection(self):
        calls = []
        model = FlowLensReadModel(
            snapshot_provider=lambda _mock, _symbols: calls.append("live") or {"generatedAt": "live"},
            persisted_provider=lambda _symbols: {"generatedAt": "persisted", "portfolio": {"total": 1}},
        )

        result = model.read()

        self.assertEqual("pending", result.status)
        for _ in range(40):
            cached = model.read()
            if cached.snapshot:
                break
            sleep(0.01)
        self.assertTrue(cached.snapshot)
        self.assertEqual("monitor-snapshot", cached.source)
        self.assertEqual([], calls)

    def test_flow_lens_read_model_starts_one_background_refresh_for_empty_view(self):
        completed = mock.MagicMock()

        def provider(_mock, _symbols):
            completed()
            return {"generatedAt": "fresh", "portfolio": {"total": 1}}

        model = FlowLensReadModel(snapshot_provider=provider, persisted_provider=lambda _symbols: None)
        first = model.read(mock=True)
        second = model.read(mock=True)

        self.assertEqual("pending", first.status)
        self.assertTrue(first.refreshing)
        self.assertTrue(second.refreshing or second.snapshot)
        for _ in range(40):
            if completed.called:
                break
            sleep(0.01)
        self.assertTrue(completed.called)
        self.assertTrue(model.read(mock=True).snapshot)


if __name__ == "__main__":
    unittest.main()
