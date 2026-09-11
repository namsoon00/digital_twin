"""State contracts for factual market-observation delivery.

Raw quote observations are operational notifications, not investment
judgements.  Their comparison anchor belongs to the monitoring aggregate so
small consecutive ticks can accumulate until a material, bounded observation
is delivered through the notification outbox.
"""

from copy import deepcopy
from math import isclose
from typing import Dict, Iterable

from digital_twin.modules.market_data.domain.market_data import number
from digital_twin.modules.notifications.contracts import MARKET_OBSERVATION, MIN_CADENCE_MINUTES
from digital_twin.modules.portfolio.contracts import AlertEvent


MARKET_OBSERVATION_BASELINES_KEY = "marketObservationBaselines"
MARKET_OBSERVATION_CANDIDATES_KEY = "marketObservationReasoningCandidates"


def market_observation_delivery_cadence_minutes(settings: Dict[str, object] = None) -> int:
    """Return the raw quote alert's dedicated duplicate-protection window.

    This cadence is an operational safety boundary. It remains active when the
    account's ordinary notification cooldown is disabled because two monitor
    workers may observe the same material quote move at nearly the same time.
    """

    source = settings if isinstance(settings, dict) else {}
    raw = source.get("marketObservationImmediateCadenceMinutes")
    try:
        value = int(float(str(raw).strip())) if str(raw or "").strip() else MIN_CADENCE_MINUTES
    except (TypeError, ValueError):
        value = MIN_CADENCE_MINUTES
    return max(MIN_CADENCE_MINUTES, value)


def _state_positions(state: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    """Return current holding/watchlist rows keyed by symbol."""

    result: Dict[str, Dict[str, object]] = {}
    for group_name in ("positions", "watchlist"):
        group = state.get(group_name) if isinstance(state, dict) else {}
        values = group.values() if isinstance(group, dict) else group if isinstance(group, list) else []
        for item in values:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper().strip()
            if not symbol or symbol == "CASH" or (group_name == "watchlist" and symbol in result):
                continue
            result[symbol] = dict(item)
    return result


def _position_price(item: Dict[str, object]) -> float:
    return number(item.get("current_price") if "current_price" in item else item.get("currentPrice"))


def _position_currency(item: Dict[str, object]) -> str:
    return str(item.get("currency") or "").upper().strip()


def _position_source(item: Dict[str, object]) -> str:
    return str(item.get("quote_source") or item.get("quoteSource") or item.get("source") or "").strip()


def market_observation_baselines(state: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    """Read valid per-symbol reasoning and owner-delivery anchors."""

    metadata = state.get("metadata") if isinstance(state, dict) else {}
    raw = metadata.get(MARKET_OBSERVATION_BASELINES_KEY) if isinstance(metadata, dict) else {}
    result: Dict[str, Dict[str, object]] = {}
    for raw_symbol, raw_value in (raw.items() if isinstance(raw, dict) else []):
        symbol = str(raw_symbol or "").upper().strip()
        value = dict(raw_value) if isinstance(raw_value, dict) else {"price": raw_value}
        price = number(
            value.get("reasoningPrice")
            or value.get("price")
            or value.get("currentPrice")
            or value.get("outboxPrice")
            or value.get("initialPrice")
        )
        if not symbol or price <= 0:
            continue
        value["price"] = price
        value["reasoningPrice"] = number(value.get("reasoningPrice")) or price
        value["initialPrice"] = number(value.get("initialPrice")) or price
        outbox_price = number(value.get("outboxPrice"))
        if outbox_price > 0:
            value["outboxPrice"] = outbox_price
        elif value.get("outboxQueuedAt"):
            # Legacy rows stored only one price plus the delivery marker. The
            # normalized copy becomes explicit on the next snapshot write.
            value["outboxPrice"] = price
        result[symbol] = value
    return result


def market_observation_baseline(
    state: Dict[str, object],
    symbol: str,
    currency: str = "",
) -> Dict[str, object]:
    """Return all durable observation anchors for a symbol.

    Currency changes invalidate an old price anchor instead of comparing two
    values with different units.
    """

    value = dict(market_observation_baselines(state).get(str(symbol or "").upper().strip()) or {})
    stored_currency = str(value.get("currency") or "").upper().strip()
    current_currency = str(currency or "").upper().strip()
    if stored_currency and current_currency and stored_currency != current_currency:
        return {}
    return value


def market_observation_delivery_admission(
    event: AlertEvent,
    authoritative_state: Dict[str, object],
) -> Dict[str, object]:
    """Revalidate one raw quote alert against the latest durable outbox anchor.

    Detection happens before the monitoring transaction opens. A concurrent
    worker can therefore advance the outbox anchor after this event was built.
    The transaction boundary must reject that stale candidate instead of
    treating its new event id as a new price move.
    """

    if str(getattr(event, "rule", "") or "") != MARKET_OBSERVATION:
        return {"accepted": True, "reasonCode": "not-market-observation"}
    metadata = dict(getattr(event, "metadata", {}) or {})
    if bool(metadata.get("deliveryDeferred")):
        return {"accepted": True, "reasonCode": "delivery-deferred"}
    observation = (
        dict(metadata.get("marketObservation") or {})
        if isinstance(metadata.get("marketObservation"), dict)
        else {}
    )
    symbol = str(getattr(event, "symbol", "") or "").upper().strip()
    currency = str(observation.get("currency") or "").upper().strip()
    current_price = number(observation.get("currentPrice"))
    candidate_anchor = (
        number(observation.get("outboxBaselinePrice"))
        or number(observation.get("baselinePrice"))
    )
    threshold = (
        number(observation.get("deliveryThresholdPct"))
        or number(observation.get("immediateThresholdPct"))
        or number(observation.get("thresholdPct"))
    )
    baseline = market_observation_baseline(authoritative_state or {}, symbol, currency)
    authoritative_anchor = (
        number(baseline.get("outboxPrice"))
        or number(baseline.get("initialPrice"))
        or number(baseline.get("price"))
    )
    details = {
        "symbol": symbol,
        "candidateAnchorPrice": candidate_anchor,
        "authoritativeAnchorPrice": authoritative_anchor,
        "currentPrice": current_price,
        "thresholdPct": threshold,
    }
    if current_price <= 0 or candidate_anchor <= 0 or threshold <= 0:
        return {**details, "accepted": False, "reasonCode": "invalid-market-observation"}
    if authoritative_anchor <= 0:
        return {**details, "accepted": True, "reasonCode": "no-authoritative-anchor"}
    if not isclose(candidate_anchor, authoritative_anchor, rel_tol=1e-9, abs_tol=1e-8):
        return {**details, "accepted": False, "reasonCode": "stale-outbox-anchor"}
    change_pct = (current_price - authoritative_anchor) / abs(authoritative_anchor) * 100.0
    details["authoritativeChangePct"] = round(change_pct, 6)
    if abs(change_pct) < threshold:
        return {**details, "accepted": False, "reasonCode": "below-delivery-threshold"}
    expected_direction = "up" if change_pct > 0 else "down" if change_pct < 0 else "flat"
    if str(observation.get("direction") or "").strip().lower() != expected_direction:
        return {**details, "accepted": False, "reasonCode": "stale-direction"}
    return {**details, "accepted": True, "reasonCode": "current-outbox-anchor"}


def hydrate_market_observation_baselines(
    state: Dict[str, object],
    previous_state: Dict[str, object],
) -> Dict[str, object]:
    """Carry alert anchors across ordinary monitor snapshots.

    A new subject is anchored to the immediately preceding verified price when
    available (otherwise its current first-seen price).  That one-time anchor
    lets sub-threshold ticks accumulate until the next outbox-accepted alert,
    instead of requiring a single three-minute jump to bootstrap the feature.
    """

    updated = deepcopy(state or {})
    current_positions = _state_positions(updated)
    if not current_positions:
        return updated
    previous_positions = _state_positions(previous_state or {})
    inherited = market_observation_baselines(previous_state or {})
    inherited.update(market_observation_baselines(updated))
    baselines: Dict[str, Dict[str, object]] = {}
    for symbol, item in current_positions.items():
        currency = _position_currency(item)
        current_price = _position_price(item)
        baseline = dict(inherited.get(symbol) or {})
        baseline_currency = str(baseline.get("currency") or "").upper().strip()
        if baseline_currency and currency and baseline_currency != currency:
            baseline = {}
        if number(baseline.get("price")) <= 0:
            previous_item = previous_positions.get(symbol) or {}
            previous_currency = _position_currency(previous_item)
            previous_price = _position_price(previous_item)
            if previous_currency and currency and previous_currency != currency:
                previous_price = 0.0
            anchor_price = previous_price or current_price
            if anchor_price <= 0:
                continue
            baseline = {
                "price": anchor_price,
                "reasoningPrice": anchor_price,
                "initialPrice": anchor_price,
                "currency": currency,
                "source": _position_source(item),
                "initializedAt": str(updated.get("generatedAt") or ""),
            }
        else:
            reasoning_price = number(baseline.get("reasoningPrice")) or number(baseline.get("price"))
            baseline["price"] = reasoning_price
            baseline["reasoningPrice"] = reasoning_price
            baseline["initialPrice"] = number(baseline.get("initialPrice")) or reasoning_price
            baseline["currency"] = currency or baseline_currency
            baseline.setdefault("source", _position_source(item))
        baselines[symbol] = baseline
    if baselines:
        metadata = dict(updated.get("metadata") or {})
        metadata[MARKET_OBSERVATION_BASELINES_KEY] = baselines
        updated["metadata"] = metadata
    return updated


def apply_market_observation_outbox_baselines(
    state: Dict[str, object],
    events: Iterable[AlertEvent],
) -> Dict[str, object]:
    """Advance anchors only for observation events accepted by the outbox.

    The monitoring snapshot and notification job are committed by the same
    transaction.  Updating the anchor at that boundary avoids losing a
    cumulative move merely because intermediate three-minute snapshots were
    below the threshold, while avoiding a baseline advance for cadence- or
    delivery-guard-suppressed candidates.
    """

    updated = deepcopy(state or {})
    metadata = dict(updated.get("metadata") or {})
    baselines = market_observation_baselines(updated)
    changed = False
    for event in events or []:
        if str(getattr(event, "rule", "") or "") != MARKET_OBSERVATION:
            continue
        symbol = str(getattr(event, "symbol", "") or "").upper().strip()
        observation = getattr(event, "metadata", {}).get("marketObservation") if isinstance(getattr(event, "metadata", None), dict) else {}
        observation = observation if isinstance(observation, dict) else {}
        price = number(observation.get("currentPrice"))
        if not symbol or price <= 0:
            continue
        previous = dict(baselines.get(symbol) or {})
        baselines[symbol] = {
            **previous,
            "price": number(previous.get("reasoningPrice")) or number(previous.get("price")) or price,
            "reasoningPrice": number(previous.get("reasoningPrice")) or number(previous.get("price")) or price,
            "outboxPrice": price,
            "initialPrice": number(previous.get("initialPrice")) or price,
            "currency": str(observation.get("currency") or "").upper().strip(),
            "source": str(observation.get("source") or "").strip(),
            "outboxQueuedAt": str(getattr(event, "generated_at", "") or updated.get("generatedAt") or ""),
        }
        changed = True
    if changed:
        metadata[MARKET_OBSERVATION_BASELINES_KEY] = baselines
        updated["metadata"] = metadata
    return updated


def market_observation_reasoning_candidates(metadata: Dict[str, object]) -> list[Dict[str, object]]:
    """Read bounded quote candidates that must receive a TypeDB follow-up.

    Candidate records are operational provenance attached to the persisted
    monitor boundary. They are not investment facts and are deliberately
    small enough to survive a worker retry without replaying a notification.
    """
    source = metadata if isinstance(metadata, dict) else {}
    raw = source.get(MARKET_OBSERVATION_CANDIDATES_KEY) or []
    if not isinstance(raw, (list, tuple)):
        return []
    candidates = []
    seen = set()
    for value in raw:
        item = dict(value) if isinstance(value, dict) else {}
        symbol = str(item.get("symbol") or "").upper().strip()
        observation = item.get("marketObservation") if isinstance(item.get("marketObservation"), dict) else {}
        if not symbol or symbol in seen or number(observation.get("currentPrice")) <= 0:
            continue
        seen.add(symbol)
        candidates.append({
            "symbol": symbol,
            "marketObservation": dict(observation),
            "deliveryDeferred": bool(item.get("deliveryDeferred")),
        })
    return candidates


def market_observation_reasoning_symbols(metadata: Dict[str, object]) -> list[str]:
    return [item["symbol"] for item in market_observation_reasoning_candidates(metadata)]


def apply_market_observation_reasoning_baselines(
    state: Dict[str, object],
    candidates: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    """Record a pending quote anchor without claiming inference completion.

    Raw delivery is now optional for ordinary changes. Advancing at the source
    The completed reasoning anchor advances only after a verified TypeDB
    generation. A separate pending anchor suppresses duplicate polling events
    while keeping an unprocessed cumulative move visible across restarts.
    """
    updated = deepcopy(state or {})
    metadata = dict(updated.get("metadata") or {})
    baselines = market_observation_baselines(updated)
    changed = False
    for candidate in candidates or []:
        item = dict(candidate) if isinstance(candidate, dict) else {}
        symbol = str(item.get("symbol") or "").upper().strip()
        observation = item.get("marketObservation") if isinstance(item.get("marketObservation"), dict) else {}
        price = number(observation.get("currentPrice"))
        if not symbol or price <= 0:
            continue
        previous = dict(baselines.get(symbol) or {})
        initial_price = (
            number(previous.get("initialPrice"))
            or number(observation.get("initialPrice"))
            or number(observation.get("outboxBaselinePrice"))
            or number(observation.get("baselinePrice"))
            or price
        )
        baselines[symbol] = {
            **previous,
            "price": number(previous.get("reasoningPrice")) or number(previous.get("price")) or price,
            "reasoningPrice": number(previous.get("reasoningPrice")) or number(previous.get("price")) or price,
            "pendingReasoningPrice": price,
            "initialPrice": initial_price,
            "currency": str(observation.get("currency") or "").upper().strip(),
            "source": str(observation.get("source") or "").strip(),
            "reasoningQueuedAt": str(updated.get("generatedAt") or ""),
        }
        changed = True
    if changed:
        metadata[MARKET_OBSERVATION_BASELINES_KEY] = baselines
    # Candidates belong to one source snapshot only. The linked TypeDB event
    # stores its own marker, so retaining them on every later snapshot would
    # falsely recreate the follow-up after a restart.
    metadata.pop(MARKET_OBSERVATION_CANDIDATES_KEY, None)
    updated["metadata"] = metadata
    return updated
