"""Official KOSPI and KOSDAQ daily index observations from KRX OpenAPI."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Mapping
import urllib.error
import urllib.parse
from zoneinfo import ZoneInfo

from digital_twin.modules.market_data.public import (
    CollectionJob,
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
)
from digital_twin.modules.portfolio.domain.portfolio import utc_now_iso
from ...external_signal_utils import default_json_fetcher
from .base import global_partition, observation, source_revision


KRX_API_ROOT = "https://data-dbg.krx.co.kr/svc/apis/idx/"
KRX_SOURCE_URL = "https://openapi.krx.co.kr/"
SEOUL = ZoneInfo("Asia/Seoul")
KRX_INDEX_ENDPOINTS = (
    ("KOSPI", "코스피", "kospi_dd_trd"),
    ("KOSDAQ", "코스닥", "kosdaq_dd_trd"),
)


def _number(value: object, *, integer: bool = False):
    text = str(value or "").replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    return int(parsed) if integer else parsed


def _source_as_of(base_date: object) -> str:
    text = str(base_date or "").strip()
    if len(text) != 8 or not text.isdigit():
        return ""
    return text[:4] + "-" + text[4:6] + "-" + text[6:] + "T15:30:00+09:00"


def _timeout(settings: Mapping[str, object]) -> float:
    try:
        parsed = float(settings.get("externalKrxTimeoutSeconds") or 12)
    except (TypeError, ValueError):
        parsed = 12.0
    return max(1.0, min(60.0, parsed))


class KrxMarketIndexAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="krx.market-indices",
        provider_id="krx-openapi",
        capability="official-korean-market-indices",
        cadence_seconds=21600,
        freshness_seconds=259200,
        priority=49,
        rate_limit_seconds=1,
        enabled_setting="externalKrxEnabled",
        cadence_setting="externalDataKrxCadenceSeconds",
        freshness_setting="externalDataKrxFreshnessSeconds",
        max_partitions=1,
        revision_mode="current",
        materiality_policy="official-daily-reference",
    )

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(
        self,
        _subjects: Iterable[ExternalSubject],
        settings: Dict[str, object],
    ) -> List[CollectionPartition]:
        if not str(settings.get("krxOpenApiKey") or "").strip():
            return []
        return global_partition(self.descriptor)

    def _rows(
        self,
        settings: Mapping[str, object],
        endpoint: str,
        base_date: str,
    ) -> List[Dict[str, object]]:
        url = KRX_API_ROOT + endpoint + "?" + urllib.parse.urlencode({"basDd": base_date})
        try:
            payload = self.json_fetcher(
                url,
                {
                    "Accept": "application/json",
                    "AUTH_KEY": str(settings.get("krxOpenApiKey") or "").strip(),
                    "User-Agent": "OrbitAlpha/1.0",
                },
                _timeout(settings),
            )
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise RuntimeError(
                    "KRX OpenAPI 인증은 저장됐지만 해당 지수 API 활용 승인이 필요합니다."
                ) from error
            raise RuntimeError("KRX OpenAPI 지수 조회 실패: HTTP " + str(error.code)) from error
        except Exception as error:
            raise RuntimeError("KRX OpenAPI 지수 조회 실패: " + type(error).__name__) from error
        return [dict(row) for row in (payload.get("OutBlock_1") or []) if isinstance(row, dict)] \
            if isinstance(payload, dict) else []

    @staticmethod
    def _main_index(rows: List[Dict[str, object]], index_key: str) -> Dict[str, object]:
        expected = {"KOSPI": {"코스피", "KOSPI"}, "KOSDAQ": {"코스닥", "KOSDAQ"}}[index_key]
        exact = [
            row for row in rows
            if str(row.get("IDX_NM") or "").strip().upper() in {name.upper() for name in expected}
        ]
        return exact[0] if exact else {}

    @staticmethod
    def _index_item(index_key: str, index_name: str, row: Mapping[str, object]) -> Dict[str, object]:
        base_date = str(row.get("BAS_DD") or "").strip()
        return {
            "indexKey": index_key,
            "indexName": index_name,
            "indexCategory": str(row.get("IDX_CLSS") or "").strip(),
            "baseDate": base_date,
            "sourceAsOf": _source_as_of(base_date),
            "fetchedAt": utc_now_iso(),
            "open": _number(row.get("OPNPRC_IDX")),
            "high": _number(row.get("HGPRC_IDX")),
            "low": _number(row.get("LWPRC_IDX")),
            "close": _number(row.get("CLSPRC_IDX")),
            "change": _number(row.get("CMPPREVDD_IDX")),
            "changePercent": _number(row.get("FLUC_RT")),
            "volume": _number(row.get("ACC_TRDVOL"), integer=True),
            "tradingValue": _number(row.get("ACC_TRDVAL"), integer=True),
            "marketCap": _number(row.get("MKTCAP"), integer=True),
            "provider": "KRX OpenAPI",
            "sourceUrl": KRX_SOURCE_URL,
            "sourceType": "official-daily-market-index",
            "decisionEligibility": "market-context",
            "realTime": False,
        }

    def fetch(self, _job: CollectionJob, settings: Dict[str, object]):
        today = datetime.now(SEOUL).date()
        selected = {}
        selected_date = ""
        # KRX daily data can lag the current session. Search a bounded window
        # and keep every index on the same official trading date.
        for offset in range(0, 12):
            base_date = (today - timedelta(days=offset)).strftime("%Y%m%d")
            candidate = {}
            for index_key, index_name, endpoint in KRX_INDEX_ENDPOINTS:
                rows = self._rows(settings, endpoint, base_date)
                row = self._main_index(rows, index_key)
                if row:
                    candidate[index_key] = self._index_item(index_key, index_name, row)
            if len(candidate) == len(KRX_INDEX_ENDPOINTS):
                selected = candidate
                selected_date = base_date
                break
        if not selected:
            return observation(
                self.descriptor,
                "global",
                {"marketIndices": {}},
                preferred_revision="no-market-index",
                preferred_source_as_of=utc_now_iso(),
                watermark={"emptyResult": True},
                quality={"dataUsable": True, "officialSource": True, "emptyResult": True},
                empty_result=True,
                retain_previous=True,
            )
        fragment = {"marketIndices": selected}
        return observation(
            self.descriptor,
            "global",
            fragment,
            preferred_revision=source_revision(fragment),
            preferred_source_as_of=max(
                (str(item.get("sourceAsOf") or "") for item in selected.values()),
                default=utc_now_iso(),
            ),
            watermark={"baseDate": selected_date, "indices": sorted(selected)},
            quality={
                "dataUsable": True,
                "provider": self.descriptor.provider_id,
                "officialSource": True,
                "realTime": False,
                "decisionEligibility": "market-context",
                "coverageState": "sufficient",
                "indexCount": len(selected),
            },
        )


__all__ = ["KrxMarketIndexAdapter", "KRX_INDEX_ENDPOINTS"]
