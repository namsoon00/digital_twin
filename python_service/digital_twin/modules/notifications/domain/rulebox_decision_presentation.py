"""Readable restrictions and candidates from the frozen decision brief."""

from digital_twin.modules.decisions.contracts import resolved_rule_threshold
from digital_twin.modules.notifications.domain.observation_market_snapshot import decimal, numeric, snapshot_facts
from digital_twin.modules.notifications.domain.alert_formatting import price_money


LABELS = {"BUY": "신규 매수", "ADD": "추가매수", "HOLD": "보유 유지",
          "TRIM": "일부 축소", "SELL": "전량 매도", "AVOID": "매수 회피"}


def threshold(condition, facts):
    # Old receipts may lack the newly captured resolution. Their own frozen
    # facts are sufficient; never consult today's account policy or catalog.
    saved = condition.get("resolvedThreshold")
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
        lead.append("관리 원칙상 " + "·".join(blocked) + "는 현재 판단 대상에서 제외됩니다.")
    if candidates:
        lead.append("검토할 대응은 " + "·".join(candidates) + "입니다. 최종 매매 판단은 아직 확정되지 않았습니다.")
    else:
        lead.append("추가 자료 확인이 필요하며, 현재 보유 유지나 매매를 권하는 결론은 없습니다.")
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
        same_threshold = (not resolved.get("field") or numeric(old.get(resolved["field"])) == value)
        if prior is not None and same_threshold and prior <= value and actual <= value:
            delta = actual - prior
            basis.append("이전 알림에서도 손실 기준을 벗어난 상태였습니다. "
                         + ("이후 손실이 " + decimal(abs(delta)) + "%p " + ("확대됐습니다." if delta < 0 else "줄었습니다.")
                            if delta else "이번 평가 수익률은 같습니다."))
        recheck = "손실 조건 재확인: 평가 수익률이 " + decimal(value, signed=True) + ("% 초과" if operator == "<=" else "% 이상") + "로 회복하는지 봅니다."
        average, price = numeric(facts.get("averagePrice")), numeric(facts.get("currentPrice"))
        # Only translate a threshold into price when the captured P/L is
        # demonstrably on that price/cost basis. FX/account-adjusted returns
        # must not produce a misleading target or stop price.
        if (average and average > 0 and price and price > 0 and value > -100
                and abs((price / average - 1) * 100 - actual) < .005):
            currency = facts.get("currency") or ("USD" if current.get("market") == "US" else "KRW")
            recheck += " 현재 평균매입가가 같다면 기준 가격은 약 " + price_money(average * (1 + value / 100), currency) + "입니다."
        rechecks.append(recheck + " 이 조건의 해소만으로 추가매수가 허용되는 것은 아닙니다.")
    weight, cap = numeric(facts.get("positionWeight")), numeric(facts.get("strategyMaxPositionWeightPct"))
    if weight is not None and cap is not None and cap > 0 and (numeric(facts.get("quantity")) or 0) > 0:
        basis.append("계좌 비중 " + decimal(weight) + "% / 설정 상한 " + decimal(cap)
                     + "% · " + ("상한 이내" if weight <= cap else "상한 초과") + " (손실 기준과 별도)")
    reasons = []
    if brief.get("qualificationPending"):
        counts = _unique(decimal(h["outcomeCount"], 0) + "건" for row in brief.get("candidates", [])
                         for h in row.get("hypotheses", []) if not h.get("executionEligible") and h.get("outcomeCount") is not None)
        reasons.append("매매 확정이 남은 이유: 후보를 뒷받침하는 가설이 실행 판단에 필요한 검증 기준을 아직 충족하지 못했습니다."
                       + (" 연결 가설별 확인 결과 " + "·".join(counts) + "." if counts else ""))
    if brief.get("comparisonRequired"):
        reasons.append("서로 다른 대응을 뒷받침하는 근거가 함께 있어 대안 비교가 남아 있습니다.")
    if brief.get("checks") and candidates:
        reasons.append("추가 확인 요청은 보유 유지 의견으로 계산하지 않았습니다. 후보가 하나여도 자동으로 최종 판단이 되지는 않습니다.")
    if not reasons:
        reasons.append("현재 결과는 규칙의 제한과 검토 후보입니다. 매매 시점·수량을 확정한 결과는 아직 없습니다.")
    return {"lead": " ".join(lead), "sections": [
        ("decision-basis", "내 관리 기준과 비교", _unique(basis)),
        ("decision-review", "판단이 확정되지 않은 이유", _unique(reasons)),
        ("decision-recheck", "제한을 다시 확인할 조건", _unique(rechecks)),
    ]}
