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


def observation_judgment(plan, quantity, causes, opposing):
    restrictions = plan.get("activeRestrictions") or []
    reviews = plan.get("reviewCandidates") or []
    pending = plan.get("pendingCandidates") or []
    if plan and quantity == 0:
        if any(row.get("action") == "BUY" for row in restrictions):
            return "현재 신규 매수가 제한돼 있습니다."
        if any(row.get("action") == "BUY" for row in pending) and not any(row.get("action") == "BUY" for row in reviews):
            return "신규 매수 판단은 보류합니다. 근거를 더 확인할 단계입니다."
        if opposing:
            return "신규 매수 판단은 보류합니다. 서로 반대되는 근거가 확인됐습니다."
        if any(row.get("action") == "BUY" for row in reviews):
            return "신규 매수는 계속 검토 중입니다."
        return "지금은 흐름을 더 지켜볼 단계입니다. 신규 매수를 검토할 조건은 아직 확인되지 않았습니다."
    if opposing:
        return "매매 판단을 보류합니다. 서로 반대되는 근거가 확인됐습니다."
    if plan.get("comparisonRequired") is True:
        actions = {row.get("action") for row in [*reviews, *pending]}
        labels = [label for action, label in ACTION_LABELS.items() if action in actions]
        return ("매매 판단을 보류합니다. 비교 중인 대응은 " + "·".join(labels) + "입니다."
                if labels else "판단 보류. 여러 대응 후보의 근거를 비교 중입니다.")
    if quantity is not None and quantity > 0:
        blocked = {row.get("action") for row in restrictions if row.get("ruleId") in causes}
        labels = [label for action, label in ACTION_LABELS.items() if action in blocked]
        if labels:
            return "·".join(labels) + " 제한이 적용돼 있습니다."
    for rows, message in ((reviews, " 검토를 이어갑니다."),
                          (pending, " 검토에는 근거가 더 필요합니다.")):
        actions = {row.get("action") for row in rows if causes.intersection(row.get("supportingRuleIds") or [])}
        labels = [label for action, label in ACTION_LABELS.items() if action in actions]
        if labels:
            return "·".join(labels) + message
    return "지금은 흐름을 더 지켜볼 단계입니다."


def current_policy_lead(current, plan):
    """Read the currently proven account policy from this snapshot."""
    quantity = numeric(snapshot_facts(current).get("quantity"))
    if current.get("proofIdentityVerified") is not True or quantity is None or quantity <= 0:
        return ""
    rules = {row.get("id"): row for row in current.get("rules") or []}
    responses = []
    for response in plan.get("policyResponses") or []:
        rule = rules.get(response.get("ruleId")) or {}
        if (response.get("authority") == "account-policy" and rule.get("matched") is True
                and rule.get("evidenceUsableForJudgement") is True and not rule.get("referenceOnly")
                and rule_notice_execution_proven(rule)):
            responses.append(response)
    lead = []
    if any(r.get("code") == "PAUSE_AVERAGING_DOWN" for r in responses):
        lead.append("추가매수는 제한돼 있습니다.")
    for response in responses:
        objective = response.get("objective") or {}
        actual, target = numeric(response.get("observedValue")), numeric(response.get("targetValue"))
        if (response.get("code") == "RESTORE_POSITION_LIMIT"
                and objective.get("field") == "positionAccountWeight" and objective.get("operator") == "<="
                and actual is not None and target is not None and actual > target):
            if not lead and any(row.get("action") == "ADD" for row in plan.get("activeRestrictions") or []):
                lead.append("추가매수는 제한돼 있습니다.")
            lead.append("이 종목의 계좌 비중은 " + decimal(actual) + "%입니다. 설정한 관리 목표는 "
                        + decimal(target) + "% 이하입니다.")
            break
    return " ".join(lead)


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


def measurement_predicate(selected, currency):
    row, field = selected["measurement"], selected["field"]
    context = row.get("context") or {}
    label = ((_context(row) + " · ") if _context(row) else "") + MEASURES[field][0]
    if field == "relativeReturnPct" and context.get("comparisonWindow") == "session-change" and context.get("proxySymbol"):
        label = "전일 종가 대비 " + str(context["proxySymbol"]) + "와의 등락률 차이"
    # Recheck the authored predicate, never invent its inverse as a buy/release rule.
    return label + " " + _number(field, row["expectedValue"], currency) + " " + OPERATORS[row["operator"]]


def measured_band(selections, currency):
    """Two bounds can describe one measured condition, provided their witness is identical."""
    if len(selections) != 2:
        return {}
    first, second = selections
    lower, upper = first["measurement"], second["measurement"]
    if lower.get("operator") in {"<", "<="}:
        lower, upper = upper, lower
    if (first["ruleId"] != second["ruleId"] or first["field"] != second["field"]
            or lower.get("operator") not in {">", ">="} or upper.get("operator") not in {"<", "<="}
            or any(lower.get(k) != upper.get(k)
                   for k in ("evidenceId", "conditionId", "observedValue", "provenance", "context"))):
        return {}
    field = first["field"]
    label = MEASURES[field][0]
    lo = _number(field, lower["expectedValue"], currency) + " " + OPERATORS[lower["operator"]]
    hi = _number(field, upper["expectedValue"], currency) + " " + OPERATORS[upper["operator"]]
    actual = _number(field, lower["observedValue"], currency)
    return {"label": label, "actual": actual, "bounds": lo + "이고 " + hi}


def _correction_only(event):
    value = event.get("deliveryValue") or {}
    return (event.get("purpose") == "hypothesis-evidence" and event.get("changeKind") == "counter-added"
            and value.get("allowed") is True and value.get("deliveredPredecessor") is True
            and value.get("reasonCode") == "delivered-premise-correction"
            and not any(value.get(k) for k in ("addedEligibleActions", "removedEligibleActions", "executionAuthorized"))
            and ((event.get("hypothesis") or {}).get("qualification") or {}).get("status") != "quarantined"
            and (event.get("decisionImpact") or {}).get("status") == "unchanged"
            and not any(event.get(k) for k in ("strategyPhases", "outcome", "managementResponseChanged")))


def _measured_changes(current, previous, events, judgment, currency):
    rules = {r.get("id"): r for r in current.get("rules") or []}
    causes = {rid for event in events for rid in notice_rule_ids(event)}
    if not causes or len(causes) > 4 or any(not rule_notice_execution_proven(rules.get(rid) or {})
            or rules[rid].get("referenceOnly") or rules[rid].get("evidenceUsableForJudgement") is False for rid in causes):
        return causes, rules, []
    selections = selected_measurements(judgment, (previous.get("decisionBrief") or {}).get("judgment") or {},
        events, currency, cause_only=True, rule_labels={rid: r.get("label") for rid, r in rules.items()})
    covered = {rid for selected in selections for rid in [selected["ruleId"], *selected["nativeRuleIds"]]}
    if not causes.issubset(covered) or any(numeric(s["measurement"].get("expectedValue")) is None
            or s["measurement"].get("operator") not in OPERATORS for s in selections):
        return causes, rules, []
    return causes, rules, selections


def compact_market_values(selections, currency):
    rows, relative = [], {}
    for selected in selections:
        row, field = selected["measurement"], selected["field"]
        prior = numeric((selected.get("previous") or {}).get("observedValue"))
        value = numeric(row["observedValue"])
        context = row.get("context") or {}
        proxy = context.get("proxySymbol")
        if field != "relativeReturnPct" or context.get("comparisonWindow") != "session-change" or not proxy or not value:
            rows.extend(_values(selected, currency, False, {}))
            continue
        stock, comparison = numeric(context.get("stockChangeRate")), numeric(context.get("proxyChangeRate"))
        both_down = stock is not None and comparison is not None and stock < 0 and comparison < 0
        key = (str(proxy), row.get("evidenceId"), value, prior, both_down, (row.get("provenance") or {}).get("sourceAsOf"))
        thresholds = relative.setdefault(key, [])
        boundary = _number(field, row["expectedValue"], currency) + " " + OPERATORS[row["operator"]]
        if boundary not in thresholds:
            thresholds.append(boundary)
    facts = []
    for (proxy, _, value, prior, both_down, _clock), thresholds in relative.items():
        if both_down:
            line = proxy + "보다 " + decimal(abs(value)) + "%p " + ("덜 하락했습니다." if value > 0 else "더 하락했습니다.")
        else:
            line = proxy + "보다 수익률이 " + decimal(abs(value)) + "%p " + ("높았습니다." if value > 0 else "낮았습니다.")
        if prior is not None and prior != value:
            line += " 이전 차이 " + _number("relativeReturnPct", prior, currency) + "."
        facts.append(line + " 기준 " + " / ".join(thresholds))
    return facts + rows, {(key[0], key[2] > 0) for key in relative}


def correction_digest(packet, judgment, currency, summary_version):
    """Lead a delivered counter-evidence update with its frozen response."""
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    events = packet.get("customerEvents") or []
    impact = (packet.get("policyLifecycle") or {}).get("decisionImpact") or {}
    plan = scoped_response_plan(judgment)
    if (not plan or current.get("proofIdentityVerified") is not True or not events
            or not all(_correction_only(event) for event in events)
            or impact.get("changedResponses") or impact.get("releasedRuleIds")
            or plan.get("selectedMarketAction") or judgment.get("finalMarketActionSelected")):
        return None
    causes, rules, selections = _measured_changes(current, previous, events, judgment, currency)
    if not selections or any(rules[rid].get("evidenceUsableForJudgement") is not True for rid in causes):
        return None

    values, relative = compact_market_values(selections, currency)
    proxies = sorted({name for name, _ in relative})
    mixed_proxies = len(proxies) > 1 and len({positive for _, positive in relative}) > 1
    facts = snapshot_facts(current)
    quantity = numeric(facts.get("quantity"))
    opposing = any(link.get("opposingDirections") for link in saved_composition(judgment).get("relationships") or [])
    policy = current_policy_lead(current, plan)
    lead = policy or observation_judgment(plan, quantity, causes, opposing)
    decision = ("매도 시점과 수량은 아직 정해지지 않았습니다." if policy
                else "이번 근거 변화로 매매 판단을 바꾸지 않았습니다.")
    purpose = ("비교 대상에 따라 주가 흐름이 다르게 나타났습니다." if mixed_proxies
               else "기존 판단을 다시 살펴볼 근거가 추가됐습니다.")
    price, daily = numeric(facts.get("currentPrice")), numeric(facts.get("priceChangeRate"))
    market = (price_money(price, currency) + (" (전일 " + decimal(daily, signed=True) + "%)" if daily is not None else "")
              if price is not None else "현재가 미확인")
    next_rows = (["다음 시세에서 " + "·".join(proxies) + "와의 등락률 차이가 이어지는지 확인합니다."] if mixed_proxies
                 else ["새 자료에서 " + measurement_predicate(selected, currency) + " 조건이 이어지는지 확인합니다."
                       for selected in selections[:2]])
    return {"version": summary_version, "lead": lead, "judgment": judgment, "decisionImpact": impact,
            "focusContract": {"version": FOCUS_VERSION, "comparisonBasis": "previous-successful-relation-receipt",
                              "changedRules": changed_rule_rows(packet), "marketSnapshotRole": "reference-only"},
            "sections": [("notification-purpose", "알림 이유", [purpose]),
                         ("decision-impact", "규칙에 사용된 수치", values),
                         ("decision-basis", "현재 판단", [decision]),
                         ("current-snapshot", "참고 시세", [market]),
                         ("next-check-contract", "다음 확인", next_rows)]}


def observation_digest(packet, judgment, currency, summary_version):
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    events = packet.get("customerEvents") or []
    impact = (packet.get("policyLifecycle") or {}).get("decisionImpact") or {}
    if (not judgment or current.get("proofIdentityVerified") is not True or not events
            or not all(_observation_only(e) for e in events)
            or impact.get("changedResponses") or impact.get("releasedRuleIds")):
        return None
    causes, rules, selections = _measured_changes(current, previous, events, judgment, currency)
    if not selections:
        return None
    states = list(dict.fromkeys(STATES[e["changeKind"]] for e in events))
    names = list(dict.fromkeys(_name(s, rules) for s in selections))
    change = " · ".join(names + states)
    values = [line for s in selections for line in _values(s, currency, len(causes) > 1, rules)]
    band = measured_band(selections, currency)
    if band:
        observed = band["label"] + " " + band["actual"]
        change = observed + "입니다. 이 값이 관찰 범위에 해당합니다."
        values = ["관찰 범위 " + band["bounds"]]
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
    plan = scoped_response_plan(judgment)
    policy = current_policy_lead(current, plan)
    lead = policy or observation_judgment(plan, numeric(facts.get("quantity")), causes, opposing)
    if policy and opposing:
        hypotheses.append("서로 반대되는 근거가 있어 매매 방향은 판단을 보류합니다.")
    price = numeric(facts.get("currentPrice"))
    market = price_money(price, currency) if price is not None else "현재가 미확인"
    next_checks = list(dict.fromkeys(measurement_predicate(s, currency) for s in selections))
    next_rows = ["새 자료에서 " + condition + " 조건 유지 여부" for condition in next_checks]
    if band:
        next_rows = ["다음 시세에서도 같은 범위에 머무는지 확인합니다."]
    return {"version": summary_version, "observationDigestVersion": VERSION,
        "lead": lead, "judgment": judgment, "decisionImpact": impact,
        "focusContract": {"version": FOCUS_VERSION, "comparisonBasis": "previous-successful-relation-receipt",
            "changedRules": changed_rule_rows(packet), "marketSnapshotRole": "reference-only"},
        "sections": [("notification-purpose", "알림 이유" if band else "달라진 규칙", [change]),
                     ("decision-impact", "규칙에 사용된 수치", values),
                     ("decision-basis", "관련 가설", hypotheses),
                     ("current-snapshot", "참고 시세", [market]),
                     ("next-check-contract", "다음 확인", next_rows)]}
