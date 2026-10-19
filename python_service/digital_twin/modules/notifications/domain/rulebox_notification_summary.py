"""A bounded notification view of a frozen judgment and receipt comparison."""

from digital_twin.modules.notifications.domain.observation_market_snapshot import clock, decimal, numeric, snapshot_facts
from digital_twin.modules.notifications.domain.alert_formatting import price_money
from digital_twin.modules.notifications.domain.relation_change_presentation import (
    FIELDS, condition_clause, current_input_rows, measurement, model_input_change_rows,
)
from digital_twin.modules.notifications.domain.relation_observation_language import hypothesis_words, next_check_words
from digital_twin.modules.notifications.domain.relation_observation_proof import window_limitation, window_name
from digital_twin.modules.notifications.domain.rule_event_presentation import ACTION_LABELS, event_conclusion
from digital_twin.modules.notifications.domain.rule_policy_lifecycle import release_conclusion


VERSION = "compact-rulebox-v1"


def _unique(values):
    return list(dict.fromkeys(v for v in values if v))


def _conclusion(judgment, events):
    management = judgment.get("managementDecision") or {}
    if management.get("responses"):
        return " · ".join(_unique(r.get("label") for r in management["responses"])) + "."
    actions = [ACTION_LABELS[r["action"]] for r in judgment.get("candidateGroups", []) if r["action"] in ACTION_LABELS]
    blocked = [ACTION_LABELS[a] for a in judgment.get("blockedActions", []) if a in ACTION_LABELS]
    restriction = "·".join(blocked) + " 제한 적용." if blocked else ""
    if len(actions) > 1:
        return " ".join(v for v in (restriction, "·".join(actions) + " 후보의 근거가 엇갈려 최종 판단을 확정하지 않았습니다.") if v)
    if actions:
        pending = any(not r.get("executionEligible") for r in judgment["candidateGroups"])
        return " ".join(v for v in (restriction, actions[0] + (" 검토 조건은 확인됐지만 근거의 운영 검증이 남았습니다." if pending
                                            else " 검토 조건이 확인됐습니다. 최종 매매 판단은 아직 확정 전입니다.")) if v)
    if restriction:
        return restriction + " 보유분의 유지·매도 판단은 아직 확정되지 않았습니다."
    released = [e for e in events if e.get("policyState") == "released"]
    if released:
        return event_conclusion(released[0])
    return "확인된 근거 변화가 있어 기존 설명을 다시 점검합니다. 이번 자료로 매매 방향을 정하지 않았습니다."


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
        value = event_conclusion(selected[0])
        if len(value) <= 130:
            return value
    return "기존 설명을 지지하거나 반박하는 근거가 함께 달라져 다시 점검합니다."


def _measurements(current, previous, judgment, events, currency, transitions):
    before_rules = {r.get("id"): r for r in previous.get("rules", [])}
    for event in events:
        for rid in event.get('sourceRuleIds') or []:
            if rid not in before_rules and event.get('previousConditions'):
                before_rules[rid] = {'conditions': event['previousConditions']}
    cause_ids = {rid for e in events for rid in e.get("sourceRuleIds") or []}
    used = cause_ids | set(judgment.get("sourceRuleIds") or [])
    changed, unchanged, reference = [], [], []
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
        if rule.get("id") not in used or rule.get("referenceOnly") or rule.get("evidenceUsableForJudgement") is False:
            continue
        for condition in rule.get("conditions", []):
            field, actual = condition.get("field"), condition.get("observedValue")
            if (condition.get("judgementEvidenceUsable") is False
                    or condition.get("freshnessStatus") in {"stale", "expired", "missing", "unknown"}
                    or not (condition.get("matchedByTypeDB") is True or condition.get("matched") is True
                            or condition.get('resolutionMatchedByTypeDB') is True)):
                continue
            if rule.get("id") in cause_ids and condition.get("modelSignalMatched"):
                for window in condition.get("sourceTemporalWindows") or []:
                    if (window_limitation(window, condition, current.get("symbol"))
                            or window.get("hasSufficientHistory") is not True or numeric(window.get("priceChangePct")) is None):
                        continue
                    key = (rule.get("id"), window.get("windowKey"))
                    if key not in seen:
                        seen.add(key)
                        unchanged.append("규칙 입력: " + window_name(window.get("windowKey")) + " 가격 등락 "
                                         + decimal(window['priceChangePct'], signed=True) + "% · " + clock(window.get("knowledgeCutoffAt")))
            if field not in FIELDS or numeric(actual) is None or field in seen:
                continue
            seen.add(field)
            old = [c for c in before_rules.get(rule.get("id"), {}).get("conditions", [])
                   if c.get("field") == field and c.get("conditionId") == condition.get("conditionId")]
            older = old[0].get("observedValue") if len(old) == 1 else None
            is_cause = rule.get("id") in cause_ids
            has_change = is_cause and numeric(older) is not None and older != actual
            text = ("" if is_cause else "현재 판단 참고: ") + FIELDS[field][0] + " "
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
        objective = response.get("objective") or {}
        field = objective.get("field")
        if field in FIELDS and field not in seen:
            is_cause = response.get("ruleId") in cause_ids
            (unchanged if is_cause else reference).append(("" if is_cause else "현재 판단 참고: ")
                             + FIELDS[field][0] + " 현재 " + value_text(field, response.get("observedValue"))
                             + " / 관리 목표 " + value_text(field, response.get("targetValue"))
                             + " " + {"<=": "이하", ">=": "이상"}.get(objective.get("operator"), ""))
    result = _unique(changed + unchanged + reference)
    return " · ".join(result[:3]) if result else "조건 변화는 확인됐지만 직접 비교할 측정값이 보존되지 않았습니다. 상세 근거를 확인해 주세요."


def _comparison(judgment, current, events):
    groups = judgment.get("candidateGroups") or []
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
        return " · ".join(targets[:2]) + "인지 새 자료에서 각각 확인합니다. 다른 제한 조건도 함께 재검사합니다."
    if any(e.get('policyState') == 'released' for e in events):
        boundaries = [condition_clause(c['field'], c['operator'], c['resolvedThreshold']['value'], currency)
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
    if profit is not None and (numeric(facts.get("quantity")) or 0) > 0:
        market += " · 보유 수익률 " + decimal(profit, signed=True) + "%"
    released_lead = release_conclusion(packet)
    lead = released_lead or _conclusion(judgment, events)
    prior_judgment = (previous.get("decisionBrief") or {}).get("judgment") or {}
    if (not released_lead and judgment.get("conclusionFingerprint") and judgment.get("conclusionFingerprint") == prior_judgment.get("conclusionFingerprint")
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
               '현재 자료에서 이 관리 대응의 적용을 처음 확인해 알려드립니다.' if causes == {'first-delivered-confirmation'} else _purpose(events))
    together = ('해제된 조건에 대한 후속 안내입니다. 추가매수 허용이나 매수·매도 권유를 뜻하지 않습니다.' if released_lead
                else _comparison(judgment, current, events))
    return {"version": VERSION, "lead": lead, "judgment": judgment,
        "sections": [("notification-purpose", "알림 이유", [purpose]),
                     ("decision-impact", "핵심 수치", [' · '.join(v for v in (target_text, _measurements(current, previous, judgment, events, currency, packet.get("transitions") or [])) if v)]),
                     ("decision-basis", "함께 보면", [together]),
                     ("current-snapshot", "현재", [market]),
                     ("next-check-contract", "다음 확인", [_next(judgment, events, currency)])]}
