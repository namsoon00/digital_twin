"""Project authored scenario transitions from already proven native phases.

No price predicate, score, action selection or delivery receipt belongs here.
Missing observations never negate a condition. Source clocks order history.
"""
from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import json


VERSION = "strategy-scenario-observation-v1"
SCOPE = ("deploymentId", "accountId", "symbol", "worldId", "contractFingerprint")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def observation_input(contract, identity, native, assessed_at):
    """Validate bounded proof provenance; reject unknown/future source clocks."""
    if (not all(identity.get(k) for k in SCOPE[:-1])
            or not all(identity.get(k) for k in ("inferenceGenerationId", "sourceAboxSnapshotId"))
            or native.get("contractFingerprint") != contract["contractFingerprint"]
            or native.get("strategyId") != contract["strategyId"]):
        return None
    phases = sorted(set(native.get("confirmedPhases") or []))
    if not phases or not set(phases).issubset(contract["phases"]):
        return None
    cutoff = timestamp(assessed_at)
    sources, rules = {}, []
    proofs = {p["phase"]: p for p in native.get("phaseProofs") or []}
    for phase in phases:
        proof = proofs.get(phase) or {}
        expected_id = "graph.strategy." + contract["contractFingerprint"][:24] + "." + phase + ".v3"
        if (proof.get("ruleId") != expected_id
                or proof.get("sourceModelRuleIds") != contract["phases"][phase]):
            return None
        conditions = {c.get("conditionId"): c for c in proof.get("conditions") or []}
        for i, rid in enumerate(contract["phases"][phase], 1):
            condition = conditions.get("strategy-model-" + str(i)) or {}
            # observedAt is the native condition's source clock, not the
            # feature snapshot's calculation / ingestion timestamp.
            source = timestamp(condition.get("observedAt"))
            if (not cutoff or not source or not 0 <= (cutoff - source).total_seconds() <= contract["signalValiditySeconds"]
                    or condition.get("matchedByTypeDB") is not True
                    or condition.get("judgementEvidenceUsable") is not True
                    or condition.get("freshnessStatus") not in {"fresh", "live", "current", "available"}):
                return None
            sources[rid] = source.isoformat()
        rules.append(expected_id)
    clocks = [timestamp(v) for v in sources.values()]
    zone = ZoneInfo("Asia/Seoul" if contract["market"] == "KR" else "America/New_York")
    sessions = {t.astimezone(zone).date().isoformat() for t in clocks}
    if len(sessions) != 1:
        return None
    scope = {k: identity[k] for k in SCOPE[:-1]}
    scope["contractFingerprint"] = contract["contractFingerprint"]
    key = "scenario:" + digest(scope)
    phases_set = set(phases)
    signals = phases_set | ({"conflict"} if "entry" in phases_set and phases_set & {"exit", "invalidation"} else set())
    signal = next(s for s in contract["scenarioContract"]["signalPriority"] if s in signals)
    value = {"version": VERSION, **scope, "scenarioKey": key, "strategyId": contract["strategyId"],
        "inferenceGenerationId": identity["inferenceGenerationId"], "sourceAboxSnapshotId": identity["sourceAboxSnapshotId"],
        # Every premise was checked for freshness above. The newest premise
        # dates the combined observation; the oldest remains explicit.
        "assessedAt": cutoff.isoformat(), "sourceObservedAt": max(clocks).isoformat(),
        "oldestSourceObservedAt": min(clocks).isoformat(),
        "session": next(iter(sessions)), "sourceClocks": sources, "confirmedPhases": phases,
        "sourceRuleIds": rules, "signal": signal, "orderAuthorized": False, "tradeTimingQualified": False}
    value["observationId"] = "scenario-observation:" + digest(value)
    return value


def project_scenario(contract, observation, previous=None):
    """Return an immutable observation and (only when accepted) a new head."""
    previous = deepcopy(previous or {})
    if previous and any(previous.get(k) != observation.get(k) for k in SCOPE):
        raise ValueError("scenario scope mismatch")
    value = deepcopy(observation)
    value.update(status="accepted", previousObservationId=previous.get("observationId", ""),
                 historyBeforeSession=previous.get("historyBeforeSession", ""))
    source = timestamp(value["sourceObservedAt"])
    prior_source = timestamp(previous.get("sourceObservedAt"))
    reason = ""
    if prior_source and (source < prior_source or any(
            timestamp(value["sourceClocks"][rid]) < timestamp(stamp)
            for rid, stamp in previous.get("sourceClocks", {}).items() if rid in value["sourceClocks"])):
        reason = "out-of-order-source"
    elif prior_source == source and value["confirmedPhases"] != previous.get("confirmedPhases"):
        reason = "same-source-different-phases"
    if reason:
        # An older replay must never receive a future head's conclusion.
        value.update(status=reason, state="unobserved", previousObservationId="", historyBeforeSession="",
                     transition=None, notifyEligible=False)
        return value, None
    same_session = previous.get("session") == value["session"]
    before = previous.get("state", "unobserved") if same_session else "unobserved"
    if previous and not same_session:
        value["historyBeforeSession"] = previous["observationId"]
    transition = next(t for t in contract["scenarioContract"]["transitions"]
                      if t["from"] == before and t["signal"] == value["signal"])
    value.update(state=transition["to"], previousState=before,
                 stateLabel=contract["scenarioContract"]["states"][transition["to"]],
                 observationGapSeconds=max(0, (source - prior_source).total_seconds()) if prior_source else 0,
                 previousSourceObservedAt=previous.get("sourceObservedAt", ""))
    if transition["kind"] != "maintained":
        change = {**deepcopy(transition), "sourceObservedAt": value["sourceObservedAt"],
                  "previousSourceObservedAt": previous.get("sourceObservedAt", "") if same_session else "",
                  "previousObservationId": previous.get("observationId", "") if same_session else "",
                  "sourceRuleIds": list(value["sourceRuleIds"]), "confirmedPhases": list(value["confirmedPhases"]),
                  "previousLabel": contract["scenarioContract"]["states"][before], "currentLabel": value["stateLabel"]}
        change["transitionId"] = "scenario-transition:" + digest([value["scenarioKey"], value["observationId"], change])
    else:
        change = previous.get("transition")
    value["transition"] = deepcopy(change)
    value["notifyEligible"] = bool(change and change["signal"] == value["signal"] and change["to"] == value["state"])
    # This describes observed evidence, not a verified release of old negatives.
    value["endingConditionsResolved"] = False
    return value, deepcopy(value)
