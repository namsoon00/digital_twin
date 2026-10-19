"""Readable restrictions and candidates from the frozen decision brief."""

from digital_twin.modules.decisions.contracts import resolved_rule_threshold
from digital_twin.modules.notifications.domain.observation_market_snapshot import decimal, numeric, snapshot_facts
from digital_twin.modules.notifications.domain.alert_formatting import price_money
from digital_twin.modules.notifications.domain.relation_observation_language import hypothesis_words
from digital_twin.modules.notifications.domain.rule_policy_lifecycle import release_conclusion


LABELS = {"BUY": "신규 매수", "ADD": "추가매수", "HOLD": "보유 유지",
          "TRIM": "일부 축소", "SELL": "전량 매도", "AVOID": "매수 회피"}


def threshold(condition, facts):
    # Old receipts may lack the newly captured resolution. Their own frozen
    # facts are sufficient; never consult today's account policy or catalog.
    saved = condition.get("resolvedThreshold")
    if isinstance(saved, dict) and saved.get("field") in facts and numeric(saved.get("value")) != numeric(facts.get(saved["field"])):
        return {**saved, "value": None, "source": "inconsistent-snapshot"}
    return saved if isinstance(saved, dict) else resolved_rule_threshold(condition, facts)


def _unique(values):
    return list(dict.fromkeys(value for value in values if value))


def _conditions(current, brief):
    ids = {r["ruleId"] for r in [*brief.get("restrictions", []), *brief.get("candidates", [])]}
    return [(rule, condition) for rule in current.get("rules", []) if rule.get("id") in ids
            and not rule.get("referenceOnly") and rule.get("evidenceUsableForJudgement") is not False
            for condition in rule.get("conditions", [])
            if condition.get("judgementEvidenceUsable") is not False
            and condition.get("freshnessStatus") not in {"stale", "expired", "unknown", "missing"}
            and (condition.get("matchedByTypeDB") is True or condition.get("matched") is True)]


def _previous_condition(previous, rule, condition):
    """A receipt's own predicate is required to explain its historical state."""
    for old_rule in previous.get("rules", []):
        if old_rule.get("id") != rule.get("id"):
            continue
        matches = [row for row in old_rule.get("conditions", [])
                   if row.get("field") == condition.get("field")
                   and row.get("conditionId") == condition.get("conditionId")]
        if len(matches) == 1:
            return matches[0]
    return {}


def qualification_rows(current, brief):
    """Describe stored qualification; never promote a candidate in a renderer."""
    by_id = {h.get("id"): h for h in current.get("hypotheses", [])}
    pending = {h.get("id"): h for row in [*brief.get("candidates", []), *brief.get("unavailableCandidates", [])]
               for h in row.get("hypotheses", []) if not h.get("executionEligible")}
    result = []
    for identity, captured in list(pending.items())[:3]:
        hypothesis = by_id.get(identity) or {}
        topic, claim = hypothesis_words(hypothesis)
        name = topic if topic != "관찰 가설" else claim
        q = hypothesis.get("qualification") or {}
        policy = q.get("policy") or (hypothesis.get("claimContract") or {}).get("qualificationPolicy") or {}
        count = numeric(q.get("decisiveOutcomeCount", captured.get("outcomeCount")))
        floor = numeric(policy.get("activeFloor"))
        details = []
        if count is not None:
            details.append("독립 검증 결과 " + decimal(count, 0) + "건"
                           + (" / 운영 기준 " + decimal(floor, 0) + "건 이상" if floor is not None else ""))
        rate, required = numeric(q.get("directionalHitRate")), numeric(policy.get("activeMinHitRate"))
        if count and rate is not None and required is not None and rate < required:
            details.append("과거 적중률 " + decimal(rate * 100) + "% / 기준 " + decimal(required * 100) + "% 이상")
        lower = numeric((q.get("directionalHitRateConfidence95") or {}).get("lower"))
        required_lower = numeric(policy.get("activeMinLowerConfidence"))
        if count and lower is not None and required_lower is not None and lower < required_lower:
            details.append("표본 오차를 고려한 적중률 하한 " + decimal(lower * 100) + "% / 기준 " + decimal(required_lower * 100) + "% 이상")
        if policy.get("requireNonNegativeActionReturn"):
            action_return = numeric(q.get("averageActionAdjustedReturnPct"))
            if q.get("actionReturnAvailable") is False:
                details.append("대응 결과 수익률 미확인")
            elif action_return is not None and action_return < 0:
                details.append("대응 결과 수익률 0% 이상 기준 미충족")
        if q.get("baselineComparisonRequired") and not q.get("baselineComparisonSatisfied"):
            details.append("비교 전략 대비 성과 검증 미충족")
        if policy.get("automaticPromotion") is False:
            details.append("검증 후 운영 자격 자동 전환 꺼짐")
        status = q.get("status", captured.get("qualification"))
        label = "검증 결과 재점검 필요" if status == "quarantined" else "운영 검증 미충족" if status != "active" else "이번 판단에 사용할 자격 미확인"
        result.append(name + ": " + label + (" · " + " · ".join(details) if details else " · 필요한 검증 기준은 이번 기록에 없습니다."))
    if len(pending) > 3:
        result.append("나머지 가설 " + str(len(pending) - 3) + "개의 검증 조건은 상세 근거에서 확인할 수 있습니다.")
    return result


def unavailable_candidate_rows(brief):
    """Explain recorded exclusions without treating them as a change of view."""
    grouped = {}
    for row in brief.get("unavailableCandidates", []):
        action = LABELS.get(row.get("action"))
        if not action:
            continue
        codes = row.get("exclusionReasons") or []
        eligibility = row.get("eligibility") or {}
        reasons = []
        if "unusable-rule-evidence" in codes:
            reasons.append("관련 규칙의 원천 자료가 허용 시간을 넘었습니다."
                           if eligibility.get("freshnessStatus") in {"stale", "expired"}
                           else eligibility.get("reason") or "이번 기록에서 규칙 근거의 사용 가능 여부를 확인하지 못했습니다.")
        if "blocked-action" in codes:
            reasons.append("현재 관리 규칙이 이 행동을 제한합니다.")
        if "no-eligible-linked-hypothesis" in codes:
            reasons.append("판단에 사용할 수 있는 근거 가설이 연결되지 않았습니다.")
        if not reasons:
            reasons.append("규칙이 참고용으로 분류됐습니다." if "reference-only-rule" in codes
                           else "이번 판단에서 허용된 대응 범위에 포함되지 않습니다.")
        grouped.setdefault(action, []).extend(reasons)
    admitted = {LABELS[a] for a in brief.get("candidateActions", []) if a in LABELS}
    return [action + (" 후보의 일부 근거 제외: " if action in admitted else " 판단에 사용하지 못한 이유: ") + " ".join(_unique(reasons))
            for action, reasons in grouped.items()]


def management_decision_presentation(decision):
    if decision.get("version") != "management-decision-v1" or decision.get("status") != "POLICY_RESPONSE_SELECTED":
        return {}
    responses = decision.get("responses") or []
    if not responses:
        return {}
    lead, guidance, basis, rechecks = [], [], [], []
    for response in responses:
        lead.append(str(response.get("label") or "") + ".")
        guidance.append(str(response.get("label") or "") + ": " + str(response.get("reason") or ""))
        target, actual = numeric(response.get("targetValue")), numeric(response.get("observedValue"))
        scenario = response.get("adjustmentScenario") or {}
        if scenario:
            basis.append("계좌 전체 대비 비중 " + decimal(actual) + "% → 관리 목표 " + decimal(target) + "% 이하"
                         + " · 상한 초과 " + decimal(scenario.get("excessWeightPercentagePoints")) + "%p")
            adjustment = "일부 매도로 목표를 맞추는 경우: 현재 보유분의 약 " + decimal(scenario.get("positionReductionPct")) + "%를 줄이는 규모"
            quantity = numeric(scenario.get("equivalentQuantity"))
            if quantity is not None:
                adjustment += " (약 " + decimal(quantity) + "주에 해당)"
            guidance.append(adjustment + ".")
            guidance.append("현재 가격·환율이 유지되고 매도 대금이 계좌에 남는다는 가정의 계산입니다. 세금·비용·거래 단위 적용 전이며 확정 주문 수량은 아닙니다.")
            guidance.append("매도 없이 외부 자금으로만 맞추려면 현재 계좌 전체 자산의 약 "
                            + decimal(scenario.get("externalDepositPctOfAccount")) + "%를 추가해야 합니다.")
        elif actual is not None and target is not None:
            basis.append("이 추가매수 제한의 손익 조건: 현재 " + decimal(actual, signed=True) + "% · 해소 경계 " + decimal(target, signed=True) + "% 이상")
        guidance.append(str(response.get("executionBoundary") or ""))
        rechecks.append(str(response.get("releaseCondition") or ""))
    return {"lead": "관리 대응 결정: " + " ".join(lead), "sections": [
        ("decision-guidance", "결정된 관리 대응과 조정 규모", _unique(guidance)),
        ("decision-basis", "결정에 사용한 관리 기준", _unique(basis)),
        ("decision-review", "이 결정의 적용 범위", ["위 대응은 계정의 관리 정책을 충족하기 위한 결정입니다. 가격 전망에 따른 유지·추가 축소 판단은 별도로 검증하며, 주문은 실행하지 않습니다."]),
        ("decision-recheck", "관리 대응을 해제할 조건", _unique(rechecks)),
    ]}


def decision_brief_presentation(packet):
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    brief = current.get("decisionBrief") or {}
    if brief.get("version") != "rulebox-decision-brief-v1" or brief.get("authority") != "typedb-materialized-decision-effects":
        return {}
    if any(brief.get(k) != current.get(k) for k in ("accountId", "symbol", "worldId", "inferenceGenerationId", "sourceAboxSnapshotId")):
        return {}
    management = brief.get("managementDecision") or {}
    released = release_conclusion(packet)
    if released:
        rows, checks = [], []
        labels = {'positionAccountWeight': '계좌 전체 대비 비중', 'profitLossRate': '보유 수익률'}
        for event in packet.get('customerEvents') or []:
            if event.get('policyState') != 'released':
                continue
            prior = {(c.get('conditionId'), c.get('field')): c for c in event.get('previousConditions') or []}
            for condition in event.get('conditions') or []:
                field = condition.get('field')
                actual = numeric(condition.get('observedValue'))
                if field not in labels or actual is None or condition.get('resolutionMatchedByTypeDB') is not True:
                    continue
                previous = numeric(prior.get((condition.get('conditionId'), field), {}).get('observedValue'))
                target = numeric((condition.get('resolvedThreshold') or {}).get('value'))
                sign = {'<=': '이하', '>=': '이상', '<': '미만', '>': '초과'}
                rows.append(labels[field] + ': ' + (decimal(previous) + '% → ' if previous is not None else '현재 ')
                            + decimal(actual) + '%' + (' · 해제 기준 ' + decimal(target) + '% '
                            + sign.get(condition.get('resolutionOperator'), '') if target is not None else ''))
                if target is not None:
                    checks.append(labels[field] + ' ' + decimal(target) + '% ' + sign.get(condition.get('operator'), '')
                                  + ' 조건이 다시 성립하는지 새 자료에서 확인합니다.')
        remaining = management_decision_presentation(management).get('sections') or []
        return {'lead': released, 'sections': [('decision-resolution', '해제된 관리 조건과 비교 수치', rows),
            ('decision-resolution-check', '다시 제한을 확인할 조건', checks), *remaining]}
    if management and all(management.get(k) == current.get(k) for k in ("accountId", "symbol", "worldId", "inferenceGenerationId", "sourceAboxSnapshotId")):
        presentation = management_decision_presentation(management)
        if presentation:
            # Policy objectives do not replace the forecast comparison or
            # erase why a previously reported candidate became unavailable.
            forecast = unavailable_candidate_rows(brief)
            candidates = [LABELS[a] for a in brief.get("candidateActions", []) if a in LABELS]
            if candidates:
                forecast.append("가격 전망에 따른 별도 검토 후보: " + "·".join(candidates)
                                + (" · 근거 가설의 운영 검증 미충족으로 선택 보류." if brief.get("qualificationPending")
                                   else " · 대안 비교를 거쳐 최종 선택 전."))
            if forecast:
                presentation["sections"].append(("decision-forecast-review", "가격 전망에 따른 별도 판단", _unique(forecast)))
            return presentation
    facts, old = snapshot_facts(current), snapshot_facts(previous)
    held = facts.get("isHolding") is True or (numeric(facts.get("quantity")) or 0) > 0
    choice = "보유 유지·축소" if held else "매수·매도"
    blocked = [LABELS[a] for a in brief.get("blockedActions", []) if a in LABELS]
    candidates = [LABELS[a] for a in brief.get("candidateActions", []) if a in LABELS]
    lead = []
    if blocked:
        lead.append("관리 원칙: " + "·".join(blocked) + " 제한.")
    if candidates:
        lead.append("대응 판단: " + "·".join(candidates) + " 검토 보류."
                    if brief.get("qualificationPending") else "대응 후보: " + "·".join(candidates) + " · 최종 선택 전.")
    else:
        lead.append(choice + " 중 어느 쪽이 나은지는 이번 규칙 결과로 결정하지 못했습니다.")
    basis, rechecks = [], []
    seen = set()
    for rule, condition in _conditions(current, brief):
        field = condition.get("field")
        resolved = threshold(condition, facts)
        value, actual = numeric(resolved.get("value")), numeric(condition.get("observedValue"))
        operator = condition.get("operator")
        if field not in {"profitLossRate", "positionAccountWeight", "positionWeight"} or (field, value, operator) in seen:
            continue
        seen.add((field, value, operator))
        if field in {"positionAccountWeight", "positionWeight"}:
            label = "계좌 전체 대비 비중" if field == "positionAccountWeight" else "보유 종목 평가액 내 비중"
            if actual is None or value is None or operator not in {">", ">="}:
                basis.append(label + " 제한은 확인됐지만 실제 비교값 또는 기준이 보존되지 않았습니다.")
                continue
            basis.append(label + " " + decimal(actual) + "% / 설정 상한 " + decimal(value) + "%"
                         + (" · 상한보다 " + decimal(actual - value) + "%p 높음" if actual > value else " · 규칙의 제한 조건 충족"))
            if actual > value:
                lead.insert(1, label + "은 " + decimal(actual) + "%로 설정 상한 " + decimal(value)
                            + "%보다 " + decimal(actual - value) + "%p 높습니다.")
            rechecks.append("비중 제한 조건 해소: " + label + " " + decimal(value)
                            + ("% 이하" if operator == ">" else "% 미만")
                            + ". 비중 조건만의 경계이며, 다른 추가매수 제한의 해제까지 뜻하지 않습니다.")
            continue
        if value is None:
            basis.append("손실 조건은 확인됐지만 실제 적용 기준값이 보존되지 않아, 기준과의 차이는 표시하지 않습니다.")
            continue
        if actual is None or value > 0 or operator not in {"<=", "<"}:
            continue
        if value == 0:
            basis.append("규칙의 손익 조건: 평가 수익률 0% " + ("미만" if operator == "<" else "이하")
                         + " · 현재 " + decimal(actual, signed=True) + "%")
        else:
            basis.append("손실 관리 기준 " + decimal(value, signed=True) + "% · 현재 평가 수익률 "
                         + decimal(actual, signed=True) + "%"
                         + (" · 기준보다 손실 " + decimal(value - actual) + "%p 큼" if actual < value else ""))
        prior = numeric(old.get(field))
        old_condition = _previous_condition(previous, rule, condition)
        old_resolved = threshold(old_condition, old)
        same_threshold = (old_condition.get("operator") == operator
                          and old_resolved.get("field") == resolved.get("field")
                          and numeric(old_resolved.get("value")) == value)
        previously_breached = prior is not None and (prior <= value if operator == "<=" else prior < value)
        if same_threshold and previously_breached:
            delta = actual - prior
            basis.append("이전 알림에서도 손실 기준을 벗어난 상태였습니다. "
                         + ("이후 손실이 " + decimal(abs(delta)) + "%p " + ("확대됐습니다." if delta < 0 else "줄었습니다.")
                            if delta else "이번 평가 수익률은 같습니다."))
        elif old_condition and numeric(old_resolved.get("value")) is not None and not same_threshold:
            basis.append("손실 기준 또는 비교 방식이 이전 알림과 달라, 기준 이탈의 지속 여부는 비교하지 않습니다.")
        recheck = ("손익 조건 해소: 평가 수익률 " if value == 0 else "손실 기준 초과 해소: 평가 수익률 ") + decimal(value, signed=True) + ("% 초과" if operator == "<=" else "% 이상") + "."
        average, price = numeric(facts.get("averagePrice")), numeric(facts.get("currentPrice"))
        # Only translate a threshold into price when the captured P/L is
        # demonstrably on that price/cost basis. FX/account-adjusted returns
        # must not produce a misleading target or stop price.
        if (average and average > 0 and price and price > 0 and value > -100
                and abs((price / average - 1) * 100 - actual) < .005):
            currency = facts.get("currency") or ("USD" if current.get("market") == "US" else "KRW")
            recheck += " 현재 평균매입가로 환산한 손실 기준 경계는 약 " + price_money(average * (1 + value / 100), currency) + "."
        rechecks.append(recheck + " 손실 기준만의 경계이며, 반등 확인이나 추가매수 허용을 뜻하지 않습니다.")
    guidance = []
    if blocked:
        guidance.append("현재 관리 기준상 " + "·".join(blocked) + " 제한을 적용합니다.")
    for entry in brief.get("restrictions", []):
        guidance.extend("관리 규칙이 요청한 점검: " + value + "." for value in entry.get("nextChecks", []))
    reasons = unavailable_candidate_rows(brief)
    if brief.get("qualificationPending"):
        reasons.append("·".join(candidates) + "는 규칙이 제시한 후보입니다. 근거 가설의 검증이 끝나지 않아 매매 의견으로 확정하지 않았습니다.")
    qualification = qualification_rows(current, brief)
    reasons.extend(qualification)
    if qualification:
        rechecks.append("대응 판단을 다시 확인할 조건: 규칙에 필요한 최신 원천 자료와 가설별 운영 검증 기준을 모두 확인해야 합니다. 시세 갱신이나 결과 건수 충족만으로 매매 판단이 확정되지는 않습니다.")
    if brief.get("comparisonRequired"):
        reasons.append("서로 다른 대응을 뒷받침하는 근거가 함께 있어 대안 비교가 남아 있습니다.")
    if not reasons:
        reasons.append("규칙이 제시한 후보의 최종 선택과 매매 시점·수량은 아직 확정되지 않았습니다."
                       if candidates else "이번 기록에는 " + choice + "를 선택할 수 있는 대응 후보가 없습니다.")
    if not candidates:
        guidance.append(choice + " 결정은 미확정입니다. 관리 조건의 제한이 보유 유지 권고나 즉시 매도 지시를 뜻하지는 않습니다.")
    elif not guidance:
        guidance.append("규칙이 제시한 대응 후보: " + "·".join(candidates) + ". 아래 검증 상태와 남은 조건을 확인합니다.")
    return {"lead": " ".join(lead), "sections": [
        ("decision-guidance", "지금 적용할 대응과 점검", _unique(guidance)),
        ("decision-basis", "내 관리 기준과 비교", _unique(basis)),
        ("decision-review", "대응 판단과 남은 조건" if candidates else ("유지·축소" if held else "매매 여부") + "를 결정하지 못한 이유", _unique(reasons)),
        ("decision-recheck", "관리 기준을 다시 볼 조건", _unique(rechecks)),
    ]}
