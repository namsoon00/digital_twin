import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from digital_twin.infrastructure.news_sources import NewsSourceGateway
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.modules.portfolio.domain.portfolio import Position
from digital_twin.modules.market_data.application.external_data.collection_recovery_service import ExternalCollectionRecoveryService
from digital_twin.modules.market_data.application.external_data.collection_service import ExternalDataCollectionService
from digital_twin.modules.market_data.application.external_data.contracts import CollectionJob, CollectionPartition, DatasetDescriptor, ExternalSubject
from digital_twin.modules.market_data.application.external_data.registry import ExternalDatasetRegistry


NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


class Adapter:
    def __init__(self, dataset, setting=""):
        self.descriptor = DatasetDescriptor(dataset, dataset, "fixture", 900, 3600, enabled_setting=setting)

    def partitions(self, subjects, settings):
        subject = ExternalSubject("release:bls-statistics") if self.descriptor.dataset_id == "official.bls-statistics" else subjects[0]
        return [CollectionPartition(self.descriptor.dataset_id, subject.subject_key, subject)]


def job(dataset="issuer.ir_documents", symbol="AAPL"):
    return CollectionJob(dataset, symbol, "fixture", 50, ExternalSubject(symbol, symbol=symbol))


class CollectionRouteRecoveryTests(unittest.TestCase):
    def service(self, settings=None, adapters=None):
        store = MagicMock()
        store.request_recovery_partition.return_value = {"status": "scheduled"}
        registry = ExternalDatasetRegistry(adapters or [Adapter("sec.submissions"), Adapter("opendart.disclosures"), Adapter("official.bls-statistics")])
        return ExternalCollectionRecoveryService(registry, store, settings or {}, lambda: NOW), store

    def test_official_routes_retain_subject_and_do_not_replace_original(self):
        service, store = self.service({"externalSecContactEmail": "test@example.org"})
        for source_job, expected, subject in [(job(), "sec.submissions", "AAPL"),
                (job(symbol="000660"), "opendart.disclosures", "000660"),
                (job("official.bls-release", "cpi"), "official.bls-statistics", "release:bls-statistics")]:
            result = service.recover(source_job, {"status": "error"})
            route = result["routes"][0]
            self.assertEqual(expected, route["datasetId"])
            self.assertEqual(subject, store.request_recovery_partition.call_args.args[1].subject.subject_key)
            self.assertFalse(route["replacesOriginal"])
            self.assertFalse(result["originalSourceRecovered"])

    def test_disabled_unconfigured_and_unregistered_sources_do_not_schedule(self):
        service, store = self.service()
        self.assertEqual("configuration-required", service.recover(job(), {"status": "error"})["routes"][0]["status"])
        service, disabled_store = self.service({"enabled": "0"}, [Adapter("sec.submissions", "enabled")])
        self.assertEqual("disabled", service.recover(job(), {"status": "error"})["routes"][0]["status"])
        self.assertEqual("unsupported", service.recover(job("official.bls-release"), {"status": "error"})["routes"][0]["status"])
        store.request_recovery_partition.assert_not_called()
        disabled_store.request_recovery_partition.assert_not_called()

    def test_only_source_gaps_trigger_recovery(self):
        service, store = self.service()
        for result in [{"status": "success", "dataUsable": True}, {"status": "disabled"},
                       {"status": "deferred", "reason": "daily-budget"},
                       {"status": "error", "diagnostics": {"stage": "store.complete-observation"}}]:
            self.assertEqual({}, service.recover(job("official.bls-release"), result))
        store.request_recovery_partition.assert_not_called()
        for result in [{"status": "deferred", "reason": "access-denied"},
                       {"status": "success", "dataUsable": False}, {"status": "no-data"}]:
            self.assertEqual("scheduled", service.recover(job("official.bls-release"), result)["routes"][0]["status"])

    def test_existing_route_status_remains_visible(self):
        service, store = self.service()
        for status in ["fresh", "leased", "retry-scheduled", "already-pending"]:
            store.request_recovery_partition.return_value = {"status": status}
            route = service.recover(job("official.bls-release"), {"status": "error"})["routes"][0]
            self.assertEqual(status, route["status"])
            self.assertFalse(route["replacesOriginal"])

    def test_recovery_failure_cannot_rewrite_completed_collection(self):
        service = ExternalDataCollectionService({}, ExternalDatasetRegistry(), MagicMock())
        service._process_job = MagicMock(return_value={"status": "success", "dataUsable": False})
        service.collection_recovery.recover = MagicMock(side_effect=RuntimeError("fixture"))
        result = service._process_provider_jobs([job()])[0]
        self.assertEqual("success", result["status"])
        self.assertEqual("error", result["collectionRecovery"]["status"])
        service.store.fail_job.assert_not_called()

    def test_gdelt_transport_honors_own_budget_including_explicit_override(self):
        url = "https://api.gdeltproject.org/api/v2/doc/doc?query=Apple"
        for settings, expected in [({}, 20), ({"newsCollectionGdeltTimeoutSeconds": "11"}, 11)]:
            budget = MagicMock()
            budget.call.side_effect = lambda fetch: fetch()
            gateway = NewsSourceGateway(settings=settings, request_budget=budget)
            with patch("digital_twin.infrastructure.news_sources.default_json_fetcher", return_value={"articles": []}) as fetch:
                gateway.fetch_json(url)
                self.assertEqual(expected, fetch.call_args.kwargs["timeout"])
                self.assertEqual(expected, gateway.provider_timeout_seconds("gdelt"))
                budget.call.assert_called_once()

    def test_other_provider_keeps_its_transport_limit(self):
        gateway = NewsSourceGateway({"newsCollectionTimeoutSeconds": "7", "newsCollectionGdeltTimeoutSeconds": "20"})
        with patch("digital_twin.infrastructure.news_sources.default_json_fetcher", return_value={}) as fetch:
            gateway.fetch_json("https://example.org/feed")
            self.assertEqual(7, fetch.call_args.kwargs["timeout"])

    def test_legacy_news_path_shares_gdelt_timeout_and_preserves_injected_transport(self):
        provider = ExternalSignalProvider({"externalApiTimeoutSeconds": "3"}, cache=MagicMock(), evidence_store=MagicMock())
        provider.news_request_budget = MagicMock()
        provider.news_request_budget.call.side_effect = lambda fetch: fetch()
        with patch("digital_twin.infrastructure.external_signal_provider_core.default_json_fetcher", return_value={"articles": []}) as fetch:
            provider.fetch_gdelt_news("AAPL", Position("AAPL", "Apple", market="US"))
            self.assertEqual(20, fetch.call_args.kwargs["timeout"])
        injected = MagicMock(return_value={"articles": []})
        provider = ExternalSignalProvider({"externalApiTimeoutSeconds": "3"}, cache=MagicMock(), evidence_store=MagicMock(), fetch_json=injected)
        provider.fetch_gdelt_news("AAPL", Position("AAPL", "Apple", market="US"))
        injected.assert_called_once()
