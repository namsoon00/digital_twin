"""A bounded notification view of a frozen judgment and receipt comparison."""

from digital_twin.modules.notifications.domain.observation_market_snapshot import clock, decimal, numeric, snapshot_facts
from digital_twin.modules.notifications.domain.alert_formatting import price_money
from digital_twin.modules.notifications.domain.relation_change_presentation import (
    FIELDS, OPERATORS, condition_clause, current_input_rows, measurement, model_input_change_rows,
)
from digital_twin.modules.notifications.domain.relation_observation_language import hypothesis_words, next_check_words
from digital_twin.modules.notifications.domain.relation_observation_proof import window_limitation, window_name, source_time
from digital_twin.modules.notifications.domain.rule_event_presentation import (
    ACTION_LABELS, event_conclusion, event_impact, qualification_notice, scoped_response_plan,
)
from digital_twin.modules.notifications.domain.rule_policy_lifecycle import release_conclusion
from digital_twin.modules.notifications.domain.rule_notice_evidence import bind_policy_notice
from digital_twin.modules.notifications.domain.relation_notice_focus import focus_relation_notice, notice_rule_ids
from digital_twin.modules.notifications.domain.rule_observation_digest import (
    compact_market_values, correction_digest, current_policy_lead, measurement_predicate, observation_digest, observation_judgment,
)


from digital_twin.modules.notifications.domain.composed_rule_presentation import (
    selected_measurements, composition_explanation, composition_next, failed_measurement_names, saved_composition,
)


VERSION = "compact-rulebox-v13"

MARKET_CHANGES = {
    "observed": "주가 흐름을 살펴볼 조건이 처음 확인됐습니다.",
    "reactivated": "전에 확인했던 주가 조건이 다시 성립했습니다.",
    "support-added": "앞선 판단을 뒷받침하는 근거가 추가됐습니다.",
    "support-removed": "앞선 판단을 뒷받침하던 근거가 약해졌습니다.",
    "counter-added": "앞선 판단에 반대되는 근거가 추가됐습니다.",
    "counter-removed": "앞선 판단에 반대하던 근거가 줄었습니다.",
    "invalidated": "앞선 판단에 쓰인 규칙 조건이 더 이상 성립하지 않습니다.",
    "expired": "앞선 판단에 쓰인 근거의 확인 기한이 지났습니다.",
}


def _unique(values):
    return list(dict.fromkeys(v for v in values if v))


def _conclusion(judgment, events):
    plan = scoped_response_plan(judgment)
    management = {'responses': plan.get('policyResponses') or []} if plan else judgment.get("managementDecision") or {}
    if management.get("responses"):
        return " · ".join(_unique(r.get("label") for r in management["responses"])) + "."
    groups = [*plan.get('reviewCandidates', []), *plan.get('pendingCandidates', [])] if plan else judgment.get('candidateGroups') or []
    actions = [ACTION_LABELS[r["action"]] for r in groups if r["action"] in ACTION_LABELS]
    blocked_codes = _unique(r.get('action') for r in plan.get('activeRestrictions') or []) if plan else judgment.get('blockedActions') or []
    blocked = [ACTION_LABELS[a] for a in blocked_codes if a in ACTION_LABELS]
    restriction = "·".join(blocked) + " 제한이 적용돼 있습니다." if blocked else ""
    if restriction:
        return restriction
    if len(actions) > 1:
        return "여러 대응 후보를 비교하고 있습니다."
    if actions:
        pending = any(not r.get("executionEligible") for r in groups)
        return actions[0] + " 검토에는 근거가 더 필요합니다." if pending else "다음 검토 대상은 " + actions[0] + "입니다."
    released = [e for e in events if e.get("policyState") == "released"]
    if released:
        return event_conclusion(released[0])
    return "지금은 흐름을 더 지켜볼 단계입니다."


def _window_measurement(window, older, condition, old_condition, symbol):
    if (window_limitation(window, condition, symbol) or window.get('hasSufficientHistory') is not True
            or numeric(window.get('priceChangePct')) is None):
        return ''
    prior = next((w for w in older if condition.get('releaseId') and condition.get('releaseId') == old_condition.get('releaseId')
                  and w.get('windowKey') == window.get('windowKey')
                  and w.get('hasSufficientHistory') is True and not window_limitation(w, old_condition, symbol)
                  and numeric(w.get('priceChangePct')) is not None
                  and source_time(w.get('knowledgeCutoffAt')) <= source_time(window.get('knowledgeCutoffAt'))), None)
    text = window_name(window.get('windowKey')) + ' 가격 등락 '
    if prior and numeric(prior['priceChangePct']) == numeric(window['priceChangePct']):
        return text + decimal(window['priceChangePct'], signed=True) + '% (이전 알림과 같음)'
    if prior:
        text += decimal(prior['priceChangePct'], signed=True) + '% → '
    text += decimal(window['priceChangePct'], signed=True) + '%'
    return text + (' (직전 알림 → 이번)' if prior else ' (이전 알림의 같은 기간 수치 없음)')


def _purpose(events):
    policy = [e for e in events if e.get("purpose") in {"management-policy", "execution-constraint"}]
    selected = policy or events
    if any(e.get("managementResponseChanged") for e in selected):
        return "관리 규칙이 요구하는 대응 또는 목표가 새로 정해졌습니다."
    kinds = {e.get("changeKind") for e in selected}
    if policy:
        if len(policy) == 1:
            value = event_conclusion(policy[0])
            if len(value) <= 130:
                return value
        states = {e.get("policyState") for e in policy}
        return ("앞서 적용하던 관리 제한의 해제 조건이 확인됐습니다." if states == {"released"}
                else "관리 제한의 적용 조건이 새로 확인됐거나 달라졌습니다.")
    if len(kinds) == 1 and selected:
        value = event_conclusion(selected[0], include_qualification=False)
        if len(value) <= 130:
            return value
    return "기존 설명을 지지하거나 반박하는 근거가 함께 달라져 다시 점검합니다."


def _context_windows(events):
    return _unique({"24h": "24시간", "7d": "7일"}.get(row.get("horizon"))
        for event in events for row in event.get("measurements") or []
        if numeric(row.get("changePct")) is not None and numeric(row.get("thresholdPct")) is not None)


def _market_purpose(events, failed, selections):
    if all(event.get("contextState") for event in events):
        windows = _context_windows(events)
        if windows:
            return "·".join(windows) + " 가격 변동률이 관찰 기준에 도달했습니다."
        names = _unique(e.get("ruleLabel") for e in events if e.get("ruleLabel") not in (e.get("sourceRuleIds") or []))
        return names[0].rstrip(".") + "." if names else "시장 흐름에 새 변화가 확인됐습니다."
    kinds = {e.get("changeKind") for e in events}
    if failed:
        text = "기존 " + "·".join(failed) + " 조건이 성립하지 않았습니다."
        if "reactivated" in kinds:
            text += " 다른 관찰 조건은 다시 성립했습니다."
        elif kinds & {"observed", "support-added"}:
            text += " 다른 관찰 조건은 새로 확인됐습니다."
        return text
    if len(kinds) != 1:
        return "앞선 판단에 쓰인 여러 근거가 달라졌습니다."
    kind = next(iter(kinds))
    if kind == "counter-added":
        labels = _unique(row["measurement"].get("conditionLabel") for row in selections)
        if len(labels) == 1 and len(labels[0]) <= 100:
            return labels[0].rstrip(".") + ". 이 근거로 앞선 판단을 다시 살펴봅니다."
    topics = _unique(hypothesis_words(e.get("hypothesis") or {})[0] for e in events)
    if len(topics) == 1 and topics[0] != "관찰 가설" and kind in {"counter-added", "counter-removed", "support-added", "support-removed"}:
        prefix = "‘" + topics[0] + "’"
        return (prefix + "에 " + MARKET_CHANGES[kind].removeprefix("앞선 판단에 ") if kind.startswith("counter-")
                else prefix + "을 " + MARKET_CHANGES[kind].removeprefix("앞선 판단을 "))
    return MARKET_CHANGES.get(kind, "판단에 쓰인 근거가 달라졌습니다.")


def _new_action_line(events):
    changes = [e["deliveryValue"] for e in events if (e.get("deliveryValue") or {}).get("allowed") is True
               and e["deliveryValue"].get("reasonCode") == "eligible-response-changed"]
    added = {action for row in changes for action in row.get("addedEligibleActions") or []}
    removed = {action for row in changes for action in row.get("removedEligibleActions") or []}
    if added & removed:
        return ""
    parts = []
    for actions, suffix in ((added, " 검토 조건이 새로 충족됐습니다."),
                            (removed, " 검토 대상에서 빠졌습니다.")):
        labels = [label for action, label in ACTION_LABELS.items() if action in actions]
        if labels:
            parts.append("·".join(labels) + suffix)
    return " ".join(parts)


def _pending_action_line(events, plan):
    pending = {r.get("action") for r in plan.get("pendingCandidates") or []
               if r.get("pendingReason") == "execution-qualification-pending"}
    added, removed = set(), set()
    for event in events:
        impact = event.get("decisionImpact") or {}
        if impact.get("status") == "changed":
            before = set((impact.get("previous") or {}).get("candidateActions") or [])
            after = set((impact.get("current") or {}).get("candidateActions") or [])
            added.update(after - before)
            removed.update(before - after)
    labels = [label for code, label in ACTION_LABELS.items() if code in added & pending]
    if labels and not removed and added <= pending:
        return "새 검토 대상은 " + "·".join(labels) + "입니다. 근거를 더 확인할 때까지 매매 판단은 보류합니다."
    return ""


def _measurements(current, previous, judgment, events, currency, transitions, *, plain_market=False):
    before_rules = {r.get("id"): r for r in previous.get("rules", [])}
    for event in events:
        for rid in event.get('sourceRuleIds') or []:
            if rid not in before_rules and event.get('previousConditions'):
                before_rules[rid] = {'conditions': event['previousConditions']}
    cause_ids = {rid for e in events for rid in notice_rule_ids(e)}
    strategy_ids = {rid for e in events if e.get("strategyPhases") for rid in e.get("sourceRuleIds") or []}
    used = cause_ids
    changed, unchanged, reference = [], [], []
    for event in events:
        if not event.get('contextState'):
            continue
        for value in event.get('measurements') or []:
            horizon = {'24h': '24시간', '7d': '7일'}.get(value.get('horizon'))
            change, threshold = numeric(value.get('changePct')), numeric(value.get('thresholdPct'))
            if horizon and change is not None and threshold is not None:
                signed_threshold = -abs(threshold) if value.get('direction') == 'down' else abs(threshold)
                unchanged.append(horizon + ' 변동 ' + decimal(change, signed=True) + '% / 관찰 기준 '
                                 + decimal(signed_threshold, signed=True) + '%')
    for transition in transitions:
        for comparison in (transition.get("changeBasis") or {}).get("ruleComparisons") or []:
            if comparison.get("ruleId") not in cause_ids:
                continue
            changed.extend(model_input_change_rows(comparison))
            if not comparison.get("previous"):
                unchanged.extend(current_input_rows(comparison)[:1])
    seen = set()
    ordered_rules = sorted(current.get("rules", []), key=lambda r: r.get("id") not in cause_ids)
    def value_text(field, value):
        return decimal(value) + "%" if field in {"positionAccountWeight", "positionWeight"} else measurement(field, value, currency)
    for rule in ordered_rules:
        if (rule.get("id") not in used or (rule.get("id") not in strategy_ids
                and (rule.get("referenceOnly") or rule.get("evidenceUsableForJudgement") is False))):
            continue
        for condition in rule.get("conditions", []):
            field, actual = condition.get("field"), condition.get("observedValue")
            if (condition.get("judgementEvidenceUsable") is False
                    or condition.get("freshnessStatus") in {"stale", "expired", "missing", "unknown"}
                    or not (condition.get("matchedByTypeDB") is True or condition.get("matched") is True
                            or condition.get('resolutionMatchedByTypeDB') is True)):
                continue
            if rule.get("id") in cause_ids and condition.get("modelSignalMatched"):
                prior = [c for c in before_rules.get(rule.get('id'), {}).get('conditions', [])
                         if c.get('conditionId') == condition.get('conditionId')]
                old_condition = prior[0] if len(prior) == 1 else {}
                for window in condition.get("sourceTemporalWindows") or []:
                    text = _window_measurement(window, old_condition.get('sourceTemporalWindows') or [],
                                               condition, old_condition, current.get('symbol'))
                    if not text:
                        continue
                    key = (rule.get("id"), window.get("windowKey"))
                    if key not in seen:
                        seen.add(key)
                        (changed if '→' in text else unchanged).append('규칙 입력: ' + text)
            condition_key = (rule.get("id"), condition.get("conditionId"), field)
            if field not in FIELDS or numeric(actual) is None or condition_key in seen:
                continue
            seen.add(condition_key)
            old = [c for c in before_rules.get(rule.get("id"), {}).get("conditions", [])
                   if c.get("field") == field and c.get("conditionId") == condition.get("conditionId")]
            older = old[0].get("observedValue") if len(old) == 1 else None
            is_cause = rule.get("id") in cause_ids
            has_change = is_cause and numeric(older) is not None and older != actual
            label = rule.get("label") if rule.get("label") != rule.get("id") else ""
            text = (str(label) + ": " if label else "") + FIELDS[field][0] + " "
            text += (value_text(field, older) + " → " if has_change else "현재 ")
            text += value_text(field, actual)
            saved = condition.get("resolvedThreshold") or {}
            expected = saved.get("value")
            if expected is not None and condition.get("operator"):
                resolution = condition.get('resolutionMatchedByTypeDB') is True
                operator = condition.get('resolutionOperator') if resolution else condition['operator']
                text += (" / 해제 기준 " if resolution else " / 기준 ") + value_text(field, expected) + " " + {
                    ">": "초과", ">=": "이상", "<": "미만", "<=": "이하", "=": "일치", "==": "일치"}.get(operator, operator)
            at = condition.get("observedAt")
            if at and at != current.get("observedAt"):
                text += " · " + clock(at)
            (changed if has_change else unchanged if is_cause else reference).append(text)
    # Native policy responses carry their exact objective and bound values,
    # even when an old receipt did not keep condition detail.
    for response in (judgment.get("managementDecision") or {}).get("responses", []):
        if response.get("ruleId") not in cause_ids:
            continue
        objective = response.get("objective") or {}
        field = objective.get("field")
        if field in FIELDS and not any(isinstance(key, tuple) and key[0] == response.get("ruleId") and key[-1] == field for key in seen):
            is_cause = response.get("ruleId") in cause_ids
            (unchanged if is_cause else reference).append(("" if is_cause else "현재 판단 참고: ")
                             + FIELDS[field][0] + " 현재 " + value_text(field, response.get("observedValue"))
                             + " / 관리 목표 " + value_text(field, response.get("targetValue"))
                             + " " + {"<=": "이하", ">=": "이상"}.get(objective.get("operator"), ""))
    policy_values = []
    for response in (judgment.get("managementDecision") or {}).get("responses") or []:
        if response.get("ruleId") not in cause_ids:
            continue
        objective = response.get("objective") or {}
        field = objective.get("field")
        if field in FIELDS and numeric(response.get("observedValue")) is not None and numeric(response.get("targetValue")) is not None:
            policy_values.append("계좌 관리: " + FIELDS[field][0] + " " + value_text(field, response["observedValue"])
                + " / 해제 기준 " + value_text(field, response["targetValue"]) + " "
                + {"<=": "이하", ">=": "이상", "<": "미만", ">": "초과"}.get(objective.get("operator"), ""))
    exact = selected_measurements(judgment, (previous.get("decisionBrief") or {}).get("judgment") or {},
                               events, currency, limit=max(2, 4 - len(policy_values)), cause_only=True,
                               rule_labels={r.get("id"): r.get("label") for r in current.get("rules") or []})
    if exact:
        if plain_market and all(numeric(row["measurement"].get("expectedValue")) is not None
                and row["measurement"].get("operator") in OPERATORS for row in exact):
            values, _ = compact_market_values(exact, currency)
        else:
            values = [row["text"] for row in exact]
        return "\n".join(values + policy_values[:2]), exact
    result = _unique(changed + unchanged + reference)
    return ("\n".join(result[:3]) if result else "이번 변화의 비교 수치를 확인할 자료가 부족합니다."), []


def _comparison(judgment, current, events):
    plan = scoped_response_plan(judgment)
    groups = [*plan.get('reviewCandidates', []), *plan.get('pendingCandidates', [])] if plan else judgment.get("candidateGroups") or []
    if groups and not any(g.get('executionEligible') for g in groups):
        return '연결된 가설의 예측 검증이 충분하지 않아 매매 대응은 아직 검토 단계입니다.'
    if len(groups) > 1:
        return "서로 다른 대응을 뒷받침하는 근거가 함께 있어 대안 비교가 남아 있습니다."
    if groups:
        ids = set(groups[0].get("hypothesisIds") or [])
        names = _unique(hypothesis_words(h)[0] for h in current.get("hypotheses", []) if h.get("id") in ids)
        readable = [n for n in names if n != "관찰 가설"]
        text = "·".join(readable[:2]) + "의 근거를 같은 대응 후보로 함께 검토합니다." if readable else "연결된 근거를 같은 대응 후보로 함께 검토합니다."
        if groups[0].get("counterEvidenceIds"):
            text += " 반대 근거도 있어 아래 확인 조건을 함께 봅니다."
        return text
    if judgment.get("managementDecision") or judgment.get("blockedActions"):
        return "계정에 설정한 관리 기준에 따른 대응입니다. 가격 전망에 따른 보유·매도 선택은 별도로 남아 있습니다."
    kinds = {e.get("changeKind") for e in events}
    if kinds & {"counter-added", "support-removed", "invalidated"}:
        return "기존 설명을 그대로 유지할 수 있는지 다시 확인해야 합니다. 위험 해소나 보유 유지로 해석하지 않습니다."
    return "조건의 성립과 이후 가격 예측의 적중은 구분해 확인합니다."


def _next(judgment, events, currency):
    hypothesis_next = ""
    if events and all(e.get('purpose') in {'hypothesis-evidence', 'context-evidence'} for e in events):
        for event in events:
            text = next_check_words((event.get('nextCheck') or {}).get('condition') or
                                    (event.get('hypothesis') or {}).get('falsificationContract'))
            if text and len(text) <= 150:
                hypothesis_next = text.rstrip('. ') + '.'
                return hypothesis_next
        return "이번 가설의 구체적인 재확인 기준은 상세 근거에서 확인합니다."
    targets = []
    for response in (judgment.get("managementDecision") or {}).get("responses", []):
        objective = response.get("objective") or {}
        field = objective.get("field")
        if field in FIELDS and numeric(response.get("targetValue")) is not None:
            targets.append((FIELDS[field][0] + " " + decimal(response["targetValue"]) + "% "
                            + {"<=": "이하", ">=": "이상"}.get(objective.get("operator"), ""))
                           if field in {"positionAccountWeight", "positionWeight"}
                           else condition_clause(field, objective.get("operator"), response["targetValue"], currency))
    if targets:
        return (" · ".join(targets[:2]) + "인지 새 자료에서 각각 확인합니다. 다른 제한 조건도 함께 재검사합니다."
                + (" 가설 확인: " + hypothesis_next if hypothesis_next else ""))
    if any(e.get('policyState') == 'released' for e in events):
        boundaries = [condition_clause(c['field'], c['operator'], c['resolvedThreshold']['value'], currency).replace("+", "")
                      if c['field'] in {'positionAccountWeight', 'positionWeight'}
                      else condition_clause(c['field'], c['operator'], c['resolvedThreshold']['value'], currency)
                      for e in events if e.get('policyState') == 'released' for c in e.get('conditions') or []
                      if c.get('resolutionMatchedByTypeDB') is True and c.get('field') in FIELDS
                      and numeric((c.get('resolvedThreshold') or {}).get('value')) is not None]
        return (' · '.join(_unique(boundaries)[:2]) + ' 조건이 다시 성립하는지 새 자료에서 확인합니다.' if boundaries
                else '새 자료에서 해제된 관리 조건이 다시 성립하는지 확인합니다.')
    restriction_ids = {r.get("ruleId") for r in judgment.get("restrictions") or []}
    cause_ids = {rid for e in events for rid in e.get("sourceRuleIds") or []}
    conditions = []
    for row in sorted(judgment.get("conditions") or [], key=lambda r: r.get("ruleId") not in cause_ids):
        field, target = row.get("field"), (row.get("resolvedThreshold") or {}).get("value")
        if row.get("ruleId") in restriction_ids and field in FIELDS and numeric(target) is not None:
            clause = condition_clause(field, row.get("operator") or "=", target, currency)
            if field in {"positionAccountWeight", "positionWeight"}:
                clause = clause.replace("+", "")
            conditions.append(clause)
    if conditions:
        return " · ".join(_unique(conditions)[:2]) + " 조건이 계속 성립하는지 새 자료에서 확인합니다. 각 제한은 별도로 재검사합니다."
    checks = _unique(list(judgment.get("nextChecks") or []) + [
        (e.get("hypothesis") or {}).get("falsificationContract") or (e.get("nextCheck") or {}).get("condition") for e in events])
    for value in checks:
        text = next_check_words(value)
        if text and len(text) <= 150:
            return text.rstrip(". ") + "."
    return "이번 규칙의 구체적인 재확인 기준이 기록되지 않았습니다. 상세 근거에서 확인이 필요합니다."


def compact_rulebox_summary(packet):
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    events = packet.get("customerEvents") or []
    brief = current.get("decisionBrief") or {}
    judgment = brief.get("judgment") or {}
    if judgment and (judgment.get("authority") != "typedb-materialized-synthesis" or any(
            judgment.get(k) != current.get(k) for k in ("accountId", "symbol", "worldId", "inferenceGenerationId", "sourceAboxSnapshotId"))):
        judgment = {}
    facts = snapshot_facts(current)
    currency = facts.get("currency") or ("USD" if current.get("market") == "US" else "KRW")
    price, daily, profit = numeric(facts.get("currentPrice")), numeric(facts.get("priceChangeRate")), numeric(facts.get("profitLossRate"))
    market = "가격 " + price_money(price, currency) if price is not None else "현재가 미확인"
    if daily is not None:
        market += " (전일 " + decimal(daily, signed=True) + "%)"
    quote_only = market
    if profit is not None and (numeric(facts.get("quantity")) or 0) > 0:
        market += " · 보유 수익률 " + decimal(profit, signed=True) + "%"
    from .rule_strategy_event import strategy_summary
    strategy_events = [e for e in events if e.get("strategyPhases")]
    plan = scoped_response_plan(judgment)
    policy_lead = current_policy_lead(current, plan)
    strategy = strategy_summary(packet, market, _measurements(current, previous, judgment, strategy_events, currency,
                                                                 packet.get("transitions") or [])[0]) if strategy_events else None
    if strategy:
        strategy["judgment"] = judgment
        if any(e.get("policyState") for e in events):
            strategy["lead"] = release_conclusion(packet) or policy_lead or _conclusion(judgment, events)
        elif policy_lead:
            strategy["lead"] = policy_lead + " " + strategy["lead"]
        return focus_relation_notice(bind_policy_notice(strategy, packet), packet)
    observation = observation_digest(packet, judgment, currency, VERSION) or correction_digest(packet, judgment, currency, VERSION)
    if observation:
        return observation
    released_lead = release_conclusion(packet)
    lead = released_lead or policy_lead or _conclusion(judgment, events)
    impact = ((packet.get('policyLifecycle') or {}).get('decisionImpact') or {}) if judgment else {}
    hypothesis_change = bool(events) and all(e.get('purpose') in {'hypothesis-evidence', 'context-evidence'} for e in events)
    evidence_only = hypothesis_change and not (impact.get('changedResponses') or impact.get('releasedRuleIds')
        or any(e.get("managementResponseChanged") for e in events))
    action = _new_action_line(events)
    context_only = hypothesis_change and all(e.get("contextState") for e in events)
    failed = failed_measurement_names(events, current.get("symbol")) if evidence_only else []
    measurements, selections = _measurements(current, previous, judgment, events, currency,
                                             packet.get("transitions") or [], plain_market=evidence_only)
    if hypothesis_change:
        opposing = any(r.get("opposingDirections") for r in saved_composition(judgment).get("relationships") or [])
        rule_ids = {rid for event in events for rid in notice_rule_ids(event)}
        quantity = numeric(facts.get("quantity"))
        if quantity is not None and quantity > 0 and current.get("proofIdentityVerified") is True and not policy_lead:
            scoped_restrictions = {r.get("action") for r in plan.get("activeRestrictions") or []}
            names = [label for code, label in ACTION_LABELS.items() if code in scoped_restrictions]
            if names:
                policy_lead = "·".join(names) + " 제한이 적용돼 있습니다."
        lead = ("시장 흐름 관찰 알림입니다. 이번에는 추이를 지켜봅니다." if context_only
                else action or policy_lead or observation_judgment(plan, quantity, rule_ids, opposing))
    elif impact.get('changedResponses') and not released_lead and not policy_lead:
        lead = ' · '.join(_unique(r.get('label') for r in impact['changedResponses'])) + '.'
    prior_judgment = (previous.get("decisionBrief") or {}).get("judgment") or {}
    if (not released_lead and not policy_lead and not hypothesis_change and judgment.get("conclusionFingerprint") and judgment.get("conclusionFingerprint") == prior_judgment.get("conclusionFingerprint")
            and judgment.get("status") in {"management-response", "restriction-only"}):
        lead = "기존 관리 대응 유지: " + lead
    lifecycle = packet.get('policyLifecycle') or {}
    causes = {t.get('cause') for t in lifecycle.get('transitions') or []}
    target_changes = [t for transition in lifecycle.get('transitions') or [] for t in transition.get('targetChanges') or []]
    target_text = ' · '.join(FIELDS[t['field']][0] + ' 관리 목표 ' + decimal(t['previous']) + '% → ' + decimal(t['current']) + '%'
                            for t in target_changes[:2] if t['field'] in FIELDS)
    purpose = ('설정된 관리 목표가 바뀌어 해당 제한의 해제 기준을 충족했습니다. 시장 개선을 뜻하지 않습니다.'
               if released_lead and 'policy-target-changed' in causes else
               '새 자료에서 이전 관리 조건의 해제 기준을 충족한 사실을 확인했습니다.' if released_lead else
               '설정된 관리 목표가 바뀌었습니다. 시장이 새로 악화됐다는 뜻은 아닙니다.' if causes == {'policy-target-changed'} else
               '해제됐던 관리 조건이 다시 성립해 해당 대응을 재적용합니다.' if causes == {'policy-reapplied'} else
               '현재 자료에서 이 관리 대응의 적용을 처음 확인해 알려드립니다.' if causes == {'first-delivered-confirmation'} else
               _market_purpose(events, failed, selections) if hypothesis_change else _purpose(events))
    together = ('해제된 관리 조건의 범위를 확인합니다. 매매 판단은 별도로 검토합니다.' if released_lead
                else _comparison(judgment, current, events))
    remaining = _unique(r.get('label') for r in impact.get('remainingResponses') or [])
    pending_change = _pending_action_line(events, plan) if evidence_only else ""
    if evidence_only:
        changes = _unique(event_impact(e, brief, changes_only=True) for e in events
                          if (e.get("decisionImpact") or {}).get("status") == "changed")
        exclusions = _unique(qualification_notice(e) for e in events
                             if ((e.get("hypothesis") or {}).get("qualification") or {}).get("status") == "quarantined")
        if context_only:
            together = "이번 관찰로 새로 정해진 매매 대응은 없습니다."
        elif action:
            together = " ".join(filter(None, [policy_lead, "매매 시점과 수량은 아직 정해지지 않았습니다."]))
        elif pending_change:
            together = " ".join([pending_change, *exclusions])
        elif changes or exclusions:
            together = " ".join(_unique([*changes, *exclusions]))
        elif policy_lead:
            together = "매매 시점과 수량은 아직 정해지지 않았습니다."
        elif all((e.get("decisionImpact") or {}).get("status") == "unchanged" for e in events):
            together = "이번 변화로 새로 정해진 매매 대응은 없습니다."
        else:
            together = "다음 자료에서 이 변화가 이어지는지 살펴봅니다."
        comparison = "여러 대응 후보의 근거를 비교 중입니다."
        if (judgment.get("comparisonRequired") is True and numeric(facts.get("quantity")) != 0
                and "비교 중인 대응" not in lead and comparison not in lead and not action):
            together = comparison if not (changes or exclusions) else together + " " + comparison
    elif remaining and not released_lead:
        together = ('유지 중인 관리 기준: ' if impact.get('status') == 'maintained' else '현재 적용 중인 관리 기준: ') + ' · '.join(remaining) + '.'
        if hypothesis_change:
            together += (' 이번 근거 변화로 관리 대응이 바뀌지는 않았습니다.' if impact.get('status') == 'maintained'
                         else ' 이번에 달라진 가설 근거와 별도로 적용되는 관리 기준입니다.')
    elif hypothesis_change and (judgment.get('candidateGroups') or judgment.get('blockedActions')):
        together = _conclusion(judgment, events)
    if not evidence_only:
        combined = composition_explanation(judgment, include_policy=not remaining)
        if combined:
            together = together + " " + combined
    next_text = _next(judgment, events, currency) if hypothesis_change else (
        composition_next(judgment, events, currency) or _next(judgment, events, currency))
    if context_only:
        windows = _context_windows(events)
        next_text = ("새 자료에서 " + "·".join(windows) + " 가격 변동률이 기준에 계속 해당하는지 확인합니다."
                     if windows else "새 자료에서 같은 시장 조건이 유지되는지 확인합니다.")
    elif failed:
        next_text = "다음 시세에서 " + "·".join(failed) + " 조건이 다시 성립하는지 확인합니다."
    elif pending_change and len(events) == 1 and selections:
        measured = selections[0]
        row = measured["measurement"]
        if numeric(row.get("expectedValue")) is not None and row.get("operator") in OPERATORS:
            next_text = "다음 시세에서도 " + measurement_predicate(measured, currency) + " 조건이 이어지는지 확인합니다."
    # Separate rows survive the shared customer-language normalizer, which
    # deliberately folds whitespace inside a single prose row.
    measurement_rows = ([target_text] if target_text else []) + measurements.splitlines()
    if failed:
        measurement_rows.insert(0, "조건을 충족하지 않은 시점의 " + "·".join(failed) + " 수치는 확인할 수 없습니다.")
    return focus_relation_notice(bind_policy_notice({"version": VERSION, "lead": lead, "judgment": judgment, "decisionImpact": impact,
        "sections": [("notification-purpose", "알림 이유", [purpose]),
                     ("decision-impact", "핵심 수치", measurement_rows),
                     ("decision-basis", "함께 보면", [together]),
                     ("current-snapshot", "현재", [quote_only if evidence_only else market]),
                     ("next-check-contract", "다음 확인", [next_text])]}, packet), packet)
