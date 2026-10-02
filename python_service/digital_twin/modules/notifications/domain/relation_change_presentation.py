"""Explain frozen graph conclusions and measurements without adding market rules."""

from digital_twin.modules.notifications.domain.observation_market_snapshot import (
    clock, decimal, market_snapshot_sections, numeric, snapshot_facts,
)
from digital_twin.modules.notifications.domain.alert_formatting import price_money


PRESENTATION_VERSION = "readable-relation-change-v2"


CHANGE = {
    "observed": "관련 근거가 새로 확인됐습니다.",
    "maintained": "기존 근거가 유지됐습니다.",
    "strengthened": "뒷받침하는 근거가 강화됐습니다.",
    "weakened": "뒷받침하는 근거가 약해졌습니다.",
    "invalidated": "기존 가설을 유지할 조건이 깨졌습니다.",
    "expired": "기존 근거의 유효기간이 끝났습니다.",
}
STATES = {"observed": "처음 확인", "maintained": "근거 유지", "strengthened": "근거 강화",
          "weakened": "근거 약화", "invalidated": "가설 유지 조건 미충족", "expired": "근거 만료"}
FIELDS = {
    "currentPrice": ("가격", "price"), "priceChangeRate": ("전일 대비", "%"),
    "profitLossRate": ("평가 수익률", "%"), "ma5Distance": ("5일 평균 가격 대비", "%"),
    "ma20Distance": ("20일 평균 가격 대비", "%"), "ma60Distance": ("60일 평균 가격 대비", "%"),
    "tradeStrength": ("체결강도", ""), "volumeRatio": ("거래량 비율", "배"),
    "timeAdjustedVolumeRatio": ("시간 보정 거래량", "배"),
    "foreignNetVolume": ("외국인 순매수", "주"), "institutionNetVolume": ("기관 순매수", "주"),
    "individualNetVolume": ("개인 순매수", "주"), "bidAskImbalance": ("매수·매도 대기 물량 차이", "%"),
    "beta": ("시장 민감도", ""), "correlation": ("시장 상관계수", ""),
    "instrumentReturnPct": ("관측 이후 주가 등락률", "%"),
    "ma20DistanceChangePp": ("20일 평균 가격과의 거리 변화", "%p"),
}


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


def rule_conditions(rule, facts, currency):
    result = []
    for condition in items(rule.get("conditions")):
        field = condition.get("field")
        actual = condition.get("observedValue")
        if field == "source" and actual == "holding":
            result.append("보유 종목 조건 확인")
        elif condition.get("modelSignalMatched"):
            result.append("연결된 분석 신호 조건 확인")
            # Show inputs, never turn model evidence into invented raw thresholds.
            measurements = [FIELDS[key][0] + " " + measurement(key, facts[key], currency)
                            for key in condition.get("measuredFactIds") or []
                            if key in FIELDS and key in facts and key not in {"foreignNetVolume", "institutionNetVolume", "individualNetVolume"}]
            if measurements:
                result.append("참고 측정값: " + " · ".join(unique(measurements)))
        elif field in FIELDS and not isinstance(actual, (dict, list)):
            expected = condition.get("expectedValue")
            threshold = ((str(condition.get("operator") or "=") + " " + measurement(field, expected, currency))
                         if expected is not None else "기준값 미기록")
            result.append(FIELDS[field][0] + " " + threshold + " / 확인값 " + measurement(field, actual, currency))
        elif condition.get("label"):
            result.append(str(condition["label"]) + (" · 조건 확인" if condition.get("matchedByTypeDB") or condition.get("matched") else " · 세부값은 상세 근거에서 확인"))
    return unique(result)


def validation_rows(hypotheses, currency):
    result = []
    for hypothesis in hypotheses:
        contract = hypothesis.get("claimContract") or {}
        opposing = hypothesis.get("falsificationContract") or contract.get("falsificationContract")
        if opposing:
            result.append("다시 볼 조건: " + str(opposing))
        else:
            result.extend("다시 볼 조건: " + str(value) for value in hypothesis.get("invalidationConditions") or [])
        outcome = contract.get("outcomeContract") or {}
        horizons = outcome.get("outcomeHorizonMinutes") or outcome.get("horizonMinutes") or []
        if isinstance(horizons, list) and horizons:
            result.append("규칙에 저장된 검증 기간: " + " · ".join(
                decimal(value / 1440) + "일" if value >= 1440 and value % 1440 == 0 else decimal(value) + "분"
                for value in horizons if isinstance(value, (int, float)) and not isinstance(value, bool)))
        for criterion in items(outcome.get("criteria")):
            label = {"result": "결과 확인 기준", "invalidation": "반증 확인 기준", "cause": "원인 확인 기준"}.get(criterion.get("role"))
            field = criterion.get("field") or criterion.get("metric")
            value = criterion.get("value", criterion.get("threshold"))
            if label and field in FIELDS and value is not None:
                horizon = numeric(criterion.get("horizonMinutes"))
                suffix = " · " + decimal(horizon) + "분 뒤" if horizon is not None and horizon > 0 else ""
                result.append(label + ": " + FIELDS[field][0] + " " + str(criterion.get("operator") or "=") + " " + measurement(field, value, currency) + suffix)
    return unique(result)


def readable_relation_change(packet):
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    facts = snapshot_facts(current)
    currency = str(facts.get("currency") or ("USD" if current.get("market") == "US" else "KRW"))
    hypotheses, rules = items(current.get("hypotheses")), items(current.get("rules"))
    transitions = items(packet.get("transitions"))
    linked_ids = unique(identity for hypothesis in hypotheses for identity in hypothesis.get("ruleIds") or [])
    primary_rules = [rule for identity in linked_ids for rule in rules if rule.get("id") == identity]
    display_rules = primary_rules or [rule for rule in rules if not rule.get("referenceOnly")]
    change_sentences = unique(CHANGE.get(row.get("currentState"), "연결된 근거가 달라졌습니다.") for row in transitions)
    topic = str(display_rules[0].get("label") or "") if len(hypotheses) <= 1 and display_rules else ""
    lead = (topic + ": " if topic else "") + " ".join(change_sentences or ["연결된 근거의 현재 상태를 확인합니다."])
    transition_rows = unique(
        STATES.get(row.get("previousState"), "이전 상태 미기록") + " → " + STATES.get(row.get("currentState"), "상태 변경")
        for row in transitions)
    hypothesis_rows = []
    for hypothesis in hypotheses[:3]:
        claim = hypothesis.get("expectedOutcome") or hypothesis.get("claim") or hypothesis.get("label")
        hypothesis_rows.append("검토 내용: " + str(claim or "가설 설명 미기록"))
        qualification = hypothesis.get("qualification") or {}
        count = numeric(qualification.get("decisiveOutcomeCount"))
        status = {"shadow": "검증 자료 축적 중", "observed": "관찰 단계", "limited-active": "제한적 검증 단계",
                  "active": "저장된 검증 기준 충족", "quarantined": "검증 상태 재점검 중"}.get(qualification.get("status"))
        if status:
            hypothesis_rows.append("예측 검증: " + status + (" · 독립 결과 " + decimal(count, 0) + "건" if count is not None else ""))
    if hypotheses:
        hypothesis_rows.append("근거의 변화이며, 예상한 주가 흐름이 입증됐다는 뜻은 아닙니다.")
    rule_rows = []
    for rule in display_rules[:4]:
        rule_rows.append(str(rule.get("label") or "규칙 이름 미기록") + " — " + "; ".join(
            rule_conditions(rule, facts, currency) or ["세부 조건값은 이번 기록에서 확인되지 않습니다."]))
    remaining = len(rules) - len(display_rules[:4])
    if remaining > 0:
        rule_rows.append("추가 규칙 " + str(remaining) + "개는 상세 근거에 있습니다.")
    limits = []
    if not packet.get("baselineAvailable"):
        limits.append("이전 알림의 측정값이 없어 전후 수치 비교는 제공하지 않습니다.")
    opposing = sum(len(item.get("counterEvidenceIds") or []) + len(item.get("counterRuleIds") or []) for item in hypotheses)
    limits.append("반대 근거 " + str(opposing) + "건이 연결돼 있습니다. 상세 근거를 함께 확인하세요." if opposing else "반대 근거가 이번 기록에 포함되지 않았습니다. 반대 근거가 없다는 뜻은 아닙니다.")
    limits.append("이 알림은 근거의 변화를 설명하며 투자 행동을 제안하지 않습니다.")
    checks = validation_rows(hypotheses[:3], currency)
    source = str(current.get("source") or "출처 미기록")
    # Provider names are useful; internal endpoint paths are not an explanation.
    source = " + ".join(value.split(" /api/")[0].strip() for value in source.split(" + "))
    return {"lead": lead, "sections": [
        ("change", "무엇이 달라졌나", transition_rows),
        ("hypotheses", "관찰 가설 · 검증할 설명", hypothesis_rows or ["가설 설명이 이번 기록에 포함되지 않았습니다."]),
        *market_snapshot_sections(current, previous),
        ("rules", "가설과 연결된 판정 규칙", rule_rows or ["연결된 규칙의 세부 근거 미기록"]),
        ("verification", "이 가설을 다시 볼 조건", checks),
        ("provenance", "확인 시점과 출처", [clock(current.get("observedAt")) + " · " + source, "각 자료의 집계 시각은 항목별로 표시합니다."]),
        ("limitations", "반대 근거와 한계", limits),
        ("next-update", "다음 알림", ["새로운 근거 변화가 확인되면 중복·발송 간격을 확인해 알려드립니다. AI 관찰은 별도로 진행됩니다."]),
        ("detail", "전체 근거", ["가설 " + str(len(hypotheses)) + "개 · 규칙 " + str(len(rules)) + "개 · 측정 항목 " + str(len(current.get("facts") or [])) + "개. 전체 비교는 상세에서 확인할 수 있습니다."]),
    ]}
