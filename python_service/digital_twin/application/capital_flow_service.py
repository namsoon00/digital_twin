"""Read-side use cases for market, sector, security and portfolio capital flow."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping

from ..domain.capital_flow import (
    CAPITAL_FLOW_WINDOWS,
    canonical_observations,
    capital_flow_summary,
    finite_number,
    subject_flow_summary,
)


def clean_symbols(values: Iterable[object]) -> List[str]:
    return sorted({str(value or "").upper().strip() for value in values or [] if str(value or "").strip()})


class CapitalFlowService:
    def __init__(self, time_series_store):
        self.time_series_store = time_series_store

    def summary(
        self,
        *,
        symbols: Iterable[str] = (),
        market: str = "",
        window_days: int = 5,
        observed_after: str = "",
        as_of: str = "",
        limit: int = 10000,
        positions: Iterable[Mapping[str, object]] = (),
    ) -> Dict[str, object]:
        bounded_window = int(window_days or 5)
        if bounded_window not in CAPITAL_FLOW_WINDOWS:
            bounded_window = 5
        rows = self.time_series_store.load_capital_flow_observations(
            symbols=clean_symbols(symbols),
            market=str(market or "").upper().strip(),
            observed_after=str(observed_after or ""),
            as_of=str(as_of or ""),
            limit=max(1, min(50000, int(limit or 10000))),
        )
        payload = capital_flow_summary(rows, bounded_window)
        canonical = canonical_observations(rows, as_of)
        grouped = {}
        for item in canonical:
            grouped.setdefault(item.subject_id, []).append(item)
        by_symbol = {item.get("subjectId"): item for item in payload.get("subjects") or []}
        for symbol, observations in grouped.items():
            subject = by_symbol.get(symbol)
            if not subject:
                continue
            subject["windows"] = {
                str(days) + "D": subject_flow_summary(observations, days)
                for days in CAPITAL_FLOW_WINDOWS
            }
        payload["availableWindows"] = [str(days) + "D" for days in CAPITAL_FLOW_WINDOWS]
        payload["transitions"] = self._transitions(grouped, bounded_window)
        payload["portfolioImpact"] = self._portfolio_impact(payload, positions)
        payload["storageQuality"] = self.time_series_store.capital_flow_quality(30)
        return payload

    @staticmethod
    def _transitions(grouped, window_days: int) -> List[Dict[str, object]]:
        transitions = []
        for symbol, observations in grouped.items():
            if len(observations) < max(2, window_days + 1):
                continue
            current = subject_flow_summary(observations[-window_days:], window_days)
            previous_rows = observations[-window_days * 2:-window_days]
            previous = subject_flow_summary(previous_rows, min(window_days, len(previous_rows))) if previous_rows else {}
            current_direction = str(current.get("direction") or "unavailable")
            previous_direction = str(previous.get("direction") or "unavailable")
            if current_direction in {"unavailable", "neutral"} or current_direction == previous_direction:
                continue
            transitions.append({
                "transitionId": "capital-flow:" + symbol + ":" + str(window_days) + "D:" + current_direction,
                "subjectId": symbol,
                "market": current.get("market"),
                "sector": current.get("sector"),
                "windowDays": window_days,
                "fromDirection": previous_direction,
                "toDirection": current_direction,
                "throughTradingDate": current.get("throughTradingDate"),
                "smartMoneyNetAmount": current.get("smartMoneyNetAmount"),
                "smartMoneyNetVolume": current.get("smartMoneyNetVolume"),
                "normalizedFlowPct": current.get("normalizedFlowPct"),
                "sourceAsOf": current.get("sourceAsOf"),
                "dataState": current.get("dataState"),
            })
        return transitions[:100]

    @staticmethod
    def _portfolio_impact(payload: Mapping[str, object], positions: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        subjects = {str(item.get("subjectId") or "").upper(): item for item in payload.get("subjects") or []}
        rows = []
        total_value = 0.0
        outflow_value = 0.0
        for raw in positions or []:
            position = dict(raw or {}) if isinstance(raw, Mapping) else {}
            symbol = str(position.get("symbol") or "").upper().strip()
            flow = subjects.get(symbol)
            if not symbol or not flow:
                continue
            value = finite_number(
                position.get("marketValueKrw")
                or position.get("market_value_krw")
                or position.get("marketValue")
                or position.get("market_value")
            ) or 0.0
            total_value += value
            if flow.get("direction") == "outflow":
                outflow_value += value
            rows.append({
                "subjectId": symbol,
                "name": position.get("name") or symbol,
                "sector": flow.get("sector") or position.get("sector") or "기타",
                "marketValueKrw": round(value, 2),
                "direction": flow.get("direction"),
                "normalizedFlowPct": flow.get("normalizedFlowPct"),
                "persistenceRatio": flow.get("persistenceRatio"),
                "sourceAsOf": flow.get("sourceAsOf"),
                "dataState": flow.get("dataState"),
            })
        return {
            "matchedHoldingCount": len(rows),
            "outflowHoldingCount": len([item for item in rows if item.get("direction") == "outflow"]),
            "matchedMarketValueKrw": round(total_value, 2),
            "outflowMarketValueKrw": round(outflow_value, 2),
            "outflowExposureRatioPct": round(outflow_value / total_value * 100, 2) if total_value > 0 else None,
            "items": rows,
        }
