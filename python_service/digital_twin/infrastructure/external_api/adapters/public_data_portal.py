from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import urllib.parse
from typing import Callable, Dict, Iterable, List

from zoneinfo import ZoneInfo

from ....application.external_data.contracts import (
    CollectionJob,
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
)
from ....domain.portfolio import utc_now_iso
from ...external_signal_utils import api_error_text, default_json_fetcher
from .base import equity_partitions, observation


PUBLIC_DATA_STOCK_PRICE_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
)
PUBLIC_DATA_STOCK_PRICE_PAGE = "https://www.data.go.kr/data/15094808/openapi.do"
SEOUL = ZoneInfo("Asia/Seoul")


def is_korean_equity(subject: ExternalSubject) -> bool:
    symbol = str(subject.symbol or subject.subject_key or "").strip()
    return symbol.isdigit() and len(symbol) == 6


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

        rows = [row for row in response_items(payload) if str(row.get("srtnCd") or "").zfill(6) == symbol]
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
