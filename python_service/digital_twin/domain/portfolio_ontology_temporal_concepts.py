from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from .investment_research import research_evidence_from_external_signals
from .market_data import number
from .market_time_series import market_session_date
from .ontology_contracts import PortfolioOntology
from .ontology_observation_quality import profile_for_domain
from .ontology_schema import add_entity, add_relation
from .portfolio import Position


DEFAULT_TEMPORAL_WINDOWS_TEXT = "1D=1:2\n3D=3:3\n5D=5:4\n20D=20:5"


@dataclass(frozen=True)
class TemporalWindowDefinition:
    key: str
    lookback_days: float
    min_samples: int


def parse_temporal_windows(text: object = None) -> List[TemporalWindowDefinition]:
    rows: List[TemporalWindowDefinition] = []
    source = str(text or DEFAULT_TEMPORAL_WINDOWS_TEXT)
    for chunk in source.replace(",", "\n").replace(";", "\n").splitlines():
        line = chunk.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        parts = [item.strip() for item in raw_value.split(":")]
        days = number(parts[0])
        min_samples = int(number(parts[1] if len(parts) > 1 else 2) or 2)
        key = key.strip().upper()
        if not key or days <= 0:
            continue
        rows.append(TemporalWindowDefinition(key, float(days), max(2, min_samples)))
    return rows or parse_temporal_windows(DEFAULT_TEMPORAL_WINDOWS_TEXT)


def parse_timestamp(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def camel_or_snake(row: Dict[str, object], camel: str, snake: str = ""):
    if not isinstance(row, dict):
        return None
    if camel in row:
        return row.get(camel)
    snake = snake or camel_to_snake(camel)
    return row.get(snake)


def camel_to_snake(value: str) -> str:
    rows = []
    for char in str(value or ""):
        if char.isupper() and rows:
            rows.append("_")
        rows.append(char.lower())
    return "".join(rows)


def position_payload(position: Position, generated_at: str = "") -> Dict[str, object]:
    payload = position.to_dict()
    payload["generatedAt"] = generated_at or position.updated_at
    payload["marketSessionDate"] = market_session_date(
        payload["generatedAt"],
        position.market,
        position.currency,
    )
    return payload


def state_position_payload(state: Dict[str, object], symbol: str) -> Dict[str, object]:
    if not isinstance(state, dict):
        return {}
    normalized = str(symbol or "").upper().strip()
    for group_key in ["positions", "watchlist"]:
        group = state.get(group_key)
        if not isinstance(group, dict):
            continue
        payload = group.get(normalized)
        if isinstance(payload, dict):
            row = dict(payload)
            row["generatedAt"] = state.get("generatedAt")
            row["externalSignals"] = state.get("externalSignals") if isinstance(state.get("externalSignals"), dict) else {}
            return row
    return {}


def temporal_history_rows(
    position: Position,
    runtime_context: Dict[str, object] = None,
) -> List[Dict[str, object]]:
    runtime_context = runtime_context if isinstance(runtime_context, dict) else {}
    metadata = runtime_context.get("metadata") if isinstance(runtime_context.get("metadata"), dict) else {}
    history = metadata.get("monitorStateHistory") if isinstance(metadata.get("monitorStateHistory"), list) else []
    symbol = str(position.symbol or "").upper().strip()
    rows = []
    for state in history:
        row = state_position_payload(state, symbol)
        if row:
            rows.append(row)
    current = position_payload(position, str(runtime_context.get("asOf") or ""))
    if current:
        rows.append(current)
    deduped: Dict[str, Dict[str, object]] = {}
    for index, row in enumerate(rows):
        stamp = str(row.get("generatedAt") or row.get("updated_at") or row.get("updatedAt") or "row-" + str(index))
        deduped[stamp + ":" + symbol] = row
    return sorted(
        deduped.values(),
        key=lambda item: parse_timestamp(item.get("generatedAt") or item.get("updated_at") or item.get("updatedAt")) or datetime.min.replace(tzinfo=timezone.utc),
    )


def stored_temporal_window_rows(
    runtime_context: Dict[str, object],
    symbol: str,
    window_key: str,
) -> Optional[List[Dict[str, object]]]:
    windows = runtime_context.get("temporalObservationWindows") if isinstance(runtime_context, dict) else None
    if not isinstance(windows, dict):
        return None
    by_symbol = windows.get(str(symbol or "").upper().strip())
    if not isinstance(by_symbol, dict):
        return None
    rows = by_symbol.get(str(window_key or "").upper().strip())
    if not isinstance(rows, list):
        return None
    return [dict(row) for row in rows if isinstance(row, dict)]


def dedupe_temporal_rows(rows: Iterable[Dict[str, object]], symbol: str) -> List[Dict[str, object]]:
    deduped: Dict[str, Dict[str, object]] = {}
    for index, row in enumerate(rows or []):
        stamp = str(
            row.get("bucketAt")
            or row.get("generatedAt")
            or row.get("updated_at")
            or row.get("updatedAt")
            or "row-" + str(index)
        )
        deduped[stamp + ":" + str(symbol or "").upper()] = row
    return sorted(
        deduped.values(),
        key=lambda item: parse_timestamp(
            item.get("bucketAt")
            or item.get("generatedAt")
            or item.get("updated_at")
            or item.get("updatedAt")
        ) or datetime.min.replace(tzinfo=timezone.utc),
    )


def trim_to_recent_sessions(rows: List[Dict[str, object]], required_sessions: int) -> List[Dict[str, object]]:
    if required_sessions <= 0:
        return rows
    recent_dates = []
    for row in reversed(rows or []):
        session_date = str(
            row.get("marketSessionDate")
            or row.get("bucketAt")
            or row.get("generatedAt")
            or row.get("updatedAt")
            or ""
        )[:10]
        if session_date and session_date not in recent_dates:
            recent_dates.append(session_date)
        if len(recent_dates) >= required_sessions:
            break
    allowed = set(recent_dates)
    return [
        row for row in rows or []
        if str(
            row.get("marketSessionDate")
            or row.get("bucketAt")
            or row.get("generatedAt")
            or row.get("updatedAt")
            or ""
        )[:10] in allowed
    ]


def row_number(row: Dict[str, object], camel: str, snake: str = "") -> float:
    return number(camel_or_snake(row, camel, snake))


def percentage_change(start: float, end: float) -> float:
    if not start:
        return 0.0
    return ((end / start) - 1.0) * 100.0


def window_rows(rows: List[Dict[str, object]], definition: TemporalWindowDefinition, current_time: Optional[datetime]) -> List[Dict[str, object]]:
    if not rows:
        return []
    if current_time:
        cutoff = current_time - timedelta(days=definition.lookback_days)
        filtered = [
            row
            for row in rows
            if (parse_timestamp(row.get("generatedAt") or row.get("updated_at") or row.get("updatedAt")) or current_time) >= cutoff
        ]
        if filtered:
            return filtered
    return rows[-max(definition.min_samples, 2):]


STALE_DATA_QUALITY_STATES = {"stale", "poor", "invalid", "missing", "unavailable", "error"}


def row_timestamp(row: Dict[str, object]) -> str:
    return str(
        row.get("bucketAt")
        or row.get("generatedAt")
        or row.get("updated_at")
        or row.get("updatedAt")
        or ""
    )


def normalized_data_quality(row: Dict[str, object]) -> str:
    quality = str(camel_or_snake(row, "dataQuality", "data_quality") or "").strip().lower()
    if any(state in quality for state in STALE_DATA_QUALITY_STATES):
        return "stale"
    if any(state in quality for state in {"actual", "live", "fresh", "verified"}):
        return "fresh"
    if any(state in quality for state in {"cached", "manual", "synthetic", "partial"}):
        return "partial"
    return "unknown"


def has_row_field(row: Dict[str, object], *fields: str) -> bool:
    return any(field in row and row.get(field) not in (None, "") for field in fields)


def has_investor_flow_observation(row: Dict[str, object]) -> bool:
    if not has_row_field(
        row,
        "foreignNetVolume",
        "foreign_net_volume",
        "institutionNetVolume",
        "institution_net_volume",
        "individualNetVolume",
        "individual_net_volume",
    ):
        return False
    return any(abs(row_number(row, camel, snake)) > 0 for camel, snake in [
        ("foreignNetVolume", "foreign_net_volume"),
        ("institutionNetVolume", "institution_net_volume"),
        ("individualNetVolume", "individual_net_volume"),
    ])


def trailing_direction_count(prices: List[float], direction: str) -> int:
    count = 0
    for previous, current in reversed(list(zip(prices, prices[1:]))):
        matched = current < previous if direction == "down" else current > previous
        if not matched:
            break
        count += 1
    return count


def direction_change_count(prices: List[float]) -> int:
    directions = []
    for previous, current in zip(prices, prices[1:]):
        direction = 1 if current > previous else -1 if current < previous else 0
        if direction:
            directions.append(direction)
    return sum(1 for previous, current in zip(directions, directions[1:]) if previous != current)


def crossing_count(values: List[float], crossing: str) -> int:
    if crossing == "reclaim":
        return sum(1 for previous, current in zip(values, values[1:]) if previous < 0 <= current)
    return sum(1 for previous, current in zip(values, values[1:]) if previous >= 0 > current)


def research_events_for_state(symbol: str, state: Dict[str, object]) -> List[Dict[str, object]]:
    signals = state.get("externalSignals") if isinstance(state, dict) and isinstance(state.get("externalSignals"), dict) else {}
    return [item.to_dict() for item in research_evidence_from_external_signals(symbol, signals)]


def event_counts(symbol: str, rows: List[Dict[str, object]]) -> Dict[str, object]:
    by_key: Dict[str, Dict[str, object]] = {}
    for row in rows:
        for item in research_events_for_state(symbol, row):
            key = str(item.get("evidenceId") or item.get("url") or item.get("title") or "")
            if key:
                by_key[key] = item
    events = list(by_key.values())
    risk_count = len([item for item in events if str(item.get("polarity") or "") in {"risk", "contradiction"}])
    support_count = len([item for item in events if str(item.get("polarity") or "") == "support"])
    return {
        "eventCount": len(events),
        "riskEventCount": risk_count,
        "supportEventCount": support_count,
    }


def temporal_window_values(rows: List[Dict[str, object]], definition: TemporalWindowDefinition) -> Dict[str, object]:
    priced_rows = [row for row in rows if row_number(row, "currentPrice", "current_price") > 0]
    first = priced_rows[0] if priced_rows else {}
    last = priced_rows[-1] if priced_rows else {}
    first_time = parse_timestamp(first.get("generatedAt") or first.get("updated_at") or first.get("updatedAt"))
    last_time = parse_timestamp(last.get("generatedAt") or last.get("updated_at") or last.get("updatedAt"))
    prices = [row_number(row, "currentPrice", "current_price") for row in priced_rows]
    ma20_distances = [
        row_number(row, "ma20Distance", "ma20_distance")
        for row in priced_rows
        if abs(row_number(row, "ma20Distance", "ma20_distance")) > 0
        or row_number(row, "ma20", "ma20") > 0
    ]
    start_price = prices[0] if prices else 0.0
    end_price = prices[-1] if prices else 0.0
    smart_money_rows = [
        row for row in priced_rows
        if has_investor_flow_observation(row)
        and normalized_data_quality(row) != "stale"
    ]
    smart_money_first = smart_money_rows[0] if smart_money_rows else {}
    smart_money_last = smart_money_rows[-1] if smart_money_rows else {}
    smart_money_start = row_number(smart_money_first, "foreignNetVolume", "foreign_net_volume") + row_number(smart_money_first, "institutionNetVolume", "institution_net_volume")
    smart_money_end = row_number(smart_money_last, "foreignNetVolume", "foreign_net_volume") + row_number(smart_money_last, "institutionNetVolume", "institution_net_volume")
    distinct_smart_money_observations = {
        (
            row_number(row, "foreignNetVolume", "foreign_net_volume"),
            row_number(row, "institutionNetVolume", "institution_net_volume"),
            str(row.get("sourceAsOf") or ""),
        )
        for row in smart_money_rows
    }
    elapsed_hours = ((last_time - first_time).total_seconds() / 3600.0) if first_time and last_time and last_time >= first_time else 0.0
    session_dates = {
        str(
            row.get("marketSessionDate")
            or row.get("bucketAt")
            or row.get("generatedAt")
            or row.get("updated_at")
            or row.get("updatedAt")
            or ""
        )[:10]
        for row in rows
        if row
    }
    session_dates.discard("")
    required_sessions = max(1, int(round(definition.lookback_days)))
    sample_coverage = len(priced_rows) / max(1.0, definition.min_samples)
    session_coverage = len(session_dates) / max(1.0, required_sessions)
    sufficient = len(priced_rows) >= definition.min_samples and len(session_dates) >= required_sessions
    valid_rows = [row for row in priced_rows if normalized_data_quality(row) != "stale"]
    stale_count = len(priced_rows) - len(valid_rows)
    midpoint_index = max(1, len(prices) // 2) if len(prices) >= 2 else 0
    midpoint_price = prices[midpoint_index] if prices else 0.0
    peak_price = max(prices) if prices else 0.0
    trough_price = min(prices) if prices else 0.0
    observation_source = next(
        (str(row.get("observationSource") or "") for row in reversed(rows) if row.get("observationSource")),
        "monitor-snapshot-history",
    )
    observation_granularity = next(
        (str(row.get("observationGranularity") or "") for row in reversed(rows) if row.get("observationGranularity")),
        "snapshot",
    )
    values = {
        "windowKey": definition.key,
        "lookbackDays": definition.lookback_days,
        "requiredSampleCount": definition.min_samples,
        "sampleCount": len(priced_rows),
        "validObservationCount": len(valid_rows),
        "invalidObservationCount": max(0, len(rows) - len(valid_rows)),
        "staleObservationCount": stale_count,
        "validObservationRatio": round(len(valid_rows) / max(1, len(rows)), 3),
        "latestObservationQuality": normalized_data_quality(last),
        "requiredSessionCount": required_sessions,
        "coveredSessionCount": len(session_dates),
        "hasSufficientHistory": sufficient,
        "coverageRatio": round(min(1.0, sample_coverage, session_coverage), 3),
        "observationSource": observation_source,
        "observationGranularity": observation_granularity,
        "firstObservedAt": first_time.isoformat().replace("+00:00", "Z") if first_time else "",
        "lastObservedAt": last_time.isoformat().replace("+00:00", "Z") if last_time else "",
        "elapsedHours": round(elapsed_hours, 2),
        "startPrice": round(start_price, 4),
        "currentPrice": round(end_price, 4),
        "priceChangePct": round(percentage_change(start_price, end_price), 2),
        "peakPrice": round(peak_price, 4),
        "troughPrice": round(trough_price, 4),
        "peakReturnPct": round(percentage_change(start_price, peak_price), 2),
        "troughReturnPct": round(percentage_change(start_price, trough_price), 2),
        "drawdownFromPeakPct": round(percentage_change(peak_price, end_price), 2),
        "reboundFromTroughPct": round(percentage_change(trough_price, end_price), 2),
        "priorPriceChangePct": round(percentage_change(start_price, midpoint_price), 2),
        "recentPriceChangePct": round(percentage_change(midpoint_price, end_price), 2),
        "priceVelocityChangePct": round(
            percentage_change(midpoint_price, end_price) - percentage_change(start_price, midpoint_price),
            2,
        ),
        "consecutiveDeclineCount": trailing_direction_count(prices, "down"),
        "consecutiveAdvanceCount": trailing_direction_count(prices, "up"),
        "directionChangeCount": direction_change_count(prices),
        "profitLossRateStart": round(row_number(first, "profitLossRate", "profit_loss_rate"), 2),
        "profitLossRateEnd": round(row_number(last, "profitLossRate", "profit_loss_rate"), 2),
        "profitLossRateChangePct": round(row_number(last, "profitLossRate", "profit_loss_rate") - row_number(first, "profitLossRate", "profit_loss_rate"), 2),
        "ma20DistanceStart": round(row_number(first, "ma20Distance", "ma20_distance"), 2),
        "ma20DistanceEnd": round(row_number(last, "ma20Distance", "ma20_distance"), 2),
        "ma20DistanceChange": round(row_number(last, "ma20Distance", "ma20_distance") - row_number(first, "ma20Distance", "ma20_distance"), 2),
        "ma20DistancePeak": round(max(ma20_distances), 2) if ma20_distances else 0.0,
        "ma20DistanceTrough": round(min(ma20_distances), 2) if ma20_distances else 0.0,
        "ma20ReclaimCount": crossing_count(ma20_distances, "reclaim"),
        "ma20BreakCount": crossing_count(ma20_distances, "break"),
        "ma20ObservationCount": len(ma20_distances),
        "ma60DistanceStart": round(row_number(first, "ma60Distance", "ma60_distance"), 2),
        "ma60DistanceEnd": round(row_number(last, "ma60Distance", "ma60_distance"), 2),
        "volumeRatioEnd": round(row_number(last, "volumeRatio", "volume_ratio"), 2),
        "tradeStrengthEnd": round(row_number(last, "tradeStrength", "trade_strength"), 2),
        "bidAskImbalanceEnd": round(row_number(last, "bidAskImbalance", "bid_ask_imbalance"), 2),
        "smartMoneyObservationCount": len(smart_money_rows),
        "smartMoneyDistinctObservationCount": len(distinct_smart_money_observations),
        "smartMoneyDataState": (
            "sufficient"
            if len(smart_money_rows) >= 2 and len(distinct_smart_money_observations) >= 2
            else "partial" if smart_money_rows else "unavailable"
        ),
    }
    if smart_money_rows:
        values.update({
            "smartMoneyNetLatest": round(smart_money_end, 2),
            "smartMoneyNetChange": round(smart_money_end - smart_money_start, 2),
            "individualNetLatest": round(
                row_number(smart_money_last, "individualNetVolume", "individual_net_volume"),
                2,
            ),
        })
    return values


def temporal_observation_anchors(rows: List[Dict[str, object]]) -> List[tuple]:
    priced_rows = [row for row in rows if row_number(row, "currentPrice", "current_price") > 0]
    if not priced_rows:
        return []
    candidates = [("first", 0)]
    if len(priced_rows) > 2:
        candidates.append(("middle", len(priced_rows) // 2))
    if len(priced_rows) > 1:
        candidates.append(("latest", len(priced_rows) - 1))
    return [(role, index, priced_rows[index]) for role, index in candidates]


def add_temporal_observation_anchors(
    graph: PortfolioOntology,
    window_id: str,
    symbol: str,
    definition: TemporalWindowDefinition,
    rows: List[Dict[str, object]],
) -> None:
    previous_id = ""
    for role, index, row in temporal_observation_anchors(rows):
        observed_at = row_timestamp(row)
        has_flow = has_investor_flow_observation(row) and normalized_data_quality(row) != "stale"
        tbox_classes = ["Observation", "PriceObservation", "TemporalWindowObservation"]
        properties = {
            "tboxClass": "TemporalWindowObservation",
            "tboxClasses": tbox_classes,
            "symbol": symbol,
            "windowKey": definition.key,
            "sequenceRole": role,
            "sequenceIndex": index,
            "observedAt": observed_at,
            "currentPrice": round(row_number(row, "currentPrice", "current_price"), 4),
            "profitLossRateEnd": round(row_number(row, "profitLossRate", "profit_loss_rate"), 2),
            "ma20DistanceEnd": round(row_number(row, "ma20Distance", "ma20_distance"), 2),
            "ma60DistanceEnd": round(row_number(row, "ma60Distance", "ma60_distance"), 2),
            "observationQuality": normalized_data_quality(row),
            "provider": str(row.get("provider") or ""),
            "source": str(row.get("observationSource") or "monitor-snapshot-history"),
        }
        if has_flow:
            tbox_classes.append("FlowObservation")
            properties["smartMoneyNetLatest"] = round(
                row_number(row, "foreignNetVolume", "foreign_net_volume")
                + row_number(row, "institutionNetVolume", "institution_net_volume"),
                2,
            )
        observation_id = add_entity(
            graph,
            "temporal-observation",
            symbol + ":" + definition.key + ":" + role,
            symbol + " " + definition.key + " " + role + " 관측",
            properties,
        )
        relation_properties = {
            "source": str(row.get("observationSource") or "monitor-snapshot-history"),
            "windowKey": definition.key,
            "field": "temporalObservation",
            "polarity": "context",
            "evidenceRole": "context",
            "dataState": "sufficient" if normalized_data_quality(row) != "stale" else "stale",
            "aiInfluenceLabel": definition.key + " " + role + " 관측",
        }
        add_relation(graph, window_id, observation_id, "WINDOW_CONTAINS_OBSERVATION", properties=relation_properties)
        if previous_id:
            add_relation(graph, previous_id, observation_id, "PRECEDES", properties=relation_properties)
        previous_id = observation_id


def add_temporal_coverage_gap(
    graph: PortfolioOntology,
    stock_id: str,
    symbol: str,
    definition: TemporalWindowDefinition,
    sample_count: int,
    covered_session_count: int = 0,
    observation_profile: Dict[str, object] = None,
) -> None:
    gap_id = add_entity(graph, "temporal-coverage-gap", symbol + ":temporal:" + definition.key, definition.key + " 기간 히스토리 부족", {
        "tboxClass": "TemporalCoverageGap",
        "tboxClasses": ["Observation", "DataQuality", "CoverageGap", "TemporalCoverageGap"],
        "symbol": symbol,
        "field": "temporalWindow",
        "windowKey": definition.key,
        "dataScope": "temporal-window",
        "domainScope": "temporalReasoning",
        "sampleCount": sample_count,
        "requiredSampleCount": definition.min_samples,
        "coveredSessionCount": covered_session_count,
        "requiredSessionCount": max(1, int(round(definition.lookback_days))),
        "reviewLevel": "blocked",
        "dataState": "insufficient",
        "evidenceRole": "blocking",
        "description": definition.key + " 기간 판단에 필요한 거래일 이력이 부족합니다.",
        "source": "temporal-window-ontology",
        **dict(observation_profile or {}),
    })
    add_relation(graph, stock_id, gap_id, "HAS_COVERAGE_GAP", weight=0.62, properties={
        "source": "temporal-window-ontology",
        "field": "temporalWindow",
        "windowKey": definition.key,
        "dataScope": "temporal-window",
        "polarity": "blocking",
        "reviewLevel": "blocked",
        "dataState": "insufficient",
        "evidenceRole": "blocking",
        "aiInfluenceLabel": definition.key + " 기간 히스토리 부족",
    })


def add_position_temporal_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    external_signals: Dict[str, object] = None,
    runtime_context: Dict[str, object] = None,
    observation_profiles: Dict[str, Dict[str, object]] = None,
) -> None:
    runtime_context = runtime_context if isinstance(runtime_context, dict) else {}
    settings = runtime_context.get("settings") if isinstance(runtime_context.get("settings"), dict) else runtime_context
    definitions = parse_temporal_windows((settings or {}).get("temporalWindowPeriods"))
    rows = temporal_history_rows(position, runtime_context)
    symbol = str(position.symbol or "").upper().strip()
    current_time = parse_timestamp(runtime_context.get("asOf") or (rows[-1].get("generatedAt") if rows else ""))
    if rows and isinstance(external_signals, dict):
        rows[-1] = {**rows[-1], "externalSignals": external_signals}
    trend_observation = profile_for_domain(observation_profiles or {}, "trend")

    for definition in definitions:
        stored_rows = stored_temporal_window_rows(runtime_context, symbol, definition.key)
        if stored_rows is None:
            selected = window_rows(rows, definition, current_time)
        else:
            selected = trim_to_recent_sessions(dedupe_temporal_rows(
                stored_rows + [position_payload(position, str(runtime_context.get("asOf") or ""))],
                symbol,
            ), max(1, int(round(definition.lookback_days))))
            if isinstance(external_signals, dict) and selected:
                selected[-1] = {**selected[-1], "externalSignals": external_signals}
        values = temporal_window_values(selected, definition)
        values.update(event_counts(symbol, selected))
        values["symbol"] = symbol
        values["source"] = str(values.get("observationSource") or "monitor-snapshot-history")
        values["retentionTier"] = str(values.get("observationGranularity") or "snapshot")

        window_id = add_entity(graph, "temporal-window", symbol + ":" + definition.key, symbol + " " + definition.key + " 기간 흐름", {
            "tboxClass": "MultiDayWindow" if definition.lookback_days > 1 else "DailyWindow",
            "tboxClasses": ["Observation", "TemporalWindow", "MultiDayWindow" if definition.lookback_days > 1 else "DailyWindow", "TemporalMateriality"],
            "field": "temporalWindow",
            "value": values.get("priceChangePct", 0),
            **values,
            **trend_observation,
        })
        data_state = "sufficient" if values.get("hasSufficientHistory") else "insufficient"
        add_relation(graph, stock_id, window_id, "HAS_TEMPORAL_WINDOW", properties={
            "source": values["source"],
            "field": "temporalWindow",
            "windowKey": definition.key,
            "polarity": "context",
            "evidenceRole": "context" if values.get("hasSufficientHistory") else "blocking",
            "reviewLevel": "normal" if values.get("hasSufficientHistory") else "blocked",
            "dataState": data_state,
            "aiInfluenceLabel": definition.key + " 기간 흐름",
        })
        add_temporal_observation_anchors(graph, window_id, symbol, definition, selected)

        if not values.get("hasSufficientHistory"):
            add_temporal_coverage_gap(
                graph,
                stock_id,
                symbol,
                definition,
                int(values.get("sampleCount") or 0),
                int(values.get("coveredSessionCount") or 0),
                trend_observation,
            )
