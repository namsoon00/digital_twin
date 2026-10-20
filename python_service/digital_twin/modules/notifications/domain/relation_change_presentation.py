"""Explain frozen graph conclusions and measurements without adding market rules."""

from digital_twin.modules.notifications.domain.observation_market_snapshot import (
    clock, decimal, market_snapshot_sections, numeric, observed, snapshot_facts, source_note, stage,
)
from digital_twin.modules.notifications.domain.alert_formatting import price_money
from digital_twin.modules.notifications.domain.relation_observation_language import hypothesis_words, next_check_words
from digital_twin.modules.notifications.domain.relation_observation_proof import model_evidence_paragraphs
from digital_twin.modules.notifications.domain.rulebox_decision_presentation import decision_brief_presentation, threshold


PRESENTATION_VERSION = "readable-relation-change-v17"

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
    "positionAccountWeight": ("계좌 전체 대비 비중", "%"), "positionWeight": ("보유 종목 평가액 내 비중", "%"),
    "ma20Distance": ("20일 평균 가격 대비", "%"), "ma60Distance": ("60일 평균 가격 대비", "%"),
    "tradeStrength": ("체결강도", ""), "volumeRatio": ("거래량 비율", "배"),
    "timeAdjustedVolumeRatio": ("시간 보정 거래량", "배"),
    "foreignNetVolume": ("외국인 순매수", "주"), "institutionNetVolume": ("기관 순매수", "주"),
    "individualNetVolume": ("개인 순매수", "주"), "bidAskImbalance": ("매수·매도 대기 물량 차이", "%"),
    "beta": ("시장 민감도", ""), "correlation": ("시장 상관계수", ""),
    "instrumentReturnPct": ("확인 이후 주가 등락률", "%"),
    "ma20DistanceChangePp": ("20일 평균 가격과의 거리 변화", "%p"),
    "excessReturnPct": ("같은 기간 비교 기준 대비 수익률 차이", "%p"),
}
OPERATORS = {">": "초과", ">=": "이상", "<": "미만", "<=": "이하", "=": "일치", "==": "일치", "!=": "불일치"}


def items(value):
    return [row for row in value or [] if isinstance(row, dict)] if isinstance(value, (list, tuple)) else []


def unique(values):
    return list(dict.fromkeys(value for value in values if value))


def model_score(value):
    rounded = round(value, 4)
    return ("약 " if rounded != value else "") + format(rounded, ".4f").rstrip("0").rstrip(".")


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
    if field == "excessReturnPct" and number is not None:
        if number < 0 and operator in {"<", "<="}:
            return "같은 기간 비교 기준보다 수익률이 " + decimal(abs(number)) + "%p " + OPERATORS[{"<": ">", "<=": ">="}[operator]] + " 낮음"
        if number > 0 and operator in {">", ">="}:
            return "같은 기간 비교 기준보다 수익률이 " + decimal(number) + "%p " + OPERATORS[operator] + " 높음"
    return FIELDS[field][0] + " " + measurement(field, value, currency) + " " + OPERATORS.get(operator, operator)


def rule_conditions(rule, facts, currency):
    result = []
    for condition in items(rule.get("conditions")):
        field, actual = condition.get("field"), condition.get("observedValue")
        if field == "source" and actual == "holding":
            result.append("보유 종목 조건 확인")
        elif isinstance(actual, dict) and actual.get("avoidAveragingDown") is True:
            result.append("종목 관리 설정: 손실 상태의 추가매수 제한 사용")
        elif condition.get("modelSignalMatched"):
            result.append("분석 신호 조건 확인")
        elif field in FIELDS and not isinstance(actual, (dict, list)):
            expected = threshold(condition, facts).get("value")
            clause = condition_clause(field, str(condition.get("operator") or "="), expected, currency) if expected is not None else FIELDS[field][0] + " 기준값 미기록"
            result.append(clause + " / 확인값 " + measurement(field, actual, currency))
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
            benchmark = criterion.get("benchmarkSymbol") or criterion.get("benchmark_symbol")
            basis = " (비교 대상: " + ("사전에 지정한 시장 기준" if benchmark == "$MARKET" else str(benchmark)) + ")" if field == "excessReturnPct" and benchmark else ""
            criteria.append(label + ": " + condition_clause(field, str(criterion.get("operator") or "="), value, currency)
                            + basis + (" (" + timing + ")" if timing else ""))
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
        matched = [h for h in hypotheses if h.get("id") in identities]
        if matched:
            return matched
    return [h for h in hypotheses if set(h.get("ruleIds") or []) & set(transition.get("sourceRuleIds") or [])]


def transition_sentence(transition, hypotheses):
    if transition.get("changeCategory") == "reassessment":
        return "분석 조건을 재평가했습니다. 새 시세 변동이나 시장의 악화·개선이 확인됐다는 뜻은 아닙니다."
    linked = transition_hypotheses(transition, hypotheses)
    topics = unique(hypothesis_words(h)[0] for h in linked)
    topic = " · ".join(topics)
    prefix = "‘" + topic + "’에 대해서는 " if topic else ""
    if transition.get("changeCategory") == "data-availability":
        sources = "·".join(unique(row.get("label") for row in items(transition.get("dataIssues")))) or "원천"
        return prefix + (sources + " 자료를 다시 사용할 수 있습니다." if transition.get("changeKind") == "data-restored"
                         else "유효한 " + sources + " 자료를 확인하지 못해 근거 유지 여부를 확인할 수 없습니다.")
    state = transition.get("currentState")
    if state == "observed" and transition.get("previousState") in {"invalidated", "expired"}:
        return prefix + "이전에 해제되거나 만료됐던 조건이 다시 확인됐습니다."
    return prefix + CHANGE.get(state, "연결된 근거가 달라졌습니다.")


def model_input_change_rows(comparison):
    """Explain actual stored model inputs, without inventing raw rule thresholds."""
    if comparison.get("comparison") != "verified":
        return []
    older, newer = comparison.get("previous") or {}, comparison.get("current") or {}
    before, after = older.get("modelInputWindows") or {}, newer.get("modelInputWindows") or {}
    fields = (("recentReturn", "후반 등락률", "%"), ("priceReturn", "전체 등락률", "%"),
              ("drawdown", "고점 대비 등락률", "%"), ("velocityChange", "앞 구간 대비 후반 등락률 변화", "%p"))
    result = []
    for window in sorted(set(before) & set(after)):
        old, new = before[window], after[window]
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        # Zero-filled short windows are not measured market changes.
        if any((numeric(row.get("sampleCount")) or 0) < 2 or row.get("stale") is True for row in (old, new)):
            continue
        for field, label, unit in fields:
            previous_value, current_value = numeric(old.get(field)), numeric(new.get(field))
            if previous_value is None or current_value is None or previous_value == current_value:
                continue
            result.append("규칙에 사용한 " + str(window).replace("D", "일") + " 구간 " + label + ": 직전 분석 "
                          + decimal(previous_value * 100, signed=True) + unit + " → 이번 "
                          + decimal(current_value * 100, signed=True) + unit)
            if len(result) == 2:
                return result
    return result


def rule_change_sentence(transition, comparison):
    label = str(comparison.get("label") or "연결 규칙").replace("->", "→")
    if label.startswith(("graph.", "rule:", "inference-")):
        label = "해당 가설의 연결 조건"
    # Show the authored condition name, not the stock prefix or bridge suffix.
    if "→" in label:
        label = label.split("→", 1)[0].split(" · ")[-1].strip()
    if label.startswith("보유 종목 + "):
        label = label[len("보유 종목 + "):]
    delta = transition.get("evidenceDelta") or {}
    rule_id = comparison.get("ruleId")
    changes = [r for r in items(transition.get("evidenceChanges")) if (r.get("evidence") or {}).get("ruleId") == rule_id]
    roles = {r.get("role") for r in changes}
    role = "반대 근거" if roles == {"counter"} else "지지 근거" if roles == {"support"} else "판단 근거"
    if not roles and delta.get("addedSupportingEvidenceKeys") and not delta.get("addedCounterEvidenceKeys"):
        role = "지지 근거"
    if rule_id in (delta.get("addedRuleIds") or []):
        meaning = "규칙이 가설의 " + role + "에 추가됐습니다."
    elif rule_id in (delta.get("removedRuleIds") or []):
        meaning = "규칙이 가설의 " + role + "에서 빠졌습니다."
    elif comparison.get("comparison") == "unavailable":
        meaning = "규칙의 현재 자료를 확인하지 못했습니다. 조건 해제와 구분해야 합니다."
    elif transition.get("currentState") == "invalidated":
        meaning = "규칙으로 뒷받침하던 조건이 더 이상 충족되지 않습니다."
    else:
        if len(changes) == 1:
            meaning = "규칙이 가설의 " + role + ("로 추가됐습니다." if changes[0].get("change") == "added" else "에서 해제됐습니다.")
        else:
            meaning = "규칙의 근거 변화가 확인됐습니다."
    return "‘" + label + "’ " + meaning


def current_input_rows(comparison):
    """Missing prior input is not a zero or a newly crossed price boundary."""
    current = comparison.get("current") or {}
    groups = {}
    for window, row in (current.get("modelInputWindows") or {}).items():
        if not isinstance(row, dict) or (numeric(row.get("sampleCount")) or 0) < 2 or row.get("stale"):
            continue
        value = numeric(row.get("priceReturn"))
        if value is not None:
            at = clock(row.get("latestObservedAt") or current.get("observedAt"))
            groups.setdefault(at, []).append(str(window).replace("D", "일") + " " + decimal(value * 100, signed=True)
                                              + "% (" + decimal(row.get("sampleCount"), 0) + "건)")
    return ["규칙에 제공된 기간별 등락: " + " · ".join(values[:4]) + " · 기준 " + at
            + (" · 나머지 기간 입력은 상세 근거에 보존" if len(values) > 4 else "")
            for at, values in list(groups.items())[:2]]


def delivery_cause_rows(packet, currency):
    """Explain only the conditions attached to the admitted transition."""
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    facts, before = snapshot_facts(current), snapshot_facts(previous)
    result, measurements = [], []
    for transition in items(packet.get("transitions")):
        if transition.get("changeCategory") == "data-availability":
            change = transition.get("dataAvailabilityChange") or {}
            result.extend(change.get("reasons") or [])
            result.append("자료의 사용 가능 여부를 안내합니다. 가설의 강화·약화로 해석하지 않습니다.")
        basis = transition.get("changeBasis") or {}
        if transition.get("changeCategory") == "reassessment":
            result.append(str(basis.get("reason") or "분석에 사용한 기간·입력 구성이 달라졌습니다."))
        comparisons = items(basis.get("ruleComparisons"))
        changed_rule_ids = {row.get("ruleId") for row in comparisons}
        changed_rule_ids.update((row.get("evidence") or {}).get("ruleId") for row in items(transition.get("evidenceChanges")))
        changed_rule_ids.update((transition.get("evidenceDelta") or {}).get("addedRuleIds") or [])
        changed_rule_ids.update((transition.get("evidenceDelta") or {}).get("removedRuleIds") or [])
        changed_rule_ids.discard(None)
        explained_rules = set()
        for comparison in comparisons:
            older, newer = comparison.get("previous") or {}, comparison.get("current") or {}
            old_score, new_score = numeric(older.get("score")), numeric(newer.get("score"))
            label = comparison.get("label") or "연결 규칙"
            result.append(rule_change_sentence(transition, comparison))
            explained_rules.add(comparison.get("ruleId"))
            input_changes = model_input_change_rows(comparison)
            result.extend(input_changes)
            if not older:
                result.extend(current_input_rows(comparison))
                result.append("이 규칙의 이전 입력값이 저장되지 않아 수치의 전후 비교는 미제공합니다.")
            elif old_score is not None and old_score != new_score and not input_changes:
                text = "모델 점수 직전 분석 " + model_score(old_score)
                current_score = ("원천 조건 불성립으로 점수 미생성" if newer.get("status") == "not-supported" else "미확인")
                text += " → 이번 " + (model_score(new_score) if new_score is not None else current_score)
                minimum = numeric(newer.get("minimumScore", older.get("minimumScore")))
                if minimum is not None:
                    text += " · 규칙 기준 " + decimal(minimum, 2) + " 이상"
                text += " (확률 아님)"
                result.append(text)
                source_before = (older.get("sourceObservation") or {}).get("observedAt")
                source_after = (newer.get("sourceObservation") or {}).get("observedAt")
                if source_before or source_after:
                    result.append("모델 입력의 원천 기준: 직전 분석 " + clock(source_before) + " → 이번 " + clock(source_after))
            if transition.get("changeCategory") == "reassessment":
                for window in ("5D", "20D"):
                    old_window = (older.get("modelInputWindows") or {}).get(window) or {}
                    new_window = (newer.get("modelInputWindows") or {}).get(window) or {}
                    old_return, new_return = numeric(old_window.get("recentReturn")), numeric(new_window.get("recentReturn"))
                    if old_return is not None and new_return is not None and old_return != new_return:
                        result.append(window.replace("D", "일") + " 계산 구간의 후반 등락률: "
                                      + decimal(old_return * 100, signed=True) + "% → " + decimal(new_return * 100, signed=True)
                                      + "% · 분석 구간 비교이며 이번 시세 등락이 아닙니다.")
        if transition.get("changeCategory") == "data-availability":
            continue
        for row in items(transition.get("evidenceChanges")):
            evidence = row.get("evidence") or {}
            if evidence.get("ruleId") in explained_rules:
                continue
            role = "반대 근거" if row.get("role") == "counter" else "지지 근거"
            verb = "추가" if row.get("change") == "added" else "해제"
            field = evidence.get("field")
            if field in FIELDS and evidence.get("observedValue") is not None:
                result.append(role + " " + verb + ": " + FIELDS[field][0] + " "
                              + measurement(field, evidence["observedValue"], currency))
            else:
                result.append(role + " " + verb + ": " + str(evidence.get("label") or "연결 규칙")
                              + " · 상세 측정값 비교는 미보존")
        for rule in items(current.get("rules")):
            if rule.get("id") not in (transition.get("sourceRuleIds") or []):
                continue
            if changed_rule_ids and rule.get("id") not in changed_rule_ids:
                continue
            for condition in items(rule.get("conditions")):
                field, actual = condition.get("field"), condition.get("observedValue")
                if field not in FIELDS or actual is None or isinstance(actual, (dict, list)):
                    continue
                if field in before and before[field] != actual:
                    row = FIELDS[field][0] + ": 이전 알림 " + measurement(field, before[field], currency) + " → 이번 " + measurement(field, actual, currency)
                    expected = threshold(condition, snapshot_facts(current)).get("value")
                    if expected is not None:
                        row += " · 판정 기준 " + condition_clause(field, str(condition.get("operator") or "="), expected, currency)
                    elif condition.get("expectedValue") is not None:
                        row += " · 실제 적용 기준값 미보존"
                    source_stage = ("ccnl" if field == "tradeStrength" else "investor" if field.endswith("NetVolume")
                                    else "orderbook" if field == "bidAskImbalance" else "volume" if "Volume" in field or field == "volumeRatio" else "price")
                    stamp = condition.get("observedAt") or (current.get("observedAt") if source_stage == "price"
                                                            else stage(current, source_stage).get("sourceAsOf"))
                    if stamp:
                        row += " · 확인 " + clock(stamp)
                    measurements.append(row)
    if not result and packet.get("transitions"):
        if not measurements:
            result.append("가설의 성립 상태 변화는 확인됐지만, 그 변화를 일으킨 측정값의 전후 비교는 저장되지 않았습니다.")
    return unique([*result, *measurements])[:6]


def hypothesis_sections(hypotheses, rules, facts, currency, snapshot):
    sections = []
    for index, hypothesis in enumerate(hypotheses[:3]):
        topic, claim = hypothesis_words(hypothesis)
        statement = "‘" + claim.rstrip(". ") + "’을 살펴보는 가설입니다."
        selected = linked_rules(hypothesis, rules)
        conditions = unique(value for rule in selected for value in rule_conditions(rule, facts, currency))
        # These are linked model inputs, not Python-authored thresholds or
        # proof that those inputs alone caused the native rule to match.
        fields = unique(key for rule in selected for condition in items(rule.get("conditions"))
                        for key in condition.get("measuredFactIds") or [] if key in FIELDS and key in facts)
        investor_fields = {"foreignNetVolume", "institutionNetVolume", "individualNetVolume"}
        values = {key: observed(snapshot, key, "investor") if key in investor_fields else numeric(facts[key]) for key in fields}
        investor_source = stage(snapshot, "investor")
        participant_states = investor_source.get("participantStatus") or snapshot.get("investorFlowParticipantStatus") or {}
        unavailable = {"not-yet-published", "unsupported", "unsupported-market", "missing", "unavailable"}
        for key in investor_fields.intersection(values):
            if investor_source.get("status") in unavailable or participant_states.get(key.replace("NetVolume", "")) in unavailable:
                values[key] = None
        fields = [key for key in fields if values[key] is not None]
        matched = []
        for label, sentence in (("보유 종목 조건 확인", "보유 종목이라는 조건"), ("분석 신호 조건 확인", "연결된 분석 신호의 조건")):
            if label in conditions:
                matched.append(sentence)
                conditions.remove(label)
        explanation = "규칙의 비교 기준과 실제 값은 " + "; ".join(conditions) + "입니다." if conditions else ""
        if fields:
            readings = []
            for key in fields:
                label, value = FIELDS[key][0], values[key]
                if key in investor_fields:
                    label = label.replace("순매수", "순매도" if value < 0 else "순매수·매도 차이" if value == 0 else "순매수")
                    value = abs(value)
                readings.append(label + " " + measurement(key, value, currency))
            explanation += " 이 가설을 연결한 규칙의 입력 수치는 " + ", ".join(readings) + "입니다."
        if investor_fields.intersection(fields):
            explanation += " 투자자별 집계는 " + source_note(snapshot, "investor") + " 기준입니다."
        proof = model_evidence_paragraphs(selected, str(snapshot.get("symbol") or ""), currency, compact=True)
        # Keep all limitations even when several windows share this claim.
        # Document normalization has an eight-row limit per section.
        rows = [statement]
        # A window is one coherent observation. Avoid a page-long paragraph
        # joining daily, multi-day and session measurements with different clocks.
        bounded_proof = proof[:2] + ([" ".join(proof[2:])] if len(proof) > 2 else [])
        rows.extend([value for value in [explanation.strip(), *bounded_proof] if value]
                    or ["규칙에 사용된 세부 측정값이 저장되지 않아 가설이 나온 배경을 수치로 확인하기는 어렵습니다."])
        checks = validation_rows(hypothesis, currency)
        if checks:
            rows.append(". ".join(checks) + ".")
        qualification = hypothesis.get("qualification") or {}
        count = numeric(qualification.get("decisiveOutcomeCount"))
        status = {"shadow": "아직 검증 중", "observed": "관찰 결과 축적 중", "limited-active": "제한된 범위에서 검증 중",
                  "active": "저장된 검증 기준 충족", "quarantined": "검증 결과 재점검 중"}.get(qualification.get("status"), "검증 상태 미기록")
        validation = "검증 상태: " + status + (" · 확인한 결과 " + decimal(count, 0) + "건" if count is not None else "") + "."
        pending_ids = {h.get("id") for row in (snapshot.get("decisionBrief") or {}).get("candidates", [])
                       for h in row.get("hypotheses", []) if not h.get("executionEligible")
                       and (snapshot.get("decisionBrief") or {}).get("qualificationPending")}
        if hypothesis.get("id") in pending_ids:
            validation = ""  # The named qualification and remaining criteria are above.
        if hypothesis.get("state") in {"blocked", "invalidated", "expired", "rejected"}:
            validation += " 현재 판단 근거로 사용 보류 상태입니다."
        reasons = unique(rule.get("freshnessGateReason") for rule in selected if rule.get("evidenceUsableForJudgement") is False)
        if reasons:
            validation += " 자료 제한 사유: " + " ".join(reasons)
        opposing = len(hypothesis.get("counterEvidenceIds") or []) + len(hypothesis.get("counterRuleIds") or [])
        if opposing:
            validation += " 반대 근거 " + str(opposing) + "건이 연결돼 있으며 상세 근거에서 확인할 수 있습니다."
        rows.append(validation)
        title = "살펴볼 가설" + (" · " + topic if topic != "관찰 가설" else "")
        sections.append(("hypotheses" if index == 0 else "hypotheses-" + str(index + 1), title, rows))
    return sections


def market_sentence(current, currency):
    """Summarize reported quote and holding return without deriving an opinion."""
    facts = snapshot_facts(current)
    price, change = numeric(facts.get("currentPrice")), observed(current, "priceChangeRate", "price")
    parts = []
    if price is not None and price > 0:
        parts.append("확인된 주가는 " + price_money(price, currency)
                     + ("(전일 대비 " + decimal(change, signed=True) + "%)" if change is not None else "") + "입니다.")
    if (numeric(facts.get("quantity")) or 0) > 0 or facts.get("isHolding") is True:
        pnl = numeric(facts.get("profitLossRate"))
        parts.append("내 보유 평가 수익률은 " + decimal(pnl, signed=True) + "%입니다." if pnl is not None else "내 보유 평가 수익률은 이번 자료에서 확인되지 않았습니다.")
    if facts.get("freshnessStatus") in {"stale", "expired"}:
        parts.append("시세가 오래돼 당시 수치로 참고해야 합니다.")
    return " ".join(parts)


def readable_relation_change(packet):
    transitions = items(packet.get("transitions"))
    if transitions and all(row.get("changeCategory") == "data-availability" for row in transitions):
        from digital_twin.modules.notifications.domain.relation_follow_up_presentation import data_follow_up_summary
        return data_follow_up_summary(packet)
    current, previous = packet.get("current") or {}, packet.get("previous") or {}
    facts = snapshot_facts(current)
    currency = str(facts.get("currency") or ("USD" if current.get("market") == "US" else "KRW"))
    hypotheses, rules = items(current.get("hypotheses")), items(current.get("rules"))
    # Ending relations may no longer be in the current hypothesis set. Use a
    # previous authored claim only for naming the change, never as current proof.
    named = hypotheses + [h for h in items(previous.get("hypotheses")) if h.get("id") not in {h.get("id") for h in hypotheses}]
    changes = unique(transition_sentence(row, named) for row in items(packet.get("transitions")))
    lead = " ".join(part for part in (" ".join(changes[:3]) or "새로운 근거 변화 없이 현재 기록을 확인합니다.", market_sentence(current, currency)) if part)
    decision = decision_brief_presentation(packet)
    from .rule_event_presentation import customer_event_summary
    events = packet.get("customerEvents") or []
    event_summary = customer_event_summary(events, current.get("decisionBrief")) if events else {}
    policy_only = bool(events) and all(e.get("purpose") in {"management-policy", "execution-constraint"} for e in events)
    if events and all(e.get("outcome") for e in events):
        parent = (packet.get("followUp") or {}).get("parentJobId") or ""
        result_rows = []
        for event in events:
            for criterion in items((event["outcome"].get("payload") or {}).get("criterionAssessments")):
                field = criterion.get("metric") or ""
                result_rows.append(condition_clause(field, criterion.get("operator"), criterion.get("threshold"), currency)
                    + " · 실제 " + measurement(field, criterion.get("observedValue"), currency)
                    + " · " + {"passed": "충족", "failed": "미충족", "unknown": "확인 불가"}.get(criterion.get("state"), "상태 미기록"))
        return {"lead": event_summary["lead"], "title": "가설 확인 결과", "compactFollowUp": True,
                "sections": [("previous-alert", "이전 알림의 확인 결과", ["원본 알림 N-" + parent[:8].upper()]),
                             *event_summary["sections"], ("outcome-evidence", "기준과 확인된 결과", result_rows),
                             ("next-update", "이후 확인", ["정해진 다른 구간의 결과가 남아 있으면 확인 후 이어서 안내합니다. 이번 결과만으로 원인·결과 관계가 입증되지는 않습니다."])]}
    causes = delivery_cause_rows(packet, currency)
    if decision:
        headline = causes[0] if causes and causes[0].startswith("‘") else " ".join(changes[:2])
        lead = " ".join(value for value in (headline, decision["lead"], market_sentence(current, currency)) if value)
        if causes and causes[0] == headline:
            causes = causes[1:]
    if event_summary:
        lead = event_summary["lead"] + " " + market_sentence(current, currency)
        if policy_only and decision.get("lead"):
            lead = decision["lead"] + " " + market_sentence(current, currency)
        elif decision.get("lead"):
            event_summary["sections"][1][2].append(decision["lead"])
        # The lead owns the claim's meaning; every changed rule stays in the
        # numeric evidence section, including counter-evidence and endings.
        causes = delivery_cause_rows(packet, currency)
        for event in events:
            if event.get("conditions"):
                causes.extend(rule_conditions({"conditions": event["conditions"]}, facts, currency))
    market = {key: (title, rows) for key, title, rows in market_snapshot_sections(current, previous)}
    quote = [*market["current-price"][1], *market["price-trend"][1]]
    changed_ids = {h.get("id") for transition in items(packet.get("transitions"))
                   for h in transition_hypotheses(transition, hypotheses)}
    # Presentation order follows the notification's changes, never a Python
    # investment ranking. Otherwise an unchanged claim can hide the changed one.
    displayed = sorted(hypotheses, key=lambda h: h.get("id") not in changed_ids)
    if changed_ids:
        displayed = [h for h in displayed if h.get("id") in changed_ids]
    if policy_only:
        displayed = []
    stories = hypothesis_sections(displayed, rules, facts, currency, current)
    if policy_only:
        stories = [("policy-context", "가설과 매매 판단의 범위", [
            "이번 알림은 관리 조건의 변화입니다. 가설 " + str(len(hypotheses))
            + "개의 기간별 분석은 상세 근거에 보존했습니다. 가격 전망에 따른 매매 의견과 계정 정책에 따른 관리 대응은 구분합니다."])]
    if not stories and not policy_only:
        context_only = events and all(e.get('contextState') for e in events)
        stories = [("hypotheses", "이번에 확인한 내용", [
                    '예측 가설을 생성하지 않는 시장 관측입니다.' if context_only else "연결된 가설 설명이 기록되지 않았습니다.",
                    *[str(rule.get("label") or "규칙") + " — " + " · ".join(rule_conditions(rule, facts, currency)) for rule in rules[:3]]])]
    notes = []
    if not packet.get("baselineAvailable"):
        notes.append("이전 알림의 측정값이 없어 전후 비교는 미제공")
    if not policy_only and len(hypotheses) > 3:
        notes.append("추가 가설 " + str(len(hypotheses) - 3) + "개는 상세 근거에서 확인")
    if current.get("evidenceComparisonPartial"):
        notes.append("일부 근거의 의미를 연결하지 못해 전후 비교가 제한됩니다. 해당 근거의 개수는 강화·약화 판정에 사용하지 않았습니다.")
    notes.append("가설 검증 수치는 과거 결과이며 이번 예측의 성공 확률이 아닙니다."
                 if decision else "가설은 검증할 설명이며 매수·매도 의견이 아닙니다.")
    source = " + ".join(value.split(" /api/")[0].strip() for value in str(current.get("source") or "출처 미기록").split(" + "))
    notes.append("확인 시점·출처: " + clock(current.get("observedAt")) + " · " + source)
    notes.append("전체 근거: 가설 " + str(len(hypotheses)) + "개 · 규칙 " + str(len(rules)) + "개 · 측정 항목 " + str(len(current.get("facts") or [])) + "개")
    notes.append("다음 알림: 근거에 새로운 변화가 생기면 발송 간격을 확인해 알려드립니다.")
    parent = (packet.get("followUp") or {}).get("parentJobId")
    return {"lead": lead, "title": event_summary.get("title"), "sections": [
        *(([("previous-alert", "이전 알림의 후속 변화", ["원본 알림 N-" + parent[:8].upper()])]) if parent else []),
        *(decision.get("sections", []) if policy_only else []),
        *event_summary.get("sections", []),
        ("delivery-cause", "이번에 달라진 근거 · 규칙 데이터", causes),
        *([] if policy_only else decision.get("sections", [])),
        *stories,
        ("holding", "내 보유 상황 · 시세 등락과 구분", [" · ".join(market["holding"][1])] if market["holding"][1] else []),
        ("current-price", "현재 전체 데이터 · 시세와 가격 흐름", quote),
        ("investor-flow", "외국인·기관·개인", market["investor-flow"][1]),
        ("trading", "체결강도와 대기 주문", market["execution-flow"][1]),
        ("volume", "거래량과 시간 보정", market["market-activity"][1]),
        ("next-update", "자료 참고 · 다음 알림", notes),
    ]}
