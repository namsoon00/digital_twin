"""Delivery value of a proven observation; never choose an investment action."""


VERSION = "rule-notice-delivery-value-v1"


def eligible_actions(snapshot, event=None):
    judgment = (snapshot.get("decisionBrief") or {}).get("judgment") or {}
    if judgment.get("authority") != "typedb-materialized-synthesis" or any(
        not snapshot.get(k) or judgment.get(k) != snapshot[k]
        for k in ("accountId", "symbol", "worldId", "inferenceGenerationId", "sourceAboxSnapshotId")
    ):
        return set()
    rules = set((event or {}).get("sourceRuleIds") or [])
    return {g["action"] for g in judgment.get("candidateGroups") or []
            if g.get("executionEligible") is True and g.get("action")
            and (event is None or rules.intersection(g.get("supportingRuleIds") or []))
            and g["action"] not in (judgment.get("blockedActions") or [])}


def delivered_baseline(current, previous):
    return bool(previous.get("receiptJobId") and previous.get("deliveredAt") and all(
        current.get(k) and current[k] == previous.get(k) for k in ("accountId", "symbol", "worldId")))


def delivered_predecessor(event, current, previous):
    if not delivered_baseline(current, previous):
        return False
    parent = event.get("parentJobId")
    intent = event.get("intent") or {}
    if not intent.get("claimContractId") or not intent.get("claimFingerprint"):
        return False
    entries = list((previous.get("notificationIntentContinuity") or {}).values())
    if parent == previous["receiptJobId"]:
        entries += [{**row, "rootJobId": parent} for row in previous.get("customerEvents") or []]
    return bool(parent and any(
        row.get("rootJobId") == parent and row.get("purpose") == event.get("purpose")
        and (row.get("intent") or {}).get("claimContractId") == intent.get("claimContractId")
        and (row.get("intent") or {}).get("claimFingerprint") == intent.get("claimFingerprint")
        for row in entries))


def delivery_value(event, current, previous):
    if event.get("purpose") != "hypothesis-evidence" or event.get("outcome") or event.get("strategyPhases"):
        return {"version": VERSION, "allowed": True, "reasonCode": "separate-notice-contract"}
    before = eligible_actions(previous) if delivered_baseline(current, previous) else set()
    after = eligible_actions(current)
    received = delivered_predecessor(event, current, previous)
    correction = event.get("changeKind") in {"invalidated", "counter-added", "support-removed"} or any(
        c.get("kind") in {"counter-added", "support-removed"} for c in event.get("evidenceChanges") or [])
    added = eligible_actions(current, event) - before
    removed = eligible_actions(previous, event) - after if received else set()
    changed = bool(added or removed)
    allowed = changed or (received and correction)
    return {"version": VERSION, "allowed": allowed,
            "previousEligibleActions": sorted(before), "currentEligibleActions": sorted(after),
            "addedEligibleActions": sorted(added), "removedEligibleActions": sorted(removed),
            "deliveredPredecessor": received,
            "reasonCode": "eligible-response-changed" if changed else "delivered-premise-correction" if allowed
                          else "hypothesis_evidence_history_only",
            "reason": "검증된 대응 후보가 달라졌습니다." if changed else
                      "앞서 전달한 설명의 반대 근거 또는 해제 조건을 확인했습니다." if allowed else
                      "가설 근거는 달라졌지만 새로 사용할 대응이나 앞선 판단의 철회가 없어 상세 이력에 남깁니다."}
