"""Selection policy for overlapping official market-index observations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Dict, Mapping, Tuple


def _timestamp(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        if re.fullmatch(r"\d{8}", text):
            parsed = datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _provider_priority(value: object) -> int:
    provider = str(value or "").casefold().strip()
    if "krx" in provider:
        return 30
    if "금융위원회" in provider or "공공데이터" in provider or "data-go-kr" in provider:
        return 20
    if provider:
        return 10
    return 0


def market_index_observation_rank(value: Mapping[str, object]) -> Tuple[float, int, float, str]:
    """Rank by observation clock, direct-source authority, then collection clock.

    Collection order must never let an older index close replace a newer one.
    The final canonical tie breaker keeps the merge deterministic when two
    observations otherwise have the same clocks and provider authority.
    """

    row = dict(value or {})
    observed_at = max(
        _timestamp(row.get("sourceAsOf")),
        _timestamp(row.get("baseDate")),
        _timestamp(row.get("date")),
    )
    fetched_at = max(
        _timestamp(row.get("fetchedAt")),
        _timestamp(row.get("observedAt")),
    )
    canonical = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return observed_at, _provider_priority(row.get("provider")), fetched_at, canonical


def merge_market_index_maps(
    base: Mapping[str, object],
    incoming: Mapping[str, object],
) -> Dict[str, object]:
    result = {
        str(key or "").upper().strip(): dict(value)
        for key, value in dict(base or {}).items()
        if str(key or "").strip() and isinstance(value, Mapping)
    }
    for raw_key, raw_value in dict(incoming or {}).items():
        key = str(raw_key or "").upper().strip()
        if not key or not isinstance(raw_value, Mapping):
            continue
        candidate = dict(raw_value)
        current = result.get(key)
        if current is None or market_index_observation_rank(candidate) > market_index_observation_rank(current):
            result[key] = candidate
    return result


__all__ = ["market_index_observation_rank", "merge_market_index_maps"]
