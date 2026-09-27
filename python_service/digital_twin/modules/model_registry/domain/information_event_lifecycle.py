"""Shared vocabulary for evidence-backed information-event progress."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Mapping


INFORMATION_EVENT_LIFECYCLE_VERSION = "information-event-lifecycle-v1"
INFORMATION_EVENT_LIFECYCLE_STATES = (
    "scheduled",
    "announced",
    "awaiting-result",
    "released",
    "assessed",
    "market-confirmed",
    "expired",
)


def _time(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def information_event_lifecycle(
    *,
    scheduled_at: object = "",
    announced_at: object = "",
    published_at: object = "",
    released_at: object = "",
    release_status: object = "",
    assessment: Mapping[str, object] = None,
    market_reaction: Mapping[str, object] = None,
    expires_at: object = "",
    evaluated_at: object = "",
) -> Dict[str, object]:
    now = _time(evaluated_at) or datetime.now(timezone.utc)
    scheduled = _time(scheduled_at)
    announced = _time(announced_at) or _time(published_at)
    released = _time(released_at)
    expires = _time(expires_at)
    assessment_values = dict(assessment or {})
    reaction = dict(market_reaction or {})
    release_state = str(release_status or "").strip().lower()
    assessed = bool(
        assessment_values
        and str(assessment_values.get("status") or "").lower()
        not in {"", "unavailable", "unsupported", "not-assessed"}
    )
    observations = [row for row in reaction.get("observations") or [] if isinstance(row, Mapping)]
    market_confirmed = bool(
        str(reaction.get("status") or "").lower() in {"observed", "complete", "confirmed"}
        and any(str(row.get("status") or "").lower() in {"observed", "ok", "complete"} for row in observations)
    )
    released_known = bool(
        released and released <= now
        or release_state in {"released", "complete", "verified"}
    )
    if expires and expires < now:
        state = "expired"
    elif market_confirmed:
        state = "market-confirmed"
    elif assessed:
        state = "assessed"
    elif released_known:
        state = "released"
    elif scheduled and scheduled <= now:
        state = "awaiting-result"
    elif announced and announced <= now:
        state = "announced"
    else:
        state = "scheduled"
    milestones = {
        "scheduledAt": str(scheduled_at or ""),
        "announcedAt": str(announced_at or ""),
        "publishedAt": str(published_at or ""),
        "releasedAt": str(released_at or ""),
        "assessed": assessed,
        "marketConfirmed": market_confirmed,
        "expiresAt": str(expires_at or ""),
    }
    return {
        "version": INFORMATION_EVENT_LIFECYCLE_VERSION,
        "state": state,
        "stateIndex": INFORMATION_EVENT_LIFECYCLE_STATES.index(state),
        "resultAvailable": released_known,
        "assessmentAvailable": assessed,
        "marketConfirmationAvailable": market_confirmed,
        "milestones": milestones,
    }
