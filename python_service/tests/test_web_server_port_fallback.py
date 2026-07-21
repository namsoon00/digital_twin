import errno
import os
import unittest
from unittest import mock

from digital_twin.infrastructure.web_server import bind_web_server, port_fallback_enabled


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


if __name__ == "__main__":
    unittest.main()
