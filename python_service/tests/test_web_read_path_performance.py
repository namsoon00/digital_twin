import unittest
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin.domain.notifications import NotificationJob
from digital_twin.infrastructure import web_server


class _NotificationListStore:
    def __init__(self, job):
        self.job = job
        self.receipt_queries = 0

    def recent_list_page_with_summary(self, **_kwargs):
        return [self.job], 1, {"done": 1}

    def receipt_states(self, *_args, **_kwargs):
        self.receipt_queries += 1
        raise AssertionError("joined receipt state must be reused")

    def inbox_summary(self, *_args, **_kwargs):
        return {"total": 1, "unread": 0, "important": 1, "actionRequired": 0}


class WebReadPathPerformanceTests(unittest.TestCase):
    def assert_stale_cache_cannot_preserve_an_embedded_fresh_claim(self):
        class Cache:
            @staticmethod
            def get_or_refresh(*_args, **_kwargs):
                return {
                    "payload": {"status": "ok", "dataFreshness": {"status": "fresh"}},
                    "stale": True,
                    "ageSeconds": 181,
                    "refreshing": True,
                    "lastSuccessAt": "2026-09-03T00:00:00Z",
                    "lastError": "dependency timeout",
                }

        payload = web_server.cached_api_payload(Cache(), "key", lambda: {})

        self.assertEqual("stale", payload["dataFreshness"]["status"])
        self.assertEqual("fresh", payload["dataFreshness"]["sourceStatus"])
        self.assertEqual("stale", payload["effectiveFreshness"]["status"])
        self.assertTrue(payload["readCache"]["stale"])

    def test_notification_list_reads_runtime_settings_once(self):
        job = NotificationJob(
            job_id="job-1",
            account_id="default",
            account_label="기본 계정",
            message_type="investmentInsight",
            text="판단 메시지",
            context={
                "symbol": "005930",
                "notificationReceipt": {
                    "readAt": "2026-08-28T00:00:00Z",
                    "acknowledgedAt": "",
                    "important": True,
                    "receiptUpdatedAt": "2026-08-28T00:00:00Z",
                },
            },
            status="done",
            created_at="2026-08-28T00:00:00Z",
            updated_at="2026-08-28T00:00:00Z",
        )
        store = _NotificationListStore(job)
        settings = {
            "notificationProcessingStaleMinutes": "2",
            "_skipOperationalSchemaBootstrap": "1",
        }

        with patch.object(web_server, "operational_read_settings", return_value=settings) as read_settings:
            with patch.object(web_server, "notification_queue_store", return_value=store):
                payload = web_server.notification_jobs_payload({"limit": ["20"]})

        self.assertEqual(1, read_settings.call_count)
        self.assertEqual(0, store.receipt_queries)
        self.assertEqual(1, len(payload["jobs"]))
        self.assertTrue(payload["jobs"][0]["important"])

    def test_notification_queue_store_disables_read_side_bootstrap(self):
        marker = object()
        settings = {"notificationProcessingStaleMinutes": "2"}

        with patch.object(web_server.stores, "notification_job_store", return_value=marker) as job_store:
            self.assertIs(marker, web_server.notification_queue_store(settings))

        configured = job_store.call_args.args[0]
        self.assertEqual("1", configured["_skipNotificationRuleDefaultsSeed"])
        self.assertEqual("1", configured["_skipOperationalSchemaBootstrap"])
        self.assertEqual("2", configured["notificationProcessingStaleMinutes"])

    def test_bootstrap_app_store_uses_read_only_operational_settings(self):
        marker = object()
        settings = {"_skipOperationalSchemaBootstrap": "1"}
        with patch.object(web_server, "operational_read_settings", return_value=settings):
            with patch.object(web_server.stores, "app_store", return_value=marker) as app_store:
                self.assertIs(marker, web_server.app_store())
        app_store.assert_called_once_with(settings)

    def test_heavy_console_reads_use_non_blocking_stale_read_models(self):
        marker = {"status": "warming"}
        calls = []

        def capture(cache, key, loader, **kwargs):
            calls.append((cache, key, loader, kwargs))
            return marker

        with patch.object(web_server, "cached_api_payload", side_effect=capture):
            self.assertIs(marker, web_server.console_portfolio_api_payload({"accountId": ["default"]}, "positions"))
            self.assertIs(marker, web_server.console_market_instruments_api_payload({}))
            self.assertIs(marker, web_server.console_market_evidence_api_payload({"limit": ["8"]}))
            self.assertIs(marker, web_server.investment_brain_hypothesis_workspace_api_payload({"view": ["summary"]}))

        self.assertEqual(4, len(calls))
        self.assertTrue(all(call[3].get("blocking_first_load") is False for call in calls))
        self.assertEqual(
            {
                web_server.PORTFOLIO_CONSOLE_READ_MODEL,
                web_server.MARKET_INSTRUMENTS_READ_MODEL,
                web_server.MARKET_EVIDENCE_READ_MODEL,
                web_server.HYPOTHESIS_WORKSPACE_READ_MODEL,
            },
            {call[0] for call in calls},
        )
        self.assert_stale_cache_cannot_preserve_an_embedded_fresh_claim()

    def test_ontology_catalog_builds_storage_service_only_inside_cache_loader(self):
        marker = {"status": "warming"}
        with patch.object(web_server, "cached_api_payload", return_value=marker) as cached:
            with patch.object(web_server, "ontology_repository_from_settings", side_effect=AssertionError("must stay lazy")):
                self.assertIs(marker, web_server.ontology_catalog_api_payload("summary", {}))
        self.assertIs(web_server.ONTOLOGY_CATALOG_SUMMARY_READ_MODEL, cached.call_args.args[0])
        self.assertFalse(cached.call_args.kwargs["blocking_first_load"])

    def test_reasoning_completion_uses_production_delivery_deployment(self):
        settings = {"reasoningEngineV2DeploymentId": "v2-candidate"}

        class Registry:
            @staticmethod
            def control():
                return SimpleNamespace(
                    active_deployment_id="v2-active",
                    delivery_deployment_id="v2-delivery",
                )

        class Jobs:
            calls = []

            @classmethod
            def market_observation_completion_summary(cls, deployment_id, limit=12):
                cls.calls.append((deployment_id, limit))
                return {"deploymentId": deployment_id, "receiptCount": 2}

        with patch.object(web_server, "operational_read_settings", return_value=settings):
            with patch.object(web_server, "build_ontology_reasoning_queue_probe", return_value=lambda: {}):
                with patch.object(web_server.stores, "reasoning_engine_registry_store", return_value=Registry()):
                    with patch.object(web_server.stores, "reasoning_engine_job_store", return_value=Jobs()):
                        payload = web_server.ontology_reasoning_status_payload()

        self.assertEqual(
            {"deploymentId": "v2-delivery", "receiptCount": 2},
            payload["marketObservationReasoningCompletion"],
        )
        self.assertEqual([("v2-delivery", 12)], Jobs.calls)


if __name__ == "__main__":
    unittest.main()
