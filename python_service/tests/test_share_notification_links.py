import json
import tempfile
import unittest
from pathlib import Path

from digital_twin.infrastructure.share_notification_links import ActiveShareNotificationLinkResolver


class ActiveShareNotificationLinkResolverTests(unittest.TestCase):
    def write_state(self, directory: str, **overrides) -> Path:
        state = {
            "version": 1,
            "provider": "cloudflared",
            "baseUrl": "https://evidence.trycloudflare.com",
            "viewerUrl": "https://evidence.trycloudflare.com/?share_token=test-viewer",
            "ownerPid": 101,
            "tunnelPid": 202,
        }
        state.update(overrides)
        path = Path(directory) / "share-runtime.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        return path

    def test_returns_active_cloudflare_viewer_url(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_state(directory)
            resolver = ActiveShareNotificationLinkResolver(path, process_alive=lambda pid: pid in {101, 202})

            resolved = resolver("http://127.0.0.1:3000?tab=notifications")

            self.assertEqual(
                "https://evidence.trycloudflare.com/?share_token=test-viewer",
                resolved,
            )

    def test_falls_back_when_share_process_is_not_alive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_state(directory)
            resolver = ActiveShareNotificationLinkResolver(path, process_alive=lambda _pid: False)

            resolved = resolver("http://127.0.0.1:3000?tab=notifications")

            self.assertEqual("http://127.0.0.1:3000?tab=notifications", resolved)

    def test_rejects_viewer_url_without_share_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_state(
                directory,
                viewerUrl="https://evidence.trycloudflare.com/",
            )
            resolver = ActiveShareNotificationLinkResolver(path, process_alive=lambda _pid: True)

            self.assertEqual("http://127.0.0.1:3000", resolver("http://127.0.0.1:3000"))


if __name__ == "__main__":
    unittest.main()
