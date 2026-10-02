"""Immutable source boundaries shared by queue claims and execution batches."""

from typing import Mapping


def reasoning_batch_key(event, lane):
    payload = dict(event.get("payload") or {})
    accounts = payload.get("accountIds") or []
    if isinstance(accounts, str):
        accounts = [accounts]
    account_ids = tuple(sorted({
        str(value or "").strip()
        for value in [*accounts, payload.get("accountId")]
        if str(value or "").strip()
    }))
    boundary = payload.get("verifiedSourceSnapshot")
    boundary = dict(boundary) if isinstance(boundary, Mapping) else {}
    boundaries = [value for value in payload.get("verifiedSourceSnapshots") or []
                  if isinstance(value, Mapping) and value]
    return (
        account_ids,
        str(boundary.get("accountId") or ""),
        str(boundary.get("snapshotId") or ""),
        str(boundary.get("generatedAt") or ""),
        tuple(sorted((str(value.get("accountId") or ""),
                      str(value.get("snapshotId") or ""),
                      str(value.get("generatedAt") or "")) for value in boundaries)),
        str(lane or ""),
    )
