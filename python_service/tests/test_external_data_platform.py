import unittest
import time
import json
import urllib.parse
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from digital_twin.modules.market_data.application.external_data.collection_service import ExternalDataCollectionService
from digital_twin.modules.market_data.application.external_data.configuration_recovery_service import ExternalDataConfigurationRecoveryService
from digital_twin.modules.market_data.application.external_data.contracts import CollectionJob, CollectionPartition, DatasetDescriptor, ExternalSubject, FollowupCollectionRequest, SourceObservation
from digital_twin.modules.market_data.application.external_data.fact_transition_service import ExternalFactTransitionService
from digital_twin.modules.market_data.application.external_data.read_model_service import ExternalSignalsReadModelService, merge_external_signal_read_models
from digital_twin.modules.market_data.application.external_data.registry import ExternalDatasetRegistry
from digital_twin.modules.market_data.domain.event_types import EXTERNAL_OBSERVATION_RECORDED
from digital_twin.modules.market_data.domain.external_data_contracts import normalized_availability
from digital_twin.modules.market_data.domain.external_dataset_catalog import external_dataset_catalog
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_external_abox import interest_rate_signal_ids
from digital_twin.modules.reasoning.domain.knowledge_world_projection import build_knowledge_world_graph
from digital_twin.modules.reasoning.domain.market_world_projection import build_market_world_graph
from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_external_signals_for_ontology
from digital_twin.modules.reasoning.domain.ontology_schema import add_entity
from digital_twin.modules.reasoning.domain.ontology_validator import validate_ontology
from digital_twin.modules.reasoning.domain.ontology_worlds import knowledge_world, market_world
from digital_twin.modules.portfolio.domain.portfolio import Position
from digital_twin.modules.reasoning.domain.portfolio_ontology_market_concepts import add_official_daily_price_concepts
from digital_twin.modules.reasoning.domain.portfolio_ontology_company_concepts import add_company_knowledge_concepts
from digital_twin.modules.reasoning.domain.portfolio_ontology_reference_concepts import (
    add_official_corporate_action_concepts,
    add_official_market_index_concepts,
    add_official_security_reference_concepts,
)
from digital_twin.infrastructure.external_api.legacy_import import LegacyExternalSignalImporter
from digital_twin.infrastructure.external_api.adapters.base import empty_signals, legacy_provider, position_for
from digital_twin.infrastructure.external_api.adapters import default_external_dataset_registry
from digital_twin.infrastructure.external_api.adapters.ecos import EcosMacroAdapter
from digital_twin.infrastructure.external_api.adapters.kosis import KosisIndustryIndicatorAdapter
from digital_twin.infrastructure.external_api.adapters.krx import KrxMarketIndexAdapter
from digital_twin.infrastructure.external_api.adapters.opendart import (
    OpenDartCompanyFactsAdapter,
    OpenDartDisclosureAdapter,
)
from digital_twin.infrastructure.external_api.adapters.public_data_portal import (
    PublicDataPortalMarketIndexAdapter,
    PublicDataPortalSecurityMasterAdapter,
    PublicDataPortalStockPriceAdapter,
)
from digital_twin.infrastructure.external_api.adapters.public_data_portal_company import (
    PublicDataPortalCapitalEventAdapter,
    PublicDataPortalCompanyFinancialAdapter,
)
from digital_twin.infrastructure.external_api.adapters.sec import SecSubmissionsAdapter
from digital_twin.infrastructure.external_api.adapters.yfinance import (
    YFinanceProfileAdapter,
    unusable_modules_error_message,
)
from digital_twin.infrastructure.external_api.mysql_stores import (
    EMPTY_DOCUMENT_HASH,
    completed_followup_needs_retry,
    external_subject_from_market_quote,
)
from digital_twin.infrastructure.external_signal_provider_yfinance import (
    ExternalSignalYFinanceMixin,
    earnings_report_from_yfinance,
    overview_from_yfinance,
)
from digital_twin.modules.portfolio.domain.valuation.evidence import (
    collect_earnings_observations,
    earnings_scenario,
)
from digital_twin.infrastructure.external_signal_utils import dart_document_permanently_unavailable
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


class NoDataAdapter(StaticAdapter):
    def fetch(self, job, settings):
        del settings
        return SourceObservation(
            dataset_id=self.descriptor.dataset_id,
            provider_id=self.descriptor.provider_id,
            subject_key=job.subject.subject_key,
            source_revision="no-data",
            source_as_of="2026-08-16T00:00:00Z",
            fetched_at="2026-08-16T00:00:01Z",
            payload={},
            quality={"dataUsable": True, "emptyResult": True},
            empty_result=True,
            retain_previous=True,
        )


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


class UnusableOnceAdapter(StaticAdapter):
    descriptor = DatasetDescriptor(
        dataset_id="test.document",
        provider_id="test-provider",
        capability="document",
        cadence_seconds=600,
        freshness_seconds=900,
        completion_mode="once",
    )

    def fetch(self, job, _settings):
        return SourceObservation(
            dataset_id=self.descriptor.dataset_id,
            provider_id=self.descriptor.provider_id,
            subject_key=job.subject.subject_key,
            source_revision="empty-document",
            source_as_of="2026-08-16T00:00:00Z",
            fetched_at="2026-08-16T00:00:01Z",
            payload={"document": {"text": ""}},
            quality={"dataUsable": False, "documentState": "document-rejected"},
        )


class MemoryCollectionStore:
    def __init__(self):
        self.jobs = []
        self.completed = []
        self.empty_completed = []
        self.events = []
        self.recorded = []
        self.followups = []
        self.current = {}
        self.failure_state = "failed"

    def list_subjects(self):
        return [ExternalSubject("NVDA", symbol="NVDA", market="US", currency="USD")]

    def sync_partitions(self, plans, _dataset_ids, now=None, deactivate_missing=True):
        del deactivate_missing
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

    def claim_due(self, worker_id, limit, lease_seconds, now=None, dataset_ids=None, subject_keys=None):
        del worker_id, lease_seconds, now, dataset_ids, subject_keys
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

    def complete_observation(self, job, descriptor, observation, due_at, event=None, events=None):
        self.completed.append((job, descriptor, observation, due_at))
        self.events.extend(list(events or ([event] if event else [])))
        return {"changed": True}

    def complete_empty_observation(self, job, observation, due_at):
        self.empty_completed.append((job, observation, due_at))
        return {"changed": False, "retainedPreviousFact": bool(self.current)}

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
                "revisionId": "revision-crypto-1",
                "sourceRevision": "provider-crypto-1",
                "payloadHash": "crypto-hash",
                "sourceSchemaVersion": "coingecko-market-source-v1",
                "availability": "observed",
            },
            {
                "datasetId": "yfinance.price",
                "subjectKey": "NVDA",
                "payload": {"equityQuotes": {"NVDA": {"price": 225.0}}},
                "fetchedAt": "2026-08-16T00:01:00Z",
                "updatedAt": "2026-08-16T00:01:01Z",
                "freshnessState": "stale",
                "revisionId": "revision-nvda-1",
                "sourceRevision": "provider-nvda-1",
                "payloadHash": "nvda-hash",
                "sourceSchemaVersion": "yfinance-price-source-v1",
                "availability": "observed",
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
    @staticmethod
    def public_data_job(symbol="005930"):
        return CollectionJob(
            dataset_id="public-data.kr-stock-daily",
            partition_key=symbol,
            provider_id="data-go-kr-fsc",
            priority=45,
            subject=ExternalSubject(
                subject_key=symbol,
                symbol=symbol,
                name="삼성전자",
                market="KR",
                currency="KRW",
            ),
        )

    def test_kosdaq_reference_corrects_generic_kr_market_for_yfinance_symbol(self):
        subject = external_subject_from_market_quote(
            {
                "symbol": "376900",
                "payload_json": json.dumps({
                    "symbol": "376900",
                    "name": "로킷헬스케어",
                    "market": "KR",
                    "currency": "KRW",
                    "collectionPurpose": "account-focus",
                }, ensure_ascii=False),
            },
            {
                "symbol": "376900",
                "name": "로킷헬스케어",
                "market": "KOSDAQ",
                "currency": "KRW",
            },
        )

        query_symbol = ExternalSignalYFinanceMixin().yfinance_query_symbol(position_for(subject))

        self.assertEqual("KOSDAQ", subject.market)
        self.assertEqual("376900.KQ", query_symbol)

    def test_domestic_reference_does_not_override_non_numeric_market_subject(self):
        subject = external_subject_from_market_quote(
            {
                "symbol": "NVDA",
                "payload_json": json.dumps({
                    "symbol": "NVDA",
                    "name": "NVIDIA",
                    "market": "US",
                    "currency": "USD",
                }),
            },
            {"symbol": "NVDA", "market": "KOSDAQ"},
        )

        self.assertEqual("US", subject.market)

    def test_public_data_stock_adapter_collects_official_daily_reference_without_secret_leak(self):
        requested = {}

        def fetcher(url, headers, timeout):
            requested.update({"url": url, "headers": headers, "timeout": timeout})
            return {
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                    "body": {
                        "items": {
                            "item": [{
                                "basDt": "20260827",
                                "srtnCd": "005930",
                                "isinCd": "KR7005930003",
                                "itmsNm": "삼성전자",
                                "mrktCtg": "KOSPI",
                                "clpr": "266000",
                                "vs": "-4000",
                                "fltRt": "-1.48",
                                "mkp": "270000",
                                "hipr": "271000",
                                "lopr": "262500",
                                "trqu": "16829395",
                                "trPrc": "4488160057932",
                                "lstgStCnt": "5843635200",
                                "mrktTotAmt": "1555110109728000",
                            }]
                        }
                    },
                }
            }

        adapter = PublicDataPortalStockPriceAdapter(fetcher)
        observation = adapter.fetch(
            self.public_data_job(),
            {"publicDataPortalServiceKey": "raw+/service==", "externalPublicDataTimeoutSeconds": "9"},
        )

        price = observation.payload["officialDailyPrices"]["005930"]
        self.assertEqual(266000, price["close"])
        self.assertEqual(16829395, price["volume"])
        self.assertEqual("20260827", price["baseDate"])
        self.assertFalse(price["realTime"])
        self.assertEqual("reference-only", price["decisionEligibility"])
        self.assertEqual("2026-08-27T06:30:00Z", observation.source_as_of)
        self.assertIn("serviceKey=raw%2B%2Fservice%3D%3D", requested["url"])
        self.assertEqual(9.0, requested["timeout"])
        self.assertNotIn("raw+/service==", json.dumps(observation.__dict__, ensure_ascii=False))

    def test_public_data_stock_adapter_filters_non_korean_symbols_and_requires_key(self):
        adapter = PublicDataPortalStockPriceAdapter(lambda *_args: {})
        subjects = [
            ExternalSubject("005930", symbol="005930", market="KR", currency="KRW"),
            ExternalSubject("NVDA", symbol="NVDA", market="US", currency="USD"),
        ]

        self.assertEqual([], adapter.partitions(subjects, {}))
        partitions = adapter.partitions(subjects, {"publicDataPortalServiceKey": "configured"})
        self.assertEqual(["005930"], [item.partition_key for item in partitions])

    def test_public_data_stock_adapter_retains_previous_fact_on_valid_empty_response(self):
        adapter = PublicDataPortalStockPriceAdapter(lambda *_args: {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                "body": {"items": {}},
            }
        })

        observation = adapter.fetch(
            self.public_data_job(),
            {"publicDataPortalServiceKey": "configured"},
        )

        self.assertTrue(observation.empty_result)
        self.assertTrue(observation.retain_previous)
        self.assertEqual({}, observation.payload["officialDailyPrices"])

    def test_official_daily_price_is_bounded_and_projected_as_reference_only_abox(self):
        signals = {
            "officialDailyPrices": {
                "005930": {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "baseDate": "20260827",
                    "sourceAsOf": "2026-08-27T06:30:00Z",
                    "fetchedAt": "2026-08-28T04:00:00Z",
                    "close": 266000,
                    "volume": 16829395,
                    "provider": "금융위원회·공공데이터포털",
                    "sourceUrl": "https://www.data.go.kr/data/15094808/openapi.do",
                    "realTime": False,
                    "decisionEligibility": "reference-only",
                    "ignoredProviderPayload": "must-not-enter-abox",
                }
            }
        }
        compact = compact_external_signals_for_ontology(signals, target_symbols=["005930"])
        graph = PortfolioOntology("test")
        add_official_daily_price_concepts(
            graph,
            "stock:005930",
            Position(symbol="005930", name="삼성전자", market="KR", currency="KRW"),
            compact,
        )

        price_rows = [item for item in graph.entities if item.kind == "price-bar"]
        self.assertEqual(1, len(price_rows))
        self.assertEqual("reference-only", price_rows[0].properties["decisionEligibility"])
        self.assertFalse(price_rows[0].properties["realTime"])
        self.assertNotIn("ignoredProviderPayload", compact["officialDailyPrices"]["005930"])
        self.assertTrue(any(item.relation_type == "HAS_PROVENANCE" for item in graph.relations))

    def test_official_daily_price_change_does_not_trigger_live_reasoning(self):
        transition = ExternalFactTransitionService().assess(
            "public-data.kr-stock-daily",
            {
                "sourceRevision": "20260826:005930:270000",
                "payload": {"officialDailyPrices": {"005930": {"baseDate": "20260826", "close": 270000}}},
            },
            {"officialDailyPrices": {"005930": {"baseDate": "20260827", "close": 266000}}},
            "20260827:005930:266000",
        )

        self.assertTrue(transition.changed)
        self.assertFalse(transition.material)
        self.assertEqual("official-daily-reference", transition.change_type)

    def test_public_data_security_master_normalizes_krx_code_and_plans_bounded_followups(self):
        adapter = PublicDataPortalSecurityMasterAdapter(lambda *_args: {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                "body": {
                    "totalCount": 1,
                    "items": {"item": [{
                        "basDt": "20260827",
                        "srtnCd": "A005930",
                        "isinCd": "KR7005930003",
                        "itmsNm": "삼성전자",
                        "mrktCtg": "KOSPI",
                        "crno": "1301110006246",
                        "corpNm": "삼성전자 주식회사",
                    }]},
                },
            }
        })
        observation = adapter.fetch(
            replace(self.public_data_job(), dataset_id=adapter.descriptor.dataset_id, priority=58),
            {"publicDataPortalServiceKey": "configured"},
        )

        master = observation.payload["securityMaster"]["005930"]
        self.assertEqual("1301110006246", master["corporateRegistrationNumber"])
        self.assertEqual("KR7005930003", master["isin"])
        followups = adapter.followup_requests(observation, {})
        self.assertEqual(5, len(followups))
        self.assertEqual(
            {
                "public-data.kr-capital-events",
                "public-data.kr-company-financials",
                "public-data.kr-company-profile",
                "public-data.kr-dividends",
                "public-data.kr-shareholder-rights",
            },
            {item.dataset_id for item in followups},
        )
        self.assertTrue(all(item.watermark["crno"] == "1301110006246" for item in followups))
        collected_at = datetime.fromisoformat(observation.fetched_at.replace("Z", "+00:00"))
        local_date = collected_at.astimezone(ZoneInfo("Asia/Seoul")).date()
        iso_year, iso_week, _iso_day = local_date.isocalendar()
        buckets = {item.dataset_id: item.partition_key.rsplit(":", 1)[-1] for item in followups}
        self.assertEqual(local_date.strftime("%Y%m"), buckets["public-data.kr-company-profile"])
        self.assertEqual(str(iso_year) + "W" + str(iso_week).zfill(2), buckets["public-data.kr-company-financials"])
        self.assertEqual(local_date.strftime("%Y%m%d"), buckets["public-data.kr-capital-events"])

    def test_public_data_market_indices_share_one_global_current_fact(self):
        requested = []

        def fetcher(url, _headers, _timeout):
            requested.append(url)
            index_name = "코스피" if len(requested) == 1 else "코스닥"
            close = "3500.1" if index_name == "코스피" else "980.4"
            return {
                "response": {
                    "header": {"resultCode": "00"},
                    "body": {
                        "totalCount": 1,
                        "items": {"item": [{
                            "basDt": "20260827",
                            "idxNm": index_name,
                            "idxCsf": "대표지수",
                            "clpr": close,
                            "fltRt": "1.2",
                        }]},
                    },
                }
            }

        adapter = PublicDataPortalMarketIndexAdapter(fetcher)
        partitions = adapter.partitions([], {"publicDataPortalServiceKey": "configured"})
        self.assertEqual(["global"], [item.partition_key for item in partitions])

        job = CollectionJob(
            dataset_id=adapter.descriptor.dataset_id,
            partition_key="global",
            provider_id=adapter.descriptor.provider_id,
            priority=adapter.descriptor.priority,
            subject=partitions[0].subject,
        )
        result = adapter.fetch(job, {"publicDataPortalServiceKey": "configured"})

        self.assertEqual("global", result.subject_key)
        self.assertEqual({"KOSPI", "KOSDAQ"}, set(result.payload["marketIndices"]))
        self.assertEqual(3500.1, result.payload["marketIndices"]["KOSPI"]["close"])
        self.assertEqual(2, result.quality["indexCount"])
        self.assertEqual("sufficient", result.quality["coverageState"])

        older = {"marketIndices": {"KOSPI": {
            "close": 3400.0,
            "baseDate": "20260826",
            "sourceAsOf": "2026-08-26T15:30:00+09:00",
            "provider": "금융위원회·공공데이터포털",
        }}}
        newer = {"marketIndices": {"KOSPI": {
            "close": 3500.1,
            "baseDate": "20260827",
            "sourceAsOf": "2026-08-27T15:30:00+09:00",
            "provider": "KRX OpenAPI",
        }}}
        for base, incoming in ((older, newer), (newer, older)):
            merged = merge_external_signal_read_models(base, incoming)
            self.assertEqual(3500.1, merged["marketIndices"]["KOSPI"]["close"])
            self.assertEqual("KRX OpenAPI", merged["marketIndices"]["KOSPI"]["provider"])

        same_date_public = {"marketIndices": {"KOSPI": {
            **newer["marketIndices"]["KOSPI"],
            "close": 3499.9,
            "provider": "금융위원회·공공데이터포털",
        }}}
        merged = merge_external_signal_read_models(same_date_public, newer)
        self.assertEqual("KRX OpenAPI", merged["marketIndices"]["KOSPI"]["provider"])

    def _assert_ecos_macro_adapter_normalizes_official_kr_rates_and_fx(self):
        requested = []

        def fetcher(url, _headers, _timeout):
            requested.append(url)
            is_fx = "/731Y001/" in url
            return {
                "StatisticSearch": {
                    "row": [
                        {
                            "TIME": "20260825",
                            "DATA_VALUE": "1381.2" if is_fx else "2.50",
                            "UNIT_NAME": "원" if is_fx else "%",
                        },
                        {
                            "TIME": "20260826",
                            "DATA_VALUE": "1384.4" if is_fx else "2.55",
                            "UNIT_NAME": "원" if is_fx else "%",
                        },
                    ]
                }
            }

        adapter = EcosMacroAdapter(fetcher)
        settings = {"ecosApiKey": "configured"}
        partition = adapter.partitions([], settings)[0]
        result = adapter.fetch(CollectionJob(
            adapter.descriptor.dataset_id,
            partition.partition_key,
            adapter.descriptor.provider_id,
            adapter.descriptor.priority,
            partition.subject,
        ), settings)

        self.assertEqual(5, len(requested))
        self.assertEqual(
            {"KRGB3Y", "KRGB10Y", "KRCAA3Y", "KRBASE"},
            set(result.payload["macro"]["series"]),
        )
        self.assertEqual(5.0, result.payload["macro"]["series"]["KRGB3Y"]["deltaBp"])
        self.assertEqual(1384.4, result.payload["fxRates"]["USDKRW"]["rate"])
        self.assertTrue(result.quality["dataUsable"])
        self.assertEqual(
            {"KRGB3Y", "KRGB10Y", "KRCAA3Y", "KRBASE"},
            set(interest_rate_signal_ids(result.payload)),
        )

    def _assert_kosis_adapter_normalizes_selected_industry_indicators(self):
        def fetcher(url, _headers, _timeout):
            table_id = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["tblId"][0]
            current = {
                "DT_1JH20202": "112.5",
                "DT_1C8015": "101.2",
                "DT_1K41017": "108.4",
            }[table_id]
            return [
                {"PRD_DE": "202607", "DT": "100.0", "UNIT_NM": "2020=100"},
                {"PRD_DE": "202608", "DT": current, "UNIT_NM": "2020=100"},
            ]

        adapter = KosisIndustryIndicatorAdapter(fetcher)
        settings = {"kosisApiKey": "configured"}
        partition = adapter.partitions([], settings)[0]
        result = adapter.fetch(CollectionJob(
            adapter.descriptor.dataset_id,
            partition.partition_key,
            adapter.descriptor.provider_id,
            adapter.descriptor.priority,
            partition.subject,
        ), settings)

        self.assertEqual(
            {"KR_ALL_INDUSTRY_PRODUCTION", "KR_LEADING_CYCLE", "KR_RETAIL_SALES"},
            set(result.payload["macro"]["series"]),
        )
        self.assertEqual("2026-08", result.watermark["observationPeriod"])
        self.assertEqual("2026-08-01T00:00:00+09:00", result.source_as_of)
        self.assertEqual({}, interest_rate_signal_ids(result.payload))

    def _assert_krx_adapter_normalizes_official_kospi_and_kosdaq_indices(self):
        def fetcher(url, headers, _timeout):
            self.assertEqual("configured", headers["AUTH_KEY"])
            kosdaq = "kosdaq_dd_trd" in url
            return {
                "OutBlock_1": [{
                    "BAS_DD": "20260826",
                    "IDX_NM": "코스닥" if kosdaq else "코스피",
                    "IDX_CLSS": "대표지수",
                    "CLSPRC_IDX": "980.4" if kosdaq else "3500.1",
                    "FLUC_RT": "1.2",
                    "ACC_TRDVOL": "1000",
                    "ACC_TRDVAL": "2000",
                    "MKTCAP": "3000",
                }]
            }

        adapter = KrxMarketIndexAdapter(fetcher)
        settings = {"krxOpenApiKey": "configured"}
        partition = adapter.partitions([], settings)[0]
        result = adapter.fetch(CollectionJob(
            adapter.descriptor.dataset_id,
            partition.partition_key,
            adapter.descriptor.provider_id,
            adapter.descriptor.priority,
            partition.subject,
        ), settings)

        self.assertEqual({"KOSPI", "KOSDAQ"}, set(result.payload["marketIndices"]))
        self.assertEqual(3500.1, result.payload["marketIndices"]["KOSPI"]["close"])
        self.assertEqual("2026-08-26T15:30:00+09:00", result.source_as_of)
        self.assertEqual("official-daily-reference", adapter.descriptor.materiality_policy)
        self.assertEqual({}, adapter._main_index([{"IDX_NM": "코스피 200"}], "KOSPI"))

    def test_public_data_financial_adapter_normalizes_official_periods_and_archives_provider_rows(self):
        def fetcher(url, _headers, _timeout):
            if "getSummFinaStat" in url:
                item = {
                    "basDt": "20251231", "bizYear": "2025", "crno": "1301110006246",
                    "curCd": "KRW", "fnclDcd": "FS_ifrs_ConsolidatedMember",
                    "fnclDcdNm": "연결재무제표", "enpSaleAmt": "3000", "enpBzopPft": "300",
                    "enpCrtmNpf": "220", "enpTastAmt": "5000", "enpTdbtAmt": "1800",
                    "enpTcptAmt": "3200", "enpCptlAmt": "100", "fnclDebtRto": "56.25",
                }
            elif "getBs" in url:
                item = {
                    "basDt": "20251231", "bizYear": "2025", "crno": "1301110006246",
                    "fnclDcd": "FS_ifrs_ConsolidatedMember", "fnclDcdNm": "연결재무제표",
                    "acitId": "ifrs_CashAndCashEquivalents", "acitNm": "현금및현금성자산",
                    "crtmAcitAmt": "700",
                }
            else:
                item = {
                    "basDt": "20251231", "bizYear": "2025", "crno": "1301110006246",
                    "fnclDcd": "PL_ifrs_ConsolidatedMember", "fnclDcdNm": "연결재무제표",
                    "acitId": "dart_OperatingIncomeLoss", "acitNm": "영업이익(손실)",
                    "crtmAcitAmt": "300",
                }
            return {
                "response": {
                    "header": {"resultCode": "00"},
                    "body": {"totalCount": 1, "items": {"item": [item]}},
                }
            }

        adapter = PublicDataPortalCompanyFinancialAdapter(fetcher)
        job = replace(
            self.public_data_job(),
            dataset_id=adapter.descriptor.dataset_id,
            priority=adapter.descriptor.priority,
            watermark={
                "crno": "1301110006246",
                "isin": "KR7005930003",
                "companyName": "삼성전자",
            },
        )
        observation = adapter.fetch(job, {"publicDataPortalServiceKey": "configured"})
        knowledge = observation.payload["companyKnowledge"]["005930"]
        annual = knowledge["financials"]["annual"]

        self.assertEqual(1, len(annual))
        self.assertEqual(3000.0, annual[0]["revenue"])
        self.assertEqual(700.0, annual[0]["cash"])
        self.assertEqual("연결재무제표", annual[0]["accountingScope"])
        self.assertEqual("financial-report-observation-v1", annual[0]["reportContract"]["contractVersion"])
        self.assertEqual(
            observation.source_revision,
            annual[0]["reportContract"]["sourceReferences"][0]["revisionId"],
        )
        self.assertIn("sourceArchive", observation.payload)

        visible = merge_external_signal_read_models({}, observation.payload)
        self.assertIn("companyKnowledge", visible)
        self.assertNotIn("sourceArchive", visible)

    def test_public_data_capital_adapter_preserves_issuance_and_current_capital_state(self):
        def fetcher(url, _headers, _timeout):
            if "getItemBasiInfo" in url:
                items = [{
                    "basDt": "20260827", "crno": "1301110006246", "isinCd": "KR7005930003",
                    "itmsShrtnCd": "A005930", "stckIssuCmpyNm": "삼성전자", "lstgDt": "19750611",
                    "scrsItmsKcd": "01", "scrsItmsKcdNm": "보통주", "stckParPrc": "100",
                    "issuStckCnt": "5969782550",
                }]
            elif "getStocIssuInfo" in url:
                items = [{
                    "basDt": "20260827", "crno": "1301110006246", "isinCd": "KR7005930003",
                    "stckIssuCmpyNm": "삼성전자", "stckIssuDt": "20260901", "lstgDt": "20260910",
                    "stckIssuRcd": "10", "stckIssuRcdNm": "유상증자", "issuStckCnt": "1000000",
                    "stckIssuSqno": "1", "scrsItmsKcd": "01", "scrsItmsKcdNm": "보통주",
                }]
            elif "getLockUpRetu" in url:
                items = []
            else:
                items = [{
                    "basDt": "20260827", "crno": "1301110006246", "stckIssuCmpyNm": "삼성전자",
                    "onskTisuCnt": "5969782550", "pfstTisuCnt": "0",
                }]
            return {
                "response": {
                    "header": {"resultCode": "00"},
                    "body": {"totalCount": len(items), "items": {"item": items}},
                }
            }

        adapter = PublicDataPortalCapitalEventAdapter(fetcher)
        job = replace(
            self.public_data_job(),
            dataset_id=adapter.descriptor.dataset_id,
            priority=adapter.descriptor.priority,
            watermark={"crno": "1301110006246", "isin": "KR7005930003", "companyName": "삼성전자"},
        )
        observation = adapter.fetch(job, {"publicDataPortalServiceKey": "configured"})

        actions = observation.payload["corporateActions"]["005930"]
        knowledge = observation.payload["companyKnowledge"]["005930"]
        self.assertTrue(any(item["tboxClass"] == "EquityIssuanceEvent" for item in actions.values()))
        self.assertEqual(5969782550, knowledge["capital"]["sharesOutstanding"])
        self.assertEqual("보통주", knowledge["listing"]["shareClassName"])

    def test_public_data_reference_abox_keeps_identity_benchmark_company_and_event_relations(self):
        signals = {
            "securityMaster": {
                "005930": {
                    "symbol": "005930", "name": "삼성전자", "legalName": "삼성전자 주식회사",
                    "market": "KOSPI", "isin": "KR7005930003",
                    "corporateRegistrationNumber": "1301110006246", "baseDate": "20260827",
                    "provider": "금융위원회·공공데이터포털",
                }
            },
            "marketIndices": {
                "KOSPI": {
                    "indexKey": "KOSPI", "indexName": "코스피", "baseDate": "20260827",
                    "close": 3500.1, "provider": "금융위원회·공공데이터포털",
                }
            },
            "companyKnowledge": {
                "005930": {
                    "symbol": "005930", "companyName": "삼성전자",
                    "identifiers": {"corporateRegistrationNumber": "1301110006246"},
                    "relationships": {
                        "affiliates": [{"companyName": "삼성SDI", "corporateRegistrationNumber": "affiliate-1", "baseDate": "20260827"}],
                        "subsidiaries": [{"companyName": "Samsung Austin", "controlBasis": "지배력", "baseDate": "20260827"}],
                    },
                    "provenance": [{"provider": "금융위원회·공공데이터포털", "scope": "official-company-profile"}],
                }
            },
            "corporateActions": {
                "005930": {
                    "issue": {
                        "eventId": "issue", "eventType": "equity-issuance", "tboxClass": "EquityIssuanceEvent",
                        "issueDate": "20260901", "eventLifecycleState": "upcoming", "issuedShareCount": 1000,
                        "provider": "금융위원회·공공데이터포털",
                    },
                    "dividend": {
                        "eventId": "dividend", "eventType": "dividend", "tboxClass": "DividendEvent",
                        "recordDate": "20260930", "eventLifecycleState": "upcoming", "cashDividendPerCommonShare": 500,
                        "provider": "금융위원회·공공데이터포털",
                    },
                    "dividend-right": {
                        "eventId": "dividend-right", "eventType": "shareholder-right",
                        "tboxClass": "ShareholderRightEvent", "eventLifecycleState": "active",
                        "exerciseStartDate": "20260901", "issuanceReason": "배당/분배",
                        "rightReason": "명부폐쇄기간", "provider": "금융위원회·공공데이터포털",
                    },
                    "rights-offering": {
                        "eventId": "rights-offering", "eventType": "shareholder-right",
                        "tboxClass": "ShareholderRightEvent", "eventLifecycleState": "upcoming",
                        "exerciseStartDate": "20261001", "issuanceReason": "유상증자",
                        "rightReason": "신주인수권 행사", "provider": "금융위원회·공공데이터포털",
                    },
                }
            },
        }
        compact = compact_external_signals_for_ontology(signals, target_symbols=["005930"])
        graph = PortfolioOntology("official-reference")
        position = Position(symbol="005930", name="삼성전자", market="KR", currency="KRW")
        stock_id = add_entity(graph, "stock", "005930", "삼성전자", {
            "tboxClass": "Stock",
            "symbol": "005930",
        })
        add_official_security_reference_concepts(graph, stock_id, position, compact)
        add_official_market_index_concepts(graph, stock_id, position, compact)
        add_company_knowledge_concepts(graph, stock_id, "005930", compact)
        add_official_corporate_action_concepts(graph, stock_id, position, compact)

        classes = {item.properties.get("tboxClass") for item in graph.entities}
        relation_types = {item.relation_type for item in graph.relations}
        self.assertTrue({"SecurityListing", "Index", "DividendEvent", "EquityIssuanceEvent"}.issubset(classes))
        self.assertTrue({"AFFILIATED_WITH", "CONTROLS", "USES_MARKET_BENCHMARK", "HAS_CORPORATE_ACTION"}.issubset(relation_types))
        external_signal_targets = {
            item.target for item in graph.relations if item.relation_type == "HAS_EXTERNAL_SIGNAL"
        }
        corporate_actions = {
            item.properties.get("eventId"): item
            for item in graph.entities
            if item.kind == "corporate-action"
        }
        self.assertIn(corporate_actions["issue"].entity_id, external_signal_targets)
        self.assertIn(corporate_actions["rights-offering"].entity_id, external_signal_targets)
        self.assertNotIn(corporate_actions["dividend"].entity_id, external_signal_targets)
        self.assertNotIn(corporate_actions["dividend-right"].entity_id, external_signal_targets)
        self.assertEqual(
            "shareholder-administration",
            corporate_actions["dividend-right"].properties["eventDecisionCategory"],
        )
        self.assertNotIn(
            "ExternalSignal",
            corporate_actions["dividend-right"].properties["tboxClasses"],
        )
        validation = validate_ontology(graph)
        self.assertEqual("valid", validation.status, [item.to_dict() for item in validation.issues])

        knowledge = build_knowledge_world_graph(graph, knowledge_world("kr"))
        knowledge_relations = {item.relation_type for item in knowledge.relations}
        self.assertTrue({"HAS_LISTING", "AFFILIATED_WITH", "CONTROLS", "HAS_CORPORATE_ACTION"}.issubset(knowledge_relations))

        market = build_market_world_graph(graph, market_world("kr"))
        self.assertTrue(any(item.kind == "price-bar" for item in market.entities))
        self.assertTrue(any(item.relation_type == "HAS_OBSERVATION" for item in market.relations))

    def test_registry_builds_only_enabled_partitions(self):
        registry = ExternalDatasetRegistry([StaticAdapter()])
        subject = ExternalSubject("NVDA", symbol="NVDA")

        partitions = registry.desired_partitions([subject], {})

        self.assertEqual(["NVDA"], [item.partition_key for item in partitions])
        self.assertEqual("test-provider", registry.adapter("test.market").descriptor.provider_id)
        self.assertEqual(["test.market"], registry.validate_dataset_ids(["test.market"]))
        self.assertEqual(
            ["NVDA"],
            [item.partition_key for item in registry.desired_partitions(
                [subject], {}, dataset_ids=["test.market"],
            )],
        )
        with self.assertRaisesRegex(ValueError, "Unknown external datasets"):
            registry.validate_dataset_ids(["missing.dataset"])
        self._assert_ecos_macro_adapter_normalizes_official_kr_rates_and_fx()
        self._assert_kosis_adapter_normalizes_selected_industry_indicators()
        self._assert_krx_adapter_normalizes_official_kospi_and_kosdaq_indices()
        self._assert_registered_dataset_catalog_exposes_owned_semantics()
        self._assert_every_default_dataset_has_owned_semantics()
        self._assert_source_reference_distinguishes_provider_correction_without_using_fetch_clock()
        self._assert_source_observation_keeps_zero_separate_from_missing_and_unsupported()
        self._assert_transition_detects_correction_beyond_display_field_limit()

    def _assert_registered_dataset_catalog_exposes_owned_semantics(self):
        catalog = external_dataset_catalog()

        self.assertIn("price_trade", catalog["yfinance.price"].category_ids)
        self.assertIn("financial", catalog["sec.company_facts"].category_ids)
        self.assertIn("company_event", catalog["opendart.document"].category_ids)
        self.assertEqual("unsupported", catalog["yfinance.options"].empty_result_semantics)

    def _assert_every_default_dataset_has_owned_semantics(self):
        registry = default_external_dataset_registry({})

        self.assertEqual(
            set(external_dataset_catalog()),
            {row["datasetId"] for row in registry.descriptors({})},
        )
        self.assertTrue(all(row["categoryIds"] for row in registry.descriptors({})))

    def _assert_source_reference_distinguishes_provider_correction_without_using_fetch_clock(self):
        base = SourceObservation(
            dataset_id="opendart.document",
            provider_id="opendart",
            subject_key="005930",
            source_revision="receipt-1",
            source_as_of="2026-08-16T00:00:00Z",
            fetched_at="2026-08-16T00:00:01Z",
            payload={"document": {"amount": 100}, "fetchedAt": "first"},
            quality={"dataUsable": True},
        )
        repeated = replace(
            base,
            fetched_at="2026-08-16T00:05:01Z",
            payload={"document": {"amount": 100}, "fetchedAt": "second"},
        )
        corrected = replace(base, payload={"document": {"amount": 120}})

        self.assertEqual(base.source_reference().revision_id, repeated.source_reference().revision_id)
        self.assertNotEqual(base.source_reference().revision_id, corrected.source_reference().revision_id)
        self.assertEqual("observed", base.source_reference().availability)

    def _assert_source_observation_keeps_zero_separate_from_missing_and_unsupported(self):
        observed_zero = SourceObservation(
            "test.market", "test", "NVDA", "r1", "2026-08-16", "2026-08-16T00:00:00Z",
            {"price": 0}, quality={"dataUsable": True},
        )
        missing = replace(observed_zero, payload={}, empty_result=True)

        self.assertEqual("observed", observed_zero.source_reference().availability)
        self.assertEqual("missing", missing.source_reference().availability)
        self.assertEqual("unsupported", normalized_availability("unsupported", quality={"dataUsable": False}))

    def test_followup_document_work_is_durable_and_not_a_static_partition(self):
        store = MemoryCollectionStore()
        registry = ExternalDatasetRegistry([FollowupSourceAdapter(), FollowupAdapter()])
        service = ExternalDataCollectionService({}, registry, store, now_provider=lambda: NOW)

        result = service.run_once()

        self.assertEqual(1, result["results"][0]["followupCount"])
        self.assertEqual("test.document", store.followups[0][0].dataset_id)
        self.assertEqual("NVDA:document-1", store.followups[0][1].partition_key)
        self.assertNotIn("test.document", registry.static_dataset_ids({}))

        class DueStore:
            calls = []

            def make_due(self, dataset_ids):
                self.calls.append(tuple(dataset_ids))
                return 4

        due_store = DueStore()
        recovery_service = ExternalDataConfigurationRecoveryService(due_store)
        recovery = recovery_service.recover(
            {
                "externalSecContactEmail": "",
                "externalSecDocumentTextEnabled": "1",
            },
            {
                "externalSecContactEmail": "owner@example.com",
                "externalSecDocumentTextEnabled": "1",
            },
        )
        repeated = recovery_service.recover(
            {"externalSecContactEmail": "owner@example.com"},
            {"externalSecContactEmail": "owner@example.com"},
        )
        self.assertEqual("scheduled", recovery["status"])
        self.assertEqual(4, recovery["madeDueCount"])
        self.assertEqual(
            [("sec.submissions", "sec.document")],
            due_store.calls,
        )
        self.assertEqual("not-required", repeated["status"])

        metadata_only = recovery_service.recover(
            {
                "externalSecContactEmail": "",
                "externalSecDocumentTextEnabled": "0",
            },
            {
                "externalSecContactEmail": "owner@example.com",
                "externalSecDocumentTextEnabled": "0",
            },
        )
        document_enabled = recovery_service.recover(
            {
                "externalSecContactEmail": "owner@example.com",
                "externalSecDocumentTextEnabled": "0",
            },
            {
                "externalSecContactEmail": "owner@example.com",
                "externalSecDocumentTextEnabled": "1",
            },
        )
        self.assertEqual(["sec.submissions"], metadata_only["datasetIds"])
        self.assertEqual(["sec.document"], document_enabled["datasetIds"])
        self.assertEqual(
            [
                ("sec.submissions", "sec.document"),
                ("sec.submissions",),
                ("sec.document",),
            ],
            due_store.calls,
        )

    def test_unusable_one_time_payload_remains_retryable(self):
        store = MemoryCollectionStore()
        store.current = {
            "payload": {"document": {"text": ""}},
            "quality": {"dataUsable": False},
        }
        service = ExternalDataCollectionService(
            {},
            ExternalDatasetRegistry([UnusableOnceAdapter()]),
            store,
            worker_id="test-worker",
            now_provider=lambda: NOW,
        )

        result = service.run_once()

        self.assertEqual("partial", result["status"])
        self.assertEqual([], store.completed)
        self.assertEqual(1, result["failureCount"])
        self.assertFalse(result["results"][0]["hasUsablePreviousFact"])
        self.assertIn("unusable one-time payload", result["results"][0]["error"])

    def test_empty_completed_official_document_is_requeued_only_for_document_datasets(self):
        self.assertTrue(completed_followup_needs_retry(
            "opendart.document",
            "completed",
            {"documentHash": EMPTY_DOCUMENT_HASH},
        ))
        self.assertFalse(completed_followup_needs_retry(
            "opendart.document",
            "pending",
            {"documentHash": EMPTY_DOCUMENT_HASH},
        ))
        self.assertFalse(completed_followup_needs_retry(
            "test.document",
            "completed",
            {"documentHash": EMPTY_DOCUMENT_HASH},
        ))

    def test_unusable_legacy_document_is_requeued_even_with_a_nonempty_hash(self):
        self.assertTrue(completed_followup_needs_retry(
            "opendart.document",
            "completed",
            {"documentHash": "nonempty-document-hash"},
            {"dataUsable": False, "documentState": "document-rejected"},
        ))
        self.assertFalse(completed_followup_needs_retry(
            "opendart.document",
            "completed",
            {"documentHash": "nonempty-document-hash"},
            {"dataUsable": True, "documentState": "document-verified"},
        ))

    def test_officially_missing_document_is_terminal_and_not_requeued(self):
        unavailable = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<result><status>014</status>'
            + "<message>파일이 존재하지 않습니다.</message>".encode("utf-8")
            + b'</result>'
        )

        self.assertTrue(dart_document_permanently_unavailable(unavailable))
        self.assertFalse(dart_document_permanently_unavailable(b"<result><status>000</status></result>"))
        self.assertFalse(completed_followup_needs_retry(
            "opendart.document",
            "completed",
            {"terminalUnavailable": True, "receiptNo": "20260824000219"},
            {"dataUsable": False, "documentState": "document-unavailable"},
        ))

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
        self.assertEqual([EXTERNAL_OBSERVATION_RECORDED], [event.name for event in store.events])
        self.assertEqual("external-observation-recorded-v1", store.events[0].payload["eventContract"])
        self.assertTrue(store.events[0].payload["sourceRef"]["revisionId"])

        scoped = service.run_once(
            dataset_ids=["test.market"],
            subject_keys=["NVDA"],
            max_batches=2,
        )
        self.assertEqual({"datasetIds": ["test.market"], "subjectKeys": ["NVDA"]}, scoped["scope"])
        self.assertEqual(1, scoped["batchCount"])

    def test_document_collection_persists_recovery_purpose_in_immutable_observation(self):
        for dataset in ("sec.document", "opendart.document"):
            for source in ("document-recovery", "sec-submissions"):
                with self.subTest(dataset=dataset, source=source):
                    adapter = StaticAdapter()
                    adapter.descriptor = replace(adapter.descriptor, dataset_id=dataset)
                    store = MemoryCollectionStore()
                    service = ExternalDataCollectionService({}, ExternalDatasetRegistry([adapter]), store)
                    job = CollectionJob(dataset, "test-doc", "test-provider", 50,
                                        ExternalSubject("NVDA", symbol="NVDA", source=source))
                    result = service._process_job(job)
                    self.assertEqual("success", result["status"])
                    self.assertEqual(source, store.completed[0][2].quality["collectionSource"])

    def test_collection_failure_retains_usable_previous_fact(self):

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

    def _assert_transition_detects_correction_beyond_display_field_limit(self):
        before = {"field" + str(index).zfill(3): index for index in range(180)}
        after = dict(before)
        after["field179"] = 999

        transition = ExternalFactTransitionService().assess(
            "opendart.document",
            {"sourceRevision": "same-provider-revision", "payload": before},
            after,
            "same-provider-revision",
        )

        self.assertTrue(transition.changed)
        self.assertTrue(transition.material)
        self.assertEqual("source-revision", transition.change_type)

    def _assert_company_facts_collect_without_recent_disclosure(self):
        settings = {
            "opendartApiKey": "test-key",
            "externalDartCorpCodes": "005930=00126380",
        }
        provider = legacy_provider(
            settings,
            externalDartEnabled="1",
            externalDartCompanyFundamentalsEnabled="1",
            externalDartMaxSymbols="1",
        )

        def fetch_json(url, _headers):
            if "/list.json" in url:
                return {"status": "013", "message": "조회된 데이타가 없습니다.", "list": []}
            if "/company.json" in url:
                return {
                    "status": "000", "corp_name": "삼성전자", "stock_code": "005930",
                    "ceo_nm": "대표", "acc_mt": "12",
                }
            if "/fnlttSinglAcntAll.json" in url:
                return {"status": "000", "list": [{
                    "account_nm": "매출액", "thstrm_amount": "100",
                    "thstrm_dt": "2026.01.01 ~ 2026.06.30", "reprt_code": "11012",
                    "bsns_year": "2026", "sj_div": "IS",
                }]}
            return {"status": "000", "list": []}

        provider.fetch_json = fetch_json
        signals = empty_signals()
        subject = ExternalSubject("005930", symbol="005930", name="삼성전자", market="KR", currency="KRW")
        provider.add_opendart(
            signals,
            [position_for(subject)],
            include_fundamentals=True,
            include_document=False,
        )

        row = signals["dartDisclosures"]["005930"]
        self.assertTrue(row["noDisclosureInLookback"])
        self.assertEqual("삼성전자", row["company"]["corp_name"])
        self.assertEqual("100", row["financialStatements"][0]["thstrm_amount"])
        self.assertTrue(any(item.get("emptyResult") for item in signals["statuses"]))

        job = CollectionJob("opendart.company_facts", "005930", "opendart", 40, subject)
        with patch(
            "digital_twin.infrastructure.external_api.adapters.opendart.legacy_provider",
            return_value=provider,
        ):
            result = OpenDartCompanyFactsAdapter().fetch(job, settings)
        self.assertFalse(result.empty_result)
        self.assertIn("005930", result.payload["dartDisclosures"])
        self.assertTrue(result.source_revision.startswith("2026:11012"))

    def test_filing_metadata_only_schedules_documents_without_reasoning_event(self):
        self._assert_company_facts_collect_without_recent_disclosure()
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
        self.assertTrue(result.empty_result)
        self.assertTrue(result.retain_previous)

        company_job = replace(job, dataset_id="opendart.company_facts", priority=40)
        with patch(
            "digital_twin.infrastructure.external_api.adapters.opendart.legacy_provider",
            return_value=EmptyProvider(),
        ):
            company_result = OpenDartCompanyFactsAdapter().fetch(company_job, settings)

        self.assertEqual({"dartDisclosures": {}}, company_result.payload)
        self.assertEqual("no-company-facts", company_result.source_revision)
        self.assertTrue(company_result.empty_result)
        self.assertTrue(company_result.retain_previous)

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

        store = MemoryCollectionStore()
        store.current = {"payload": {"company": {"name": "existing"}}}
        service = ExternalDataCollectionService(
            {},
            ExternalDatasetRegistry([NoDataAdapter()]),
            store,
            worker_id="test-worker",
            now_provider=lambda: NOW,
        )

        result = service.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["successCount"])
        self.assertEqual(1, result["noDataCount"])
        self.assertEqual(0, result["failureCount"])
        self.assertEqual([], store.completed)
        self.assertEqual(1, len(store.empty_completed))
        self.assertEqual([], store.events)
        self.assertEqual("no-data", store.recorded[-1][0][1])
        self.assertTrue(result["results"][0]["retainedPreviousFact"])

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
        self.assertEqual(
            "revision-nvda-1",
            signals["externalDataLineage"]["yfinance.price:NVDA"]["revisionId"],
        )
        compact = compact_external_signals_for_ontology(signals, target_symbols=["NVDA"])
        self.assertEqual(
            "revision-nvda-1",
            compact["externalDataLineage"]["yfinance.price:NVDA"]["revisionId"],
        )
        self.assertEqual(
            "revision-crypto-1",
            compact["externalDataLineage"]["coingecko.market:GLOBAL"]["revisionId"],
        )
        self.assertTrue(any(item.get("datasetId") == "fred.macro" and not item.get("ok") for item in signals["statuses"]))
        fitness = signals["externalDataPlatform"]["fitness"]
        self.assertEqual("stale", fitness["subjects"]["NVDA"]["purposes"]["market-price"]["state"])
        self.assertEqual("not-collected", fitness["subjects"]["NVDA"]["purposes"]["derivatives"]["state"])
        self.assertEqual("fresh", fitness["subjects"]["GLOBAL"]["purposes"]["crypto-market"]["state"])
        self.assertEqual("failed", fitness["subjects"]["GLOBAL"]["purposes"]["macro-regime"]["state"])
        self._assert_read_model_binds_company_event_to_exact_fact_revision()
        self._assert_consensus_revision_survives_both_dataset_orders_into_valuation_evidence()

    def _assert_consensus_revision_survives_both_dataset_orders_into_valuation_evidence(self):
        def fact(dataset_id, revision_id, payload, fetched_at):
            return {
                "datasetId": dataset_id,
                "providerId": "yfinance",
                "subjectKey": "PLTR",
                "payload": payload,
                "fetchedAt": fetched_at,
                "updatedAt": fetched_at,
                "freshnessState": "fresh",
                "revisionId": revision_id,
                "sourceRevision": "provider-" + revision_id,
                "payloadHash": "hash-" + revision_id,
                "sourceSchemaVersion": "yfinance-source-v2",
                "sourceAsOf": "",
                "availability": "observed",
            }

        def fragment(payload):
            return {
                "companyOverviews": {"PLTR": overview_from_yfinance("PLTR", payload)},
                "earningsReports": {"PLTR": earnings_report_from_yfinance("PLTR", payload)},
            }

        analyst_payload = {
            "collectedAt": "2026-09-25T01:00:00Z",
            "earningsEstimate": [
                {"period": "0y", "low": 1.8, "avg": 2.0, "high": 2.2, "numberOfAnalysts": 12},
                {"period": "+1y", "avg": 2.6, "numberOfAnalysts": 10},
            ],
            "epsTrend": [{"period": "0y", "30daysAgo": 1.9}],
        }
        fundamental_payload = {
            "collectedAt": "2026-09-25T02:00:00Z",
            "info": {"currency": "USD", "forwardEps": 9.9, "trailingEps": 0.8},
        }
        analyst = fact("yfinance.analyst", "analyst-r2", fragment(analyst_payload), "2026-09-25T01:00:00Z")
        fundamental = fact("yfinance.fundamental", "fundamental-r3", fragment(fundamental_payload), "2026-09-25T02:00:00Z")

        class ConsensusStore:
            def __init__(self, rows):
                self.rows = rows

            def list_current(self, _subject_keys):
                return list(self.rows)

            def provider_statuses(self):
                return []

        projections = []
        for rows in ([analyst, fundamental], [fundamental, analyst]):
            signals = ExternalSignalsReadModelService(ConsensusStore(rows)).signals_for_subjects(["PLTR"])
            overview = signals["companyOverviews"]["PLTR"]
            report = signals["earningsReports"]["PLTR"]
            estimates = overview["earningsEstimates"]
            self.assertEqual(["fy1", "fy2"], [item["horizon"] for item in estimates])
            self.assertEqual([1.8, 2.0, 2.2, 12], [estimates[0]["low"], estimates[0]["base"], estimates[0]["high"], estimates[0]["analystCount"]])
            self.assertEqual(2.6, estimates[1]["base"])
            self.assertEqual("analyst-r2", estimates[0]["sourceReferences"][0]["revisionId"])
            self.assertEqual("exact-source-revision", estimates[0]["revisionState"])
            self.assertEqual("observed-partial-period", estimates[0]["validationState"])
            self.assertEqual(2.0, overview["forwardEPS"])
            self.assertEqual("fy1", overview["epsPeriod"])
            observations = collect_earnings_observations(overview, report)
            self.assertEqual({"fy1", "fy2", "ttm"}, {item["period"] for item in observations})
            scenario = earnings_scenario(observations)
            self.assertEqual([1.8, 2.0, 2.2], [scenario["low"], scenario["base"], scenario["high"]])
            self.assertEqual(12, scenario["analystCount"])
            projections.append({"overview": overview, "report": report, "scenario": scenario})
        self.assertEqual(projections[0], projections[1])

        negative_payload = {
            "collectedAt": "2026-09-25T03:00:00Z",
            "earningsEstimate": [
                {"period": "0y", "avg": -1.0, "numberOfAnalysts": 4},
                {"period": "+1y", "avg": 2.0, "numberOfAnalysts": 4},
            ],
        }
        negative = fact("yfinance.analyst", "analyst-negative-r1", fragment(negative_payload), "2026-09-25T03:00:00Z")
        signals = ExternalSignalsReadModelService(ConsensusStore([negative])).signals_for_subjects(["PLTR"])
        observations = collect_earnings_observations(
            signals["companyOverviews"]["PLTR"],
            signals["earningsReports"]["PLTR"],
        )
        by_period = {item["period"]: item for item in observations}
        self.assertEqual(-1.0, by_period["fy1"]["base"])
        self.assertEqual(2.0, by_period["fy2"]["base"])
        self.assertFalse(by_period["fy1"]["positivePerEligible"])
        self.assertIn("non-positive-eps", by_period["fy1"]["excludedReasons"])
        self.assertFalse(by_period["fy2"]["positivePerEligible"])
        self.assertIn("unsupported-per-horizon", by_period["fy2"]["excludedReasons"])
        self.assertEqual({}, earnings_scenario(observations))

    def _assert_read_model_binds_company_event_to_exact_fact_revision(self):
        class EventFactStore:
            def list_current(self, _subject_keys):
                return [{
                    "datasetId": "public-data.kr-capital-events",
                    "providerId": "data-go-kr-fsc",
                    "subjectKey": "005930",
                    "revisionId": "capital-revision-1",
                    "sourceRevision": "provider-capital-1",
                    "payloadHash": "capital-hash-1",
                    "sourceSchemaVersion": "official-corporate-action-source-v1",
                    "sourceAsOf": "2026-08-27T00:00:00Z",
                    "fetchedAt": "2026-08-27T00:01:00Z",
                    "availability": "observed",
                    "freshnessState": "fresh",
                    "payload": {"corporateActions": {"005930": {"issue": {
                        "eventId": "issue", "eventType": "equity-issuance",
                        "tboxClass": "EquityIssuanceEvent", "issueDate": "20260901",
                        "eventLifecycleState": "upcoming", "issuedShareCount": 1000,
                        "provider": "금융위원회·공공데이터포털", "officialSource": True,
                    }}}},
                }]

            @staticmethod
            def provider_statuses():
                return []

        signals = ExternalSignalsReadModelService(EventFactStore()).signals_for_subjects(["005930"])
        event = signals["corporateActions"]["005930"]["issue"]
        contract = event["companyEventContract"]

        self.assertEqual("company-event-observation-v1", contract["version"])
        self.assertEqual("capital-revision-1", contract["sourceReferences"][0]["revisionId"])
        compact = compact_external_signals_for_ontology(signals, target_symbols=["005930"])
        compact_contract = compact["corporateActions"]["005930"]["issue"]["companyEventContract"]
        self.assertEqual("capital-hash-1", compact_contract["sourceReferences"][0]["payloadHash"])


if __name__ == "__main__":
    unittest.main()
