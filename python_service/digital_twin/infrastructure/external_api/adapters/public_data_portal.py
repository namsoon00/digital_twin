from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import urllib.parse
from typing import Callable, Dict, Iterable, List

from zoneinfo import ZoneInfo

from ....application.external_data.contracts import (
    CollectionJob,
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
    FollowupCollectionRequest,
)
from ....domain.portfolio import utc_now_iso
from ...external_signal_utils import api_error_text, default_json_fetcher
from .base import equity_partitions, observation


PUBLIC_DATA_STOCK_PRICE_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
)
PUBLIC_DATA_STOCK_PRICE_PAGE = "https://www.data.go.kr/data/15094808/openapi.do"
PUBLIC_DATA_SECURITY_MASTER_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"
)
PUBLIC_DATA_SECURITY_MASTER_PAGE = "https://www.data.go.kr/data/15094775/openapi.do"
PUBLIC_DATA_MARKET_INDEX_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService/getStockMarketIndex"
)
PUBLIC_DATA_MARKET_INDEX_PAGE = "https://www.data.go.kr/data/15094807/openapi.do"
PUBLIC_DATA_PROVIDER_LABEL = "금융위원회·공공데이터포털"
SEOUL = ZoneInfo("Asia/Seoul")


def is_korean_equity(subject: ExternalSubject) -> bool:
    symbol = str(subject.symbol or subject.subject_key or "").strip()
    return symbol.isdigit() and len(symbol) == 6


def normalized_security_code(value: object) -> str:
    text = str(value or "").upper().strip()
    if len(text) == 7 and text[0].isalpha() and text[1:].isdigit():
        return text[1:]
    return text.zfill(6) if text.isdigit() else text


def encoded_service_key(value: object) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    return key if "%" in key else urllib.parse.quote(key, safe="")


def numeric_value(value: object, *, integer: bool = False):
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    if integer:
        return int(parsed)
    return float(parsed)


def source_as_of_for_base_date(value: object) -> str:
    text = str(value or "").strip()
    try:
        observed = datetime.strptime(text, "%Y%m%d").replace(
            hour=15,
            minute=30,
            tzinfo=SEOUL,
        )
    except ValueError:
        return ""
    return observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def response_items(payload: object) -> List[Dict[str, object]]:
    root = payload.get("response") if isinstance(payload, dict) else {}
    body = root.get("body") if isinstance(root, dict) else {}
    items = body.get("items") if isinstance(body, dict) else {}
    item = items.get("item") if isinstance(items, dict) else []
    if isinstance(item, dict):
        return [dict(item)]
    return [dict(row) for row in item or [] if isinstance(row, dict)]


def response_total_count(payload: object) -> int:
    root = payload.get("response") if isinstance(payload, dict) else {}
    body = root.get("body") if isinstance(root, dict) else {}
    try:
        return max(0, int(str(body.get("totalCount") or "0"))) if isinstance(body, dict) else 0
    except (TypeError, ValueError):
        return 0


def stable_revision(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def public_data_timeout(settings: Dict[str, object]) -> float:
    try:
        value = float(settings.get("externalPublicDataTimeoutSeconds") or 12)
    except (TypeError, ValueError):
        value = 12.0
    return max(1.0, min(60.0, value))


def fetch_public_data_rows(
    json_fetcher: Callable,
    endpoint: str,
    settings: Dict[str, object],
    params: Dict[str, object],
    *,
    label: str,
    max_rows: int = 500,
) -> List[Dict[str, object]]:
    """Fetch bounded official rows while keeping the service key out of state."""

    service_key = encoded_service_key(settings.get("publicDataPortalServiceKey"))
    if not service_key:
        raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
    page_size = max(1, min(100, int(max_rows or 100)))
    rows: List[Dict[str, object]] = []
    page_no = 1
    total_count = 0
    while len(rows) < max_rows:
        query = urllib.parse.urlencode({
            "pageNo": page_no,
            "numOfRows": page_size,
            "resultType": "json",
            **{key: value for key, value in dict(params or {}).items() if value not in (None, "")},
        })
        request_url = endpoint + "?serviceKey=" + service_key + "&" + query
        try:
            payload = json_fetcher(
                request_url,
                {"Accept": "application/json", "User-Agent": "OrbitAlpha/1.0"},
                public_data_timeout(settings),
            )
        except Exception as error:
            raise RuntimeError(
                "공공데이터포털 " + label + " 조회 실패: " + api_error_text(error)
            ) from error
        root = payload.get("response") if isinstance(payload, dict) else {}
        header = root.get("header") if isinstance(root, dict) else {}
        result_code = str(header.get("resultCode") or "").strip()
        if result_code != "00":
            message = str(header.get("resultMsg") or "알 수 없는 응답 오류").strip()
            raise RuntimeError("공공데이터포털 " + label + " 오류 " + result_code + ": " + message)
        page_rows = response_items(payload)
        rows.extend(page_rows[: max_rows - len(rows)])
        total_count = max(total_count, response_total_count(payload))
        if not page_rows or len(page_rows) < page_size or (total_count and len(rows) >= total_count):
            break
        page_no += 1
    return rows


def latest_rows(rows: Iterable[Dict[str, object]], date_field: str = "basDt") -> List[Dict[str, object]]:
    values = [dict(row) for row in rows or [] if isinstance(row, dict)]
    latest = max((str(row.get(date_field) or "") for row in values), default="")
    return [row for row in values if str(row.get(date_field) or "") == latest] if latest else values


class PublicDataPortalSecurityMasterAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="public-data.kr-security-master",
        provider_id="data-go-kr-fsc",
        capability="official-security-and-issuer-identity",
        cadence_seconds=21600,
        freshness_seconds=604800,
        priority=58,
        rate_limit_seconds=1,
        enabled_setting="externalPublicDataReferenceEnabled",
        cadence_setting="externalDataPublicReferenceCadenceSeconds",
        freshness_setting="externalDataPublicReferenceFreshnessSeconds",
        max_partitions_setting="externalDataPublicReferenceMaxPartitions",
        max_partitions=100,
        revision_mode="current",
        materiality_policy="official-security-identity",
    )

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(
        self,
        subjects: Iterable[ExternalSubject],
        settings: Dict[str, object],
    ) -> List[CollectionPartition]:
        if not str(settings.get("publicDataPortalServiceKey") or "").strip():
            return []
        return equity_partitions(self.descriptor, subjects, is_korean_equity)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        symbol = str(job.subject.symbol or job.partition_key or "").strip().zfill(6)
        rows = fetch_public_data_rows(
            self.json_fetcher,
            PUBLIC_DATA_SECURITY_MASTER_ENDPOINT,
            settings,
            {"likeSrtnCd": symbol},
            label="금융위원회 KRX 상장종목정보",
            max_rows=100,
        )
        exact = [row for row in rows if normalized_security_code(row.get("srtnCd")) == symbol]
        exact.sort(key=lambda row: str(row.get("basDt") or ""), reverse=True)
        if not exact:
            return observation(
                self.descriptor,
                symbol,
                {"securityMaster": {}},
                preferred_revision="no-security-master",
                preferred_source_as_of=utc_now_iso(),
                watermark={"emptyResult": True, "symbol": symbol},
                quality={
                    "dataUsable": True,
                    "provider": self.descriptor.provider_id,
                    "officialSource": True,
                    "emptyResult": True,
                    "decisionEligibility": "identity-only",
                },
                empty_result=True,
                retain_previous=True,
            )
        row = exact[0]
        base_date = str(row.get("basDt") or "").strip()
        fetched_at = utc_now_iso()
        master = {
            "symbol": symbol,
            "name": str(row.get("itmsNm") or job.subject.name or symbol).strip(),
            "legalName": str(row.get("corpNm") or row.get("itmsNm") or job.subject.name or symbol).strip(),
            "market": str(row.get("mrktCtg") or job.subject.market or "KR").strip(),
            "isin": str(row.get("isinCd") or "").strip(),
            "corporateRegistrationNumber": str(row.get("crno") or "").strip(),
            "baseDate": base_date,
            "sourceAsOf": source_as_of_for_base_date(base_date),
            "fetchedAt": fetched_at,
            "provider": PUBLIC_DATA_PROVIDER_LABEL,
            "sourceUrl": PUBLIC_DATA_SECURITY_MASTER_PAGE,
            "sourceType": "official-security-master",
            "decisionEligibility": "identity-only",
            "realTime": False,
        }
        return observation(
            self.descriptor,
            symbol,
            {"securityMaster": {symbol: master}},
            preferred_revision=stable_revision(master),
            preferred_source_as_of=master["sourceAsOf"] or fetched_at,
            watermark={
                "baseDate": base_date,
                "symbol": symbol,
                "crno": master["corporateRegistrationNumber"],
                "isin": master["isin"],
            },
            quality={
                "dataUsable": bool(master["corporateRegistrationNumber"] and master["isin"]),
                "provider": self.descriptor.provider_id,
                "officialSource": True,
                "decisionEligibility": "identity-only",
            },
        )

    def followup_requests(
        self,
        observation_value,
        _settings: Dict[str, object],
    ) -> List[FollowupCollectionRequest]:
        symbol = str(observation_value.subject_key or "").upper().strip()
        masters = observation_value.payload.get("securityMaster") if isinstance(observation_value.payload, dict) else {}
        master = masters.get(symbol) if isinstance(masters, dict) and isinstance(masters.get(symbol), dict) else {}
        crno = str(master.get("corporateRegistrationNumber") or "").strip()
        if not symbol or not crno:
            return []
        base_date = str(master.get("baseDate") or "").strip()
        try:
            collected_at = datetime.fromisoformat(
                str(observation_value.fetched_at or "").replace("Z", "+00:00")
            ).astimezone(SEOUL)
        except (TypeError, ValueError):
            collected_at = datetime.now(SEOUL)
        daily_bucket = collected_at.strftime("%Y%m%d")
        monthly_bucket = collected_at.strftime("%Y%m")
        iso_year, iso_week, _iso_day = collected_at.isocalendar()
        weekly_bucket = str(iso_year) + "W" + str(iso_week).zfill(2)
        subject = ExternalSubject(
            subject_key=symbol,
            symbol=symbol,
            name=str(master.get("name") or symbol),
            market=str(master.get("market") or "KR"),
            currency="KRW",
            source="public-data-security-master",
        )
        common = {
            "symbol": symbol,
            "crno": crno,
            "isin": str(master.get("isin") or ""),
            "companyName": str(master.get("legalName") or master.get("name") or symbol),
            "market": str(master.get("market") or "KR"),
            "masterBaseDate": base_date,
        }
        plans = [
            ("public-data.kr-company-profile", monthly_bucket, 53),
            ("public-data.kr-company-financials", weekly_bucket, 52),
            ("public-data.kr-dividends", daily_bucket, 57),
            ("public-data.kr-capital-events", daily_bucket, 59),
            ("public-data.kr-shareholder-rights", daily_bucket, 58),
        ]
        return [
            FollowupCollectionRequest(
                dataset_id=dataset_id,
                partition_key=symbol + ":" + bucket,
                subject=subject,
                watermark={**common, "collectionBucket": bucket},
                priority=priority,
            )
            for dataset_id, bucket, priority in plans
        ]


class PublicDataPortalMarketIndexAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="public-data.kr-market-index-daily",
        provider_id="data-go-kr-fsc",
        capability="official-daily-market-index",
        cadence_seconds=21600,
        freshness_seconds=259200,
        priority=47,
        rate_limit_seconds=1,
        enabled_setting="externalPublicDataReferenceEnabled",
        cadence_setting="externalDataPublicReferenceCadenceSeconds",
        freshness_setting="externalDataPublicReferenceFreshnessSeconds",
        max_partitions=1,
        revision_mode="current",
        materiality_policy="official-daily-reference",
    )

    INDEXES = (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"))

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(
        self,
        _subjects: Iterable[ExternalSubject],
        settings: Dict[str, object],
    ) -> List[CollectionPartition]:
        if not str(settings.get("publicDataPortalServiceKey") or "").strip():
            return []
        return [
            CollectionPartition(
                self.descriptor.dataset_id,
                "global",
                ExternalSubject(
                    "global",
                    name="한국 주식시장 기준지수",
                    market="KR",
                    currency="KRW",
                    source="public-data-index",
                ),
                self.descriptor.priority,
            )
        ]

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        indices = {}
        for index_key, index_name in self.INDEXES:
            rows = fetch_public_data_rows(
                self.json_fetcher,
                PUBLIC_DATA_MARKET_INDEX_ENDPOINT,
                settings,
                {"idxNm": index_name},
                label="금융위원회 주가지수시세정보 " + index_name,
                max_rows=100,
            )
            exact = [row for row in rows if str(row.get("idxNm") or "").strip() == index_name]
            exact.sort(key=lambda row: str(row.get("basDt") or ""), reverse=True)
            if not exact:
                continue
            row = exact[0]
            base_date = str(row.get("basDt") or "").strip()
            fetched_at = utc_now_iso()
            indices[index_key] = {
                "indexKey": index_key,
                "indexName": index_name,
                "indexCategory": str(row.get("idxCsf") or "").strip(),
                "baseDate": base_date,
                "sourceAsOf": source_as_of_for_base_date(base_date),
                "fetchedAt": fetched_at,
                "baseIndex": numeric_value(row.get("basIdx")),
                "constituentCount": numeric_value(row.get("epyItmsCnt"), integer=True),
                "open": numeric_value(row.get("mkp")),
                "high": numeric_value(row.get("hipr")),
                "low": numeric_value(row.get("lopr")),
                "close": numeric_value(row.get("clpr")),
                "change": numeric_value(row.get("vs")),
                "changePercent": numeric_value(row.get("fltRt")),
                "volume": numeric_value(row.get("trqu"), integer=True),
                "tradingValue": numeric_value(row.get("trPrc"), integer=True),
                "marketCap": numeric_value(row.get("lstgMrktTotAmt"), integer=True),
                "yearHigh": numeric_value(row.get("yrWRcrdHgst")),
                "yearHighDate": str(row.get("yrWRcrdHgstDt") or "").strip(),
                "yearLow": numeric_value(row.get("yrWRcrdLwst")),
                "yearLowDate": str(row.get("yrWRcrdLwstDt") or "").strip(),
                "provider": PUBLIC_DATA_PROVIDER_LABEL,
                "sourceUrl": PUBLIC_DATA_MARKET_INDEX_PAGE,
                "sourceType": "official-daily-market-index",
                "decisionEligibility": "market-context",
                "realTime": False,
            }
        if not indices:
            return observation(
                self.descriptor,
                "global",
                {"marketIndices": {}},
                preferred_revision="no-market-index",
                preferred_source_as_of=utc_now_iso(),
                watermark={"emptyResult": True, "indices": []},
                quality={"dataUsable": True, "officialSource": True, "emptyResult": True},
                empty_result=True,
                retain_previous=True,
            )
        source_as_of = max((str(item.get("sourceAsOf") or "") for item in indices.values()), default="")
        fetched_at = max((str(item.get("fetchedAt") or "") for item in indices.values()), default=utc_now_iso())
        return observation(
            self.descriptor,
            "global",
            {"marketIndices": indices},
            preferred_revision=stable_revision(indices),
            preferred_source_as_of=source_as_of or fetched_at,
            watermark={
                "baseDate": max((str(item.get("baseDate") or "") for item in indices.values()), default=""),
                "indices": sorted(indices),
            },
            quality={
                "dataUsable": all(item.get("baseDate") and item.get("close") is not None for item in indices.values()),
                "provider": self.descriptor.provider_id,
                "officialSource": True,
                "realTime": False,
                "decisionEligibility": "market-context",
                "indexCount": len(indices),
                "expectedIndexCount": len(self.INDEXES),
                "coverageState": "sufficient" if len(indices) == len(self.INDEXES) else "partial",
                "missingIndices": sorted({key for key, _name in self.INDEXES} - set(indices)),
            },
        )


class PublicDataPortalStockPriceAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="public-data.kr-stock-daily",
        provider_id="data-go-kr-fsc",
        capability="official-daily-stock-price",
        cadence_seconds=21600,
        freshness_seconds=259200,
        priority=45,
        rate_limit_seconds=1,
        enabled_setting="externalPublicDataStockEnabled",
        cadence_setting="externalDataPublicStockCadenceSeconds",
        freshness_setting="externalDataPublicStockFreshnessSeconds",
        max_partitions_setting="externalDataPublicStockMaxPartitions",
        max_partitions=100,
        revision_mode="current",
        materiality_policy="official-daily-reference",
    )

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(
        self,
        subjects: Iterable[ExternalSubject],
        settings: Dict[str, object],
    ) -> List[CollectionPartition]:
        if not str(settings.get("publicDataPortalServiceKey") or "").strip():
            return []
        return equity_partitions(self.descriptor, subjects, is_korean_equity)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        symbol = str(job.subject.symbol or job.partition_key or "").strip().zfill(6)
        service_key = encoded_service_key(settings.get("publicDataPortalServiceKey"))
        if not service_key:
            raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
        query = urllib.parse.urlencode({
            "pageNo": 1,
            "numOfRows": 10,
            "resultType": "json",
            "likeSrtnCd": symbol,
        })
        request_url = PUBLIC_DATA_STOCK_PRICE_ENDPOINT + "?serviceKey=" + service_key + "&" + query
        timeout = max(1.0, min(60.0, float(settings.get("externalPublicDataTimeoutSeconds") or 12)))
        try:
            payload = self.json_fetcher(
                request_url,
                {"Accept": "application/json", "User-Agent": "OrbitAlpha/1.0"},
                timeout,
            )
        except Exception as error:
            raise RuntimeError(
                "공공데이터포털 금융위원회 주식시세정보 조회 실패: " + api_error_text(error)
            ) from error

        root = payload.get("response") if isinstance(payload, dict) else {}
        header = root.get("header") if isinstance(root, dict) else {}
        result_code = str(header.get("resultCode") or "").strip()
        if result_code != "00":
            message = str(header.get("resultMsg") or "알 수 없는 응답 오류").strip()
            raise RuntimeError("공공데이터포털 금융위원회 주식시세정보 오류 " + result_code + ": " + message)

        rows = [row for row in response_items(payload) if normalized_security_code(row.get("srtnCd")) == symbol]
        rows.sort(key=lambda row: str(row.get("basDt") or ""), reverse=True)
        if not rows:
            return observation(
                self.descriptor,
                symbol,
                {"officialDailyPrices": {}},
                preferred_revision="no-official-daily-price",
                preferred_source_as_of=utc_now_iso(),
                watermark={"emptyResult": True},
                quality={
                    "dataUsable": True,
                    "provider": self.descriptor.provider_id,
                    "officialSource": True,
                    "emptyResult": True,
                    "decisionEligibility": "reference-only",
                },
                empty_result=True,
                retain_previous=True,
            )

        row = rows[0]
        base_date = str(row.get("basDt") or "").strip()
        source_as_of = source_as_of_for_base_date(base_date)
        collected_at = utc_now_iso()
        official_price = {
            "symbol": symbol,
            "name": str(row.get("itmsNm") or job.subject.name or symbol).strip(),
            "market": str(row.get("mrktCtg") or job.subject.market or "KR").strip(),
            "isin": str(row.get("isinCd") or "").strip(),
            "baseDate": base_date,
            "sourceAsOf": source_as_of,
            "fetchedAt": collected_at,
            "open": numeric_value(row.get("mkp"), integer=True),
            "high": numeric_value(row.get("hipr"), integer=True),
            "low": numeric_value(row.get("lopr"), integer=True),
            "close": numeric_value(row.get("clpr"), integer=True),
            "change": numeric_value(row.get("vs"), integer=True),
            "changePercent": numeric_value(row.get("fltRt")),
            "volume": numeric_value(row.get("trqu"), integer=True),
            "tradingValue": numeric_value(row.get("trPrc"), integer=True),
            "listedShares": numeric_value(row.get("lstgStCnt"), integer=True),
            "marketCap": numeric_value(row.get("mrktTotAmt"), integer=True),
            "provider": "금융위원회·공공데이터포털",
            "sourceUrl": PUBLIC_DATA_STOCK_PRICE_PAGE,
            "sourceType": "official-daily-close",
            "updateCadence": "daily",
            "realTime": False,
            "decisionEligibility": "reference-only",
            "usageRole": "official-cross-check-and-history-backfill",
            "publicationPolicy": "next-business-day-after-13:00-kst",
        }
        return observation(
            self.descriptor,
            symbol,
            {"officialDailyPrices": {symbol: official_price}},
            preferred_revision=base_date + ":" + symbol + ":" + str(official_price.get("close") or ""),
            preferred_source_as_of=source_as_of or collected_at,
            watermark={"baseDate": base_date, "symbol": symbol},
            quality={
                "dataUsable": bool(base_date and official_price.get("close") is not None),
                "provider": self.descriptor.provider_id,
                "officialSource": True,
                "realTime": False,
                "decisionEligibility": "reference-only",
                "usageRole": "official-cross-check-and-history-backfill",
            },
        )
