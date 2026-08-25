"""Stateful ingress policies for noisy market observations.

Raw ticks remain in the time-series store.  This module only decides whether
an observed state has persisted long enough to become a replayable TypeDB
reasoning trigger.  It never selects an investment action.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, Mapping

from .market_data import number


MARKET_SIGNAL_TRANSITION_STATE_KEY = "marketSignalTransitionStates"
MARKET_SIGNAL_TRANSITION_RESULTS_KEY = "marketSignalTransitionResults"
MARKET_SIGNAL_TRANSITION_POLICY_VERSION = "market-signal-transition-policy-v1"


def _integer_setting(
    settings: Mapping[str, object],
    key: str,
    fallback: int,
    minimum: int = 1,
    maximum: int = 10,
) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback).strip()))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


def _float_setting(
    settings: Mapping[str, object],
    key: str,
    fallback: float,
    minimum: float = 0.0,
    maximum: float = 1000.0,
) -> float:
    raw = (settings or {}).get(key)
    value = fallback if raw in (None, "") else number(raw)
    return max(minimum, min(maximum, float(value if value is not None else fallback)))


def transition_policy_enabled(settings: Mapping[str, object]) -> bool:
    value = str((settings or {}).get("marketSignalTransitionPolicyEnabled") or "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


@dataclass(frozen=True)
class SignalTransitionPolicy:
    signal_id: str
    label: str
    enter_value: float
    exit_value: float
    confirmations: int
    immediate_value: float = 0.0
    unit: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "policyVersion": MARKET_SIGNAL_TRANSITION_POLICY_VERSION,
            "signalId": self.signal_id,
            "label": self.label,
            "enterValue": self.enter_value,
            "exitValue": self.exit_value,
            "confirmationObservations": self.confirmations,
            "immediateValue": self.immediate_value,
            "unit": self.unit,
        }


def market_signal_transition_policies(settings: Mapping[str, object] = None) -> Dict[str, SignalTransitionPolicy]:
    source = settings or {}
    confirmations = _integer_setting(source, "marketSignalPersistenceObservations", 2)
    return {
        "price": SignalTransitionPolicy(
            "price",
            "누적 가격 변동",
            _float_setting(source, "marketMaterialityPriceChangePct", 0.6, 0.1, 20.0),
            _float_setting(source, "marketSignalPriceResetPct", 0.25, 0.0, 10.0),
            _integer_setting(source, "marketSignalPricePersistenceObservations", confirmations),
            _float_setting(source, "marketSignalPriceImmediatePct", 3.0, 0.5, 30.0),
            "%",
        ),
        "orderbook": SignalTransitionPolicy(
            "orderbook",
            "호가 불균형",
            _float_setting(source, "marketSignalOrderbookEnterPct", 25.0, 1.0, 100.0),
            _float_setting(source, "marketSignalOrderbookExitPct", 15.0, 0.0, 99.0),
            confirmations,
            unit="%",
        ),
        "trade-strength": SignalTransitionPolicy(
            "trade-strength",
            "체결강도",
            _float_setting(source, "marketSignalTradeStrengthBand", 10.0, 1.0, 100.0),
            _float_setting(source, "marketSignalTradeStrengthExitBand", 2.0, 0.0, 99.0),
            confirmations,
            unit="index",
        ),
        "volume": SignalTransitionPolicy(
            "volume",
            "시간 보정 거래량",
            _float_setting(source, "marketSignalVolumeEnterRatio", 1.5, 0.1, 20.0),
            _float_setting(source, "marketSignalVolumeExitRatio", 1.2, 0.0, 19.0),
            confirmations,
            unit="ratio",
        ),
        "investor-flow": SignalTransitionPolicy(
            "investor-flow",
            "투자자 수급 압력",
            _float_setting(source, "marketMaterialityInvestorFlowRatioPct", 15.0, 1.0, 100.0),
            _float_setting(source, "marketSignalInvestorFlowExitPct", 10.0, 0.0, 99.0),
            confirmations,
            unit="%",
        ),
        "trend-cross": SignalTransitionPolicy(
            "trend-cross",
            "이동평균 교차",
            _float_setting(source, "marketSignalTrendCrossBufferPct", 0.3, 0.0, 10.0),
            _float_setting(source, "marketSignalTrendCrossExitPct", 0.1, 0.0, 9.0),
            confirmations,
            unit="%",
        ),
        "trend-distance": SignalTransitionPolicy(
            "trend-distance",
            "이동평균 이격",
            _float_setting(source, "marketMaterialityTrendDistancePct", 2.0, 0.1, 30.0),
            _float_setting(source, "marketSignalTrendDistanceExitPct", 1.5, 0.0, 29.0),
            confirmations,
            unit="%",
        ),
        "data-validity": SignalTransitionPolicy(
            "data-validity",
            "데이터 사용 상태",
            1.0,
            0.0,
            _integer_setting(source, "marketSignalDataStatePersistenceObservations", confirmations),
            unit="state",
        ),
    }


def market_signal_transition_policy_snapshot(settings: Mapping[str, object] = None) -> Dict[str, object]:
    return {
        "version": MARKET_SIGNAL_TRANSITION_POLICY_VERSION,
        "enabled": transition_policy_enabled(settings or {}),
        "policies": [
            policy.to_dict()
            for policy in market_signal_transition_policies(settings).values()
        ],
    }


def _value(payload: Mapping[str, object], camel: str, snake: str = "") -> float:
    if camel in payload:
        return number(payload.get(camel))
    return number(payload.get(snake or camel))


def _text(payload: Mapping[str, object], camel: str, snake: str = "") -> str:
    if camel in payload:
        return str(payload.get(camel) or "").strip().lower()
    return str(payload.get(snake or camel) or "").strip().lower()


def _directional_state(value: float, confirmed: str, enter: float, exit_value: float) -> str:
    if confirmed == "positive":
        if value <= -enter:
            return "negative"
        if value <= exit_value:
            return "neutral"
        return "positive"
    if confirmed == "negative":
        if value >= enter:
            return "positive"
        if value >= -exit_value:
            return "neutral"
        return "negative"
    if value >= enter:
        return "positive"
    if value <= -enter:
        return "negative"
    return "neutral"


def _positive_state(value: float, confirmed: str, enter: float, exit_value: float) -> str:
    if confirmed == "high":
        return "normal" if value <= exit_value else "high"
    return "high" if value >= enter else "normal"


def _data_validity_state(payload: Mapping[str, object]) -> str:
    statuses = {
        _text(payload, "dataQuality", "data_quality"),
        _text(payload, "freshnessStatus", "freshness_status"),
        _text(payload, "sourceTimestampState", "source_timestamp_state"),
        _text(payload, "latencyStatus", "latency_status"),
    }
    statuses.discard("")
    session = _text(payload, "marketSession", "market_session")
    realtime = payload.get("realTime") if "realTime" in payload else payload.get("real_time")
    if session in {"closed", "closed_exception"} or statuses & {"last-close", "reference-only"}:
        return "reference"
    if statuses & {"unavailable", "failed", "error", "poor", "stale", "no-observation"}:
        return "degraded"
    if realtime is True and not statuses & {"partial", "reference", "no-tick", "transport-idle"}:
        return "live"
    if statuses & {"partial", "reference", "no-tick", "transport-idle"}:
        return "limited"
    return "usable"


def _advance_state(
    previous: Mapping[str, object],
    target_state: str,
    policy: SignalTransitionPolicy,
    *,
    observed_value: float = 0.0,
    observed_at: str = "",
    immediate: bool = False,
) -> Dict[str, object]:
    stored = dict(previous or {})
    confirmed = str(stored.get("confirmedState") or "").strip()
    if not confirmed:
        return {
            "state": {
                "policyVersion": MARKET_SIGNAL_TRANSITION_POLICY_VERSION,
                "confirmedState": target_state,
                "candidateState": "",
                "candidateCount": 0,
                "observedValue": observed_value,
                "lastObservedAt": observed_at,
                "lastConfirmedAt": observed_at,
            },
            "transition": "",
            "fromState": "",
            "toState": target_state,
            "confirmationCount": 0,
            "requiredConfirmations": policy.confirmations,
            "immediate": False,
            "baseline": True,
        }
    if target_state == confirmed:
        stored.update({
            "policyVersion": MARKET_SIGNAL_TRANSITION_POLICY_VERSION,
            "candidateState": "",
            "candidateCount": 0,
            "observedValue": observed_value,
            "lastObservedAt": observed_at,
        })
        return {
            "state": stored,
            "transition": "",
            "fromState": confirmed,
            "toState": confirmed,
            "confirmationCount": 0,
            "requiredConfirmations": policy.confirmations,
            "immediate": False,
            "baseline": False,
        }
    candidate_state = str(stored.get("candidateState") or "")
    candidate_count = int(stored.get("candidateCount") or 0) + 1 if candidate_state == target_state else 1
    required = 1 if immediate else policy.confirmations
    if candidate_count >= required:
        stored.update({
            "policyVersion": MARKET_SIGNAL_TRANSITION_POLICY_VERSION,
            "confirmedState": target_state,
            "candidateState": "",
            "candidateCount": 0,
            "observedValue": observed_value,
            "lastObservedAt": observed_at,
            "lastConfirmedAt": observed_at,
        })
        return {
            "state": stored,
            "transition": "changed",
            "fromState": confirmed,
            "toState": target_state,
            "confirmationCount": candidate_count,
            "requiredConfirmations": required,
            "immediate": bool(immediate),
            "baseline": False,
        }
    stored.update({
        "policyVersion": MARKET_SIGNAL_TRANSITION_POLICY_VERSION,
        "candidateState": target_state,
        "candidateCount": candidate_count,
        "observedValue": observed_value,
        "lastObservedAt": observed_at,
    })
    return {
        "state": stored,
        "transition": "pending",
        "fromState": confirmed,
        "toState": target_state,
        "confirmationCount": candidate_count,
        "requiredConfirmations": required,
        "immediate": False,
        "baseline": False,
    }


def _condition(signal_id: str, before: str, after: str) -> str:
    if signal_id == "price":
        return "price-move-immediate" if after.startswith("immediate-") else "price-move"
    if signal_id.startswith("ma20-cross"):
        return "ma20-cross"
    if signal_id.startswith("ma60-cross"):
        return "ma60-cross"
    if signal_id.startswith("ma20-distance"):
        return "ma20-distance-cleared" if after == "neutral" else "ma20-distance"
    if signal_id.startswith("ma60-distance"):
        return "ma60-distance-cleared" if after == "neutral" else "ma60-distance"
    if signal_id == "orderbook":
        if after == "neutral":
            return "orderbook-imbalance-cleared"
        return "orderbook-direction-changed" if before not in {"", "neutral", after} else "orderbook-imbalance"
    if signal_id == "trade-strength":
        if after == "neutral":
            return "trade-pressure-cleared"
        return "trade-pressure-direction-changed" if before not in {"", "neutral", after} else "trade-pressure"
    if signal_id == "volume":
        return "volume-confirmation-cleared" if after == "normal" else "volume-confirmation"
    if signal_id.startswith("foreign-flow"):
        if after == "neutral":
            return "foreign-flow-pressure-cleared"
        return "foreign-flow-direction" if before not in {"", "neutral", after} else "foreign-flow-pressure"
    if signal_id.startswith("institution-flow"):
        if after == "neutral":
            return "institution-flow-pressure-cleared"
        return "institution-flow-direction" if before not in {"", "neutral", after} else "institution-flow-pressure"
    if signal_id == "data-validity":
        return "source-validity-state-change"
    return ""


def _investor_pressure(payload: Mapping[str, object], prefix: str) -> tuple[float, float]:
    net = _value(payload, prefix + "NetVolume", prefix.lower() + "_net_volume")
    buy = _value(payload, prefix + "BuyVolume", prefix.lower() + "_buy_volume")
    sell = _value(payload, prefix + "SellVolume", prefix.lower() + "_sell_volume")
    gross = abs(buy) + abs(sell)
    if not gross:
        gross = abs(_value(payload, "volume", "volume"))
    return net, (abs(net) / gross * 100.0 if gross else 0.0)


def evaluate_market_signal_transitions(
    previous_position: Mapping[str, object],
    current_position: Mapping[str, object],
    previous_state: Mapping[str, object] = None,
    settings: Mapping[str, object] = None,
    observed_at: str = "",
) -> Dict[str, object]:
    """Advance bounded signal candidates and emit only confirmed transitions."""

    policies = market_signal_transition_policies(settings)
    stored_signals = dict((previous_state or {}).get("signals") or {})
    next_signals: Dict[str, object] = {}
    transitions = []
    pending = []

    price = _value(current_position, "currentPrice", "current_price")
    previous_price_state = dict(stored_signals.get("price") or {})
    previous_price_value = _value(previous_position, "currentPrice", "current_price")
    if not previous_price_state and previous_price_value:
        previous_price_state = {
            "policyVersion": MARKET_SIGNAL_TRANSITION_POLICY_VERSION,
            "confirmedState": "anchored",
            "candidateState": "",
            "candidateCount": 0,
            "anchorValue": previous_price_value,
            "observedValue": 0.0,
            "lastObservedAt": observed_at,
            "lastConfirmedAt": observed_at,
        }
    price_anchor = number(previous_price_state.get("anchorValue")) or _value(
        previous_position, "currentPrice", "current_price"
    ) or price
    price_change = ((price - price_anchor) / abs(price_anchor) * 100.0) if price and price_anchor else 0.0
    price_policy = policies["price"]
    price_immediate = abs(price_change) >= price_policy.immediate_value > 0
    if price_immediate:
        price_target = "immediate-positive" if price_change > 0 else "immediate-negative"
    elif abs(price_change) >= price_policy.enter_value:
        price_target = "positive" if price_change > 0 else "negative"
    elif abs(price_change) <= price_policy.exit_value:
        price_target = "anchored"
    else:
        price_target = str(previous_price_state.get("confirmedState") or "anchored")
    price_result = _advance_state(
        previous_price_state,
        price_target,
        price_policy,
        observed_value=round(price_change, 4),
        observed_at=observed_at,
        immediate=price_immediate,
    )
    if price_result["transition"] == "changed":
        price_result["state"]["anchorValue"] = price
        # Price movement is an event relative to a cumulative anchor, not a
        # durable positive/negative regime. Reset the durable state at the new
        # anchor so an unchanged next quote cannot emit a synthetic clear.
        price_result["state"]["confirmedState"] = "anchored"
    else:
        price_result["state"]["anchorValue"] = price_anchor or price
    next_signals["price"] = price_result["state"]
    if price_result["transition"]:
        (transitions if price_result["transition"] == "changed" else pending).append({
            "signalId": "price",
            "condition": _condition("price", price_result["fromState"], price_result["toState"]),
            **{key: value for key, value in price_result.items() if key != "state"},
            "observedValue": round(price_change, 4),
        })

    raw_trade_strength = _value(current_position, "tradeStrength", "trade_strength")
    directional_inputs = {
        "orderbook": (
            _value(current_position, "bidAskImbalance", "bid_ask_imbalance"),
            policies["orderbook"],
        ),
        "trade-strength": (
            raw_trade_strength - 100.0 if raw_trade_strength > 0 else 0.0,
            policies["trade-strength"],
        ),
    }
    for signal_id, (observed_value, policy) in directional_inputs.items():
        previous_signal = dict(stored_signals.get(signal_id) or {})
        target = _directional_state(
            observed_value,
            str(previous_signal.get("confirmedState") or "neutral"),
            policy.enter_value,
            policy.exit_value,
        )
        result = _advance_state(
            previous_signal,
            target,
            policy,
            observed_value=round(observed_value, 4),
            observed_at=observed_at,
        )
        next_signals[signal_id] = result["state"]
        if result["transition"]:
            (transitions if result["transition"] == "changed" else pending).append({
                "signalId": signal_id,
                "condition": _condition(signal_id, result["fromState"], result["toState"]),
                **{key: value for key, value in result.items() if key != "state"},
                "observedValue": round(observed_value, 4),
            })

    volume_policy = policies["volume"]
    volume_value = _value(current_position, "volumeRatio", "volume_ratio")
    volume_previous = dict(stored_signals.get("volume") or {})
    volume_target = _positive_state(
        volume_value,
        str(volume_previous.get("confirmedState") or "normal"),
        volume_policy.enter_value,
        volume_policy.exit_value,
    )
    volume_result = _advance_state(
        volume_previous,
        volume_target,
        volume_policy,
        observed_value=round(volume_value, 4),
        observed_at=observed_at,
    )
    next_signals["volume"] = volume_result["state"]
    if volume_result["transition"]:
        (transitions if volume_result["transition"] == "changed" else pending).append({
            "signalId": "volume",
            "condition": _condition("volume", volume_result["fromState"], volume_result["toState"]),
            **{key: value for key, value in volume_result.items() if key != "state"},
            "observedValue": round(volume_value, 4),
        })

    flow_policy = policies["investor-flow"]
    for prefix, signal_id in (("foreign", "foreign-flow"), ("institution", "institution-flow")):
        net, pressure = _investor_pressure(current_position, prefix)
        previous_signal = dict(stored_signals.get(signal_id) or {})
        signed_pressure = pressure if net > 0 else -pressure if net < 0 else 0.0
        target = _directional_state(
            signed_pressure,
            str(previous_signal.get("confirmedState") or "neutral"),
            flow_policy.enter_value,
            flow_policy.exit_value,
        )
        result = _advance_state(
            previous_signal,
            target,
            flow_policy,
            observed_value=round(signed_pressure, 4),
            observed_at=observed_at,
        )
        next_signals[signal_id] = result["state"]
        if result["transition"]:
            (transitions if result["transition"] == "changed" else pending).append({
                "signalId": signal_id,
                "condition": _condition(signal_id, result["fromState"], result["toState"]),
                **{key: value for key, value in result.items() if key != "state"},
                "observedValue": round(signed_pressure, 4),
            })

    for period in ("ma20", "ma60"):
        distance = _value(current_position, period + "Distance", period + "_distance")
        for suffix, policy_key in (("cross", "trend-cross"), ("distance", "trend-distance")):
            signal_id = period + "-" + suffix
            policy = policies[policy_key]
            previous_signal = dict(stored_signals.get(signal_id) or {})
            target = _directional_state(
                distance,
                str(previous_signal.get("confirmedState") or "neutral"),
                policy.enter_value,
                policy.exit_value,
            )
            result = _advance_state(
                previous_signal,
                target,
                policy,
                observed_value=round(distance, 4),
                observed_at=observed_at,
            )
            next_signals[signal_id] = result["state"]
            if result["transition"]:
                (transitions if result["transition"] == "changed" else pending).append({
                    "signalId": signal_id,
                    "condition": _condition(signal_id, result["fromState"], result["toState"]),
                    **{key: value for key, value in result.items() if key != "state"},
                    "observedValue": round(distance, 4),
                })

    data_policy = policies["data-validity"]
    data_previous = dict(stored_signals.get("data-validity") or {})
    data_target = _data_validity_state(current_position)
    data_result = _advance_state(
        data_previous,
        data_target,
        data_policy,
        observed_at=observed_at,
    )
    next_signals["data-validity"] = data_result["state"]
    if data_result["transition"]:
        (transitions if data_result["transition"] == "changed" else pending).append({
            "signalId": "data-validity",
            "condition": _condition("data-validity", data_result["fromState"], data_result["toState"]),
            **{key: value for key, value in data_result.items() if key != "state"},
            "observedValue": data_target,
        })

    return {
        "version": MARKET_SIGNAL_TRANSITION_POLICY_VERSION,
        "enabled": transition_policy_enabled(settings or {}),
        "state": {"version": MARKET_SIGNAL_TRANSITION_POLICY_VERSION, "signals": next_signals},
        "confirmedTransitions": transitions if transition_policy_enabled(settings or {}) else [],
        "pendingTransitions": pending if transition_policy_enabled(settings or {}) else [],
        "confirmedConditions": [
            str(item.get("condition") or "")
            for item in transitions
            if str(item.get("condition") or "")
        ] if transition_policy_enabled(settings or {}) else [],
        "immediate": any(bool(item.get("immediate")) for item in transitions),
    }


def prepare_market_signal_transition_metadata(
    snapshot,
    previous_state: Mapping[str, object] = None,
    settings: Mapping[str, object] = None,
) -> Dict[str, Dict[str, object]]:
    """Attach bounded candidate checkpoints before the snapshot transaction."""

    metadata = dict(getattr(snapshot, "metadata", {}) or {})
    previous_metadata = (
        (previous_state or {}).get("metadata")
        if isinstance((previous_state or {}).get("metadata"), Mapping)
        else {}
    )
    previous_states = dict((previous_metadata or {}).get(MARKET_SIGNAL_TRANSITION_STATE_KEY) or {})
    previous_positions: Dict[str, Dict[str, object]] = {}
    for group in ("positions", "watchlist"):
        values = (previous_state or {}).get(group) if isinstance(previous_state, Mapping) else {}
        rows = values.values() if isinstance(values, Mapping) else values if isinstance(values, list) else []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if symbol and symbol != "CASH" and symbol not in previous_positions:
                previous_positions[symbol] = dict(row)
    results: Dict[str, Dict[str, object]] = {}
    next_states: Dict[str, Dict[str, object]] = {}
    for position in list(getattr(snapshot, "positions", []) or []) + list(getattr(snapshot, "watchlist", []) or []):
        if position.is_cash():
            continue
        symbol = str(position.symbol or "").upper().strip()
        if not symbol or symbol in results:
            continue
        result = evaluate_market_signal_transitions(
            previous_positions.get(symbol) or {},
            position.to_dict(),
            previous_states.get(symbol) if isinstance(previous_states.get(symbol), Mapping) else {},
            settings=settings,
            observed_at=str(getattr(snapshot, "generated_at", "") or ""),
        )
        results[symbol] = {
            key: deepcopy(value)
            for key, value in result.items()
            if key != "state"
        }
        next_states[symbol] = deepcopy(result["state"])
    metadata[MARKET_SIGNAL_TRANSITION_STATE_KEY] = next_states
    metadata[MARKET_SIGNAL_TRANSITION_RESULTS_KEY] = results
    snapshot.metadata = metadata
    return results
