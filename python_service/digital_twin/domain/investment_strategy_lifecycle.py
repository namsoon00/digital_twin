from typing import Dict, Iterable, List

from .investment_strategy_proposals import clean_list, clean_text
from .portfolio import utc_now_iso


MAX_REVIEW_LOG_ENTRIES = 80
MAX_PERFORMANCE_SAMPLES = 80


def append_strategy_review_log(proposal, action: str, payload: Dict[str, object] = None):
    lifecycle = dict(getattr(proposal, "lifecycle", {}) or {})
    entries = [
        dict(item)
        for item in lifecycle.get("reviewLog") or []
        if isinstance(item, dict)
    ]
    entry = {
        "action": clean_text(action, 80),
        "at": utc_now_iso(),
    }
    for key, value in dict(payload or {}).items():
        if value not in (None, "", [], {}):
            entry[str(key)] = value
    entries.append(entry)
    lifecycle["reviewLog"] = entries[-MAX_REVIEW_LOG_ENTRIES:]
    lifecycle["lastReviewAction"] = entry["action"]
    lifecycle["lastReviewedAt"] = entry["at"]
    proposal.lifecycle = lifecycle
    return proposal


def strategy_performance_sample(payload: Dict[str, object], default_symbols: Iterable[object] = None) -> Dict[str, object]:
    payload = dict(payload or {})
    return {
        "observedAt": clean_text(payload.get("observedAt") or payload.get("at") or utc_now_iso(), 80),
        "source": clean_text(payload.get("source") or "manual", 80),
        "symbols": clean_list(payload.get("symbols") or list(default_symbols or []), 64),
        "portfolioReturnPct": number_or_none(first_present(payload, "portfolioReturnPct", "returnPct")),
        "benchmarkReturnPct": number_or_none(payload.get("benchmarkReturnPct")),
        "maxDrawdownPct": number_or_none(payload.get("maxDrawdownPct")),
        "signalCount": int_or_none(first_present(payload, "signalCount", "alertCount")),
        "falsePositiveCount": int_or_none(payload.get("falsePositiveCount")),
        "notes": clean_text(payload.get("notes"), 500),
    }


def merge_strategy_performance(
    performance: Dict[str, object],
    sample: Dict[str, object],
    max_samples: int = MAX_PERFORMANCE_SAMPLES,
) -> Dict[str, object]:
    rows = [
        dict(item)
        for item in (performance or {}).get("samples") or []
        if isinstance(item, dict)
    ]
    rows.append(dict(sample or {}))
    rows = rows[-max(1, int(max_samples or MAX_PERFORMANCE_SAMPLES)):]
    return {
        "samples": rows,
        "summary": strategy_performance_summary(rows),
        "updatedAt": utc_now_iso(),
    }


def strategy_performance_summary(samples: Iterable[Dict[str, object]]) -> Dict[str, object]:
    rows = [dict(item) for item in samples or [] if isinstance(item, dict)]
    portfolio_returns = numeric_values(item.get("portfolioReturnPct") for item in rows)
    benchmark_returns = numeric_values(item.get("benchmarkReturnPct") for item in rows)
    excess_returns = []
    for item in rows:
        portfolio = number_or_none(item.get("portfolioReturnPct"))
        benchmark = number_or_none(item.get("benchmarkReturnPct"))
        if portfolio is not None and benchmark is not None:
            excess_returns.append(round(portfolio - benchmark, 4))
    signal_count = sum(int(item.get("signalCount") or 0) for item in rows)
    false_positive_count = sum(int(item.get("falsePositiveCount") or 0) for item in rows)
    drawdowns = numeric_values(item.get("maxDrawdownPct") for item in rows)
    return {
        "sampleCount": len(rows),
        "latestObservedAt": max([str(item.get("observedAt") or "") for item in rows] or [""]),
        "avgPortfolioReturnPct": average(portfolio_returns),
        "avgBenchmarkReturnPct": average(benchmark_returns),
        "avgExcessReturnPct": average(excess_returns),
        "worstDrawdownPct": min(drawdowns) if drawdowns else None,
        "signalCount": signal_count,
        "falsePositiveCount": false_positive_count,
        "falsePositiveRate": round(false_positive_count / signal_count, 4) if signal_count > 0 else None,
    }


def numeric_values(values: Iterable[object]) -> List[float]:
    result = []
    for value in values or []:
        parsed = number_or_none(value)
        if parsed is not None:
            result.append(parsed)
    return result


def average(values: Iterable[float]):
    rows = list(values or [])
    if not rows:
        return None
    return round(sum(rows) / len(rows), 4)


def number_or_none(value: object):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value: object):
    parsed = number_or_none(value)
    return int(parsed) if parsed is not None else None


def first_present(payload: Dict[str, object], *keys: str):
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return None
