"""Selected official Korean industry indicators from the KOSIS OpenAPI."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Mapping
import urllib.parse

from digital_twin.modules.market_data.public import (
    CollectionJob,
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
)
from digital_twin.modules.portfolio.domain.portfolio import utc_now_iso
from ...external_signal_utils import default_json_fetcher
from .base import global_partition, observation


KOSIS_DATA_ENDPOINT = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
KOSIS_SOURCE_URL = "https://kosis.kr/openapi/"

# These broad indicators are deliberately small and auditable. Sector-specific
# tables should be admitted through a separate reviewed mapping instead of a
# keyword search that can silently change its meaning.
KOSIS_SERIES = (
    {
        "seriesId": "KR_ALL_INDUSTRY_PRODUCTION",
        "orgId": "101",
        "tableId": "DT_1JH20202",
        "itemId": "T1",
        "object1": "1",
        "period": "M",
        "label": "전산업생산지수(계절조정)",
    },
    {
        "seriesId": "KR_LEADING_CYCLE",
        "orgId": "101",
        "tableId": "DT_1C8015",
        "itemId": "T1",
        "object1": "A03",
        "period": "M",
        "label": "선행지수 순환변동치",
    },
    {
        "seriesId": "KR_RETAIL_SALES",
        "orgId": "101",
        "tableId": "DT_1K41017",
        "itemId": "T2",
        "object1": "G0",
        "period": "M",
        "label": "소매판매액 불변지수",
    },
)


def _number(value: object):
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"-", "..."}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _timeout(settings: Mapping[str, object]) -> float:
    try:
        parsed = float(settings.get("externalKosisTimeoutSeconds") or 12)
    except (TypeError, ValueError):
        parsed = 12.0
    return max(1.0, min(60.0, parsed))


def _period_text(value: object) -> str:
    text = str(value or "").strip()
    if len(text) == 6 and text.isdigit():
        return text[:4] + "-" + text[4:]
    if len(text) == 8 and text.isdigit():
        return text[:4] + "-" + text[4:6] + "-" + text[6:]
    return text


def _period_source_as_of(value: object) -> str:
    period = _period_text(value)
    if len(period) == 7:
        return period + "-01T00:00:00+09:00"
    if len(period) == 10:
        return period + "T00:00:00+09:00"
    return period


class KosisIndustryIndicatorAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="kosis.indicators",
        provider_id="kosis",
        capability="official-korean-industry-indicators",
        cadence_seconds=21600,
        freshness_seconds=45 * 86400,
        priority=43,
        rate_limit_seconds=1,
        enabled_setting="externalKosisEnabled",
        cadence_setting="externalDataKosisCadenceSeconds",
        freshness_setting="externalDataKosisFreshnessSeconds",
        max_partitions=1,
        revision_mode="changes",
        materiality_policy="published-observation",
    )

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(
        self,
        _subjects: Iterable[ExternalSubject],
        settings: Dict[str, object],
    ) -> List[CollectionPartition]:
        if not str(settings.get("kosisApiKey") or "").strip():
            return []
        return global_partition(self.descriptor)

    def _rows(self, settings: Mapping[str, object], spec: Mapping[str, object]) -> List[Dict[str, object]]:
        params = {
            "method": "getList",
            "apiKey": str(settings.get("kosisApiKey") or "").strip(),
            "format": "json",
            "jsonVD": "Y",
            "orgId": spec["orgId"],
            "tblId": spec["tableId"],
            "itmId": spec["itemId"],
            "objL1": spec["object1"],
            "prdSe": spec["period"],
            "newEstPrdCnt": "24",
            "prdInterval": "1",
            "outputFields": "ORG_ID TBL_ID TBL_NM ITM_ID ITM_NM UNIT_NM PRD_SE PRD_DE DT LST_CHN_DE",
        }
        try:
            payload = self.json_fetcher(
                KOSIS_DATA_ENDPOINT + "?" + urllib.parse.urlencode(params),
                {"Accept": "application/json", "User-Agent": "OrbitAlpha/1.0"},
                _timeout(settings),
            )
        except Exception as error:
            raise RuntimeError("KOSIS 통계 조회에 실패했습니다: " + type(error).__name__) from error
        if isinstance(payload, dict) and payload.get("err"):
            raise RuntimeError("KOSIS 응답 오류: " + str(payload.get("err") or "unknown"))
        rows = [
            dict(row) for row in payload or []
            if isinstance(row, dict) and _number(row.get("DT")) is not None
        ] if isinstance(payload, list) else []
        rows.sort(key=lambda row: str(row.get("PRD_DE") or ""))
        return rows

    @staticmethod
    def _item(spec: Mapping[str, object], rows: List[Dict[str, object]]) -> Dict[str, object]:
        current = rows[-1]
        previous = rows[-2] if len(rows) > 1 else {}
        value = _number(current.get("DT"))
        previous_value = _number(previous.get("DT"))
        item = {
            "provider": "KOSIS",
            "officialSource": True,
            "seriesId": str(spec.get("seriesId") or ""),
            "label": str(spec.get("label") or ""),
            "sourceOrganizationId": str(spec.get("orgId") or ""),
            "sourceTableId": str(spec.get("tableId") or ""),
            "sourceItemId": str(spec.get("itemId") or ""),
            "sourceObject1": str(spec.get("object1") or ""),
            "date": _period_text(current.get("PRD_DE")),
            "observationDate": _period_text(current.get("PRD_DE")),
            "sourceAsOf": _period_text(current.get("PRD_DE")),
            "lastChangedAtSource": str(current.get("LST_CHN_DE") or ""),
            "value": value,
            "unit": str(current.get("UNIT_NM") or "index"),
            "sourceUrl": KOSIS_SOURCE_URL,
            "history": [
                {"period": _period_text(row.get("PRD_DE")), "value": _number(row.get("DT"))}
                for row in rows
            ],
        }
        if previous_value is not None:
            delta = value - previous_value
            item.update({
                "previousValue": previous_value,
                "previousDate": _period_text(previous.get("PRD_DE")),
                "deltaValue": round(delta, 6),
                "deltaPct": round(delta / previous_value * 100.0, 6) if previous_value else None,
            })
        if len(rows) > 12:
            year_ago = _number(rows[-13].get("DT"))
            if year_ago:
                item["yearAgoValue"] = year_ago
                item["yearOverYearPct"] = round((value - year_ago) / year_ago * 100.0, 6)
                item["yearAgoPeriod"] = _period_text(rows[-13].get("PRD_DE"))
        return item

    def fetch(self, _job: CollectionJob, settings: Dict[str, object]):
        series = {}
        missing = []
        for spec in KOSIS_SERIES:
            rows = self._rows(settings, spec)
            if not rows:
                missing.append(str(spec["seriesId"]))
                continue
            series[str(spec["seriesId"])] = self._item(spec, rows)
        if not series:
            raise RuntimeError("KOSIS에서 사용할 수 있는 산업지표를 받지 못했습니다.")
        observation_period = max(
            (str(item.get("sourceAsOf") or "") for item in series.values()),
            default="",
        )
        source_as_of = _period_source_as_of(observation_period) or utc_now_iso()
        return observation(
            self.descriptor,
            "global",
            {"macro": {"series": series, "sourceAsOf": source_as_of}},
            preferred_source_as_of=source_as_of,
            watermark={"observationPeriod": observation_period, "series": sorted(series)},
            quality={
                "dataUsable": bool(series),
                "provider": self.descriptor.provider_id,
                "officialSource": True,
                "coverageState": "sufficient" if not missing else "partial",
                "missingSeries": missing,
            },
        )


__all__ = ["KosisIndustryIndicatorAdapter", "KOSIS_SERIES"]
