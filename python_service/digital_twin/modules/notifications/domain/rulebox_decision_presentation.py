"""Readable restrictions and candidates from the frozen decision brief."""

from digital_twin.modules.decisions.contracts import resolved_rule_threshold
from digital_twin.modules.notifications.domain.observation_market_snapshot import decimal, numeric, snapshot_facts
from digital_twin.modules.notifications.domain.alert_formatting import price_money
from digital_twin.modules.notifications.domain.relation_observation_language import hypothesis_words


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
    pending = {h.get("id"): h for row in brief.get("candidates", [])
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


def decision_brief_presentation(packet):
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    brief = current.get("decisionBrief") or {}
    if brief.get("version") != "rulebox-decision-brief-v1" or brief.get("authority") != "typedb-materialized-decision-effects":
        return {}
    if any(brief.get(k) != current.get(k) for k in ("accountId", "symbol", "worldId", "inferenceGenerationId", "sourceAboxSnapshotId")):
        return {}
    facts, old = snapshot_facts(current), snapshot_facts(previous)
    blocked = [LABELS[a] for a in brief.get("blockedActions", []) if a in LABELS]
    candidates = [LABELS[a] for a in brief.get("candidateActions", []) if a in LABELS]
    lead = []
    if blocked:
        lead.append("관리 원칙: " + "·".join(blocked) + " 제한.")
    if candidates:
        lead.append("대응 판단: " + "·".join(candidates) + " 검토 보류."
                    if brief.get("qualificationPending") else "대응 후보: " + "·".join(candidates) + " · 최종 선택 전.")
    else:
        lead.append("대응 판단: 확인된 매매 의견 없음.")
    basis, rechecks = [], []
    seen = set()
    for rule, condition in _conditions(current, brief):
        field = condition.get("field")
        resolved = threshold(condition, facts)
        value, actual = numeric(resolved.get("value")), numeric(condition.get("observedValue"))
        operator = condition.get("operator")
        if field != "profitLossRate" or (field, value, operator) in seen:
            continue
        seen.add((field, value, operator))
        if value is None:
            basis.append("손실 조건은 확인됐지만 실제 적용 기준값이 보존되지 않아, 기준과의 차이는 표시하지 않습니다.")
            continue
        if actual is None or value >= 0 or operator not in {"<=", "<"}:
            continue
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
        recheck = "손실 기준 초과 해소: 평가 수익률 " + decimal(value, signed=True) + ("% 초과" if operator == "<=" else "% 이상") + "."
        average, price = numeric(facts.get("averagePrice")), numeric(facts.get("currentPrice"))
        # Only translate a threshold into price when the captured P/L is
        # demonstrably on that price/cost basis. FX/account-adjusted returns
        # must not produce a misleading target or stop price.
        if (average and average > 0 and price and price > 0 and value > -100
                and abs((price / average - 1) * 100 - actual) < .005):
            currency = facts.get("currency") or ("USD" if current.get("market") == "US" else "KRW")
            recheck += " 현재 평균매입가로 환산한 손실 기준 경계는 약 " + price_money(average * (1 + value / 100), currency) + "."
        rechecks.append(recheck + " 손실 기준만의 경계이며, 반등 확인이나 추가매수 허용을 뜻하지 않습니다.")
    weight, cap = numeric(facts.get("positionWeight")), numeric(facts.get("strategyMaxPositionWeightPct"))
    if weight is not None and cap is not None and cap > 0 and (numeric(facts.get("quantity")) or 0) > 0:
        basis.append("계좌 비중 " + decimal(weight) + "% / 설정 상한 " + decimal(cap)
                     + "% · " + ("상한 이내" if weight <= cap else "상한 초과") + " (손실 기준과 별도)")
    reasons = []
    if brief.get("qualificationPending"):
        reasons.append("·".join(candidates) + "는 규칙이 제시한 후보입니다. 근거 가설의 검증이 끝나지 않아 매매 의견으로 확정하지 않았습니다.")
        reasons.extend(qualification_rows(current, brief))
        reasons.append("판단을 다시 확인할 조건: 가설의 운영 검증 기준 충족 여부와 그때의 지지·반대 근거. 결과 건수만 채워도 매매가 확정되는 것은 아닙니다.")
    if brief.get("comparisonRequired"):
        reasons.append("서로 다른 대응을 뒷받침하는 근거가 함께 있어 대안 비교가 남아 있습니다.")
    if not reasons:
        reasons.append("현재 근거와 반대 근거의 비교 결과가 필요합니다. 매매 시점·수량을 확정한 판단은 아직 없습니다.")
    return {"lead": " ".join(lead), "sections": [
        ("decision-basis", "내 관리 기준과 비교", _unique(basis)),
        ("decision-review", "대응 판단과 남은 조건", _unique(reasons)),
        ("decision-recheck", "관리 기준을 다시 볼 조건", _unique(rechecks)),
    ]}
