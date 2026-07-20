from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set

from .message_types import (
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_DART_DISCLOSURE,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_MACRO_SHIFT,
    HOLDING_TIMING,
    INVESTMENT_INSIGHT,
    PORTFOLIO_HOLDINGS_SNAPSHOT,
    MODEL_BUY,
    MODEL_SELL,
    MONITOR_DECISION_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_POSITION_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_VALUE_CHANGE,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_ONTOLOGY_SIGNAL,
    WATCHLIST_QUOTE,
)


DATA_FRESHNESS_MESSAGE_TYPES = {
    INVESTMENT_INSIGHT,
    PORTFOLIO_HOLDINGS_SNAPSHOT,
    MODEL_BUY,
    MODEL_SELL,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_ONTOLOGY_SIGNAL,
    WATCHLIST_QUOTE,
    HOLDING_TIMING,
    MONITOR_POSITION_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_VALUE_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_DECISION_CHANGE,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_MACRO_SHIFT,
    EXTERNAL_DART_DISCLOSURE,
}

QUOTE_MESSAGE_TYPES = {
    INVESTMENT_INSIGHT,
    PORTFOLIO_HOLDINGS_SNAPSHOT,
    MODEL_BUY,
    MODEL_SELL,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_ONTOLOGY_SIGNAL,
    WATCHLIST_QUOTE,
    HOLDING_TIMING,
    MONITOR_POSITION_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_VALUE_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_DECISION_CHANGE,
}
KIS_STAGE_MAX_AGE_SETTINGS = {
    "price": ("dataFreshnessKisPriceMaxAgeMinutes", 3),
    "ccnl": ("dataFreshnessKisMicrostructureMaxAgeMinutes", 2),
    "orderbook": ("dataFreshnessKisMicrostructureMaxAgeMinutes", 2),
    "investor": ("dataFreshnessKisInvestorMaxAgeMinutes", 5),
}

KIS_STAGE_EVIDENCE_FIELDS = {
    "price": {
        "currentPrice",
        "changeRate",
        "priceChangeRate",
        "ma5Distance",
        "ma20Distance",
        "ma60Distance",
        "ma20Slope",
        "ma60Slope",
        "trendCurve",
        "trendDynamicRiskScore",
        "volume",
        "volumeRatio",
        "timeAdjustedVolumeRatio",
        "tradingValue",
    },
    "ccnl": {"tradeStrength", "buyVolume", "sellVolume", "buyShare", "sellShare"},
    "orderbook": {"bidAskImbalance", "orderbookAskVolume", "orderbookBidVolume"},
    "investor": {
        "investorFlowScore",
        "smartMoneyNetVolume",
        "foreignBuyVolume",
        "foreignNetAmount",
        "foreignNetVolume",
        "foreignSellVolume",
        "individualBuyVolume",
        "individualNetAmount",
        "individualNetVolume",
        "individualSellVolume",
        "institutionBuyVolume",
        "institutionNetAmount",
        "institutionNetVolume",
        "institutionSellVolume",
    },
}


def bool_setting(settings: Dict[str, object], key: str, fallback: bool = True) -> bool:
    value = (settings or {}).get(key)
    if value in (None, ""):
        return fallback
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def int_setting(settings: Dict[str, object], key: str, fallback: int, minimum: int = 1, maximum: int = 1440 * 30) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def parse_datetime(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text + "T00:00:00+00:00")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def utc_iso(value=None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def age_minutes(value: object, now=None):
    parsed = parse_datetime(value)
    if not parsed:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))


def max_age_minutes_for_message_type(message_type: str, settings: Dict[str, object] = None) -> int:
    settings = settings or {}
    key = str(message_type or "")
    if key == EXTERNAL_CRYPTO_MOVE:
        return int_setting(settings, "dataFreshnessExternalCryptoMaxAgeMinutes", 10)
    if key == EXTERNAL_EQUITY_MOVE:
        return int_setting(settings, "dataFreshnessExternalEquityMaxAgeMinutes", 10)
    if key == EXTERNAL_MACRO_SHIFT:
        return int_setting(settings, "dataFreshnessMacroMaxAgeMinutes", 120)
    if key == EXTERNAL_DART_DISCLOSURE:
        return int_setting(settings, "dataFreshnessDisclosureMaxAgeMinutes", 120)
    if key in QUOTE_MESSAGE_TYPES:
        return int_setting(settings, "dataFreshnessQuoteMaxAgeMinutes", 10)
    return int_setting(settings, "dataFreshnessDefaultMaxAgeMinutes", 10)


def max_age_minutes_for_kis_stage(stage: str, settings: Dict[str, object] = None) -> int:
    key, fallback = KIS_STAGE_MAX_AGE_SETTINGS.get(str(stage or ""), ("dataFreshnessQuoteMaxAgeMinutes", 10))
    return int_setting(settings or {}, key, fallback)


def data_freshness_required(message_type: str) -> bool:
    return str(message_type or "") in DATA_FRESHNESS_MESSAGE_TYPES


def combine_quality(*values: object) -> str:
    qualities = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not qualities:
        return ""
    unique = []
    for quality in qualities:
        if quality not in unique:
            unique.append(quality)
    if any(quality in {"cached", "stale", "unavailable"} for quality in unique):
        return "cached" if len(unique) == 1 and unique[0] == "cached" else "mixed"
    return unique[0] if len(unique) == 1 else "mixed"


def freshness_record(
    source: str,
    message_type: str,
    settings: Dict[str, object] = None,
    source_fetched_at: object = "",
    source_as_of: object = "",
    data_quality: object = "",
    now=None,
    max_age_minutes: int = 0,
    require_source_as_of: bool = False,
) -> Dict[str, object]:
    settings = settings or {}
    max_age = int(max_age_minutes or max_age_minutes_for_message_type(message_type, settings))
    timestamp = source_as_of or ("" if require_source_as_of else source_fetched_at)
    age = age_minutes(timestamp, now=now)
    quality = str(data_quality or "").strip()
    if age is None:
        status = "unknown"
        reason = "기준시각 없음"
    elif age <= max_age:
        status = "fresh"
        reason = "신선도 기준 통과"
    else:
        status = "stale"
        reason = "기준 " + str(max_age) + "분 초과"
    return {
        "source": str(source or "").strip() or "unknown",
        "status": status,
        "reason": reason,
        "ageMinutes": age,
        "maxAgeMinutes": max_age,
        "sourceFetchedAt": str(source_fetched_at or ""),
        "sourceAsOf": str(source_as_of or ""),
        "sourceTimestampPresent": bool(source_as_of or (source_fetched_at and not require_source_as_of)),
        "sourceAsOfRequired": bool(require_source_as_of),
        "dataQuality": quality,
        "checkedAt": utc_iso(now),
    }


def kis_stage_freshness_records(position: Dict[str, object], message_type: str, settings: Dict[str, object] = None, now=None) -> List[Dict[str, object]]:
    item = position or {}
    coverage = item.get("marketSignalCoverage") if isinstance(item.get("marketSignalCoverage"), dict) else item.get("market_signal_coverage")
    if not isinstance(coverage, dict):
        return []
    records: List[Dict[str, object]] = []
    for stage, raw_stage in coverage.items():
        stage_payload = raw_stage if isinstance(raw_stage, dict) else {}
        status = str(stage_payload.get("status") or "").strip()
        fields = stage_payload.get("fields") if isinstance(stage_payload.get("fields"), list) else []
        source = "KIS " + str(stage or "signal")
        if status == "available" and fields:
            fetched_at = stage_payload.get("fetchedAt") or stage_payload.get("sourceFetchedAt")
            source_as_of = stage_payload.get("sourceAsOf") or ""
            if not fetched_at and not source_as_of:
                records.append({
                    "source": source,
                    "status": "unknown",
                    "reason": "단계별 기준시각 없음",
                    "ageMinutes": None,
                    "maxAgeMinutes": max_age_minutes_for_kis_stage(str(stage), settings),
                    "sourceFetchedAt": "",
                    "sourceAsOf": "",
                    "dataQuality": item.get("dataQuality") or item.get("data_quality") or "",
                    "checkedAt": utc_iso(now),
                    "stage": str(stage or ""),
                    "fields": list(fields or []),
                })
                continue
            record = freshness_record(
                source,
                message_type,
                settings=settings,
                source_fetched_at=fetched_at,
                source_as_of=source_as_of,
                data_quality=item.get("dataQuality") or item.get("data_quality") or "",
                now=now,
                max_age_minutes=max_age_minutes_for_kis_stage(str(stage), settings),
                require_source_as_of=True,
            )
            record["stage"] = str(stage or "")
            record["fields"] = list(fields or [])
            if "realTime" in stage_payload:
                record["realTime"] = bool(stage_payload.get("realTime"))
            for key in [
                "cadence",
                "transport",
                "freshnessStatus",
                "sourceAsOfConfidence",
                "latencyStatus",
                "latencyLabel",
                "latencyReason",
                "aiUsableAsStrongEvidence",
                "judgementEvidenceUsable",
            ]:
                if stage_payload.get(key) not in (None, ""):
                    record[key] = stage_payload.get(key)
            if stage_payload.get("unchangedCount") not in (None, ""):
                record["unchangedCount"] = stage_payload.get("unchangedCount")
            records.append(record)
        elif status in {"stale", "unknown"}:
            records.append({
                "source": source,
                "status": "stale" if status == "stale" else "unknown",
                "reason": str(stage_payload.get("staleReason") or stage_payload.get("reason") or "단계별 신선도 문제"),
                "ageMinutes": None,
                "maxAgeMinutes": max_age_minutes_for_kis_stage(str(stage), settings),
                "sourceFetchedAt": str(stage_payload.get("fetchedAt") or stage_payload.get("sourceFetchedAt") or ""),
                "sourceAsOf": str(stage_payload.get("sourceAsOf") or ""),
                "dataQuality": item.get("dataQuality") or item.get("data_quality") or "",
                "checkedAt": utc_iso(now),
                "stage": str(stage or ""),
                "fields": list(fields or []),
                "unchangedCount": stage_payload.get("unchangedCount"),
                "realTime": bool(stage_payload.get("realTime")) if "realTime" in stage_payload else None,
                "transport": stage_payload.get("transport"),
                "freshnessStatus": stage_payload.get("freshnessStatus"),
                "sourceAsOfConfidence": stage_payload.get("sourceAsOfConfidence"),
                "aiUsableAsStrongEvidence": stage_payload.get("aiUsableAsStrongEvidence"),
                "judgementEvidenceUsable": stage_payload.get("judgementEvidenceUsable"),
                "cadence": stage_payload.get("cadence"),
                "latencyStatus": stage_payload.get("latencyStatus"),
                "latencyLabel": stage_payload.get("latencyLabel"),
                "latencyReason": stage_payload.get("latencyReason"),
            })
    return records


def freshness_from_position(position: Dict[str, object], message_type: str, settings: Dict[str, object] = None, now=None) -> Dict[str, object]:
    item = position or {}
    has_explicit_source_clock = any(key in item for key in ["sourceAsOf", "source_as_of", "sourceFetchedAt", "source_fetched_at"])
    source_as_of = item.get("source_as_of") or item.get("sourceAsOf")
    if not has_explicit_source_clock:
        source_as_of = item.get("updated_at") or item.get("updatedAt")
    base = freshness_record(
        item.get("quote_source") or item.get("quoteSource") or item.get("source") or "position",
        message_type,
        settings=settings,
        source_fetched_at=item.get("source_fetched_at") or item.get("sourceFetchedAt"),
        source_as_of=source_as_of,
        data_quality=item.get("data_quality") or item.get("dataQuality"),
        now=now,
        require_source_as_of=True,
    )
    records = [base]
    records.extend(kis_stage_freshness_records(item, message_type, settings=settings, now=now))
    return aggregate_freshness(records, message_type, settings=settings, now=now)


def aggregate_freshness(records: Iterable[Dict[str, object]], message_type: str, settings: Dict[str, object] = None, now=None) -> Dict[str, object]:
    items = [dict(item) for item in records or [] if isinstance(item, dict)]
    if not items:
        return {
            "status": "unknown",
            "reason": "신선도 입력 없음",
            "sources": [],
            "checkedAt": utc_iso(now),
            "maxAgeMinutes": max_age_minutes_for_message_type(message_type, settings),
        }
    stale = [item for item in items if str(item.get("status") or "") == "stale"]
    unknown = [item for item in items if str(item.get("status") or "") == "unknown"]
    if stale:
        status = "stale"
        reason = ", ".join(str(item.get("source") or "unknown") + " " + str(item.get("reason") or "") for item in stale[:3])
    elif unknown:
        status = "unknown"
        reason = ", ".join(str(item.get("source") or "unknown") + " " + str(item.get("reason") or "") for item in unknown[:3])
    else:
        status = "fresh"
        reason = "모든 소스 신선도 기준 통과"
    ages = [item.get("ageMinutes") for item in items if isinstance(item.get("ageMinutes"), int)]
    max_ages = [int(item.get("maxAgeMinutes") or 0) for item in items if int(item.get("maxAgeMinutes") or 0)]
    return {
        "status": status,
        "reason": reason,
        "ageMinutes": max(ages) if ages else None,
        "maxAgeMinutes": min(max_ages) if max_ages else max_age_minutes_for_message_type(message_type, settings),
        "sources": items,
        "checkedAt": utc_iso(now),
    }


@dataclass
class DataFreshnessDecision:
    enabled: bool
    evaluated: bool
    should_send: bool
    status: str = "bypass"
    reason: str = ""
    age_minutes: object = None
    max_age_minutes: object = None
    stale_sources: List[str] = field(default_factory=list)
    ignored_sources: List[str] = field(default_factory=list)

    def to_context(self) -> Dict[str, object]:
        return {
            "dataFreshnessGateEnabled": bool(self.enabled),
            "dataFreshnessEvaluated": bool(self.evaluated),
            "dataFreshnessDecision": "send" if self.should_send else "suppressed",
            "dataFreshnessStatus": self.status,
            "dataFreshnessReason": self.reason,
            "dataFreshnessAgeMinutes": self.age_minutes,
            "dataFreshnessMaxAgeMinutes": self.max_age_minutes,
            "dataFreshnessStaleSources": list(self.stale_sources),
            "dataFreshnessIgnoredSources": list(self.ignored_sources),
        }


def freshness_leaf_records(records: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    leaves: List[Dict[str, object]] = []
    for item in records or []:
        if not isinstance(item, dict):
            continue
        children = item.get("sources")
        if isinstance(children, list) and children:
            leaves.extend(freshness_leaf_records(children))
        else:
            leaves.append(dict(item))
    return leaves


def selected_inference_fact_fields(context: Dict[str, object]) -> List[str]:
    relation_context = context.get("ontologyRelationContext")
    if not isinstance(relation_context, dict):
        return []
    decision = relation_context.get("decision")
    if not isinstance(decision, dict):
        return []
    breakdown = decision.get("scoreBreakdown")
    if not isinstance(breakdown, dict):
        return []
    return list(dict.fromkeys(
        str(field or "").strip()
        for field in breakdown.get("appliedFactFields") or []
        if str(field or "").strip()
    ))


def required_kis_stages_for_notification(context: Dict[str, object]) -> Optional[Set[str]]:
    fields = set(selected_inference_fact_fields(context or {}))
    if not fields:
        return None
    required = {"price"}
    for stage, stage_fields in KIS_STAGE_EVIDENCE_FIELDS.items():
        if fields & stage_fields:
            required.add(stage)
    return required


def evaluate_notification_data_freshness(context: Dict[str, object], settings: Dict[str, object] = None, now=None) -> DataFreshnessDecision:
    settings = settings or {}
    if not bool_setting(settings, "dataFreshnessEnabled", True):
        return DataFreshnessDecision(False, False, True, status="bypass", reason="데이터 신선도 게이트 꺼짐")
    context = context or {}
    message_type = str(context.get("messageType") or context.get("rule") or "")
    if not data_freshness_required(message_type):
        return DataFreshnessDecision(True, False, True, status="bypass", reason="신선도 게이트 대상 아님")
    data = context.get("dataFreshness") if isinstance(context.get("dataFreshness"), dict) else {}
    if not data:
        return DataFreshnessDecision(
            True,
            True,
            False,
            status="missing",
            reason="신선도 메타데이터 없음",
            stale_sources=["unknown"],
        )
    records = freshness_leaf_records(data.get("sources") or [data])
    required_kis_stages = required_kis_stages_for_notification(context)
    ignored_records = []
    if required_kis_stages is not None:
        required_records = []
        for item in records:
            stage = str(item.get("stage") or "").strip()
            status = str(item.get("status") or "").strip()
            if stage in KIS_STAGE_MAX_AGE_SETTINGS and stage not in required_kis_stages:
                if status in {"stale", "unknown"}:
                    ignored_records.append(item)
                continue
            required_records.append(item)
        records = required_records
    record = aggregate_freshness(records, message_type, settings=settings, now=now)
    status = str(record.get("status") or "unknown")
    stale_sources = [
        str(item.get("source") or "unknown")
        for item in record.get("sources") or []
        if str(item.get("status") or "") in {"stale", "unknown"}
    ]
    should_send = status == "fresh"
    return DataFreshnessDecision(
        True,
        True,
        should_send,
        status=status,
        reason=str(record.get("reason") or ""),
        age_minutes=record.get("ageMinutes"),
        max_age_minutes=record.get("maxAgeMinutes"),
        stale_sources=stale_sources,
        ignored_sources=list(dict.fromkeys(
            str(item.get("source") or "unknown")
            for item in ignored_records
        )),
    )
