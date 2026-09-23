"""Point-in-time temporal input reads through a vendor-neutral query port."""

from __future__ import annotations

from digital_twin.modules.portfolio.contracts import AccountSnapshot
from digital_twin.modules.reasoning.domain.portfolio_ontology_temporal_concepts import parse_temporal_windows
from typing import Dict
from digital_twin.modules.reasoning.application.projection_input.ports import (
    TemporalInputs,
)
from digital_twin.modules.reasoning.domain.reasoning_shadow import (
    unpack_projection_runtime_contexts,
)


def temporal_feature_input_from_packet(packet, account_id: str) -> Dict[str, object]:
    """Return the immutable feature identity used by one graph assembly."""

    try:
        contexts = unpack_projection_runtime_contexts(packet or {})
    except ValueError:
        return {}
    context = dict(contexts.get(str(account_id or "")) or {})
    snapshot = dict(context.get("temporalFeatureSnapshot") or {})
    snapshot_id = str(snapshot.get("snapshotId") or "").strip()
    payload_hash = str(snapshot.get("payloadHash") or "").strip()
    if not snapshot_id or not payload_hash:
        return {}
    return {
        "snapshotId": snapshot_id,
        "payloadHash": payload_hash,
        "windowsHash": str(snapshot.get("windowsHash") or "").strip(),
        "backendId": str(snapshot.get("backendId") or "").strip(),
        "featureSetVersion": str(snapshot.get("featureSetVersion") or "").strip(),
        "asOf": str(snapshot.get("asOf") or "").strip(),
        "symbols": sorted(
            {
                str(symbol or "").upper().strip()
                for symbol in snapshot.get("symbols") or []
                if str(symbol or "").strip()
            }
        ),
    }


def temporal_observation_windows(
    _inputs: TemporalInputs,
    snapshot: AccountSnapshot,
    target_symbols=None,
) -> Dict[str, object]:
    if not _inputs.market_time_series_store or not hasattr(
        _inputs.market_time_series_store, "load_temporal_windows"
    ):
        return {}
    projection_input = getattr(snapshot, "projection_observation_input", None)
    if callable(projection_input):
        symbols = {
            str(symbol or "").upper().strip()
            for symbol in projection_input().get("availableSymbols") or []
            if str(symbol or "").strip()
        }
    else:
        symbols = {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(getattr(position, "symbol", "") or "").strip()
            and not position.is_cash()
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
    # A successful read with short history returns explicit empty windows. A
    # backend failure is materially different: swallowing it here would turn
    # an infrastructure incident into apparent market-data absence and let the
    # investment brain continue without its price-path evidence.
    return _inputs.market_time_series_store.load_temporal_windows(
        snapshot.account_id,
        symbols,
        definitions,
        as_of=str(snapshot.generated_at or ""),
    )
