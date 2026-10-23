"""Production strategy notices from exact native phase proofs and receipts."""
from copy import deepcopy

from digital_twin.modules.model_registry.contracts import rule_notification_intent
from .rule_notification_event import VERSION, fingerprint, rows


PHASE_LABELS = {"setup": "회복 관찰", "entry": "진입 검토", "invalidation": "진입 근거 취소", "exit": "보유 종료 검토"}


def strategy_phase_events(current, previous):
    scope = {k: current.get(k) or "" for k in ("accountId", "symbol", "worldId")}
    if not current.get("proofIdentityVerified") or not all(scope.values()):
        return []
    groups = {}
    native_rules = {r.get("id"): r for r in rows(current.get("rules")) if r.get("nativeProof")}
    for rule in rows(current.get("rules")):
        claim = rule.get("claimContract") or {}
        contract = claim.get("strategyContract") or {}
        phase = claim.get("strategyPhase")
        intent = rule_notification_intent(claim)
        if (contract.get("version") != "rule-strategy-contract-v2" or contract.get("mode") != "live-observation"
                or contract.get("market") != current.get("market") or phase not in PHASE_LABELS
                or not rule.get("nativeProof") or rule.get("matched") is not True
                or not intent or intent.get("ruleId") != rule.get("id") or intent.get("purpose") != "context-evidence"):
            continue
        conditions = rows(rule.get("conditions"))
        proof_rules = {rule["id"]: rule}
        required = {"strategy-market", *["strategy-model-" + str(i + 1) for i in range(len(contract["phases"][phase]))]}
        proved = {c.get("conditionId") for c in conditions if c.get("matchedByTypeDB") is True
                  and c.get("judgementEvidenceUsable") is True
                  and c.get("freshnessStatus") in {"fresh", "live", "current", "available", "not-applicable"}}
        # PortfolioWorld evaluates public predicates through exact shared
        # premise references. Resolve only the linked native trace, never an
        # unrelated matching rule or the disappearance of a prior condition.
        for condition in conditions:
            lineage = condition.get("premiseLineage") or {}
            shared = native_rules.get("shared.premise." + rule["id"]) or {}
            if (condition.get("conditionId") not in proved
                    or condition.get("conditionId") != "shared-market-premise:" + rule["id"]
                    or lineage.get("originalRuleId") != rule["id"]
                    or lineage.get("status") != "available" or not lineage.get("premiseProofId")
                    or shared.get("traceId") != lineage["premiseProofId"] or shared.get("matched") is False):
                continue
            proof_rules[shared["id"]] = shared
            proved.update(c.get("conditionId") for c in rows(shared.get("conditions"))
                          if c.get("matchedByTypeDB") is True and c.get("judgementEvidenceUsable") is True
                          and c.get("freshnessStatus") in {"fresh", "live", "current", "available", "not-applicable"})
        if not required.issubset(proved):
            continue
        key = fingerprint({**scope, "strategyId": contract["strategyId"], "contract": contract["contractFingerprint"]})
        group = groups.setdefault(key, {"contract": contract, "rules": {}, "proofRules": {}, "intent": intent})
        group["rules"][phase] = rule
        group["proofRules"].update(proof_rules)
    history = (previous.get("notificationIntentContinuity") or {}) if (
        previous.get("receiptJobId") and previous.get("deliveredAt")
        and all(previous.get(k) == v for k, v in scope.items())) else {}
    result = []
    for key, group in groups.items():
        phase_rules, contract = group["rules"], group["contract"]
        phases = [p for p in PHASE_LABELS if p in phase_rules]
        old = history.get(key) or {}
        prior = old.get("strategyPhases") or []
        added = [p for p in phases if p not in prior]
        # Missing/stale phase evidence is unknown. Its disappearance alone
        # cannot withdraw a prior cancellation or announce a new entry.
        if not added:
            continue
        signature = fingerprint({"contract": contract["contractFingerprint"], "phases": phases})
        conditions = [{**deepcopy(c), "ruleId": rule["id"]}
                      for rule in group["proofRules"].values() for c in rule["conditions"]]
        next_checks = list(dict.fromkeys(text for rule in phase_rules.values() for text in rule.get("nextChecks") or []))
        result.append({"version": VERSION, **scope,
            "eventId": "rule-strategy:" + fingerprint([key, signature])[:24], "intentKey": key,
            "intent": deepcopy(group["intent"]), "purpose": "context-evidence", "changeKind": "observed",
            "strategyId": contract["strategyId"], "strategyLabel": contract["label"],
            "strategyPhases": phases, "previousStrategyPhases": prior, "addedStrategyPhases": added,
            "phaseRules": {p: phase_rules[p]["id"] for p in phases},
            "phaseModelClaims": {p: [contract["models"][rid]["claimContract"]["statement"]
                                    for rid in contract["phases"][p]] for p in phases},
            "inferenceGenerationId": current.get("inferenceGenerationId"), "sourceAboxSnapshotId": current.get("sourceAboxSnapshotId"),
            "transitionId": "", "lifecycleKey": "", "observedAt": current.get("observedAt"), "hypothesis": {},
            "sourceRuleIds": list(group["proofRules"]), "conditions": conditions,
            "signalValiditySeconds": contract["signalValiditySeconds"],
            "tradeTimingQualified": False, "orderAuthorized": False,
            "conflictingConditions": "entry" in phases and bool(set(phases) & {"invalidation", "exit"}),
            "nextCheck": {"condition": " ".join(next_checks), "nextChecks": next_checks,
                          "conditions": conditions, "trackingStatus": "strategy-phase-reevaluation", "tracking": []},
            "parentJobId": old.get("rootJobId") or "", "fingerprint": signature,
            "eligible": True, "reasonCode": "verified-strategy-phase-change"})
    return result


def strategy_summary(packet, market_text, measurements):
    events = [e for e in packet.get("customerEvents") or [] if e.get("strategyPhases")]
    if not events:
        return None
    event = events[0]
    labels = " · ".join(PHASE_LABELS[p] for p in event["addedStrategyPhases"])
    lead = ("진입 조건과 취소·종료 조건이 동시에 확인됐습니다." if event["conflictingConditions"]
            else labels + " 조건이 새로 확인됐습니다.")
    purpose = "이전 알림: " + (" · ".join(PHASE_LABELS[p] for p in event["previousStrategyPhases"]) or "이 전략의 발송 이력 없음")
    purpose += " → 이번 확인: " + " · ".join(PHASE_LABELS[p] for p in event["strategyPhases"])
    claims = []
    for p in event["addedStrategyPhases"]:
        statements = event["phaseModelClaims"][p]
        claims.append(PHASE_LABELS[p] + ": " + " / ".join(s.split(":", 1)[0].replace("->", "→") for s in statements))
    brief = ((packet.get("current") or {}).get("decisionBrief") or {})
    policies = ((brief.get("judgment") or {}).get("managementDecision") or {}).get("responses") or []
    together = " ".join(r.get("label", "") for r in policies[:2])
    if event["conflictingConditions"]:
        together = "진입 근거와 반대 근거가 함께 성립한 상태입니다. 한쪽 방향으로 확정된 판단은 없습니다. " + together
    together += " 매매 성과 검증 중이며 자동 주문은 실행하지 않습니다."
    return {"version": "strategy-phase-notice-v1", "lead": lead,
            "sections": [("notification-purpose", "왜 왔나요", [purpose]),
                         ("decision-basis", "확인된 조건", claims),
                         ("decision-impact", "근거 수치", measurements.splitlines()[:4]),
                         ("current-snapshot", "현재", [market_text]),
                         ("next-check-contract", "다음 판단", [event["nextCheck"]["condition"], together.strip()])]}
