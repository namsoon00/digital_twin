from collections import OrderedDict
from typing import Dict, Iterable, List

from .alert_formatting import compact_number
from .portfolio import AccountSnapshot, Position, monitor_state_has_live_account_data


PROVIDER_ALIASES = {
    "alpha vantage news": "Alpha Vantage",
    "alpha-vantage": "Alpha Vantage",
    "alphavantage": "Alpha Vantage",
    "coin gecko": "CoinGecko",
    "coingecko": "CoinGecko",
    "gdelt news": "GDELT",
    "kis websocket": "KIS",
    "kis open api": "KIS",
    "runtime settings": "RuntimeSettings",
    "sec": "SEC EDGAR",
}

KIS_COVERAGE_LABELS = {
    "price": "현재가",
    "quote": "현재가",
    "ccnl": "체결강도",
    "orderbook": "호가",
    "investor": "투자자별 수급",
}

FX_SOURCE_LABELS = {
    "broker_applied_valuation": "계좌 적용 환율",
    "fallback_setting": "설정값 환율",
    "market_daily": "환율 일일 API",
    "market_realtime": "환율 실시간 API",
}


def _provider_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return PROVIDER_ALIASES.get(text.lower().replace("_", " "), text)


def _detail_list(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _add_source(
    rows: "OrderedDict[str, Dict[str, object]]",
    provider: object,
    details: object,
    status: str = "사용",
    external: bool = True,
) -> None:
    label = _provider_label(provider)
    if not label:
        return
    row = rows.setdefault(label, {
        "provider": label,
        "details": [],
        "statuses": [],
        "external": bool(external),
    })
    row["external"] = bool(row.get("external")) or bool(external)
    for detail in _detail_list(details):
        if detail not in row["details"]:
            row["details"].append(detail)
    if status and status not in row["statuses"]:
        row["statuses"].append(status)


def _group_has_items(value: object) -> bool:
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return value not in (None, "", False)


def _position_dicts(positions: Iterable[Position]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for position in positions or []:
        if isinstance(position, Position):
            rows.append(position.to_dict())
        elif isinstance(position, dict):
            rows.append(dict(position))
    return rows


def _add_account_and_market_sources(rows: "OrderedDict[str, Dict[str, object]]", snapshot: AccountSnapshot) -> None:
    provider = str(getattr(snapshot, "provider", "") or "").strip()
    if provider.lower() == "toss" or monitor_state_has_live_account_data({"mode": snapshot.mode, "status": snapshot.status}):
        _add_source(rows, "Toss", "계좌 보유·평가금액(/api/v1/holdings)", external=True)

    for item in _position_dicts(list(snapshot.positions or []) + list(snapshot.watchlist or [])):
        quote_source = str(item.get("quote_source") or item.get("quoteSource") or "").strip()
        if "Toss" in quote_source:
            _add_source(rows, "Toss", "종목 시세·이동평균(/api/v1/prices)", external=True)
        if "KIS" in quote_source:
            _add_source(rows, "KIS", "국내 종목 시세", external=True)
        coverage = item.get("market_signal_coverage") or item.get("marketSignalCoverage")
        if not isinstance(coverage, dict) or not coverage:
            continue
        details = []
        for key, label in KIS_COVERAGE_LABELS.items():
            stage = coverage.get(key)
            if isinstance(stage, dict) and str(stage.get("status") or "").lower() in {"available", "ok", "live", "cached"}:
                details.append(label)
            elif stage:
                details.append(label)
        if details:
            _add_source(rows, "KIS", "·".join(details), external=True)


def _add_fx_sources(rows: "OrderedDict[str, Dict[str, object]]", signals: Dict[str, object]) -> None:
    rates = signals.get("fxRates") if isinstance(signals.get("fxRates"), dict) else {}
    for pair, row in rates.items():
        if not isinstance(row, dict):
            continue
        pair_text = str(pair or row.get("pair") or "").upper().strip()
        source_type = str(row.get("sourceType") or "").strip()
        provider = row.get("provider") or row.get("marketProvider") or row.get("valuationProvider")
        detail = (FX_SOURCE_LABELS.get(source_type) or "환율") + (" " + pair_text if pair_text else "")
        external = _provider_label(provider) not in {"RuntimeSettings", "BrokerAccount"}
        _add_source(rows, provider, detail, external=external)
        market_provider = row.get("marketProvider")
        if market_provider:
            _add_source(rows, market_provider, "환율 " + pair_text + "(CURRENCY_EXCHANGE_RATE)", external=True)
        fallback_rate = row.get("fallbackRate")
        if fallback_rate not in (None, ""):
            rate_text = compact_number(float(fallback_rate or 0))
            _add_source(rows, "RuntimeSettings", "설정값 환율 " + pair_text + " " + rate_text, external=False)


def _add_external_signal_payload_sources(rows: "OrderedDict[str, Dict[str, object]]", signals: Dict[str, object]) -> None:
    if _group_has_items(signals.get("equityQuotes")):
        _add_source(rows, "Alpha Vantage", "해외 주식 가격·거래량(GLOBAL_QUOTE)")
    if _group_has_items(signals.get("companyOverviews")):
        _add_source(rows, "Alpha Vantage", "기업 개요(OVERVIEW)")
    if _group_has_items(signals.get("earningsReports")):
        _add_source(rows, "Alpha Vantage", "실적(EARNINGS)")
    if _group_has_items(signals.get("yfinanceData")):
        _add_source(rows, "yfinance", "Yahoo Finance 기반 가격·재무·실적·애널리스트·보유자·옵션 데이터")
    if _group_has_items(signals.get("cryptoMarkets")):
        _add_source(rows, "CoinGecko", "크립토 가격·거래액(coins/markets)")

    macro = signals.get("macro") if isinstance(signals.get("macro"), dict) else {}
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    if series:
        names = "/".join(str(key or "").upper() for key in series.keys() if str(key or "").strip())
        _add_source(rows, "FRED", "미국 금리·거시 지표" + (" " + names if names else ""))

    if _group_has_items(signals.get("secFilings")):
        _add_source(rows, "SEC EDGAR", "미국 공시·재무팩트(submissions/companyfacts)")
    if _group_has_items(signals.get("dartDisclosures")):
        _add_source(rows, "OpenDART", "국내 공시(list)")

    news = signals.get("newsHeadlines") if isinstance(signals.get("newsHeadlines"), dict) else {}
    for item in news.values():
        if not isinstance(item, dict):
            continue
        provider = _provider_label(item.get("provider") or "News")
        if provider == "Alpha Vantage":
            _add_source(rows, provider, "뉴스 헤드라인·감성(NEWS_SENTIMENT)")
        elif provider == "GDELT":
            _add_source(rows, provider, "뉴스 헤드라인(Doc API)")
        else:
            _add_source(rows, provider, "뉴스 헤드라인")

    if _group_has_items(signals.get("researchEvidence")):
        _add_source(rows, "ResearchEvidence", "저장된 기사·공시 근거", external=False)


def _status_detail(message: str) -> str:
    text = str(message or "").strip()
    lowered = text.lower()
    if "fx:" in lowered:
        return "환율 호출 상태"
    if "global_quote" in lowered:
        return "해외 주식 가격 호출 상태"
    if "yfinance" in lowered:
        return "yfinance 호출 상태"
    if "news" in lowered or "sentiment" in lowered or "doc:" in lowered:
        return "뉴스 호출 상태"
    if "series" in lowered:
        return "금리 호출 상태"
    if "companyfacts" in lowered or "submissions" in lowered or "cik" in lowered:
        return "공시 호출 상태"
    if "bulk cap" in lowered:
        return "대상 수 제한"
    return "호출 상태"


def _add_status_sources(rows: "OrderedDict[str, Dict[str, object]]", signals: Dict[str, object]) -> None:
    statuses = signals.get("statuses") if isinstance(signals.get("statuses"), list) else []
    for item in statuses:
        if not isinstance(item, dict):
            continue
        source = _provider_label(item.get("source"))
        if not source:
            continue
        ok = bool(item.get("ok"))
        message = str(item.get("message") or "")
        status = "사용" if ok else "실패"
        if ok and "cache" in message.lower():
            status = "캐시 사용"
        _add_source(rows, source, _status_detail(message), status=status, external=source not in {"ResearchEvidence"})


def external_api_source_rows(snapshot: AccountSnapshot) -> List[Dict[str, object]]:
    rows: "OrderedDict[str, Dict[str, object]]" = OrderedDict()
    _add_account_and_market_sources(rows, snapshot)
    signals = snapshot.external_signals if isinstance(snapshot.external_signals, dict) else {}
    _add_fx_sources(rows, signals)
    _add_external_signal_payload_sources(rows, signals)
    _add_status_sources(rows, signals)
    return [
        {
            "provider": row["provider"],
            "details": list(row.get("details") or []),
            "status": " / ".join(row.get("statuses") or ["사용"]),
            "external": bool(row.get("external")),
        }
        for row in rows.values()
        if row.get("details")
    ]


def external_api_source_line(row: Dict[str, object]) -> str:
    provider = str(row.get("provider") or "").strip()
    details = ", ".join(str(item or "").strip() for item in row.get("details", []) if str(item or "").strip())
    status = str(row.get("status") or "").strip()
    if status and status != "사용":
        details = (details + " · " if details else "") + status
    return provider + ": " + details if provider and details else ""


def external_api_source_metadata(snapshot: AccountSnapshot) -> Dict[str, object]:
    rows = external_api_source_rows(snapshot)
    lines = [line for line in (external_api_source_line(row) for row in rows) if line]
    if not lines:
        return {}
    return {
        "externalApiSources": rows,
        "externalApiSourceLines": lines,
    }
