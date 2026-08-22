import unittest
from unittest.mock import patch

from digital_twin.infrastructure import web_server


class FakeReadModelCache:
    def __init__(self, snapshot):
        self.value = dict(snapshot)
        self.scheduled = 0

    def snapshot(self, _key):
        return dict(self.value)

    def refresh_async(self, _key, _loader):
        self.scheduled += 1
        return True


def empty_snapshot(**overrides):
    payload = {
        "payload": {},
        "hasData": False,
        "stale": False,
        "ageSeconds": 0,
        "lastSuccessAt": "",
        "lastAttemptAt": "",
        "lastError": "",
        "refreshing": False,
        "retryAfterSeconds": 0,
    }
    payload.update(overrides)
    return payload


class OntologyDiagnosticsApiTests(unittest.TestCase):
    def test_uncached_full_request_returns_warming_without_running_typedb_inline(self):
        cache = FakeReadModelCache(empty_snapshot())
        with patch.object(web_server, "ONTOLOGY_DIAGNOSTICS_READ_MODEL", cache), \
                patch.object(web_server, "ontology_diagnostics_source_payload") as source:
            payload = web_server.ontology_diagnostics_payload({"accountId": ["default"]})

        self.assertEqual("warming", payload["status"])
        self.assertTrue(payload["cache"]["refreshing"])
        self.assertEqual(1, cache.scheduled)
        source.assert_not_called()

    def test_cached_full_request_returns_payload_and_refreshes_when_stale(self):
        cache = FakeReadModelCache(empty_snapshot(
            payload={"status": "ok", "rulebox": {"ruleCount": 118}},
            hasData=True,
            stale=True,
            ageSeconds=75,
            lastSuccessAt="2026-08-22T00:00:00Z",
        ))
        with patch.object(web_server, "ONTOLOGY_DIAGNOSTICS_READ_MODEL", cache):
            payload = web_server.ontology_diagnostics_payload({"accountId": ["default"]})

        self.assertEqual("ok", payload["status"])
        self.assertEqual(118, payload["rulebox"]["ruleCount"])
        self.assertTrue(payload["cache"]["stale"])
        self.assertTrue(payload["cache"]["refreshing"])
        self.assertEqual(1, cache.scheduled)

    def test_explicit_refresh_is_async_even_when_cache_is_fresh(self):
        cache = FakeReadModelCache(empty_snapshot(
            payload={"status": "ok"},
            hasData=True,
        ))
        with patch.object(web_server, "ONTOLOGY_DIAGNOSTICS_READ_MODEL", cache):
            payload = web_server.ontology_diagnostics_payload({
                "accountId": ["default"],
                "refresh": ["1"],
            })

        self.assertEqual("ok", payload["status"])
        self.assertTrue(payload["cache"]["refreshing"])
        self.assertEqual(1, cache.scheduled)

    def test_quick_request_does_not_schedule_full_diagnostics(self):
        cache = FakeReadModelCache(empty_snapshot())
        with patch.object(web_server, "ONTOLOGY_DIAGNOSTICS_READ_MODEL", cache):
            payload = web_server.ontology_diagnostics_payload({"quick": ["1"]})

        self.assertEqual("deferred", payload["status"])
        self.assertEqual(0, cache.scheduled)


if __name__ == "__main__":
    unittest.main()
