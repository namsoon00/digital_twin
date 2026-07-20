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


def price_path_pattern(values: Dict[str, float], prices: List[float], ma20_distances: List[float]) -> str:
    change = values.get("priceChangePct", 0.0)
    end_ma20 = values.get("ma20DistanceEnd", 0.0)
    recent_change = percentage_change(prices[-2], prices[-1]) if len(prices) >= 2 else 0.0
    earlier_change = percentage_change(prices[0], prices[1]) if len(prices) >= 2 else 0.0
    if ma20_distances and max(ma20_distances[:-1] or [ma20_distances[-1]]) > 0 and end_ma20 < 0:
        return "FailedRecovery"
    if change <= -5 and end_ma20 < -2:
        return "PersistentDecline"
    if change < -1 and (recent_change > earlier_change or recent_change > 0):
        return "DeclineDeceleration"
    if change >= 2 and end_ma20 < 0:
        return "RecoveryAttempt"
    if abs(change) <= 1:
        return "Consolidation"
    if change > 2:
        return "UpwardContinuation"
    return "MixedPath"


def flow_pattern(values: Dict[str, float]) -> str:
    price_change = values.get("priceChangePct", 0.0)
    smart_money_latest = values.get("smartMoneyNetLatest", 0.0)
    smart_money_change = values.get("smartMoneyNetChange", 0.0)
    smart_money_signal = smart_money_change if smart_money_change else smart_money_latest
    if price_change <= 0 and smart_money_signal > 0:
        return "AccumulationDuringWeakness"
    if price_change >= 0 and smart_money_signal < 0:
        return "DistributionDuringBounce"
    if smart_money_signal > 0:
        return "SmartMoneySupport"
    if smart_money_signal < 0:
        return "SmartMoneyOutflow"
    return "NeutralFlow"


def temporal_decision_state(
    path_pattern: str,
    flow: str,
    risk_events: int,
    support_events: int,
    *,
    has_sufficient_history: bool,
) -> Dict[str, object]:
    if not has_sufficient_history:
        return {
            "temporalEvidenceRole": "blocking",
            "temporalReviewLevel": "blocked",
            "temporalDataState": "insufficient",
            "temporalChangeState": "unchanged",
            "temporalConflictState": "context-only",
            "temporalRiskConditions": [],
            "temporalSupportConditions": [],
        }

    risk_conditions: List[str] = []
    support_conditions: List[str] = []
    if path_pattern in {"PersistentDecline", "FailedRecovery"}:
        risk_conditions.append(path_pattern)
    if flow in {"SmartMoneyOutflow", "DistributionDuringBounce"}:
        risk_conditions.append(flow)
    if risk_events >= 2:
        risk_conditions.append("EventDrivenRiskCluster")
    if path_pattern in {"DeclineDeceleration", "RecoveryAttempt"}:
        support_conditions.append(path_pattern)
    if flow in {"AccumulationDuringWeakness", "SmartMoneySupport"}:
        support_conditions.append(flow)
    if support_events >= 2:
        support_conditions.append("SupportiveEventCluster")

    if risk_conditions and support_conditions:
        conflict_state = "mixed"
    elif risk_conditions:
        conflict_state = "risk-only"
    elif support_conditions:
        conflict_state = "support-only"
    else:
        conflict_state = "context-only"
    evidence_role = "risk" if risk_conditions else ("support" if support_conditions else "context")
    review_level = "act" if path_pattern in {"PersistentDecline", "FailedRecovery"} or risk_events >= 2 else ("check" if risk_conditions else ("observe" if support_conditions else "normal"))
    change_state = "worsening" if risk_conditions else ("improving" if support_conditions else "unchanged")
    return {
        "temporalEvidenceRole": evidence_role,
        "temporalReviewLevel": review_level,
        "temporalDataState": "sufficient",
        "temporalChangeState": change_state,
        "temporalConflictState": conflict_state,
        "temporalRiskConditions": risk_conditions,
        "temporalSupportConditions": support_conditions,
    }


def research_events_for_state(symbol: str, state: Dict[str, object]) -> List[Dict[str, object]]:
    signals = state.get("externalSignals") if isinstance(state, dict) and isinstance(state.get("externalSignals"), dict) else {}
    return [item.to_dict() for item in research_evidence_from_external_signals(symbol, signals)]


def event_cluster(symbol: str, rows: List[Dict[str, object]]) -> Dict[str, object]:
    by_key: Dict[str, Dict[str, object]] = {}
    for row in rows:
        for item in research_events_for_state(symbol, row):
            key = str(item.get("evidenceId") or item.get("url") or item.get("title") or "")
            if key:
                by_key[key] = item
    events = list(by_key.values())
    risk_count = len([item for item in events if str(item.get("polarity") or "") in {"risk", "contradiction"}])
    support_count = len([item for item in events if str(item.get("polarity") or "") == "support"])
    if risk_count >= 2:
        cluster_type = "EventDrivenRiskCluster"
    elif support_count >= 2:
        cluster_type = "SupportiveEventCluster"
    elif not events:
        cluster_type = "QuietPeriod"
    else:
        cluster_type = "MixedEventCluster"
    return {
        "eventCount": len(events),
        "riskEventCount": risk_count,
        "supportEventCount": support_count,
        "eventClusterType": cluster_type,
    }


def temporal_window_values(rows: List[Dict[str, object]], definition: TemporalWindowDefinition) -> Dict[str, object]:
    first = rows[0] if rows else {}
    last = rows[-1] if rows else {}
    first_time = parse_timestamp(first.get("generatedAt") or first.get("updated_at") or first.get("updatedAt"))
    last_time = parse_timestamp(last.get("generatedAt") or last.get("updated_at") or last.get("updatedAt"))
    prices = [row_number(row, "currentPrice", "current_price") for row in rows]
    prices = [value for value in prices if value > 0]
    ma20_distances = [row_number(row, "ma20Distance", "ma20_distance") for row in rows]
    start_price = prices[0] if prices else 0.0
    end_price = prices[-1] if prices else 0.0
    smart_money_start = row_number(first, "foreignNetVolume", "foreign_net_volume") + row_number(first, "institutionNetVolume", "institution_net_volume")
    smart_money_end = row_number(last, "foreignNetVolume", "foreign_net_volume") + row_number(last, "institutionNetVolume", "institution_net_volume")
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
    sample_coverage = len(rows) / max(1.0, definition.min_samples)
    session_coverage = len(session_dates) / max(1.0, required_sessions)
    sufficient = len(rows) >= definition.min_samples and len(session_dates) >= required_sessions
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
        "sampleCount": len(rows),
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
        "profitLossRateStart": round(row_number(first, "profitLossRate", "profit_loss_rate"), 2),
        "profitLossRateEnd": round(row_number(last, "profitLossRate", "profit_loss_rate"), 2),
        "profitLossRateChangePct": round(row_number(last, "profitLossRate", "profit_loss_rate") - row_number(first, "profitLossRate", "profit_loss_rate"), 2),
        "ma20DistanceStart": round(row_number(first, "ma20Distance", "ma20_distance"), 2),
        "ma20DistanceEnd": round(row_number(last, "ma20Distance", "ma20_distance"), 2),
        "ma20DistanceChange": round(row_number(last, "ma20Distance", "ma20_distance") - row_number(first, "ma20Distance", "ma20_distance"), 2),
        "ma60DistanceStart": round(row_number(first, "ma60Distance", "ma60_distance"), 2),
        "ma60DistanceEnd": round(row_number(last, "ma60Distance", "ma60_distance"), 2),
        "volumeRatioEnd": round(row_number(last, "volumeRatio", "volume_ratio"), 2),
        "tradeStrengthEnd": round(row_number(last, "tradeStrength", "trade_strength"), 2),
        "bidAskImbalanceEnd": round(row_number(last, "bidAskImbalance", "bid_ask_imbalance"), 2),
        "smartMoneyNetLatest": round(smart_money_end, 2),
        "smartMoneyNetChange": round(smart_money_end - smart_money_start, 2),
        "individualNetLatest": round(row_number(last, "individualNetVolume", "individual_net_volume"), 2),
    }
    path = price_path_pattern(values, prices, ma20_distances)
    flow = flow_pattern(values)
    values["pricePathPattern"] = path
    values["flowPattern"] = flow
    return values


def primary_episodes(values: Dict[str, object], cluster: Dict[str, object]) -> List[str]:
    episodes = []
    path = str(values.get("pricePathPattern") or "")
    flow = str(values.get("flowPattern") or "")
    if path in {"PersistentDecline", "DeclineDeceleration", "RecoveryAttempt", "FailedRecovery"}:
        episodes.append(path)
    if flow in {"AccumulationDuringWeakness", "DistributionDuringBounce"}:
        episodes.append(flow)
    return episodes or ["TemporalObservation"]


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
    flow_observation = profile_for_domain(observation_profiles or {}, "flow")

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
        cluster = event_cluster(symbol, selected)
        temporal_state = temporal_decision_state(
            str(values.get("pricePathPattern") or ""),
            str(values.get("flowPattern") or ""),
            int(cluster.get("riskEventCount") or 0),
            int(cluster.get("supportEventCount") or 0),
            has_sufficient_history=bool(values.get("hasSufficientHistory")),
        )
        values.update(cluster)
        values.update(temporal_state)
        values["symbol"] = symbol
        values["source"] = str(values.get("observationSource") or "monitor-snapshot-history")
        values["retentionTier"] = str(values.get("observationGranularity") or "snapshot")

        window_id = add_entity(graph, "temporal-window", symbol + ":" + definition.key, symbol + " " + definition.key + " 기간 흐름", {
            "tboxClass": "MultiDayWindow" if definition.lookback_days > 1 else "DailyWindow",
            "tboxClasses": ["Observation", "TemporalWindow", "MultiDayWindow" if definition.lookback_days > 1 else "DailyWindow", "TemporalMateriality"],
            "field": "temporalWindow",
            "value": str(values.get("pricePathPattern") or "UnknownPath"),
            **values,
            **trend_observation,
        })
        add_relation(graph, stock_id, window_id, "HAS_TEMPORAL_WINDOW", properties={
            "source": values["source"],
            "field": "temporalWindow",
            "windowKey": definition.key,
            "polarity": values.get("temporalEvidenceRole"),
            "evidenceRole": values.get("temporalEvidenceRole"),
            "reviewLevel": values.get("temporalReviewLevel"),
            "dataState": values.get("temporalDataState"),
            "changeState": values.get("temporalChangeState"),
            "conflictState": values.get("temporalConflictState"),
            "aiInfluenceLabel": definition.key + " 기간 흐름",
        })

        path_id = add_entity(graph, "price-path-pattern", symbol + ":" + definition.key + ":" + str(values.get("pricePathPattern")), str(values.get("pricePathPattern")) + " 가격 경로", {
            "tboxClass": "PricePathPattern",
            "tboxClasses": ["Observation", "PricePathPattern", "TemporalMateriality"],
            "field": "pricePathPattern",
            "value": str(values.get("pricePathPattern") or "UnknownPath"),
            **values,
            **trend_observation,
        })
        add_relation(graph, window_id, path_id, "HAS_PRICE_PATH_PATTERN", properties={
            "source": values["source"],
            "field": "pricePathPattern",
            "windowKey": definition.key,
            "polarity": values.get("temporalEvidenceRole"),
            "evidenceRole": values.get("temporalEvidenceRole"),
            "reviewLevel": values.get("temporalReviewLevel"),
            "dataState": values.get("temporalDataState"),
            "aiInfluenceLabel": definition.key + " " + str(values.get("pricePathPattern")),
        })

        flow_id = add_entity(graph, "flow-pattern", symbol + ":" + definition.key + ":" + str(values.get("flowPattern")), str(values.get("flowPattern")) + " 수급 패턴", {
            "tboxClass": "FlowPattern",
            "tboxClasses": ["Observation", "FlowPattern", "TemporalMateriality"],
            "field": "flowPattern",
            "value": str(values.get("flowPattern") or "NeutralFlow"),
            **values,
            **flow_observation,
        })
        flow_role = "risk" if values.get("flowPattern") in {"SmartMoneyOutflow", "DistributionDuringBounce"} else "support" if values.get("flowPattern") in {"AccumulationDuringWeakness", "SmartMoneySupport"} else "context"
        add_relation(graph, window_id, flow_id, "HAS_FLOW_PATTERN", properties={
            "source": values["source"],
            "field": "flowPattern",
            "windowKey": definition.key,
            "polarity": flow_role,
            "evidenceRole": flow_role,
            "dataState": values.get("temporalDataState"),
            "aiInfluenceLabel": definition.key + " " + str(values.get("flowPattern")),
        })

        event_id = add_entity(graph, "event-cluster", symbol + ":" + definition.key + ":" + str(cluster.get("eventClusterType")), str(cluster.get("eventClusterType")) + " 이벤트 묶음", {
            "tboxClass": str(cluster.get("eventClusterType") or "EventCluster"),
            "tboxClasses": ["Observation", "EventCluster", "TemporalMateriality", str(cluster.get("eventClusterType") or "EventCluster")],
            "field": "eventClusterType",
            "value": max(cluster.get("riskEventCount", 0), cluster.get("supportEventCount", 0)),
            **values,
        })
        event_role = "risk" if cluster.get("riskEventCount", 0) >= 2 else "support" if cluster.get("supportEventCount", 0) >= 2 else "context"
        add_relation(graph, window_id, event_id, "HAS_EVENT_CLUSTER", properties={
            "source": values["source"],
            "field": "eventClusterType",
            "windowKey": definition.key,
            "polarity": event_role,
            "evidenceRole": event_role,
            "dataState": values.get("temporalDataState"),
            "aiInfluenceLabel": definition.key + " 이벤트 묶음",
        })

        for episode in primary_episodes(values, cluster):
            episode_id = add_entity(graph, "trend-episode", symbol + ":" + definition.key + ":" + episode, episode + " 기간 에피소드", {
                "tboxClass": episode if episode != "TemporalObservation" else "TrendEpisode",
                "tboxClasses": ["Signal", "SignalTransition", "TrendEpisode", "TemporalMateriality", episode],
                "field": "trendEpisodeType",
                "value": episode,
                "trendEpisodeType": episode,
                **values,
                **trend_observation,
            })
            episode_role = "support" if episode in {"DeclineDeceleration", "RecoveryAttempt", "AccumulationDuringWeakness"} else "risk" if episode in {"PersistentDecline", "FailedRecovery", "DistributionDuringBounce", "EventDrivenRiskCluster"} else "context"
            add_relation(graph, stock_id, episode_id, "DERIVES_TREND_EPISODE", properties={
                "source": values["source"],
                "field": "trendEpisodeType",
                "windowKey": definition.key,
                "polarity": episode_role,
                "evidenceRole": episode_role,
                "reviewLevel": values.get("temporalReviewLevel"),
                "dataState": values.get("temporalDataState"),
                "aiInfluenceLabel": definition.key + " " + episode,
            })

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
