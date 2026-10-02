"""Explain frozen graph conclusions and measurements without adding market rules."""

from digital_twin.modules.notifications.domain.observation_market_snapshot import (
    clock, decimal, market_snapshot_sections, numeric, snapshot_facts,
)
from digital_twin.modules.notifications.domain.alert_formatting import price_money
from digital_twin.modules.notifications.domain.relation_observation_language import hypothesis_words, next_check_words


PRESENTATION_VERSION = "readable-relation-change-v3"

CHANGE = {
    "observed": "조건이 새로 확인됐습니다.",
    "maintained": "근거가 유지됐습니다.",
    "strengthened": "근거가 이전 분석보다 강해졌습니다.",
    "weakened": "근거가 이전 분석보다 약해졌습니다.",
    "invalidated": "기존 설명을 뒷받침하던 조건이 더 이상 충족되지 않습니다.",
    "expired": "근거의 확인 기간이 끝나 다시 확인이 필요합니다.",
}
FIELDS = {
    "currentPrice": ("가격", "price"), "priceChangeRate": ("전일 대비", "%"),
    "profitLossRate": ("평가 수익률", "%"), "ma5Distance": ("5일 평균 가격 대비", "%"),
    "ma20Distance": ("20일 평균 가격 대비", "%"), "ma60Distance": ("60일 평균 가격 대비", "%"),
    "tradeStrength": ("체결강도", ""), "volumeRatio": ("거래량 비율", "배"),
    "timeAdjustedVolumeRatio": ("시간 보정 거래량", "배"),
    "foreignNetVolume": ("외국인 순매수", "주"), "institutionNetVolume": ("기관 순매수", "주"),
    "individualNetVolume": ("개인 순매수", "주"), "bidAskImbalance": ("매수·매도 대기 물량 차이", "%"),
    "beta": ("시장 민감도", ""), "correlation": ("시장 상관계수", ""),
    "instrumentReturnPct": ("확인 이후 주가 등락률", "%"),
    "ma20DistanceChangePp": ("20일 평균 가격과의 거리 변화", "%p"),
}
OPERATORS = {">": "초과", ">=": "이상", "<": "미만", "<=": "이하", "=": "일치", "==": "일치", "!=": "불일치"}


def items(value):
    return [row for row in value or [] if isinstance(row, dict)] if isinstance(value, (list, tuple)) else []


def unique(values):
    return list(dict.fromkeys(value for value in values if value))


def measurement(field, value, currency):
    unit = FIELDS.get(field, ("", ""))[1]
    if numeric(value) is None:
        return str(value) if isinstance(value, (str, bool)) else "세부값 미기록"
    if unit == "price":
        return price_money(value, currency)
    return decimal(value, 0 if unit == "주" else 2, signed=unit in {"%", "%p"}) + unit


def condition_clause(field, operator, value, currency):
    """Read authored comparisons as Korean without changing their boundary."""
    number = numeric(value)
    if field == "instrumentReturnPct" and number is not None:
        if number > 0 and operator in {">", ">="}:
            return "확인 이후 주가 " + decimal(number) + "% " + OPERATORS[operator] + " 상승"
        if number < 0 and operator in {"<", "<="}:
            return "확인 이후 주가 " + decimal(abs(number)) + "% " + OPERATORS[{"<": ">", "<=": ">="}[operator]] + " 하락"
    return FIELDS[field][0] + " " + measurement(field, value, currency) + " " + OPERATORS.get(operator, operator)


def rule_conditions(rule, facts, currency):
    result = []
    for condition in items(rule.get("conditions")):
        field, actual = condition.get("field"), condition.get("observedValue")
        if field == "source" and actual == "holding":
            result.append("보유 종목 조건 확인")
        elif condition.get("modelSignalMatched"):
            result.append("분석 신호 조건 확인")
        elif field in FIELDS and not isinstance(actual, (dict, list)):
            expected = condition.get("expectedValue")
            threshold = condition_clause(field, str(condition.get("operator") or "="), expected, currency) if expected is not None else FIELDS[field][0] + " 기준값 미기록"
            result.append(threshold + " / 확인값 " + measurement(field, actual, currency))
        elif condition.get("label"):
            result.append(str(condition["label"]) + (" · 조건 확인" if condition.get("matchedByTypeDB") or condition.get("matched") else " · 세부값 미기록"))
    return unique(result)


def validation_rows(hypothesis, currency):
    """Keep the next check and numeric outcome contract beside its own claim."""
    result = []
    contract = hypothesis.get("claimContract") or {}
    opposing = hypothesis.get("falsificationContract") or contract.get("falsificationContract")
    checks = [opposing] if opposing else hypothesis.get("invalidationConditions") or []
    if checks:
        result.append("다음 확인: " + " · ".join(unique(next_check_words(value) for value in checks)))
    outcome = contract.get("outcomeContract") or {}
    horizons = outcome.get("outcomeHorizonMinutes") or outcome.get("horizonMinutes") or []
    horizon_text = " · ".join(
        decimal(value / 1440) + "일" if value >= 1440 and value % 1440 == 0 else decimal(value) + "분"
        for value in horizons if numeric(value) is not None and not isinstance(value, bool) and isinstance(value, (int, float)) and value > 0
    ) if isinstance(horizons, list) else ""
    criteria = []
    for criterion in items(outcome.get("criteria")):
        # Cause metrics and the complete formula stay in the expandable
        # evidence view. The alert keeps the actionable reading distinction:
        # what would agree with the prediction, and what would challenge it.
        label = {"result": "예상 결과 확인", "invalidation": "가설 재검토"}.get(criterion.get("role"))
        field = criterion.get("field") or criterion.get("metric")
        value = criterion.get("value", criterion.get("threshold"))
        if label and field in FIELDS and value is not None:
            horizon = numeric(criterion.get("horizonMinutes"))
            timing = decimal(horizon) + "분 뒤" if horizon is not None and horizon > 0 else ""
            criteria.append(label + ": " + condition_clause(field, str(criterion.get("operator") or "="), value, currency)
                            + (" (" + timing + ")" if timing else ""))
    if criteria:
        result.append("검증 기준" + (" (" + horizon_text + ")" if horizon_text else "") + " — " + "; ".join(unique(criteria)))
    elif horizon_text:
        result.append("검증 기간: " + horizon_text)
    return result


def linked_rules(hypothesis, rules):
    return [rule for rule in rules if rule.get("id") in (hypothesis.get("ruleIds") or [])]


def transition_hypotheses(transition, hypotheses):
    identities = transition.get("hypothesisIds") or []
    if identities:
        return [h for h in hypotheses if h.get("id") in identities]
    return [h for h in hypotheses if set(h.get("ruleIds") or []) & set(transition.get("sourceRuleIds") or [])]


def transition_sentence(transition, hypotheses):
    linked = transition_hypotheses(transition, hypotheses)
    topics = unique(hypothesis_words(h)[0] for h in linked)
    topic = " · ".join(topics)
    prefix = "‘" + topic + "’ — " if topic else ""
    state = transition.get("currentState")
    if state == "observed" and transition.get("previousState") in {"invalidated", "expired"}:
        return prefix + "이전에 해제되거나 만료됐던 조건이 다시 확인됐습니다."
    return prefix + CHANGE.get(state, "연결된 근거가 달라졌습니다.")


def hypothesis_sections(hypotheses, rules, facts, currency):
    sections = []
    for index, hypothesis in enumerate(hypotheses[:3]):
        topic, claim = hypothesis_words(hypothesis)
        rows = ["가능한 해석: " + claim]
        selected = linked_rules(hypothesis, rules)
        conditions = unique(value for rule in selected for value in rule_conditions(rule, facts, currency))
        rows.append("판정 규칙: " + (" · ".join(conditions) if conditions else "세부 조건값 미기록 · 원문은 상세 근거에서 확인"))
        # These are linked model inputs, not Python-authored thresholds or
        # proof that those inputs alone caused the native rule to match.
        fields = unique(key for rule in selected for condition in items(rule.get("conditions"))
                        for key in condition.get("measuredFactIds") or [] if key in FIELDS and key in facts
                        and key not in {"foreignNetVolume", "institutionNetVolume", "individualNetVolume"})
        if fields:
            rows.append("함께 본 수치: " + " · ".join(FIELDS[key][0] + " " + measurement(key, facts[key], currency) for key in fields))
        rows.extend(validation_rows(hypothesis, currency))
        qualification = hypothesis.get("qualification") or {}
        count = numeric(qualification.get("decisiveOutcomeCount"))
        status = {"shadow": "아직 검증 중", "observed": "관찰 결과 축적 중", "limited-active": "제한된 범위에서 검증 중",
                  "active": "저장된 검증 기준 충족", "quarantined": "검증 결과 재점검 중"}.get(qualification.get("status"), "검증 상태 미기록")
        rows.append("검증 상태: " + status + (" · 확인한 결과 " + decimal(count, 0) + "건" if count is not None else ""))
        if hypothesis.get("state") in {"blocked", "invalidated", "expired", "rejected"}:
            rows.append("현재 판단 근거로 사용 보류 · 조건 확인과 예측 검증은 구분합니다.")
        opposing = len(hypothesis.get("counterEvidenceIds") or []) + len(hypothesis.get("counterRuleIds") or [])
        if opposing:
            rows.append("반대 근거 " + str(opposing) + "건 연결 · 상세 근거에서 확인")
        title = "살펴볼 가설" + (" · " + topic if topic != "관찰 가설" else "")
        sections.append(("hypotheses" if index == 0 else "hypotheses-" + str(index + 1), title, rows))
    return sections


def readable_relation_change(packet):
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    facts = snapshot_facts(current)
    currency = str(facts.get("currency") or ("USD" if current.get("market") == "US" else "KRW"))
    hypotheses, rules = items(current.get("hypotheses")), items(current.get("rules"))
    # Ending relations may no longer be in the current hypothesis set. Use a
    # previous authored claim only for naming the change, never as current proof.
    named = hypotheses + [h for h in items(previous.get("hypotheses")) if h.get("id") not in {h.get("id") for h in hypotheses}]
    changes = unique(transition_sentence(row, named) for row in items(packet.get("transitions")))
    lead = " ".join(changes[:3]) or "새로운 근거 변화 없이 현재 기록을 확인합니다."
    market = {key: (title, rows) for key, title, rows in market_snapshot_sections(current, previous)}
    quote = [*market["current-price"][1], *market["price-trend"][1]]
    if market["holding"][1]:
        quote.append(" · ".join(market["holding"][1]))
    changed_ids = {h.get("id") for transition in items(packet.get("transitions"))
                   for h in transition_hypotheses(transition, hypotheses)}
    # Presentation order follows the notification's changes, never a Python
    # investment ranking. Otherwise an unchanged claim can hide the changed one.
    displayed = sorted(hypotheses, key=lambda h: h.get("id") not in changed_ids)
    stories = hypothesis_sections(displayed, rules, facts, currency)
    if not stories:
        stories = [("hypotheses", "이번에 확인한 내용", ["연결된 가설 설명이 기록되지 않았습니다.",
                    *[str(rule.get("label") or "규칙") + " — " + " · ".join(rule_conditions(rule, facts, currency)) for rule in rules[:3]]])]
    notes = []
    if not packet.get("baselineAvailable"):
        notes.append("이전 알림의 측정값이 없어 전후 비교는 미제공")
    if len(hypotheses) > 3:
        notes.append("추가 가설 " + str(len(hypotheses) - 3) + "개는 상세 근거에서 확인")
    notes.append("가설은 검증할 설명이며 매수·매도 의견이 아닙니다.")
    source = " + ".join(value.split(" /api/")[0].strip() for value in str(current.get("source") or "출처 미기록").split(" + "))
    notes.append("확인 시점·출처: " + clock(current.get("observedAt")) + " · " + source)
    notes.append("전체 근거: 가설 " + str(len(hypotheses)) + "개 · 규칙 " + str(len(rules)) + "개 · 측정 항목 " + str(len(current.get("facts") or [])) + "개")
    notes.append("다음 알림: 근거에 새로운 변화가 생기면 발송 간격을 확인해 알려드립니다.")
    return {"lead": lead, "sections": [
        ("current-price", "지금 확인한 시세", quote),
        *stories,
        ("investor-flow", "외국인·기관·개인", market["investor-flow"][1]),
        ("trading", "거래·체결·대기 주문", [*market["market-activity"][1], *market["execution-flow"][1]]),
        ("next-update", "자료 참고 · 다음 알림", notes),
    ]}
