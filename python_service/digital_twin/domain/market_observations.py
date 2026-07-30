"""State contracts for factual market-observation delivery.

Raw quote observations are operational notifications, not investment
judgements.  Their comparison anchor belongs to the monitoring aggregate so
small consecutive ticks can accumulate until a material, bounded observation
is delivered through the notification outbox.
"""

from copy import deepcopy
from typing import Dict, Iterable

from .market_data import number
from .message_types import MARKET_OBSERVATION
from .portfolio import AlertEvent


MARKET_OBSERVATION_BASELINES_KEY = "marketObservationBaselines"
MARKET_OBSERVATION_CANDIDATES_KEY = "marketObservationReasoningCandidates"


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
    """Read valid per-symbol alert anchors from one persisted monitor state."""

    metadata = state.get("metadata") if isinstance(state, dict) else {}
    raw = metadata.get(MARKET_OBSERVATION_BASELINES_KEY) if isinstance(metadata, dict) else {}
    result: Dict[str, Dict[str, object]] = {}
    for raw_symbol, raw_value in (raw.items() if isinstance(raw, dict) else []):
        symbol = str(raw_symbol or "").upper().strip()
        value = dict(raw_value) if isinstance(raw_value, dict) else {"price": raw_value}
        price = number(value.get("price") if "price" in value else value.get("currentPrice"))
        if not symbol or price <= 0:
            continue
        value["price"] = price
        result[symbol] = value
    return result


def market_observation_baseline(
    state: Dict[str, object],
    symbol: str,
    currency: str = "",
) -> Dict[str, object]:
    """Return the last outbox-accepted observation anchor for a symbol.

    Currency changes invalidate an old price anchor instead of comparing two
    values with different units.
    """

    value = dict(market_observation_baselines(state).get(str(symbol or "").upper().strip()) or {})
    stored_currency = str(value.get("currency") or "").upper().strip()
    current_currency = str(currency or "").upper().strip()
    if stored_currency and current_currency and stored_currency != current_currency:
        return {}
    return value


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
                "currency": currency,
                "source": _position_source(item),
                "initializedAt": str(updated.get("generatedAt") or ""),
            }
        else:
            baseline["price"] = number(baseline.get("price"))
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
            "price": price,
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
    """Advance a quote anchor once its persisted follow-up has been queued.

    Raw delivery is now optional for ordinary changes. Advancing at the source
    snapshot boundary prevents the same cumulative move from re-entering the
    mailbox every poll while retaining the durable reasoning request for retry.
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
        if bool(item.get("deliveryDeferred")):
            # This anchor no longer represents the last owner-visible raw
            # alert. It represents a TypeDB-first follow-up boundary.
            previous.pop("outboxQueuedAt", None)
        baselines[symbol] = {
            **previous,
            "price": price,
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
