import unittest
from email.message import Message

from digital_twin.infrastructure.share_access import (
    SHARE_ROLE_OWNER,
    SHARE_ROLE_VIEWER,
    authenticate_share_token,
    direct_loopback_request,
    issue_share_session,
    share_access_from_cookie,
    share_mode_enabled,
    share_session_cookie,
    verify_share_session,
)


class ShareAccessTests(unittest.TestCase):
    def environment(self):
        return {
            "SHARE_VIEW_TOKEN": "viewer-secret",
            "SHARE_OWNER_TOKEN": "owner-secret",
            "SHARE_SESSION_SECRET": "session-secret",
            "SHARE_SESSION_DAYS": "30",
        }

    def test_viewer_and_owner_tokens_create_distinct_access(self):
        environment = self.environment()

        viewer, viewer_token = authenticate_share_token("viewer-secret", SHARE_ROLE_VIEWER, environment)
        owner, owner_token = authenticate_share_token("owner-secret", SHARE_ROLE_OWNER, environment)

        self.assertTrue(share_mode_enabled(environment))
        self.assertEqual(SHARE_ROLE_VIEWER, viewer.role)
        self.assertFalse(viewer.writable)
        self.assertEqual("viewer-secret", viewer_token)
        self.assertEqual(SHARE_ROLE_OWNER, owner.role)
        self.assertTrue(owner.writable)
        self.assertEqual("owner-secret", owner_token)

    def test_signed_cookie_does_not_store_raw_token_and_survives_followup_requests(self):
        environment = self.environment()
        owner, token = authenticate_share_token("owner-secret", SHARE_ROLE_OWNER, environment)
        session = issue_share_session(owner, token, environment)
        cookie = share_session_cookie(session, secure=True, environment=environment)

        restored = share_access_from_cookie(cookie, environment)

        self.assertNotIn("owner-secret", session)
        self.assertNotIn("owner-secret", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("Max-Age=2592000", cookie)
        self.assertEqual(SHARE_ROLE_OWNER, restored.role)
        self.assertTrue(restored.writable)

    def test_token_rotation_invalidates_existing_session(self):
        environment = self.environment()
        viewer, token = authenticate_share_token("viewer-secret", SHARE_ROLE_VIEWER, environment)
        session = issue_share_session(viewer, token, environment)
        rotated = dict(environment, SHARE_VIEW_TOKEN="rotated-viewer-secret")

        restored = verify_share_session(session, rotated)

        self.assertFalse(restored.authenticated)

    def test_role_specific_link_does_not_accept_the_other_role_token(self):
        environment = self.environment()

        owner_as_viewer, _ = authenticate_share_token("owner-secret", SHARE_ROLE_VIEWER, environment)
        viewer_as_owner, _ = authenticate_share_token("viewer-secret", SHARE_ROLE_OWNER, environment)

        self.assertFalse(owner_as_viewer.authenticated)
        self.assertFalse(viewer_as_owner.authenticated)

    def test_legacy_share_token_remains_viewer_only(self):
        environment = {
            "SHARE_TOKEN": "legacy-secret",
            "SHARE_SESSION_SECRET": "session-secret",
        }

        access, _ = authenticate_share_token("legacy-secret", SHARE_ROLE_VIEWER, environment)

        self.assertEqual(SHARE_ROLE_VIEWER, access.role)
        self.assertFalse(access.writable)

    def test_direct_loopback_request_excludes_cloudflare_forwarded_traffic(self):
        local_headers = Message()
        forwarded_headers = Message()
        forwarded_headers["CF-Connecting-IP"] = "203.0.113.10"
        forwarded_headers["X-Forwarded-Proto"] = "https"

        self.assertTrue(direct_loopback_request(("127.0.0.1", 51234), local_headers))
        self.assertTrue(direct_loopback_request(("::1", 51234), local_headers))
        self.assertFalse(direct_loopback_request(("127.0.0.1", 51234), forwarded_headers))
        self.assertFalse(direct_loopback_request(("203.0.113.10", 51234), local_headers))


if __name__ == "__main__":
    unittest.main()
