"""A changed-rule view of proven observations, without a new trading judgment.

Only saved execution witnesses supply names, numbers and recheck predicates.
Policy/action changes and incomplete witnesses retain their existing renderer.
"""

from digital_twin.modules.reasoning.contracts import rule_notice_execution_proven
from .alert_formatting import price_money
from .composed_rule_presentation import MEASURES, _context, _number, saved_composition, selected_measurements
from .observation_market_snapshot import decimal, numeric, snapshot_facts
from .relation_change_presentation import OPERATORS
from .relation_notice_focus import VERSION as FOCUS_VERSION, changed_rule_rows, notice_rule_ids
from .relation_observation_language import hypothesis_words
from .rule_event_presentation import ACTION_LABELS, event_impact, scoped_response_plan


VERSION = "rule-observation-digest-v2"
STATES = {"support-added": "지지 근거 추가", "counter-added": "반대 근거 추가",
          "observed": "처음 확인", "reactivated": "다시 확인"}
OBSERVATION_STATES = {"shadow", "observed", "limited-active", "active"}


def _observation_only(event):
    value = event.get("deliveryValue") or {}
    state = ((event.get("hypothesis") or {}).get("qualification") or {}).get("status")
    return (event.get("purpose") == "hypothesis-evidence" and event.get("changeKind") in STATES
            and not any(event.get(k) for k in ("strategyPhases", "outcome", "contextState", "managementResponseChanged"))
            and state in OBSERVATION_STATES and value.get("allowed") is True
            and value.get("observationOnly") is True and value.get("executionAuthorized") is False
            and not value.get("addedEligibleActions") and not value.get("removedEligibleActions")
            and not ((event.get("decisionImpact") or {}).get("status") == "changed"
                     and event_impact(event, changes_only=True)))


def _judgment_line(plan, quantity, causes, opposing):
    if opposing:
        return "판단 보류. 반대 방향의 근거도 함께 확인됐습니다."
    if plan.get("comparisonRequired") is True:
        return "판단 보류. 여러 대응 후보의 근거를 비교 중입니다."
    restrictions = plan.get("activeRestrictions") or []
    reviews = plan.get("reviewCandidates") or []
    pending = plan.get("pendingCandidates") or []
    if plan and quantity == 0:
        if any(row.get("action") == "BUY" for row in restrictions):
            return "관찰 계속. 신규 매수 제한이 적용 중입니다."
        if any(row.get("action") == "BUY" for row in reviews):
            return "신규 매수 검토 중. 이번 알림으로 검토 상태는 달라지지 않았습니다."
        if any(row.get("action") == "BUY" for row in pending):
            return "관찰 계속. 신규 매수 후보는 근거 검증을 기다리고 있습니다."
        return "관찰 계속. 현재 신규 매수 검토 후보가 없습니다."
    if quantity is not None and quantity > 0:
        blocked = {row.get("action") for row in restrictions if row.get("ruleId") in causes}
        labels = [label for action, label in ACTION_LABELS.items() if action in blocked]
        if labels:
            return "관찰 계속. " + "·".join(labels) + " 제한이 적용 중입니다."
    for rows, message in ((reviews, " 검토 중. 이번 알림으로 검토 상태는 달라지지 않았습니다."),
                          (pending, " 후보는 근거 검증을 기다리고 있습니다.")):
        actions = {row.get("action") for row in rows if causes.intersection(row.get("supportingRuleIds") or [])}
        labels = [label for action, label in ACTION_LABELS.items() if action in actions]
        if labels:
            return "·".join(labels) + message
    return "관찰 계속. 이번 근거로 새로 검토할 매매 대응은 없습니다."


def _name(selected, rules):
    row, field = selected["measurement"], selected["field"]
    context, target = row.get("context") or {}, numeric(row.get("expectedValue"))
    if field == "relativeReturnPct" and context.get("comparisonWindow") == "session-change" and context.get("proxySymbol"):
        label = str(context["proxySymbol"]) + " 대비 "
        if target is not None and target < 0 and row.get("operator") in {"<", "<="}:
            return label + "상대 약세"
        if target is not None and target > 0 and row.get("operator") in {">", ">="}:
            return label + "상대 강세"
        return label + "등락률 차이"
    rule = rules.get(selected["ruleId"]) or next((rules[r] for r in selected["nativeRuleIds"] if r in rules), {})
    label = rule.get("label")
    return label if label and label != rule.get("id") else ((_context(row) + " · ") if _context(row) else "") + MEASURES[field][0]


def _values(selected, currency, include_name, rules):
    row, prior, field = selected["measurement"], selected.get("previous"), selected["field"]
    context, lines = row.get("context") or {}, []
    prefix = _name(selected, rules) + ": " if include_name else ""
    relative = field == "relativeReturnPct" and context.get("comparisonWindow") == "session-change"
    if relative:
        stock, proxy = numeric(context.get("stockChangeRate")), numeric(context.get("proxyChangeRate"))
        if stock is not None and proxy is not None and context.get("proxySymbol"):
            lines.append(prefix + "전일 종가 대비: 종목 " + decimal(stock, signed=True) + "% · "
                         + str(context["proxySymbol"]) + " " + decimal(proxy, signed=True) + "%")
    label = "등락률 차이" if relative else ((_context(row) + " · ") if _context(row) else "") + MEASURES[field][0]
    value = _number(field, row["observedValue"], currency)
    if prior is not None:
        older = _number(field, prior["observedValue"], currency)
        value = older + " → " + value if numeric(prior["observedValue"]) != numeric(row["observedValue"]) else value + " (이전 알림과 같음)"
    text = prefix + label + " " + value + " / 기준 " + _number(field, row["expectedValue"], currency) + " " + OPERATORS[row["operator"]]
    if prior is None:
        text += " · 이전 비교값 없음"
    source_at = (row.get("provenance") or {}).get("sourceAsOf")
    if isinstance(source_at, str) and len(source_at) == 10:
        text += " · " + source_at + " 자료"
    lines.append(text)
    return lines


def _next(selected, currency):
    row, field = selected["measurement"], selected["field"]
    context = row.get("context") or {}
    label = ((_context(row) + " · ") if _context(row) else "") + MEASURES[field][0]
    if field == "relativeReturnPct" and context.get("comparisonWindow") == "session-change" and context.get("proxySymbol"):
        label = "전일 종가 대비 " + str(context["proxySymbol"]) + "와의 등락률 차이"
    # Recheck the authored predicate, never invent its inverse as a buy/release rule.
    return label + " " + _number(field, row["expectedValue"], currency) + " " + OPERATORS[row["operator"]]


def observation_digest(packet, judgment, currency, summary_version):
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    events = packet.get("customerEvents") or []
    impact = (packet.get("policyLifecycle") or {}).get("decisionImpact") or {}
    if (not judgment or current.get("proofIdentityVerified") is not True or not events
            or not all(_observation_only(e) for e in events)
            or impact.get("changedResponses") or impact.get("releasedRuleIds")):
        return None
    rules = {r.get("id"): r for r in current.get("rules") or []}
    causes = {rid for event in events for rid in notice_rule_ids(event)}
    if not causes or len(causes) > 4 or any(not rule_notice_execution_proven(rules.get(rid) or {})
            or rules[rid].get("referenceOnly") or rules[rid].get("evidenceUsableForJudgement") is False for rid in causes):
        return None
    selections = selected_measurements(judgment, (previous.get("decisionBrief") or {}).get("judgment") or {},
        events, currency, cause_only=True, rule_labels={rid: r.get("label") for rid, r in rules.items()})
    covered = {rid for selected in selections for rid in [selected["ruleId"], *selected["nativeRuleIds"]]}
    if not causes.issubset(covered) or any(numeric(s["measurement"].get("expectedValue")) is None
            or s["measurement"].get("operator") not in OPERATORS for s in selections):
        return None
    states = list(dict.fromkeys(STATES[e["changeKind"]] for e in events))
    names = list(dict.fromkeys(_name(s, rules) for s in selections))
    change = " · ".join(names + states)
    values = [line for s in selections for line in _values(s, currency, len(causes) > 1, rules)]
    hypotheses = list(dict.fromkeys(hypothesis_words(e.get("hypothesis") or {})[0] for e in events))
    # Keep material opposing evidence visible even when the other rule did not change.
    composition = saved_composition(judgment)
    group_ids = {g["id"] for g in composition.get("evidenceGroups") or [] if g.get("id")
                 and (g.get("ruleId") in causes or set(g.get("nativeRuleIds") or []) & causes)}
    related = [r for r in composition.get("relationships") or [] if {r.get("left"), r.get("right")} & group_ids]
    opposing = any(r.get("opposingDirections") for r in related)
    if not opposing and len(causes) > 1 and any(r.get("sharedObservation") and {r.get("left"), r.get("right")} <= group_ids for r in related):
        hypotheses.append("같은 자료를 쓰는 규칙은 별개의 확인 근거로 합산하지 않음")
    facts = snapshot_facts(current)
    lead = _judgment_line(scoped_response_plan(judgment), numeric(facts.get("quantity")), causes, opposing)
    price = numeric(facts.get("currentPrice"))
    market = price_money(price, currency) if price is not None else "현재가 미확인"
    next_checks = list(dict.fromkeys(_next(s, currency) for s in selections))
    next_rows = ["새 자료에서 " + condition + " 조건 유지 여부" for condition in next_checks]
    return {"version": summary_version, "observationDigestVersion": VERSION,
        "lead": lead, "judgment": judgment, "decisionImpact": impact,
        "focusContract": {"version": FOCUS_VERSION, "comparisonBasis": "previous-successful-relation-receipt",
            "changedRules": changed_rule_rows(packet), "marketSnapshotRole": "reference-only"},
        "sections": [("notification-purpose", "달라진 규칙", [change]),
                     ("decision-impact", "규칙에 사용된 수치", values),
                     ("decision-basis", "관련 가설", hypotheses),
                     ("current-snapshot", "참고 시세", [market]),
                     ("next-check-contract", "다음 확인", next_rows)]}
