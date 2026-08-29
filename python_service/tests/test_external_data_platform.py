import unittest
import time
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from digital_twin.application.external_data.collection_service import ExternalDataCollectionService
from digital_twin.application.external_data.contracts import (
    CollectionJob,
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
    FollowupCollectionRequest,
    SourceObservation,
)
from digital_twin.application.external_data.fact_transition_service import ExternalFactTransitionService
from digital_twin.application.external_data.read_model_service import ExternalSignalsReadModelService
from digital_twin.application.external_data.registry import ExternalDatasetRegistry
from digital_twin.infrastructure.external_api.legacy_import import LegacyExternalSignalImporter
from digital_twin.infrastructure.external_api.adapters.base import empty_signals, legacy_provider, position_for
from digital_twin.infrastructure.external_api.adapters.opendart import OpenDartDisclosureAdapter
from digital_twin.infrastructure.external_api.adapters.sec import SecSubmissionsAdapter
from digital_twin.infrastructure.external_api.adapters.yfinance import (
    YFinanceProfileAdapter,
    unusable_modules_error_message,
)
from digital_twin.infrastructure.schedulers import external_data_failure_requires_alert


NOW = datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc)


class StaticAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="test.market",
        provider_id="test-provider",
        capability="polling-snapshot",
        cadence_seconds=600,
        freshness_seconds=900,
        priority=70,
    )

    def partitions(self, subjects, _settings):
        return [
            CollectionPartition(self.descriptor.dataset_id, subject.subject_key, subject, self.descriptor.priority)
            for subject in subjects
        ]

    def fetch(self, job, _settings):
        return SourceObservation(
            dataset_id=self.descriptor.dataset_id,
            provider_id=self.descriptor.provider_id,
            subject_key=job.subject.subject_key,
            source_revision="revision-1",
            source_as_of="2026-08-16T00:00:00Z",
            fetched_at="2026-08-16T00:00:01Z",
            payload={"equityQuotes": {job.subject.symbol: {"price": 100.0}}},
        )


class FailingAdapter(StaticAdapter):
    def fetch(self, job, settings):
        del job, settings
        raise RuntimeError("temporary provider response")


class ConcurrencyTrackingAdapter(StaticAdapter):
    def __init__(self):
        self.active = 0
        self.maximum_active = 0

    def fetch(self, job, settings):
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            time.sleep(0.01)
            return super().fetch(job, settings)
        finally:
            self.active -= 1


class FollowupAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="test.document",
        provider_id="test-provider",
        capability="document",
        cadence_seconds=600,
        freshness_seconds=900,
        partition_strategy="followup",
        completion_mode="once",
    )

    def partitions(self, _subjects, _settings):
        raise AssertionError("follow-up datasets must not create static partitions")

    def fetch(self, job, _settings):
        return SourceObservation(
            dataset_id=self.descriptor.dataset_id,
            provider_id=self.descriptor.provider_id,
            subject_key=job.subject.subject_key,
            source_revision=str(job.watermark.get("revision") or "document-1"),
            source_as_of="2026-08-16T00:00:00Z",
            fetched_at="2026-08-16T00:00:01Z",
            payload={"document": dict(job.watermark)},
        )


class FollowupSourceAdapter(StaticAdapter):
    def followup_requests(self, observation, _settings):
        return [FollowupCollectionRequest(
            dataset_id="test.document",
            partition_key=observation.subject_key + ":document-1",
            subject=ExternalSubject(observation.subject_key, symbol=observation.subject_key),
            watermark={"revision": "document-1"},
            priority=60,
        )]


class MemoryCollectionStore:
    def __init__(self):
        self.jobs = []
        self.completed = []
        self.events = []
        self.recorded = []
        self.followups = []
        self.current = {}
        self.failure_state = "failed"

    def list_subjects(self):
        return [ExternalSubject("NVDA", symbol="NVDA", market="US", currency="USD")]

    def sync_partitions(self, plans, _dataset_ids, now=None):
        self.jobs = [
            CollectionJob(
                descriptor.dataset_id,
                partition.partition_key,
                descriptor.provider_id,
                partition.priority,
                partition.subject,
            )
            for descriptor, partition in plans
        ]
        return len(self.jobs)

    def claim_due(self, worker_id, limit, lease_seconds, now=None):
        del worker_id, lease_seconds, now
        jobs, self.jobs = self.jobs[:limit], self.jobs[limit:]
        return [replace(job, attempt_count=job.attempt_count + 1) for job in jobs]

    def reserve_provider_call(self, descriptor, now=None):
        del descriptor, now
        return {"allowed": True}

    def current_fact(self, dataset_id, subject_key):
        del dataset_id, subject_key
        return dict(self.current)

    def fail_job(self, job, descriptor, error, next_due_at):
        del job, descriptor, error, next_due_at
        return {"state": self.failure_state}

    def complete_observation(self, job, descriptor, observation, due_at, event=None):
        self.completed.append((job, descriptor, observation, due_at))
        if event:
            self.events.append(event)
        return {"changed": True}

    def enqueue_followups(self, plans, now=None):
        del now
        for descriptor, request in plans:
            self.followups.append((descriptor, request))
        return len(plans)

    def mark_provider_success(self, descriptor):
        del descriptor

    def record_run(self, *args, **kwargs):
        self.recorded.append((args, kwargs))

    def summary(self):
        return {"datasets": [], "facts": {"count": len(self.completed)}, "providers": [], "runs24h": []}


class MemoryFactStore:
    def list_current(self, subject_keys):
        self.requested = list(subject_keys)
        return [
            {
                "datasetId": "coingecko.market",
                "subjectKey": "global",
                "payload": {"cryptoMarkets": {"bitcoin": {"priceUsd": 65000}}},
                "fetchedAt": "2026-08-16T00:00:00Z",
                "updatedAt": "2026-08-16T00:00:01Z",
                "freshnessState": "fresh",
            },
            {
                "datasetId": "yfinance.price",
                "subjectKey": "NVDA",
                "payload": {"equityQuotes": {"NVDA": {"price": 225.0}}},
                "fetchedAt": "2026-08-16T00:01:00Z",
                "updatedAt": "2026-08-16T00:01:01Z",
                "freshnessState": "stale",
            },
        ]

    def provider_statuses(self):
        return [
            {
                "datasetId": "fred.macro",
                "providerId": "fred",
                "state": "failed",
                "lastError": "timeout",
            }
        ]


class MigratedStore:
    @staticmethod
    def summary():
        return {"facts": {"count": 5}}


class RecordingCache:
    def __init__(self):
        self.payload = {"entries": {"old": {"signals": {"large": "payload"}}}}
        self.replace_count = 0

    def load(self):
        return self.payload

    def replace(self, payload):
        self.payload = dict(payload)
        self.replace_count += 1

class ExternalDataPlatformTest(unittest.TestCase):
    def test_registry_builds_only_enabled_partitions(self):
        registry = ExternalDatasetRegistry([StaticAdapter()])
        subject = ExternalSubject("NVDA", symbol="NVDA")

        partitions = registry.desired_partitions([subject], {})

        self.assertEqual(["NVDA"], [item.partition_key for item in partitions])
        self.assertEqual("test-provider", registry.adapter("test.market").descriptor.provider_id)

    def test_followup_document_work_is_durable_and_not_a_static_partition(self):
        store = MemoryCollectionStore()
        registry = ExternalDatasetRegistry([FollowupSourceAdapter(), FollowupAdapter()])
        service = ExternalDataCollectionService({}, registry, store, now_provider=lambda: NOW)

        result = service.run_once()

        self.assertEqual(1, result["results"][0]["followupCount"])
        self.assertEqual("test.document", store.followups[0][0].dataset_id)
        self.assertEqual("NVDA:document-1", store.followups[0][1].partition_key)
        self.assertNotIn("test.document", registry.static_dataset_ids({}))

    def test_collection_service_executes_vendor_fetch_outside_request_path(self):
        store = MemoryCollectionStore()
        service = ExternalDataCollectionService(
            {},
            ExternalDatasetRegistry([StaticAdapter()]),
            store,
            worker_id="test-worker",
            now_provider=lambda: NOW,
        )

        result = service.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["processedCount"])
        self.assertEqual(1, len(store.completed))
        self.assertEqual([], store.events, "initial baselines must not fan out reasoning events")

        store = MemoryCollectionStore()
        store.current = {
            "payload": {"equityQuotes": {"NVDA": {"price": 225.0}}},
            "sourceAsOf": "2026-08-16T00:00:00Z",
        }
        service = ExternalDataCollectionService(
            {},
            ExternalDatasetRegistry([FailingAdapter()]),
            store,
            worker_id="test-worker",
            now_provider=lambda: NOW,
        )

        result = service.run_once()
        failure = result["results"][0]

        self.assertEqual(1, failure["partitionFailureCount"])
        self.assertTrue(failure["hasUsablePreviousFact"])
        self.assertFalse(external_data_failure_requires_alert(failure))
        self.assertTrue(external_data_failure_requires_alert({
            **failure,
            "providerState": "circuit_open",
        }))
        self.assertTrue(external_data_failure_requires_alert({
            **failure,
            "partitionFailureCount": 2,
        }))
        self.assertTrue(external_data_failure_requires_alert({
            **failure,
            "hasUsablePreviousFact": False,
        }))
        message = unusable_modules_error_message("price", "000680.KS", {
            "errors": [{"section": "history", "message": "temporary empty response"}],
        })
        self.assertIn("000680.KS", message)
        self.assertIn("history: temporary empty response", message)

    def test_market_transition_ignores_collection_clock_and_applies_threshold(self):
        service = ExternalFactTransitionService()
        previous = {
            "sourceRevision": "one",
            "payload": {"equityQuotes": {"NVDA": {"price": 100.0}}, "fetchedAt": "old"},
        }

        unchanged = service.assess(
            "yfinance.price",
            previous,
            {"equityQuotes": {"NVDA": {"price": 100.0}}, "fetchedAt": "new"},
            "one",
        )
        small = service.assess(
            "yfinance.price",
            previous,
            {"equityQuotes": {"NVDA": {"price": 100.4}}},
            "two",
        )
        material = service.assess(
            "yfinance.price",
            previous,
            {"equityQuotes": {"NVDA": {"price": 101.0}}},
            "three",
        )

        self.assertFalse(unchanged.changed)
        self.assertTrue(small.changed)
        self.assertFalse(small.material)
        self.assertTrue(material.material)

    def test_filing_metadata_only_schedules_documents_without_reasoning_event(self):
        service = ExternalFactTransitionService()
        previous = {
            "sourceRevision": "filing-list-one",
            "payload": {"items": [{"receiptNo": "one"}]},
        }

        metadata = service.assess(
            "opendart.disclosures",
            previous,
            {"items": [{"receiptNo": "two"}]},
            "filing-list-two",
        )
        document = service.assess(
            "opendart.document",
            previous,
            {"documentText": "verified official document body"},
            "document-two",
        )

        self.assertTrue(metadata.changed)
        self.assertFalse(metadata.material)
        self.assertEqual("document-discovery", metadata.change_type)
        self.assertTrue(document.material)
        self.assertEqual("source-revision", document.change_type)

        settings = {
            "opendartApiKey": "test-key",
            "externalDartCorpCodes": "005930=00126380",
        }
        provider = legacy_provider(settings, externalDartEnabled="1", externalDartMaxSymbols="1")
        provider.fetch_json = lambda *_args, **_kwargs: {
            "status": "013",
            "message": "조회된 데이타가 없습니다.",
            "list": [],
        }
        signals = empty_signals()
        subject = ExternalSubject("005930", symbol="005930", name="삼성전자", market="KR", currency="KRW")

        provider.add_opendart(
            signals,
            [position_for(subject)],
            include_fundamentals=False,
            include_document=False,
        )

        self.assertEqual({}, signals["dartDisclosures"])
        self.assertTrue(any(
            item.get("ok") and item.get("emptyResult") and item.get("target") == "005930"
            for item in signals["statuses"]
        ))

        class EmptyProvider:
            @staticmethod
            def add_opendart(target, _positions, **_kwargs):
                target["statuses"].append({
                    "source": "OpenDART",
                    "ok": True,
                    "target": "005930",
                    "corpCode": "00126380",
                    "dataUsable": True,
                    "emptyResult": True,
                })

        job = CollectionJob(
            "opendart.disclosures",
            "005930",
            "opendart",
            85,
            subject,
        )
        with patch(
            "digital_twin.infrastructure.external_api.adapters.opendart.legacy_provider",
            return_value=EmptyProvider(),
        ):
            result = OpenDartDisclosureAdapter().fetch(job, settings)

        self.assertEqual({"dartDisclosures": {}}, result.payload)
        self.assertEqual("no-disclosures", result.source_revision)
        self.assertEqual("00126380", result.watermark["corpCode"])
        self.assertTrue(result.quality["dataUsable"])
        self.assertTrue(result.quality["emptyResult"])

        maintenance_provider = legacy_provider(
            {"opendartApiKey": "test-key"},
            externalDartEnabled="1",
            externalDartMaxSymbols="1",
        )
        maintenance_provider.fetch_bytes = lambda *_args, **_kwargs: (
            b"<?xml version='1.0' encoding='UTF-8'?><result>"
            b"<status>800</status><message>service maintenance</message></result>"
        )
        maintenance_signals = empty_signals()
        maintenance_provider.add_opendart(
            maintenance_signals,
            [position_for(ExternalSubject("000680", symbol="000680", name="LS Networks", market="KR"))],
            include_fundamentals=False,
            include_document=False,
        )
        self.assertTrue(any(
            not item.get("ok") and "OpenDART 800 service maintenance" in item.get("message", "")
            for item in maintenance_signals["statuses"]
        ))

        captured_settings = {}

        class RecoveredCodeProvider:
            @staticmethod
            def add_opendart(target, _positions, **_kwargs):
                target["dartDisclosures"]["000680"] = {
                    "provider": "OpenDART",
                    "corpCode": "00104698",
                    "corpName": "LS Networks",
                    "receiptNo": "20260827000403",
                    "receiptDate": "20260827",
                    "items": [],
                }

        def recovered_provider(configured, **_overrides):
            captured_settings.update(configured)
            return RecoveredCodeProvider()

        recovered_job = CollectionJob(
            "opendart.disclosures",
            "000680",
            "opendart",
            85,
            ExternalSubject("000680", symbol="000680", name="LS Networks", market="KR"),
        )
        with patch(
            "digital_twin.infrastructure.external_api.adapters.opendart.legacy_provider",
            side_effect=recovered_provider,
        ):
            recovered = OpenDartDisclosureAdapter(
                lambda: {"000680": "00104698"}
            ).fetch(recovered_job, {"opendartApiKey": "test-key"})

        self.assertIn("000680=00104698", captured_settings["externalDartCorpCodes"])
        self.assertEqual("00104698", recovered.watermark["corpCode"])

    def test_same_provider_jobs_are_serialized_while_provider_groups_are_parallelizable(self):
        adapter = ConcurrencyTrackingAdapter()
        store = MemoryCollectionStore()
        store.list_subjects = lambda: [
            ExternalSubject("NVDA", symbol="NVDA"),
            ExternalSubject("AAPL", symbol="AAPL"),
        ]
        service = ExternalDataCollectionService(
            {"externalDataWorkerConcurrency": "3"},
            ExternalDatasetRegistry([adapter]),
            store,
            worker_id="test-worker",
            now_provider=lambda: NOW,
        )

        result = service.run_once()

        self.assertEqual(2, result["successCount"])
        self.assertEqual(1, adapter.maximum_active)

    def test_sec_partitions_skip_unmapped_symbols_without_compliant_contact(self):
        adapter = SecSubmissionsAdapter()
        subjects = [
            ExternalSubject("AAPL", symbol="AAPL", market="US", currency="USD"),
            ExternalSubject("PLTR", symbol="PLTR", market="US", currency="USD"),
        ]

        without_contact = adapter.partitions(subjects, {"externalSecUserAgent": "OrbitAlpha/1.0 local-contact"})
        with_contact = adapter.partitions(subjects, {"externalSecUserAgent": "OrbitAlpha/1.0 owner@example.com"})

        self.assertEqual(["AAPL", "PLTR"], [item.partition_key for item in without_contact])
        self.assertEqual(["AAPL", "PLTR"], [item.partition_key for item in with_contact])

    def test_read_model_merges_facts_and_surfaces_stale_and_failed_sources(self):
        read_model = ExternalSignalsReadModelService(MemoryFactStore())

        signals = read_model.signals_for_subjects(["NVDA"])

        self.assertEqual(225.0, signals["equityQuotes"]["NVDA"]["price"])
        self.assertEqual(65000, signals["cryptoMarkets"]["bitcoin"]["priceUsd"])
        self.assertEqual(2, signals["externalDataPlatform"]["factCount"])
        self.assertEqual(["yfinance.price"], signals["externalDataPlatform"]["staleDatasets"])
        self.assertTrue(any(item.get("datasetId") == "fred.macro" and not item.get("ok") for item in signals["statuses"]))


if __name__ == "__main__":
    unittest.main()
