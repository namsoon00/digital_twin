"""Durable completion contract for market-observation reasoning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Tuple


MARKET_OBSERVATION_REASONING_RECEIPT_VERSION = "market-observation-reasoning-receipt-v1"

COMPLETION_MODE_DIRECT = "direct"
COMPLETION_MODE_COALESCED = "coalesced"
COMPLETION_MODE_VERIFIED_LATER_BOUNDARY = "verified-later-boundary"


def _text(value: object) -> str:
    return str(value or "").strip()


def _upper(value: object) -> str:
    return _text(value).upper()


def _items(values: object, normalizer=_text) -> Tuple[str, ...]:
    if not isinstance(values, (list, tuple, set)):
        values = []
    return tuple(sorted({normalizer(value) for value in values if normalizer(value)}))


def market_observation_completion_scope(source_event: Mapping[str, object]) -> Dict[str, object]:
    """Return every source identity represented by one possibly merged event."""

    event = dict(source_event or {})
    payload = dict(event.get("payload") or {})
    shard = dict(payload.get("reasoningShard") or {})
    coalesced = dict(payload.get("coalescedReasoningChanges") or {})
    event_ids = {
        _text(event.get("eventId") or event.get("event_id")),
        _text(shard.get("parentEventId")),
        *{
            _text(value)
            for value in coalesced.get("sourceEventIds") or []
        },
    }
    account_ids = _items(payload.get("accountIds"))
    symbols = {
        _upper(value)
        for key in ("affectedSymbols", "symbols", "targetSymbols")
        for value in payload.get(key) or []
        if _upper(value)
    }
    return {
        "eventIds": tuple(sorted(value for value in event_ids if value)),
        "accountIds": account_ids,
        "symbols": tuple(sorted(symbols)),
    }


def completion_mode(source_event_id: str, represented_event_id: str) -> str:
    return (
        COMPLETION_MODE_DIRECT
        if _text(source_event_id) == _text(represented_event_id)
        else COMPLETION_MODE_COALESCED
    )


@dataclass(frozen=True)
class MarketObservationReasoningReceipt:
    source_event_id: str
    account_id: str
    symbol: str
    survivor_job_id: str
    deployment_id: str = ""
    source_snapshot_id: str = ""
    source_snapshot_at: str = ""
    source_abox_snapshot_id: str = ""
    inference_generation_id: str = ""
    release_fingerprint: str = ""
    tbox_release_id: str = ""
    tbox_fingerprint: str = ""
    rulebox_release_id: str = ""
    rulebox_fingerprint: str = ""
    completion_mode: str = COMPLETION_MODE_DIRECT
    completed_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "contractVersion": MARKET_OBSERVATION_REASONING_RECEIPT_VERSION,
            "sourceEventId": _text(self.source_event_id),
            "accountId": _text(self.account_id),
            "symbol": _upper(self.symbol),
            "survivorJobId": _text(self.survivor_job_id),
            "deploymentId": _text(self.deployment_id),
            "sourceSnapshotId": _text(self.source_snapshot_id),
            "sourceSnapshotAt": _text(self.source_snapshot_at),
            "sourceAboxSnapshotId": _text(self.source_abox_snapshot_id),
            "inferenceGenerationId": _text(self.inference_generation_id),
            "releaseFingerprint": _text(self.release_fingerprint),
            "tboxReleaseId": _text(self.tbox_release_id),
            "tboxFingerprint": _text(self.tbox_fingerprint),
            "ruleboxReleaseId": _text(self.rulebox_release_id),
            "ruleboxFingerprint": _text(self.rulebox_fingerprint),
            "completionMode": _text(self.completion_mode) or COMPLETION_MODE_DIRECT,
            "completedAt": _text(self.completed_at),
        }


def represented_event_ids(source_events: Iterable[Mapping[str, object]]) -> Tuple[str, ...]:
    values = set()
    for source_event in source_events or []:
        values.update(market_observation_completion_scope(source_event)["eventIds"])
    return tuple(sorted(values))
