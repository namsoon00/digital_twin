"""Durable delivery transitions for source-proven price movements."""
from copy import deepcopy
import hashlib
import json

from .price_observation_windows import STATE_KEY as PATHS, advance_paths, price_windows, positions, finite
from .market_time_series import parse_timestamp

VERSION = "price-movement-episode-v1"
STATE_KEY = "marketPriceEpisodes"


def enabled(settings):
    return str((settings or {}).get("marketObservationSmartEnabled", "0")).lower() in {"1", "true", "yes"}


def policy(settings):
    settings = settings or {}
    def value(key, default, low, high):
        parsed = finite(settings.get(key))
        return max(low, min(high, parsed if parsed is not None else default))
    return {"version": VERSION,
        "thresholds": {str(m): value("marketObservation" + str(m) + "mChangePct", d, .2, 30)
                       for m, d in ((3, 1.5), (15, 2.5), (60, 4.0))},
        "watchlistMultiplier": value("marketObservationWatchlistMultiplier", 1.5, 1, 3),
        "expansionStepPct": value("marketObservationExpansionStepPct", 1.5, .5, 10),
        "recoveryHoldSeconds": 120, "recoveryFraction": .5,
        "transitionFloorSeconds": 60}


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def advance_episodes(current, previous, settings):
    if current.get("accountId") != previous.get("accountId"):
        previous = {}
    paths = advance_paths(current, previous)
    prior = (previous.get("metadata") or {}).get(STATE_KEY) or {}
    states, proposals = {}, {}
    configured = policy(settings)
    for symbol, (_item, role) in positions(current).items():
        path = paths.get(symbol) or {}
        old = deepcopy(prior.get(symbol) or {})
        states[symbol] = old
        if path.get("status") != "available":
            continue
        point = path["points"][-1]
        windows = price_windows(path)
        multiplier = configured["watchlistMultiplier"] if role == "watchlist" else 1
        candidates = []
        for window in windows:
            threshold = configured["thresholds"][str(window["windowMinutes"])] * multiplier
            # A 4-minute observation must not pass a 3-minute speed threshold unchanged.
            threshold *= window["elapsedSeconds"] / (window["windowMinutes"] * 60)
            if abs(window["changePct"]) + 1e-9 >= threshold:
                candidates.append({**window, "thresholdPct": threshold})
        compatible = (old.get("version") == VERSION and old.get("scope") == path.get("scope")
                      and old.get("role") == role and old.get("policy") == configured)
        if not compatible:
            old = {}
        # Closed episodes cannot be reopened from the same old rolling window.
        if old.get("lastQueuedStage") == "recovery":
            candidates = [w for w in candidates if w["startAt"] > old.get("lastQueuedAt", "")]
            if not candidates:
                states[symbol] = old
                continue
            old = {}
        if old and (parse_timestamp(point["at"]) - parse_timestamp(old["lastObservedAt"])).total_seconds() > 180:
            old = {}  # No recovery claim across an outage.
        reversal_of = ""
        if old and old.get("lastQueuedSequence"):
            sign = 1 if old["direction"] == "up" else -1
            crossed = sign * (point["price"] / old["anchorPrice"] - 1) * 100
            opposite = [w for w in candidates if sign * w["changePct"] < 0]
            if opposite and crossed <= -configured["thresholds"]["3"] * multiplier:
                # A verified move through the original start must not wait for
                # recovery confirmation or the ordinary initial-alert cooldown.
                reversal_of, candidates = old["episodeId"], opposite
                old = {}
        if not old:
            if not candidates:
                states[symbol] = {}
                continue
            trigger = candidates[0]
            old = {"version": VERSION, "episodeId": identity([current.get("accountId"), symbol, path["scope"],
                trigger["startAt"], point["at"], trigger["changePct"]]), "scope": path["scope"], "role": role,
                "policy": configured, "trigger": trigger, "direction": "up" if trigger["changePct"] > 0 else "down",
                "anchorPrice": trigger["startPrice"], "anchorAt": trigger["startAt"], "openedAt": point["at"],
                "peakMagnitude": abs(trigger["changePct"]), "lastQueuedSequence": 0, "lastQueuedLevel": 0,
                "lastQueuedStage": "", "lastQueuedAt": "", "reversalOf": reversal_of}
        direction = 1 if old["direction"] == "up" else -1
        change = (point["price"] / old["anchorPrice"] - 1) * 100
        magnitude = direction * change
        old["peakMagnitude"] = max(old["peakMagnitude"], magnitude)
        old["lastObservedAt"] = point["at"]
        old["lastPrice"] = point["price"]
        recovering = magnitude <= old["peakMagnitude"] * (1 - configured["recoveryFraction"])
        if recovering:
            old.setdefault("recoverySince", point["at"])
        else:
            old.pop("recoverySince", None)
        step = configured["expansionStepPct"] * multiplier
        level = 1 + max(0, int((magnitude - abs(old["trigger"]["changePct"]) + 1e-9) / step))
        recovered = recovering and (parse_timestamp(point["at"]) - parse_timestamp(old["recoverySince"])).total_seconds() >= configured["recoveryHoldSeconds"]
        stage = (("reversed" if old.get("reversalOf") else "initial") if not old["lastQueuedSequence"] and not recovering else
                 "recovery" if recovered and old["lastQueuedSequence"] else
                 "expanded" if old["lastQueuedSequence"] and level > old["lastQueuedLevel"] else "")
        # Recovery before any queued alert closes silently; it is not a customer event.
        if recovered and not old["lastQueuedSequence"]:
            old.update(lastQueuedStage="recovery", lastQueuedAt=point["at"])
        states[symbol] = old
        if not stage:
            continue
        last_at = parse_timestamp(old.get("lastQueuedAt"))
        if last_at and (parse_timestamp(point["at"]) - last_at).total_seconds() < configured["transitionFloorSeconds"]:
            continue
        proposals[symbol] = {"version": VERSION, "episodeId": old["episodeId"], "stage": stage,
            "reversalOf": old.get("reversalOf", ""),
            "basisEpisodeId": (prior.get(symbol) or {}).get("episodeId", ""),
            "basisSequence": (prior.get(symbol) or {}).get("lastQueuedSequence", 0),
            "expectedSequence": old["lastQueuedSequence"], "sequence": old["lastQueuedSequence"] + 1,
            "level": level, "role": role, "direction": old["direction"], "observedAt": point["at"],
            "anchorAt": old["anchorAt"], "anchorPrice": old["anchorPrice"], "currentPrice": point["price"],
            "changePct": change, "peakMagnitude": old["peakMagnitude"], "policy": configured,
            "scope": deepcopy(path["scope"]), "trigger": deepcopy(old["trigger"]), "windows": windows,
            "recoverySince": old.get("recoverySince"), "authority": "price-observation-only"}
    return paths, states, proposals


def persist_price_state(current, previous, settings):
    from .price_observation_context import advance_price_context, STATE_KEY as CONTEXT
    from .price_observation_diagnostics import record_observations
    paths, states, _ = advance_episodes(current, previous, settings)
    context, _ = advance_price_context(current, previous, paths)
    metadata = {**current.get("metadata", {}), PATHS: paths, CONTEXT: context}
    if enabled(settings):
        metadata[STATE_KEY] = states
    return record_observations({**current, "metadata": metadata}, previous, paths)


def admit_transition(event, previous):
    transition = (event.metadata or {}).get("priceMovement") or {}
    if transition.get("version") != VERSION:
        return None
    old = ((previous.get("metadata") or {}).get(STATE_KEY) or {}).get(event.symbol) or {}
    sequence = old.get("lastQueuedSequence", 0) if old.get("episodeId") == transition.get("episodeId") else 0
    accepted = (sequence == transition.get("expectedSequence") and transition.get("sequence") == sequence + 1)
    if old.get("episodeId", "") != transition.get("basisEpisodeId") or old.get("lastQueuedSequence", 0) != transition.get("basisSequence"):
        accepted = False
    if old.get("episodeId") and old.get("episodeId") != transition.get("episodeId") and transition.get("stage") not in {"initial", "reversed"}:
        accepted = False
    if old.get("lastObservedAt", "") > transition.get("observedAt", ""):
        accepted = False
    # Another worker can persist a newer quiet quote without opening an episode.
    # Its path still invalidates an older initial candidate.
    path = ((previous.get("metadata") or {}).get(PATHS) or {}).get(event.symbol) or {}
    points = path.get("points") or []
    if points and points[-1]["at"] > transition.get("observedAt", ""):
        accepted = False
    return {"accepted": accepted, "symbol": event.symbol,
            "reasonCode": "price-movement-transition" if accepted else "stale-price-movement-transition"}


def acknowledge_transitions(state, events):
    result = deepcopy(state)
    episodes = (result.get("metadata") or {}).get(STATE_KEY) or {}
    for event in events:
        transition = (event.metadata or {}).get("priceMovement") or {}
        old = episodes.get(event.symbol) or {}
        if transition.get("version") == VERSION and old.get("episodeId") == transition.get("episodeId"):
            old.update(lastQueuedSequence=transition["sequence"], lastQueuedStage=transition["stage"],
                       lastQueuedLevel=transition["level"], lastQueuedAt=transition["observedAt"])
    return result
