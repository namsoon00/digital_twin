import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from digital_twin.infrastructure.share_runtime import (
    active_share_runtime_state,
    fixed_access_url,
    load_or_create_share_credentials,
    request_share_tunnel_rotation,
)


class ShareRuntimeTests(unittest.TestCase):
    def test_credentials_are_persistent_private_and_not_rotated_on_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "share-access.json"

            first = load_or_create_share_credentials(path=path)
            first_mtime = path.stat().st_mtime_ns
            second = load_or_create_share_credentials(path=path)

            self.assertEqual(first, second)
            self.assertEqual(first_mtime, path.stat().st_mtime_ns)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertTrue(first["viewToken"])
            self.assertTrue(first["ownerToken"])

    def test_fixed_access_token_is_only_in_fragment(self):
        url = fixed_access_url(
            "https://example.github.io/orbit/live/",
            "share_token",
            "viewer secret",
        )

        self.assertEqual(
            "https://example.github.io/orbit/live/#share_token=viewer+secret",
            url,
        )
        self.assertNotIn("share_token", url.split("#", 1)[0])

    def test_active_runtime_requires_both_owner_and_tunnel_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "share-runtime.json"
            path.write_text(json.dumps({"ownerPid": os.getpid(), "tunnelPid": 43210}), encoding="utf-8")
            real_kill = os.kill

            def process_probe(pid, signal):
                if pid == 43210:
                    return None
                return real_kill(pid, signal)

            with patch("digital_twin.infrastructure.share_runtime.os.kill", side_effect=process_probe):
                self.assertTrue(active_share_runtime_state(path))

            with patch("digital_twin.infrastructure.share_runtime.os.kill", side_effect=OSError):
                self.assertEqual({}, active_share_runtime_state(path))

    def test_rotation_request_is_private_and_contains_a_stable_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "share-rotation-request.json"

            queued = request_share_tunnel_rotation(
                reason="owner-request",
                requested_by="local-owner",
                path=path,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual("queued", queued["status"])
            self.assertEqual("owner-request", saved["reason"])
            self.assertEqual("local-owner", saved["requestedBy"])
            self.assertTrue(saved["requestId"].startswith("share-rotation-"))
            self.assertTrue(saved["requestedAt"].endswith("Z"))
            self.assertEqual(0o600, path.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
