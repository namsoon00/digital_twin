from datetime import datetime, timezone
from typing import Dict

from .data_freshness import (
    aggregate_freshness,
    freshness_record,
    int_setting,
    kis_stage_freshness_records,
    parse_datetime,
)
from .market_hours import evaluate_market_hours, normalize_market_key
from .message_types import INVESTMENT_INSIGHT
from .portfolio import Position


def position_observation_profiles(
    position: Position,
    runtime_context: Dict[str, object] = None,
) -> Dict[str, Dict[str, object]]:
    runtime_context = runtime_context if isinstance(runtime_context, dict) else {}
    settings = runtime_context.get("settings") if isinstance(runtime_context.get("settings"), dict) else {}
    checked_at = parse_datetime(runtime_context.get("asOf")) or datetime.now(timezone.utc)
    market = normalize_market_key(position.market or position.currency)
    market_decision = market_session_profile(position, checked_at)
    quote = freshness_record(
        position.quote_source or "position-quote",
        INVESTMENT_INSIGHT,
        settings=settings,
        source_fetched_at=position.source_fetched_at,
        source_as_of=position.source_as_of,
        data_quality=position.data_quality,
        now=checked_at,
        max_age_minutes=int_setting(settings, "dataFreshnessQuoteMaxAgeMinutes", 10),
        require_source_as_of=True,
    )
    indicator_as_of = position.indicator_as_of or position.source_as_of
    indicator_fetched_at = position.indicator_fetched_at or position.source_fetched_at
    trend = freshness_record(
        (position.quote_source or "position") + " candles",
        INVESTMENT_INSIGHT,
        settings=settings,
        source_fetched_at=indicator_fetched_at,
        source_as_of=indicator_as_of,
        data_quality=position.data_quality,
        now=checked_at,
        max_age_minutes=int_setting(settings, "dataFreshnessTechnicalMaxAgeMinutes", 4320),
        require_source_as_of=True,
    )
    stage_records = kis_stage_freshness_records(
        position.to_dict(),
        INVESTMENT_INSIGHT,
        settings=settings,
        now=checked_at,
    )
    flow = aggregate_freshness(
        stage_records,
        INVESTMENT_INSIGHT,
        settings=settings,
        now=checked_at,
    ) if stage_records else dict(quote)
    if stage_records:
        source_rows = [item for item in stage_records if isinstance(item, dict)]
        source_as_of_rows = [
            str(item.get("sourceAsOf") or "")
            for item in source_rows
            if str(item.get("sourceAsOf") or "").strip()
        ]
        fetched_at_rows = [
            str(item.get("sourceFetchedAt") or "")
            for item in source_rows
            if str(item.get("sourceFetchedAt") or "").strip()
        ]
        flow["source"] = ", ".join(dict.fromkeys(
            str(item.get("source") or "") for item in source_rows if str(item.get("source") or "").strip()
        ))
        flow["sourceAsOf"] = min(source_as_of_rows) if source_as_of_rows else ""
        flow["sourceFetchedAt"] = max(fetched_at_rows) if fetched_at_rows else ""
        flow["dataQuality"] = position.data_quality
    if not stage_records:
        flow["reason"] = "별도 수급 기준시각이 없어 시세 기준시각을 사용합니다."
        flow["source"] = position.quote_source or "position-flow"
    return {
        "quote": observation_profile(quote, market_decision, "quote", market),
        "trend": observation_profile(trend, market_decision, "trend", market),
        "flow": observation_profile(flow, market_decision, "flow", market),
        "static": static_observation_profile(market_decision, market),
    }


def market_session_profile(position: Position, now: datetime) -> Dict[str, object]:
    market = normalize_market_key(position.market or position.currency)
    if not market and str(position.market or "").upper() in {"CRYPTO", "COIN"}:
        return {
            "market": "CRYPTO",
            "status": "open",
            "label": "24시간 시장",
            "reason": "24시간 거래 시장",
            "localTime": now.astimezone(timezone.utc).isoformat(),
            "timezone": "UTC",
        }
    if not market:
        return {
            "market": "",
            "status": "unknown",
            "label": "시장 미확인",
            "reason": "시장 식별 정보가 없습니다.",
            "localTime": now.astimezone(timezone.utc).isoformat(),
            "timezone": "UTC",
        }
    decision = evaluate_market_hours(
        INVESTMENT_INSIGHT,
        {
            "market": market,
            "currency": position.currency,
            "symbol": position.symbol,
        },
        True,
        [market],
        now=now,
    )
    return {
        "market": market,
        "status": decision.status,
        "label": decision.label,
        "reason": decision.reason,
        "localTime": decision.local_time,
        "timezone": decision.timezone,
        "openTime": decision.open_time,
        "closeTime": decision.close_time,
    }


def observation_profile(
    record: Dict[str, object],
    market_session: Dict[str, object],
    domain: str,
    market: str,
) -> Dict[str, object]:
    status = str((record or {}).get("status") or "unknown")
    session_status = str((market_session or {}).get("status") or "unknown")
    source_as_of = str((record or {}).get("sourceAsOf") or "")
    source_fetched_at = str((record or {}).get("sourceFetchedAt") or "")
    timestamp_present = bool(source_as_of)
    evidence_usable = status == "fresh" and session_status == "open" and timestamp_present
    if not timestamp_present:
        gate_reason = "원천 기준시각이 없어 투자 판단 근거로 사용할 수 없습니다."
    elif status != "fresh":
        gate_reason = str((record or {}).get("reason") or "신선도 기준 미충족")
    elif session_status != "open":
        gate_reason = str((market_session or {}).get("reason") or "현재 거래 세션이 닫혀 있습니다.")
    else:
        gate_reason = "원천 기준시각과 거래 세션 기준을 통과했습니다."
    return {
        "observationDomain": domain,
        "freshnessRequired": True,
        "freshnessStatus": status,
        "freshnessReason": str((record or {}).get("reason") or ""),
        "freshnessGateReason": gate_reason,
        "freshnessAgeMinutes": (record or {}).get("ageMinutes"),
        "maxAgeMinutes": (record or {}).get("maxAgeMinutes"),
        "observationSource": str((record or {}).get("source") or ""),
        "sourceAsOf": source_as_of,
        "sourceFetchedAt": source_fetched_at,
        "sourceTimestampPresent": timestamp_present,
        "sourceAsOfConfidence": str((record or {}).get("sourceAsOfConfidence") or ("provider" if timestamp_present else "missing")),
        "sourceReliability": source_reliability(record),
        "judgementEvidenceUsable": evidence_usable,
        "market": market,
        "marketSessionStatus": session_status,
        "marketSessionLabel": str((market_session or {}).get("label") or ""),
        "marketSessionReason": str((market_session or {}).get("reason") or ""),
        "marketSessionLocalTime": str((market_session or {}).get("localTime") or ""),
        "marketSessionTimezone": str((market_session or {}).get("timezone") or ""),
    }


def static_observation_profile(
    market_session: Dict[str, object],
    market: str,
) -> Dict[str, object]:
    return {
        "observationDomain": "static",
        "freshnessRequired": False,
        "freshnessStatus": "not-applicable",
        "freshnessReason": "시간이 지나도 의미가 바뀌지 않는 정책·구조 정보입니다.",
        "freshnessGateReason": "신선도 검사 대상이 아닙니다.",
        "sourceTimestampPresent": False,
        "judgementEvidenceUsable": True,
        "market": market,
        "marketSessionStatus": str((market_session or {}).get("status") or "unknown"),
        "marketSessionLabel": str((market_session or {}).get("label") or ""),
    }


def source_reliability(record: Dict[str, object]) -> float:
    quality = str((record or {}).get("dataQuality") or "").strip().lower()
    if quality in {"actual", "live", "verified"}:
        return 90.0
    if quality in {"mixed", "partial"}:
        return 65.0
    if quality in {"cached", "stale", "unavailable"}:
        return 35.0
    return 55.0


def profile_for_domain(
    profiles: Dict[str, Dict[str, object]],
    domain: str,
) -> Dict[str, object]:
    item = (profiles or {}).get(domain)
    if not isinstance(item, dict):
        item = (profiles or {}).get("static")
    return dict(item or {})
