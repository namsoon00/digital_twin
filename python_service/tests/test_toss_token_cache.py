import unittest
from pathlib import Path
import sys
from unittest import mock
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.accounts import AccountConfig
from digital_twin.infrastructure.toss_snapshots import TossProvider


class TossTokenCacheTests(unittest.TestCase):
    def account(self):
        return AccountConfig("main", "메인", "toss", "https://example.test", "client-id", "secret", "", [])

    def test_reuses_token_until_refresh_window(self):
        token_calls = []
        clock = [1000.0]

        def fake_http_json(method, url, headers, body=None, timeout=12):
            token_calls.append(url)
            return {"access_token": "token-" + str(len(token_calls)), "expires_in": 3600}

        cache = {}
        provider = TossProvider(self.account(), token_cache=cache, now_fn=lambda: clock[0])
        with mock.patch("digital_twin.infrastructure.toss_snapshots.http_json", side_effect=fake_http_json):
            first = provider.fetch_access_token()
            second = provider.fetch_access_token()

        self.assertEqual("token-1", first)
        self.assertEqual("token-1", second)
        self.assertEqual(1, len(token_calls))
        self.assertEqual(1, provider.diagnostics_payload()["toss"]["tokenCacheHits"])
        self.assertTrue(provider.diagnostics_payload()["toss"]["tokenExpiresAt"])

    def test_refreshes_before_expiration_window(self):
        token_calls = []
        clock = [1000.0]

        def fake_http_json(method, url, headers, body=None, timeout=12):
            token_calls.append(url)
            return {"access_token": "token-" + str(len(token_calls)), "expires_in": 100}

        provider = TossProvider(self.account(), token_cache={}, now_fn=lambda: clock[0])
        with mock.patch("digital_twin.infrastructure.toss_snapshots.http_json", side_effect=fake_http_json):
            self.assertEqual("token-1", provider.fetch_access_token())
            clock[0] = 1091.0
            self.assertEqual("token-2", provider.fetch_access_token())

        self.assertEqual(2, len(token_calls))

    def test_401_forces_refresh_even_when_cached_token_is_valid(self):
        token_calls = []
        account_calls = []

        def fake_http_json(method, url, headers, body=None, timeout=12):
            path = urllib.parse.urlparse(url).path
            if path == "/oauth2/token":
                token_calls.append(url)
                return {"access_token": "token-" + str(len(token_calls)), "expires_in": 3600}
            if path == "/api/v1/accounts":
                account_calls.append(headers.get("Authorization"))
                if len(account_calls) == 1:
                    raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
                return {"result": []}
            return {}

        provider = TossProvider(self.account(), token_cache={})
        with mock.patch("digital_twin.infrastructure.toss_snapshots.http_json", side_effect=fake_http_json), \
                mock.patch("digital_twin.infrastructure.toss_snapshots.time.sleep", return_value=None):
            token = provider.fetch_access_token()
            payload, refreshed = provider.token_request("accounts", "GET", "https://example.test/api/v1/accounts", token)

        self.assertEqual({"result": []}, payload)
        self.assertEqual("token-2", refreshed)
        self.assertEqual(["Bearer token-1", "Bearer token-2"], account_calls)
        self.assertEqual(2, len(token_calls))
        self.assertEqual(1, provider.diagnostics_payload()["toss"]["authRefreshes"])
