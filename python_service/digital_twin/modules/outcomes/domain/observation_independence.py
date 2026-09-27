"""Deterministic observation selection, before eligibility or results are read."""

from datetime import datetime, timedelta, timezone


OBSERVATION_SELECTION_VERSION = "outcome-interval-selection-v2"


def observation_time(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def select_independent_observations(observations, *, require_interval=False):
    """Keep the earliest episode per event and non-overlapping horizon.

    Later measurements may correct the *same* episode; a later episode cannot
    replace a failed or missing first prediction. Different horizons are never
    pooled. Legacy rows can remain in audit reports, but not v2 qualification.
    """
    rows, excluded, latest = list(observations or []), [], {}
    for source in rows:
        row = dict(source)
        episode = str(row.get("episodeId") or row.get("sourceEpisodeId") or "")
        try:
            horizon = int(row.get("horizonMinutes") or 0)
        except (ValueError, TypeError):
            horizon = 0
        start = observation_time(row.get("startedAt"))
        observed = observation_time(row.get("observedAt"))
        verified = bool(start and horizon > 0 and observed and observed >= start + timedelta(minutes=horizon))
        # Inferred intervals are diagnostic only. Strict qualification requires
        # the original prediction clock above, never a reconstructed clock.
        if not require_interval and start is None and observed and horizon > 0:
            start = observed - timedelta(minutes=horizon)
        scope = tuple(str(row.get(k) or "") for k in ("accountId", "symbol", "claimFingerprint"))
        identity = str(row.get("claimFingerprint") or row.get("hypothesisTemplateId") or "|".join(row.get("ruleIds") or []))
        key = (scope, identity, horizon, episode)
        row.update(_start=start, _end=observed, _horizon=horizon, _scope=(scope, identity, horizon), _verified=verified)
        prior = latest.get(key)
        if not episode:
            excluded.append({"episodeId": episode, "reason": "missing-episode-identity"})
        elif prior is None or str(row.get("observedAt") or "") > str(prior.get("observedAt") or ""):
            if prior is not None:
                excluded.append({"episodeId": episode, "reason": "superseded-measurement"})
            latest[key] = row
        else:
            excluded.append({"episodeId": episode, "reason": "superseded-measurement"})
    selected, events, ends = [], set(), {}
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    for row in sorted(latest.values(), key=lambda r: (r["_start"] or minimum, str(r.get("episodeId") or r.get("sourceEpisodeId") or ""))):
        scope, start, horizon = row["_scope"], row["_start"], row["_horizon"]
        event = str(row.get("sourceEventId") or row.get("independentEpisodeKey") or row.get("independenceKey") or row.get("episodeId") or row.get("sourceEpisodeId"))
        event_key = (scope, event)
        reason = "duplicate-event" if event_key in events else (
            "overlapping-outcome-window" if start and start < ends.get(scope, minimum) else ""
        )
        if reason:
            excluded.append({"episodeId": row.get("episodeId") or row.get("sourceEpisodeId"), "reason": reason})
            continue
        events.add(event_key)
        if start and horizon > 0:
            # Actual observation intervals can span market closures or delays.
            ends[scope] = max(start + timedelta(minutes=horizon), row["_end"] or minimum)
        if require_interval and not row["_verified"]:
            excluded.append({"episodeId": row.get("episodeId") or row.get("sourceEpisodeId"), "reason": "unverified-observation-interval"})
            continue
        selected.append({key: value for key, value in row.items() if not key.startswith("_")})
    return {"version": OBSERVATION_SELECTION_VERSION, "selected": selected, "excluded": excluded,
            "sourceCount": len(rows), "selectionBasis": "earliest-prediction-per-event-and-nonoverlapping-horizon"}
