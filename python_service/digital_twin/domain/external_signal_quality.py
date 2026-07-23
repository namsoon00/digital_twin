from datetime import datetime, timezone
from typing import Dict, Iterable, List

from .market_data import number
from .portfolio import Position


SOURCE_KEYS = {
    "equityQuotes": "Alpha Vantage",
    "cryptoMarkets": "CoinGecko",
    "macro": "FRED",
    "secFilings": "SEC EDGAR",
    "dartDisclosures": "OpenDART",
    "newsHeadlines": "GDELT News",
    "yfinanceData": "yfinance",
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


def utc_iso(value=None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def age_minutes(value: str, now=None) -> int:
    parsed = parse_iso_datetime(value)
    if not parsed:
        return 0
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))


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
        return {"ok": True, "messages": [], "deferred": False}
    request_ok = all(bool(item.get("ok")) for item in rows)
    data_usable = all(bool(item.get("dataUsable", True)) for item in rows)
    return {
        "ok": request_ok and data_usable,
        "messages": [str(item.get("message") or "") for item in rows if str(item.get("message") or "")],
        "deferred": any(bool(item.get("deferred")) for item in rows),
    }


def yfinance_freshness_messages(payload: object) -> List[str]:
    if not isinstance(payload, dict):
        return []
    messages: List[str] = []
    for symbol, item in payload.items():
        if not isinstance(item, dict):
            continue
        freshness = item.get("freshness") if isinstance(item.get("freshness"), dict) else {}
        status = str(freshness.get("status") or "").strip()
        if status in {"stale", "unknown"}:
            modules = freshness.get("staleModules") if isinstance(freshness.get("staleModules"), list) else []
            module_text = ",".join(str(module) for module in modules[:4] if str(module or "").strip())
            messages.append(str(symbol) + " " + status + ((" · " + module_text) if module_text else ""))
    return messages


def configured_source(key: str, settings: Dict[str, object]) -> bool:
    if key == "cryptoMarkets" or key == "newsHeadlines" or key == "secFilings" or key == "yfinanceData":
        return True
    config_key = CONFIG_KEYS.get(key)
    return bool(str((settings or {}).get(config_key) or "").strip()) if config_key else True


def symbol_coverage(signals: Dict[str, object], symbols: List[str]) -> Dict[str, object]:
    if not symbols:
        return {"requested": 0, "covered": 0, "ratio": 1.0, "missingSymbols": []}
    covered = set()
    for key in ["equityQuotes", "secFilings", "dartDisclosures", "newsHeadlines", "yfinanceData"]:
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
    now=None,
) -> Dict[str, object]:
    signals = signals if isinstance(signals, dict) else {}
    settings = settings or {}
    fetched_at = str(signals.get("fetchedAt") or utc_iso(now))
    symbols = non_cash_symbols(positions or [])
    coverage = symbol_coverage(signals, symbols)
    statuses = status_rows(signals)
    source_rows = []
    for key, label in SOURCE_KEYS.items():
        payload = signals.get(key)
        count = group_count(payload)
        status = source_status(signals, label)
        messages = list(status.get("messages") or [])[:5]
        ok = bool(status.get("ok"))
        if key == "yfinanceData":
            freshness_messages = yfinance_freshness_messages(payload)
            if freshness_messages:
                ok = False
                messages.extend(freshness_messages[:5 - len(messages)])
        configured = configured_source(key, settings)
        source_rows.append({
            "key": key,
            "source": label,
            "configured": configured,
            "itemCount": count,
            "ok": ok and (configured or count > 0),
            "deferred": bool(status.get("deferred")),
            "messages": messages[:5],
        })
    configured_rows = [row for row in source_rows if row["configured"]]
    healthy_rows = [row for row in configured_rows if row["ok"] and row["itemCount"] > 0]
    source_health = (len(healthy_rows) / len(configured_rows)) if configured_rows else 1.0
    error_count = len([row for row in statuses if not row.get("ok")])
    if configured_rows and not healthy_rows:
        data_state = "unavailable"
    elif coverage["ratio"] < 0.5 or source_health < 0.5:
        data_state = "insufficient"
    elif coverage["ratio"] < 1.0 or source_health < 1.0 or error_count:
        data_state = "partial"
    else:
        data_state = "sufficient"
    coverage_state = "complete" if coverage["ratio"] >= 1.0 else ("partial" if coverage["ratio"] >= 0.5 else "insufficient")
    source_health_state = "healthy" if source_health >= 1.0 else ("degraded" if source_health >= 0.5 else "unavailable")
    return {
        "generatedAt": utc_iso(now),
        "fetchedAt": fetched_at,
        "ageMinutes": age_minutes(fetched_at, now=now),
        "dataState": data_state,
        "coverageState": coverage_state,
        "sourceHealthState": source_health_state,
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
    now=None,
) -> Dict[str, object]:
    payload = dict(signals or {})
    quality = evaluate_external_signal_quality(payload, positions=positions, settings=settings, now=now)
    payload["quality"] = quality
    payload["freshness"] = {
        "fetchedAt": quality["fetchedAt"],
        "ageMinutes": quality["ageMinutes"],
        "status": "fresh" if int(quality["ageMinutes"] or 0) <= int(number((settings or {}).get("externalApiFetchIntervalMinutes")) or 30) else "stale",
    }
    payload["provenance"] = {
        "sources": [row["source"] for row in quality.get("sourceCoverage", []) if row.get("configured")],
        "unavailableSources": [row["source"] for row in quality.get("sourceCoverage", []) if row.get("configured") and not row.get("ok")],
    }
    return payload
