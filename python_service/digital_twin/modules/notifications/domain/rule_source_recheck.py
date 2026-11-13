"""Track new-source reevaluation without resending or rewriting old evidence."""

from .rule_dispatch_validity import rule_dispatch_validity, timestamp


VERSION = "rule-source-recheck-v1"
RECHECK_REASONS = {"rule_evidence_expired_at_dispatch", "rule_evidence_unavailable_at_dispatch"}
SCOPE = ("accountId", "symbol", "worldId")


def claim_keys(events):
    return sorted({(e.get("purpose"), e["intent"]["claimContractId"], e["intent"]["claimFingerprint"])
                   for e in events if e.get("eligible") and e.get("purpose")
                   and (e.get("intent") or {}).get("claimContractId")
                   and (e.get("intent") or {}).get("claimFingerprint")})


def source_clocks(snapshot, rule_ids):
    clocks = {}
    for rule in snapshot.get("rules") or []:
        if rule.get("id") not in rule_ids:
            continue
        for condition in rule.get("conditions") or []:
            if condition.get("kind") == "any-condition-group" or condition.get("freshnessRequired") is False:
                continue
            if condition.get("freshnessStatus") == "not-applicable":
                continue
            clock = timestamp(condition.get("observedAt"))
            key = (rule["id"], condition.get("conditionId") or condition.get("field"), condition.get("source"))
            if clock and key[1] and key[2]:
                clocks[key] = min(clocks.get(key, clock), clock)
    return [{"key": list(key), "observedAt": value.isoformat()} for key, value in sorted(clocks.items())]


def recheck_request(packet, reason, source_id, validity=None):
    current = packet.get("current") or {}
    events = packet.get("customerEvents") or []
    rules = sorted({rid for e in events if e.get("eligible") and not e.get("outcome") for rid in e.get("sourceRuleIds") or []})
    clocks = source_clocks(current, rules)
    if reason not in RECHECK_REASONS or not current.get("proofIdentityVerified") or not clocks or not all(current.get(k) for k in SCOPE):
        return {}
    failed = {tuple((r.get("ruleId"), r.get("conditionId") or r.get("field"), r.get("source")))
              for r in (validity or rule_dispatch_validity(packet)).get("checks") or []
              if r.get("status") in {"expired", "unusable"}}
    for row in clocks:
        row["refreshRequired"] = tuple(row["key"]) in failed
    if not any(row["refreshRequired"] for row in clocks):
        return {}
    return {"version": VERSION, "sourceJobId": source_id, **{k: current[k] for k in SCOPE},
            "sourceAboxSnapshotId": current.get("sourceAboxSnapshotId"),
            "inferenceGenerationId": current.get("inferenceGenerationId"),
            "sourceRuleIds": rules, "sourceClocks": clocks, "claimKeys": claim_keys(events), "reasonCode": reason}


def reevaluation(request, current, outcome, now=None):
    """Rejected premises must actually advance; still-valid facts may stay the same.

    A changed fetch/generation alone is insufficient. Missing or no-longer-matched
    proof remains unresolved, rather than claiming the old notice recovered.
    """
    if not current.get("proofIdentityVerified") or any(current.get(k) != request.get(k) for k in SCOPE):
        return {}
    if any(not current.get(k) or current[k] == request.get(k) for k in ("inferenceGenerationId", "sourceAboxSnapshotId")):
        return {}
    rule_ids = request.get("sourceRuleIds") or []
    clocks = {tuple(r["key"]): timestamp(r["observedAt"]) for r in source_clocks(current, rule_ids)}
    old = request.get("sourceClocks") or []
    if not old or any(tuple(r["key"]) not in clocks or clocks[tuple(r["key"])] < timestamp(r["observedAt"])
                     or (r.get("refreshRequired", True) and clocks[tuple(r["key"])] == timestamp(r["observedAt"])) for r in old):
        return {}
    proof = rule_dispatch_validity({"current": current, "customerEvents": [{"eligible": True, "sourceRuleIds": rule_ids}]}, now=now)
    if not proof["allowed"] or not outcome.get("subjectCaseId"):
        return {}
    same_claims = bool(request.get("claimKeys")) and set(map(tuple, request["claimKeys"])).issubset(
        claim_keys(current.get("customerEvents") or []))
    job_id = outcome.get("notificationJobId") if outcome.get("queued") and same_claims else ""
    return {"status": "awaiting-delivery" if job_id else "reevaluated-no-notice",
            **{k: current[k] for k in SCOPE}, "claimKeys": request.get("claimKeys") or [],
            "subjectCaseId": outcome["subjectCaseId"], "successorJobId": job_id or "",
            "inferenceGenerationId": current["inferenceGenerationId"], "sourceAboxSnapshotId": current["sourceAboxSnapshotId"],
            "sourceClocks": source_clocks(current, rule_ids), "reasonCode": outcome.get("reasonCode") or "",
            "receiptVerified": False}


def receipt_matches(result, snapshot):
    """The delivered packet itself must contain the same claim, not just its job."""
    return bool(result.get("claimKeys") and all(result.get(k) and snapshot.get(k) == result[k]
        for k in (*SCOPE, "inferenceGenerationId", "sourceAboxSnapshotId"))
        and set(map(tuple, result["claimKeys"])).issubset(claim_keys(snapshot.get("customerEvents") or [])))
