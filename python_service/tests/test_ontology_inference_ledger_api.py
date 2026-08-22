import unittest
from unittest.mock import Mock, patch

from digital_twin.infrastructure import web_server


class FakeReadModelCache:
    def __init__(self, snapshot):
        self.value = dict(snapshot)
        self.scheduled = 0
        self.refreshed = 0

    def snapshot(self, _key):
        return dict(self.value)

    def refresh_async(self, _key, _loader):
        self.scheduled += 1
        self.value["refreshing"] = True
        return True

    def refresh(self, _key, _loader):
        self.refreshed += 1
        return dict(self.value)


class ProjectionRunStore:
    def execution_trace(self, **_kwargs):
        return {"status": "ok", "runCount": 1, "runs": []}

    def rule_runtime_summary(self, **_kwargs):
        return {"status": "ok", "sampleCount": 0, "ruleCount": 0, "rules": []}

    def rule_result_slot_summary(self, **_kwargs):
        return {"status": "ok", "slotCount": 0, "symbolCount": 0, "symbols": []}


class OntologyInferenceLedgerApiTests(unittest.TestCase):
    def test_rule_audit_summary_omits_domain_manifest(self):
        audit = web_server.compact_rule_audit({
            "ruleCount": 1,
            "rules": [{
                "ruleId": "rule-1",
                "status": "observed",
                "p95DurationMs": 12,
                "executionProfile": {"executionStage": "core", "large": [1, 2, 3]},
                "domainManifest": {"large": [1, 2, 3]},
            }],
        })

        self.assertEqual("summary", audit["detailLevel"])
        self.assertEqual("core", audit["rules"][0]["executionProfile"]["executionStage"])
        self.assertNotIn("domainManifest", audit["rules"][0])
        self.assertNotIn("large", audit["rules"][0]["executionProfile"])

    def test_execution_history_summary_keeps_visible_fields_and_omits_raw_detail(self):
        history = web_server.compact_reasoning_execution_history({
            "status": "ok",
            "runCount": 1,
            "runs": [{
                "runId": "run-1",
                "lane": "CORE_REASONING",
                "privateAudit": {"large": [1, 2, 3]},
                "stages": [{
                    "stageKey": "rulebox-selection",
                    "status": "ok",
                    "durationMs": 12,
                    "detail": {
                        "candidateRuleCount": 10,
                        "executedRuleCount": 8,
                        "deferredRuleCount": 2,
                        "rawCandidates": [{"large": True}],
                    },
                }],
                "rules": [{
                    "ruleId": "rule-1",
                    "status": "ok",
                    "durationMs": 4,
                    "detail": {"large": [1, 2, 3]},
                }],
            }],
        })

        self.assertEqual("summary", history["detailLevel"])
        run = history["runs"][0]
        self.assertNotIn("privateAudit", run)
        self.assertEqual(10, run["stages"][0]["detail"]["candidateRuleCount"])
        self.assertNotIn("rawCandidates", run["stages"][0]["detail"])
        self.assertNotIn("detail", run["rules"][0])

    def test_default_request_returns_mysql_read_model_and_schedules_background_refresh(self):
        cache = FakeReadModelCache({
            "payload": {},
            "hasData": False,
            "stale": False,
            "ageSeconds": 0,
            "lastSuccessAt": "",
            "lastAttemptAt": "",
            "lastError": "",
            "refreshing": False,
            "retryAfterSeconds": 0,
        })
        repository = Mock()
        with patch.object(web_server, "ONTOLOGY_INFERENCE_LEDGER_READ_MODEL", cache), \
                patch.object(web_server, "runtime_settings", return_value={}), \
                patch.object(web_server, "ontology_repository_from_settings", return_value=repository), \
                patch.object(web_server.stores, "ontology_projection_run_store", return_value=ProjectionRunStore()):
            payload = web_server.ontology_inference_ledger_api_payload({})

        self.assertEqual("degraded", payload["status"])
        self.assertTrue(payload["usable"])
        self.assertEqual(1, payload["executionHistory"]["runCount"])
        self.assertEqual(1, cache.scheduled)
        repository.rulebox_snapshot.assert_not_called()

    def test_direct_request_reports_unavailable_when_dependency_refresh_fails(self):
        cache = FakeReadModelCache({
            "payload": {},
            "hasData": False,
            "stale": False,
            "ageSeconds": 0,
            "lastSuccessAt": "",
            "lastAttemptAt": "2026-08-20T00:00:00Z",
            "lastError": "TypeDB unavailable",
            "refreshing": False,
            "retryAfterSeconds": 20,
        })
        empty_store = ProjectionRunStore()
        empty_store.execution_trace = lambda **_kwargs: {"status": "ok", "runCount": 0, "runs": []}
        with patch.object(web_server, "ONTOLOGY_INFERENCE_LEDGER_READ_MODEL", cache), \
                patch.object(web_server, "runtime_settings", return_value={}), \
                patch.object(web_server.stores, "ontology_projection_run_store", return_value=empty_store):
            payload = web_server.ontology_inference_ledger_api_payload({"direct": ["1"]})

        self.assertEqual("unavailable", payload["status"])
        self.assertFalse(payload["usable"])
        self.assertTrue(payload["retryable"])
        self.assertIn("TypeDB unavailable", payload["error"])
        self.assertEqual(1, cache.refreshed)


if __name__ == "__main__":
    unittest.main()
