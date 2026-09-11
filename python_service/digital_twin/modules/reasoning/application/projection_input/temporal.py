"""Point-in-time temporal input reads through a vendor-neutral query port."""

from __future__ import annotations

from digital_twin.modules.portfolio.contracts import AccountSnapshot
from digital_twin.modules.reasoning.domain.portfolio_ontology_temporal_concepts import parse_temporal_windows
from typing import Dict
from digital_twin.modules.reasoning.application.projection_input.ports import (
    TemporalInputs,
)


def temporal_observation_windows(
    _inputs: TemporalInputs,
    snapshot: AccountSnapshot,
    target_symbols=None,
) -> Dict[str, object]:
    if not _inputs.market_time_series_store or not hasattr(
        _inputs.market_time_series_store, "load_temporal_windows"
    ):
        return {}
    symbols = {
        str(getattr(position, "symbol", "") or "").upper().strip()
        for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
        if str(getattr(position, "symbol", "") or "").strip() and not position.is_cash()
    }
    requested = {
        str(symbol or "").upper().strip()
        for symbol in target_symbols or []
        if str(symbol or "").strip()
    }
    if requested:
        symbols.intersection_update(requested)
    if not symbols:
        return {}
    definitions = parse_temporal_windows(_inputs.settings.get("temporalWindowPeriods"))
    try:
        return _inputs.market_time_series_store.load_temporal_windows(
            snapshot.account_id,
            symbols,
            definitions,
            as_of=str(snapshot.generated_at or ""),
        )
    except (
        Exception
    ):  # noqa: BLE001 - short snapshot history remains a valid compatibility fallback.
        return {}
