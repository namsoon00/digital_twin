from datetime import datetime, timezone
from typing import Dict, Iterable, List

from .market_data import clamp, number
from .portfolio import Position, utc_now_iso


SOURCE_KEYS = {
    "equityQuotes": "Alpha Vantage",
    "cryptoMarkets": "CoinGecko",
    "macro": "FRED",
    "secFilings": "SEC EDGAR",
    "dartDisclosures": "OpenDART",
    "newsHeadlines": "GDELT News",
}

CONFIG_KEYS = {
    "equityQuotes": "alphaVantageApiKey",
    "macro": "fredApiKey",
    "dartDisclosures": "opendartApiKey",
}


def parse_iso_datetime(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def age_minutes(value: str) -> int:
    parsed = parse_iso_datetime(value)
    if not parsed:
        return 0
    return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))


def non_cash_symbols(positions: Iterable[Position]) -> List[str]:
    symbols = []
    seen = set()
    for position in positions or []:
        if position.is_cash():
            continue
        symbol = str(position.symbol or "").upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def group_count(value) -> int:
    if isinstance(value, dict):
        if "series" in value and isinstance(value.get("series"), dict):
            return len(value.get("series") or {})
        return len(value)
    if isinstance(value, list):
        return len(value)
    return 1 if value not in (None, "", False) else 0


def status_rows(signals: Dict[str, object]) -> List[Dict[str, object]]:
    rows = signals.get("statuses") if isinstance(signals.get("statuses"), list) else []
    return [item for item in rows if isinstance(item, dict)]


def source_status(signals: Dict[str, object], source_label: str) -> Dict[str, object]:
    rows = [item for item in status_rows(signals) if str(item.get("source") or "") == source_label]
    if not rows:
        return {"ok": True, "messages": []}
    return {
        "ok": all(bool(item.get("ok")) for item in rows),
        "messages": [str(item.get("message") or "") for item in rows if str(item.get("message") or "")],
    }


def configured_source(key: str, settings: Dict[str, object]) -> bool:
    if key == "cryptoMarkets" or key == "newsHeadlines" or key == "secFilings":
        return True
    config_key = CONFIG_KEYS.get(key)
    return bool(str((settings or {}).get(config_key) or "").strip()) if config_key else True


def symbol_coverage(signals: Dict[str, object], symbols: List[str]) -> Dict[str, object]:
    if not symbols:
        return {"requested": 0, "covered": 0, "ratio": 1.0, "missingSymbols": []}
    covered = set()
    for key in ["equityQuotes", "secFilings", "dartDisclosures", "newsHeadlines"]:
        group = signals.get(key)
        if isinstance(group, dict):
            covered.update(str(symbol or "").upper() for symbol in group.keys() if str(symbol or "").strip())
    missing = [symbol for symbol in symbols if symbol not in covered]
    return {
        "requested": len(symbols),
        "covered": len(symbols) - len(missing),
        "ratio": round((len(symbols) - len(missing)) / len(symbols), 4),
        "missingSymbols": missing[:20],
    }


def fundamental_event_count(signals: Dict[str, object]) -> int:
    count = 0
    filings = signals.get("secFilings") if isinstance(signals.get("secFilings"), dict) else {}
    for item in filings.values():
        if not isinstance(item, dict):
            continue
        facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
        latest = item.get("latestFiling") if isinstance(item.get("latestFiling"), dict) else {}
        if latest:
            count += 1
        if any(isinstance(value, dict) and value.get("value") not in (None, "") for value in facts.values()):
            count += 1
    return count


def evaluate_external_signal_quality(
    signals: Dict[str, object],
    positions: Iterable[Position] = None,
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    signals = signals if isinstance(signals, dict) else {}
    settings = settings or {}
    fetched_at = str(signals.get("fetchedAt") or utc_now_iso())
    symbols = non_cash_symbols(positions or [])
    coverage = symbol_coverage(signals, symbols)
    statuses = status_rows(signals)
    source_rows = []
    for key, label in SOURCE_KEYS.items():
        payload = signals.get(key)
        count = group_count(payload)
        status = source_status(signals, label)
        configured = configured_source(key, settings)
        source_rows.append({
            "key": key,
            "source": label,
            "configured": configured,
            "itemCount": count,
            "ok": bool(status.get("ok")) and (configured or count > 0),
            "messages": list(status.get("messages") or [])[:5],
        })
    configured_rows = [row for row in source_rows if row["configured"]]
    healthy_rows = [row for row in configured_rows if row["ok"] and row["itemCount"] > 0]
    source_health = (len(healthy_rows) / len(configured_rows)) if configured_rows else 1.0
    error_count = len([row for row in statuses if not row.get("ok")])
    coverage_score = coverage["ratio"] * 100
    source_score = source_health * 100
    error_penalty = min(25.0, error_count * 6.0)
    score = clamp(coverage_score * 0.45 + source_score * 0.4 + (100 - error_penalty) * 0.15, 0.0, 100.0)
    return {
        "generatedAt": utc_now_iso(),
        "fetchedAt": fetched_at,
        "ageMinutes": age_minutes(fetched_at),
        "score": round(score, 2),
        "coverageScore": round(coverage_score, 2),
        "sourceHealthScore": round(source_score, 2),
        "errorCount": error_count,
        "symbolCoverage": coverage,
        "sourceCoverage": source_rows,
        "fundamentalEventCount": fundamental_event_count(signals),
        "macroSeriesCount": group_count((signals.get("macro") or {}).get("series") if isinstance(signals.get("macro"), dict) else {}),
        "cryptoMarketCount": group_count(signals.get("cryptoMarkets")),
        "newsHeadlineCount": sum(group_count((item or {}).get("items")) for item in (signals.get("newsHeadlines") or {}).values()) if isinstance(signals.get("newsHeadlines"), dict) else 0,
    }


def attach_external_signal_quality(
    signals: Dict[str, object],
    positions: Iterable[Position] = None,
    settings: Dict[str, object] = None,
) -> Dict[str, object]:
    payload = dict(signals or {})
    quality = evaluate_external_signal_quality(payload, positions=positions, settings=settings)
    payload["quality"] = quality
    payload["freshness"] = {
        "fetchedAt": quality["fetchedAt"],
        "ageMinutes": quality["ageMinutes"],
        "status": "fresh" if int(quality["ageMinutes"] or 0) <= int(number((settings or {}).get("externalApiFetchIntervalMinutes")) or 60) else "stale",
    }
    payload["provenance"] = {
        "sources": [row["source"] for row in quality.get("sourceCoverage", []) if row.get("configured")],
        "unavailableSources": [row["source"] for row in quality.get("sourceCoverage", []) if row.get("configured") and not row.get("ok")],
    }
    return payload
