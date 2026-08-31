import unittest
import time
import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

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
from digital_twin.application.external_data.read_model_service import (
    ExternalSignalsReadModelService,
    merge_external_signal_read_models,
)
from digital_twin.application.external_data.registry import ExternalDatasetRegistry
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.knowledge_world_projection import build_knowledge_world_graph
from digital_twin.domain.market_world_projection import build_market_world_graph
from digital_twin.domain.ontology_projection_input import compact_external_signals_for_ontology
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.ontology_validator import validate_ontology
from digital_twin.domain.ontology_worlds import knowledge_world, market_world
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_ontology_market_concepts import add_official_daily_price_concepts
from digital_twin.domain.portfolio_ontology_company_concepts import add_company_knowledge_concepts
from digital_twin.domain.portfolio_ontology_reference_concepts import (
    add_official_corporate_action_concepts,
    add_official_market_index_concepts,
    add_official_security_reference_concepts,
)
from digital_twin.infrastructure.external_api.legacy_import import LegacyExternalSignalImporter
from digital_twin.infrastructure.external_api.adapters.base import empty_signals, legacy_provider, position_for
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
        self.assertTrue(any(item.relation_type == "HAS_EXTERNAL_SIGNAL" for item in graph.relations))
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
        self.assertTrue(any(item.get("datasetId") == "fred.macro" and not item.get("ok") for item in signals["statuses"]))


if __name__ == "__main__":
    unittest.main()
