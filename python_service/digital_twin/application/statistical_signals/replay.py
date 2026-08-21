"""Point-in-time outcome observation for immutable statistical signals."""

from datetime import datetime, timezone
import re
from typing import Iterable, Mapping, Optional

from ...domain.statistical_signals import ModelSignal, ModelSignalOutcome


def _timestamp(value: object) -> Optional[datetime]:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_timestamp(row: Mapping[str, object]) -> str:
    for key in ("bucketAt", "bucket_at", "observedAt", "observed_at", "generatedAt"):
        if str(row.get(key) or "").strip():
            return str(row.get(key) or "").strip()
    return ""


def _price(row: Mapping[str, object]) -> float:
    for key in ("currentPrice", "current_price", "close", "closePrice"):
        try:
            value = float(row.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return 0.0


def _horizon_sessions(value: object) -> int:
    matched = re.search(r"([0-9]+)", str(value or ""))
    return max(1, int(matched.group(1))) if matched else 1


def observe_model_signal_outcome(
    signal: ModelSignal,
    future_observations: Iterable[Mapping[str, object]],
) -> Optional[ModelSignalOutcome]:
    cutoff = _timestamp(signal.observed_at)
    entry_price = float(signal.input_features.get("currentPrice") or 0)
    if not cutoff or entry_price <= 0:
        return None
    ordered = sorted(
        [dict(item) for item in future_observations or [] if isinstance(item, Mapping)],
        key=lambda row: _row_timestamp(row),
    )
    eligible = [
        row for row in ordered
        if _timestamp(_row_timestamp(row)) and _timestamp(_row_timestamp(row)) > cutoff and _price(row) > 0
    ]
    sessions = []
    selected = []
    for row in eligible:
        session = _row_timestamp(row)[:10]
        if session not in sessions:
            sessions.append(session)
        if len(sessions) > _horizon_sessions(signal.horizon):
            break
        selected.append(row)
    if not selected:
        return None
    prices = [_price(row) for row in selected]
    returns = [(price / entry_price) - 1.0 for price in prices]
    return ModelSignalOutcome(
        signal_id=signal.signal_id,
        signal_type=signal.signal_type,
        subject_id=signal.subject_id,
        horizon=signal.horizon,
        observed_at=signal.observed_at,
        outcome_at=_row_timestamp(selected[-1]),
        polarity=signal.polarity,
        score=signal.score,
        probability=signal.probability,
        forward_return=returns[-1],
        maximum_favorable_excursion=max(returns),
        maximum_adverse_excursion=min(returns),
        point_in_time_verified=True,
    )
