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
    successors = {(r.get("claimContract") or {}).get("strategyContract", {}).get("predecessorContractFingerprint")
                  for r in rows(current.get("rules"))
                  if (r.get("claimContract") or {}).get("strategyContract", {}).get("version") == "rule-strategy-contract-v3"}
    native_rules = {r.get("id"): r for r in rows(current.get("rules")) if r.get("nativeProof")}
    for rule in rows(current.get("rules")):
        claim = rule.get("claimContract") or {}
        contract = claim.get("strategyContract") or {}
        phase = claim.get("strategyPhase")
        intent = rule_notification_intent(claim)
        if (contract.get("version") not in {"rule-strategy-contract-v2", "rule-strategy-contract-v3"}
                or contract.get("contractFingerprint") in successors or contract.get("mode") != "live-observation"
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
    saved_scenarios = ((current.get("decisionBrief") or {}).get("judgment") or {}).get("strategyScenarios") or []
    for key, group in groups.items():
        phase_rules, contract = group["rules"], group["contract"]
        phases = [p for p in PHASE_LABELS if p in phase_rules]
        old = history.get(key) or {}
        prior = old.get("strategyPhases") or []
        added = [p for p in phases if p not in prior]
        # Missing/stale phase evidence is unknown. Its disappearance alone
        # cannot withdraw a prior cancellation or announce a new entry.
        scenario = None
        if contract["version"] == "rule-strategy-contract-v3":
            scenario = next((s for s in saved_scenarios if s.get("version") == "strategy-scenario-observation-v1"
                and s.get("contractFingerprint") == contract["contractFingerprint"]
                and s.get("status") == "accepted" and s.get("notifyEligible") is True
                and all(s.get(k) == current.get(k) for k in (*scope, "inferenceGenerationId", "sourceAboxSnapshotId"))
                and set(s.get("confirmedPhases") or []) == set(phases)), None)
            # The successor never falls back to delivery-derived sequencing.
            if not scenario or not (scenario.get("transition") or {}).get("transitionId"):
                continue
            added = phases
        if not added:
            continue
        signature = (fingerprint(scenario["transition"]["transitionId"]) if scenario else
                     fingerprint({"contract": contract["contractFingerprint"], "phases": phases}))
        if scenario and old.get("fingerprint") == signature:
            continue
        conditions = [{**deepcopy(c), "ruleId": rule["id"]}
                      for rule in group["proofRules"].values() for c in rule["conditions"]]
        next_checks = list(dict.fromkeys(text for rule in phase_rules.values() for text in rule.get("nextChecks") or []))
        event = {"version": VERSION, **scope,
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
            "eligible": True, "reasonCode": "verified-strategy-phase-change"}
        if scenario:
            event.update(scenarioTransition=deepcopy(scenario["transition"]),
                scenarioObservationId=scenario["observationId"], scenarioKey=scenario["scenarioKey"],
                scenarioState=scenario["state"], contractFingerprint=contract["contractFingerprint"],
                observationGapSeconds=scenario.get("observationGapSeconds", 0),
                reasonCode="verified-strategy-scenario-change")
        result.append(event)
    return result


def strategy_summary(packet, market_text, measurements):
    events = [e for e in packet.get("customerEvents") or [] if e.get("strategyPhases")]
    if not events:
        return None
    if len(events) > 1:
        return multiple_strategy_summary(events, packet, market_text, measurements)
    event = events[0]
    if event.get("scenarioTransition"):
        return scenario_summary(event, packet, market_text, measurements)
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


def scenario_summary(event, packet, market_text, measurements):
    change = event["scenarioTransition"]
    sequence = change["previousLabel"] + " → " + change["currentLabel"]
    if event.get("observationGapSeconds", 0) > event["signalValiditySeconds"]:
        sequence += ". 확인 사이에 공백이 있어 조건이 계속 유지됐는지는 알 수 없습니다."
    brief = ((packet.get("current") or {}).get("decisionBrief") or {})
    policies = ((brief.get("judgment") or {}).get("managementDecision") or {}).get("responses") or []
    boundary = " ".join(r.get("label", "") for r in policies[:2])
    if change["to"] == "reentry-review":
        next_check = "이전 취소·종료 조건의 해제와 현재 회복 조건의 유지를 함께 확인해야 합니다."
    else:
        next_check = event["nextCheck"]["condition"]
    claims = list(dict.fromkeys(s.split(":", 1)[0].replace("->", "→")
        for phase in event["strategyPhases"] for s in event["phaseModelClaims"][phase]))
    return {"version": "strategy-scenario-notice-v1", "title": change["currentLabel"], "lead": change["meaning"],
        "sections": [("notification-purpose", "어떤 변화인가요", [sequence]),
                     ("decision-basis", "함께 확인한 조건", claims[:3]),
                     ("decision-impact", "확인된 수치", measurements.splitlines()[:3]),
                     ("current-snapshot", "현재", [market_text]),
                     ("next-check-contract", "다음 판단", [next_check,
                        (boundary + " 조건의 연결을 확인한 것이며 매매 성과는 검증 중입니다.").strip()])]}


def multiple_strategy_summary(events, packet, market_text, measurements):
    """Present each native strategy change without choosing an investment side."""
    changes, meanings, checks = [], [], []
    for event in sorted(events, key=lambda e: (str(e.get("strategyId")), str(e.get("eventId")))):
        label = event.get("strategyLabel") or "전략 조건"
        transition = event.get("scenarioTransition") or {}
        if transition:
            sequence = transition["previousLabel"] + " → " + transition["currentLabel"]
            meaning = transition["meaning"]
            if event.get("observationGapSeconds", 0) > event["signalValiditySeconds"]:
                sequence += " (관찰 공백 있음)"
        else:
            sequence = (" · ".join(PHASE_LABELS[p] for p in event["previousStrategyPhases"]) or "이전 발송 없음")
            sequence += " → " + " · ".join(PHASE_LABELS[p] for p in event["strategyPhases"])
            meaning = ("진입 조건과 취소·종료 조건이 함께 확인됐습니다." if event["conflictingConditions"] else
                       " / ".join(dict.fromkeys(s for p in event["addedStrategyPhases"] for s in event["phaseModelClaims"][p])))
        changes.append(label + ": " + sequence)
        meanings.append(label + ": " + meaning)
        check = ("이전 취소·종료 조건의 해제와 현재 조건의 유지를 함께 확인해야 합니다."
                 if transition.get("to") == "reentry-review" else event["nextCheck"]["condition"])
        checks.append(label + ": " + check)
    brief = (packet.get("current") or {}).get("decisionBrief") or {}
    policies = ((brief.get("judgment") or {}).get("managementDecision") or {}).get("responses") or []
    boundary = " ".join(r.get("label", "") for r in policies[:2])
    # Four strategies are bounded in the compact notice; every event remains
    # explicit in the count and the immutable detail/receipt packet.
    overflow = (["추가 " + str(len(events) - 4) + "개 전략 변화는 전체 판단 근거에서 확인할 수 있습니다."]
                if len(events) > 4 else [])
    return {"version": "multiple-strategy-notice-v1", "title": "여러 전략의 조건 변화",
        "lead": str(len(events)) + "개 전략에서 조건 변화가 확인됐습니다. 전략별 확인 내용과 다음 조건을 함께 봅니다.",
        "sections": [("notification-purpose", "어떤 변화인가요", changes[:4] + overflow),
                     ("decision-basis", "확인된 의미", meanings[:4]),
                     ("decision-impact", "확인된 수치", measurements.splitlines()[:4]),
                     ("current-snapshot", "현재", [market_text]),
                     ("next-check-contract", "다음 판단", checks[:4] +
                         [(boundary + " 같은 자료를 공유하는 전략은 독립된 근거 수로 세지 않습니다. 매매 성과는 검증 중입니다.").strip()])]}
