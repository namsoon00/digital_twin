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
        baselines[symbol] = {
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
