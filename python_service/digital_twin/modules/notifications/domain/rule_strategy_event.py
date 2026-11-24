"""Production strategy notices from exact native phase proofs and receipts."""
from digital_twin.modules.reasoning.contracts import rule_notice_execution_proven, condition_execution_proven

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
    native_rules = {r.get("id"): r for r in rows(current.get("rules")) if rule_notice_execution_proven(r)}
    for rule in rows(current.get("rules")):
        claim = rule.get("claimContract") or {}
        contract = claim.get("strategyContract") or {}
        phase = claim.get("strategyPhase")
        intent = rule_notification_intent(claim)
        if (contract.get("version") not in {"rule-strategy-contract-v2", "rule-strategy-contract-v3"}
                or contract.get("contractFingerprint") in successors or contract.get("mode") != "live-observation"
                or contract.get("market") != current.get("market") or phase not in PHASE_LABELS
                or not rule_notice_execution_proven(rule) or rule.get("matched") is not True
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
        return multiple_strategy_summary(events, market_text, measurements)
    event = events[0]
    if event.get("scenarioTransition"):
        return scenario_summary(event, market_text, measurements)
    labels = " · ".join(PHASE_LABELS[p] for p in event["addedStrategyPhases"])
    lead = _strategy_judgment(event)
    purpose = "이전 알림: " + (" · ".join(PHASE_LABELS[p] for p in event["previousStrategyPhases"]) or "이 전략의 발송 이력 없음")
    purpose += " → 이번 확인: " + " · ".join(PHASE_LABELS[p] for p in event["strategyPhases"])
    return {"version": "strategy-phase-notice-v1", "lead": lead,
            "sections": [("notification-purpose", "전략 변화", [purpose]),
                         ("decision-basis", "확인된 조건", [labels + " 조건이 새로 확인됐습니다."]),
                         ("decision-impact", "근거 수치", measurements.splitlines()[:3]),
                         ("current-snapshot", "현재", [market_text]),
                         ("next-check-contract", "다음 확인", [event["nextCheck"]["condition"]])]}


def _strategy_judgment(event):
    transition = event.get("scenarioTransition") or {}
    phases = set(event.get("addedStrategyPhases") or [transition.get("signal")])
    if event.get("conflictingConditions"):
        return "진입과 취소 조건이 함께 확인돼 매매 판단을 보류합니다."
    if transition.get("to") == "reentry-review":
        return "이전 취소 조건이 풀렸는지 확인한 뒤 재진입을 검토할 단계입니다."
    if "exit" in phases:
        return "보유분을 줄이거나 정리할지 검토할 단계입니다."
    if "invalidation" in phases:
        return "진입 검토를 중단하고 조건이 회복되는지 살펴봅니다."
    if "entry" in phases:
        return "진입을 검토할 단계입니다. 매매 시점과 수량은 아직 정해지지 않았습니다."
    return "회복 흐름이 이어지는지 지켜볼 단계입니다."


def scenario_summary(event, market_text, measurements):
    change = event["scenarioTransition"]
    sequence = change["previousLabel"] + " → " + change["currentLabel"]
    if event.get("observationGapSeconds", 0) > event["signalValiditySeconds"]:
        sequence += ". 확인 사이에 공백이 있어 조건이 계속 유지됐는지는 알 수 없습니다."
    if change["to"] == "reentry-review":
        next_check = "이전 취소·종료 조건의 해제와 현재 회복 조건의 유지를 함께 확인해야 합니다."
    else:
        next_check = event["nextCheck"]["condition"]
    return {"version": "strategy-scenario-notice-v1", "title": change["currentLabel"], "lead": _strategy_judgment(event),
        "sections": [("notification-purpose", "전략 변화", [sequence]),
                     ("decision-basis", "확인된 의미", [change["meaning"]]),
                     ("decision-impact", "확인된 수치", measurements.splitlines()[:3]),
                     ("current-snapshot", "현재", [market_text]),
                     ("next-check-contract", "다음 확인", [next_check])]}


def multiple_strategy_summary(events, market_text, measurements):
    """Present each native strategy change without choosing an investment side."""
    changes, meanings, checks = [], [], []
    for event in sorted(events, key=lambda e: (str(e.get("strategyId")), str(e.get("eventId")))):
        label = event.get("strategyLabel") or "전략 조건"
        transition = event.get("scenarioTransition") or {}
        if transition:
            sequence = transition["previousLabel"] + " → " + transition["currentLabel"]
            if event.get("observationGapSeconds", 0) > event["signalValiditySeconds"]:
                sequence += " (관찰 공백 있음)"
        else:
            sequence = (" · ".join(PHASE_LABELS[p] for p in event["previousStrategyPhases"]) or "이전 발송 없음")
            sequence += " → " + " · ".join(PHASE_LABELS[p] for p in event["strategyPhases"])
        changes.append(label + " · " + sequence)
        meanings.append(label + " · " + _strategy_judgment(event))
        check = ("이전 취소·종료 조건의 해제와 현재 조건의 유지를 함께 확인해야 합니다."
                 if transition.get("to") == "reentry-review" else event["nextCheck"]["condition"])
        checks.append(label + " · " + check)
    # Four strategies are bounded in the compact notice; every event remains
    # explicit in the count and the immutable detail/receipt packet.
    overflow = (["추가 " + str(len(events) - 4) + "개 전략 변화는 전체 판단 근거에서 확인할 수 있습니다."]
                if len(events) > 4 else [])
    return {"version": "multiple-strategy-notice-v1", "title": "여러 전략의 조건 변화",
        "lead": "전략마다 다른 조건이 확인됐습니다. 각 전략의 검토 단계부터 확인하세요.",
        "sections": [("notification-purpose", "어떤 변화인가요", changes[:4] + overflow),
                     ("decision-basis", "확인된 의미", meanings[:4]),
                     ("decision-impact", "확인된 수치", measurements.splitlines()[:4]),
                     ("current-snapshot", "현재", [market_text]),
                     ("next-check-contract", "다음 확인", checks[:4])]}
