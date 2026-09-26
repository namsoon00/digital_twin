"""Official Korean rates and FX observations from the Bank of Korea ECOS API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


ECOS_API_ROOT = "https://ecos.bok.or.kr/api/StatisticSearch"
ECOS_SOURCE_URL = "https://ecos.bok.or.kr/api/"

ECOS_RATE_SERIES = (
    {
        "seriesId": "KRGB3Y",
        "statCode": "817Y002",
        "itemCode": "010200000",
        "label": "한국 국고채 3년 금리",
        "cycle": "D",
        "lookbackDays": 75,
    },
    {
        "seriesId": "KRGB10Y",
        "statCode": "817Y002",
        "itemCode": "010210000",
        "label": "한국 국고채 10년 금리",
        "cycle": "D",
        "lookbackDays": 75,
    },
    {
        "seriesId": "KRCAA3Y",
        "statCode": "817Y002",
        "itemCode": "010300000",
        "label": "한국 회사채 3년 AA- 금리",
        "cycle": "D",
        "lookbackDays": 75,
    },
    {
        "seriesId": "KRBASE",
        "statCode": "722Y001",
        "itemCode": "0101000",
        "label": "한국은행 기준금리",
        "cycle": "D",
        "lookbackDays": 800,
    },
)

ECOS_USDKRW = {
    "statCode": "731Y001",
    "itemCode": "0000001",
    "label": "원/미국달러 매매기준율",
    "cycle": "D",
    "lookbackDays": 30,
}


def _number(value: object):
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _timeout(settings: Mapping[str, object]) -> float:
    try:
        parsed = float(settings.get("externalEcosTimeoutSeconds") or 10)
    except (TypeError, ValueError):
        parsed = 10.0
    return max(1.0, min(60.0, parsed))


def _date_text(value: object) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return text[:4] + "-" + text[4:6] + "-" + text[6:]
    if len(text) == 6 and text.isdigit():
        return text[:4] + "-" + text[4:]
    return text


def _window_delta(rows: List[Dict[str, object]], offset: int, *, basis_points: bool) -> object:
    if len(rows) <= offset:
        return None
    current = _number(rows[-1].get("DATA_VALUE"))
    previous = _number(rows[-1 - offset].get("DATA_VALUE"))
    if current is None or previous is None:
        return None
    change = current - previous
    return round(change * 100.0 if basis_points else change, 6)


class EcosMacroAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="ecos.macro",
        provider_id="bok-ecos",
        capability="official-korean-rates-and-fx",
        cadence_seconds=21600,
        freshness_seconds=172800,
        priority=61,
        rate_limit_seconds=1,
        enabled_setting="externalEcosEnabled",
        cadence_setting="externalDataEcosCadenceSeconds",
        freshness_setting="externalDataEcosFreshnessSeconds",
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
        if not str(settings.get("ecosApiKey") or "").strip():
            return []
        return global_partition(self.descriptor)

    def _rows(self, settings: Mapping[str, object], spec: Mapping[str, object]) -> List[Dict[str, object]]:
        api_key = str(settings.get("ecosApiKey") or "").strip()
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=max(7, int(spec.get("lookbackDays") or 75)))
        parts = [
            ECOS_API_ROOT,
            urllib.parse.quote(api_key, safe=""),
            "json",
            "kr",
            "1",
            "1000",
            str(spec.get("statCode") or ""),
            str(spec.get("cycle") or "D"),
            start.strftime("%Y%m%d"),
            today.strftime("%Y%m%d"),
            str(spec.get("itemCode") or ""),
        ]
        try:
            payload = self.json_fetcher(
                "/".join(parts),
                {"Accept": "application/json", "User-Agent": "OrbitAlpha/1.0"},
                _timeout(settings),
            )
        except Exception as error:
            raise RuntimeError("한국은행 ECOS 통계 조회에 실패했습니다: " + type(error).__name__) from error
        block = payload.get("StatisticSearch") if isinstance(payload, dict) else {}
        result = block.get("RESULT") if isinstance(block, dict) else {}
        if isinstance(result, dict) and str(result.get("CODE") or "").strip() not in {"", "INFO-000"}:
            raise RuntimeError("한국은행 ECOS 응답 오류: " + str(result.get("CODE") or "unknown"))
        rows = block.get("row") if isinstance(block, dict) else []
        usable = [
            dict(row) for row in rows or []
            if isinstance(row, dict) and _number(row.get("DATA_VALUE")) is not None
        ]
        usable.sort(key=lambda row: str(row.get("TIME") or ""))
        return usable

    @staticmethod
    def _rate_item(spec: Mapping[str, object], rows: List[Dict[str, object]]) -> Dict[str, object]:
        current = rows[-1]
        previous = rows[-2] if len(rows) > 1 else {}
        value = _number(current.get("DATA_VALUE"))
        previous_value = _number(previous.get("DATA_VALUE"))
        item = {
            "provider": "한국은행 ECOS",
            "officialSource": True,
            "seriesId": str(spec.get("seriesId") or ""),
            "sourceSeriesCode": str(spec.get("statCode") or ""),
            "sourceItemCode": str(spec.get("itemCode") or ""),
            "label": str(spec.get("label") or ""),
            "date": _date_text(current.get("TIME")),
            "observationDate": _date_text(current.get("TIME")),
            "sourceAsOf": _date_text(current.get("TIME")),
            "value": value,
            "unit": str(current.get("UNIT_NAME") or "percent"),
            "currency": "KRW",
            "sourceUrl": ECOS_SOURCE_URL,
            "changeBasis": "percentage-point-to-basis-points",
        }
        if previous_value is not None:
            item.update({
                "previousValue": previous_value,
                "previousDate": _date_text(previous.get("TIME")),
                "deltaBp": round((value - previous_value) * 100.0, 6),
                "delta1dBp": round((value - previous_value) * 100.0, 6),
                "comparison1dDate": _date_text(previous.get("TIME")),
            })
        delta_5d = _window_delta(rows, 5, basis_points=True)
        delta_20d = _window_delta(rows, 20, basis_points=True)
        if delta_5d is not None:
            item["delta5dBp"] = delta_5d
            item["comparison5dDate"] = _date_text(rows[-6].get("TIME"))
        if delta_20d is not None:
            item["delta20dBp"] = delta_20d
            item["comparison20dDate"] = _date_text(rows[-21].get("TIME"))
        return item

    @staticmethod
    def _fx_item(rows: List[Dict[str, object]]) -> Dict[str, object]:
        current = rows[-1]
        previous = rows[-2] if len(rows) > 1 else {}
        value = _number(current.get("DATA_VALUE"))
        previous_value = _number(previous.get("DATA_VALUE"))
        item = {
            "pair": "USDKRW",
            "base": "USD",
            "quote": "KRW",
            "rate": value,
            "value": value,
            "provider": "한국은행 ECOS",
            "officialSource": True,
            "sourceType": "official-reference-rate",
            "evidenceStrength": "official",
            "date": _date_text(current.get("TIME")),
            "sourceAsOf": _date_text(current.get("TIME")),
            "observedAt": utc_now_iso(),
            "sourceSeriesCode": ECOS_USDKRW["statCode"],
            "sourceItemCode": ECOS_USDKRW["itemCode"],
            "sourceUrl": ECOS_SOURCE_URL,
        }
        if previous_value is not None:
            delta = value - previous_value
            item.update({
                "previousRate": previous_value,
                "previousValue": previous_value,
                "previousDate": _date_text(previous.get("TIME")),
                "deltaKrw": round(delta, 6),
                "deltaPct": round(delta / previous_value * 100.0, 6) if previous_value else None,
            })
        if len(rows) > 7:
            seven_day = _number(rows[-8].get("DATA_VALUE"))
            if seven_day:
                item["delta7dKrw"] = round(value - seven_day, 6)
                item["delta7dPct"] = round((value - seven_day) / seven_day * 100.0, 6)
        return item

    def fetch(self, _job: CollectionJob, settings: Dict[str, object]):
        series = {}
        missing = []
        for spec in ECOS_RATE_SERIES:
            rows = self._rows(settings, spec)
            if not rows:
                missing.append(str(spec["seriesId"]))
                continue
            series[str(spec["seriesId"])] = self._rate_item(spec, rows)
        fx_rows = self._rows(settings, ECOS_USDKRW)
        fx_rates = {"USDKRW": self._fx_item(fx_rows)} if fx_rows else {}
        if not series and not fx_rates:
            raise RuntimeError("한국은행 ECOS에서 사용할 수 있는 금리·환율 관측값을 받지 못했습니다.")
        source_dates = [
            str(item.get("sourceAsOf") or "")
            for item in [*series.values(), *fx_rates.values()]
            if isinstance(item, dict)
        ]
        source_as_of = max(source_dates, default=utc_now_iso())
        fragment = {
            "macro": {"series": series, "sourceAsOf": source_as_of},
            "fxRates": fx_rates,
        }
        return observation(
            self.descriptor,
            "global",
            fragment,
            preferred_source_as_of=source_as_of,
            watermark={"observationDate": source_as_of, "series": sorted(series), "fxPairs": sorted(fx_rates)},
            quality={
                "dataUsable": bool(series or fx_rates),
                "provider": self.descriptor.provider_id,
                "officialSource": True,
                "coverageState": "sufficient" if not missing and fx_rates else "partial",
                "missingSeries": missing + ([] if fx_rates else ["USDKRW"]),
            },
        )


__all__ = ["EcosMacroAdapter", "ECOS_RATE_SERIES", "ECOS_USDKRW"]
