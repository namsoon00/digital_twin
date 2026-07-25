"""Read models for TypeDB InferenceBox execution guidance.

The module is intentionally a formatter.  It may show raw ABox observations
and group already-materialized RuleBox relations, but it must not infer an
investment action from prices, moving averages, flow, or action-group names.
"""

from typing import Dict, Iterable, List

from .investment_ubiquitous_language import investment_archetype_labels, position_intent_sentence
from .ontology_relation_contracts import OntologyRuleMatch
from .ontology_rulebox_contracts import (
    WATCHLIST_ACTION_POLICY,
    WATCHLIST_ALLOWED_ACTIONS,
    WATCHLIST_BLOCKED_ACTIONS,
    WATCHLIST_TARGET_ROLE,
)


def _float_value(value: object) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _signed_pct(value: object) -> str:
    amount = _float_value(value)
    return ("+" if amount > 0 else "") + ("%.1f" % amount) + "%"


def _plain_number(value: object) -> str:
    amount = _float_value(value)
    if abs(amount) >= 1000:
        return format(int(round(amount)), ",")
    text = ("%.2f" % amount).rstrip("0").rstrip(".")
    return text or "0"


def _ratio_number(value: object) -> str:
    amount = _float_value(value)
    if amount <= 0:
        return "0"
    if amount < 0.01:
        return ("%.4f" % amount).rstrip("0").rstrip(".")
    if amount < 0.1:
        return ("%.2f" % amount).rstrip("0").rstrip(".")
    if amount < 10:
        return ("%.1f" % amount).rstrip("0").rstrip(".")
    return _plain_number(amount)


def _volume_pace_phrase(facts: Dict[str, object]) -> str:
    raw_ratio = _float_value(facts.get("rawVolumeRatio") if facts.get("rawVolumeRatio") is not None else facts.get("volumeRatio"))
    adjusted_ratio = _float_value(facts.get("timeAdjustedVolumeRatio"))
    expected_ratio = _float_value(facts.get("expectedVolumeRatioNow"))
    elapsed_pct = _float_value(facts.get("volumePaceElapsedPct"))
    session_label = str(facts.get("volumePaceSessionLabel") or "").strip()
    pieces = []
    if raw_ratio:
        pieces.append("평균 대비 원본 " + _ratio_number(raw_ratio) + "배")
    if adjusted_ratio:
        pieces.append("시간 보정 " + _ratio_number(adjusted_ratio) + "배")
    if session_label and elapsed_pct:
        pieces.append(session_label + " " + _plain_number(elapsed_pct) + "% 경과")
    if expected_ratio:
        pieces.append("현시점 기대 누적 " + _ratio_number(expected_ratio) + "배")
    return ", ".join(pieces)


def _append_unique(rows: List[str], value: object) -> None:
    text = str(value or "").strip()
    if text and text not in rows:
        rows.append(text)


def _append_driver(
    rows: List[Dict[str, object]],
    seen: set,
    category: str,
    direction: str,
    label: str,
    summary: str,
    data_keys: Iterable[str],
    source: str,
) -> None:
    text = " ".join(str(summary or "").split())
    key = (category, label, text)
    if not text or key in seen:
        return
    seen.add(key)
    evidence_role = {
        "risk": "risk",
        "support": "support",
        "counter": "counter",
        "blocking": "blocking",
    }.get(direction, "context")
    rows.append({
        "category": category,
        "direction": direction,
        "evidenceRole": evidence_role,
        "label": str(label or ""),
        "summary": text,
        "dataKeys": [str(item) for item in data_keys or [] if str(item or "").strip()],
        "source": source,
    })


def _active_matches(matches: List[OntologyRuleMatch]) -> List[OntologyRuleMatch]:
    return [item for item in matches or [] if item.matched and not item.reference_only]


def _active_rule_labels(matches: List[OntologyRuleMatch]) -> List[str]:
    result: List[str] = []
    for item in _active_matches(matches):
        _append_unique(result, item.label or item.decision_label or item.rule_id)
    return result


def _add_buy_assessment_from_matches(
    facts: Dict[str, object],
    matches: List[OntologyRuleMatch],
) -> Dict[str, object]:
    """Group only RuleBox-authored action metadata for display.

    The UI keeps this compact add-buy pane for continuity. It never derives an
    add-buy state from action groups, rule IDs, prices, or scores: TypeDB has
    already materialized the candidate and action-policy fields.
    """
    active = _active_matches(matches)
    def action_code(item: OntologyRuleMatch) -> str:
        return str(item.candidate_action or "").strip().upper()

    allowed = [
        item for item in active
        if action_code(item) == "ADD"
    ]
    blocked = [
        item for item in active
        if "ADD" in {str(code or "").strip().upper() for code in item.blocked_actions or []}
    ]
    watches = [item for item in active if action_code(item) == "HOLD"]
    defenses = [item for item in active if action_code(item) == "AVOID"]
    profile = str(facts.get("investmentStrategyProfileLabel") or facts.get("investmentStrategyProfile") or "").strip()
    if allowed and blocked:
        state = "conflict"
        label = "추가매수 허용·차단 관계가 함께 있음"
        status_text = "TypeDB가 추가매수 검토 관계와 차단 관계를 함께 만들었습니다. 차단 관계가 해소될 때까지 실행하지 않습니다."
        guidance = [item for item in blocked + allowed]
    elif allowed:
        state = "allow"
        label = str(allowed[0].decision_label or allowed[0].label or "조건부 추가매수 검토")
        status_text = "TypeDB가 조건부 추가매수 검토 관계를 만들었습니다. 즉시 매수 지시가 아니라 RuleBox 조건을 다시 확인할 후보입니다."
        guidance = allowed
    elif blocked:
        state = "block"
        label = str(blocked[0].decision_label or blocked[0].label or "추가매수 보류")
        status_text = "TypeDB가 추가매수 차단 관계를 만들었습니다."
        guidance = blocked
    elif watches or defenses:
        state = "watch" if watches else "defense"
        row = (watches + defenses)[0]
        label = str(row.decision_label or row.label or "추가매수 관찰")
        status_text = "TypeDB가 회복 또는 방어 관계를 만들었지만 추가매수 허용 관계는 아직 없습니다."
        guidance = watches + defenses
    else:
        state = "none"
        label = "추가매수 관련 TypeDB 관계 없음"
        status_text = "추가매수 허용 또는 차단 관계가 이번 InferenceBox 결과에 없습니다."
        guidance = []
    next_checks: List[str] = []
    for item in guidance:
        for value in item.next_checks:
            _append_unique(next_checks, value)
    return {
        "state": state,
        "label": label,
        "statusText": status_text,
        "investmentProfile": profile,
        "allowedReasons": [str(item.label or item.decision_label or "") for item in allowed][:5],
        "blockedReasons": [str(item.label or item.decision_label or "") for item in blocked][:5],
        "watchReasons": [str(item.label or item.decision_label or "") for item in watches + defenses][:5],
        "ruleIds": [str(item.rule_id or "") for item in allowed + blocked + watches + defenses][:8],
        "nextChecks": next_checks[:5],
    }


def _instrument_profile_driver(facts: Dict[str, object], rows: List[Dict[str, object]], seen: set) -> None:
    profile_label = str(facts.get("instrumentProfileLabel") or "").strip()
    archetypes = [str(item) for item in (facts.get("instrumentArchetypes") or []) if str(item or "").strip()]
    archetype_labels = [
        str(item)
        for item in (facts.get("instrumentArchetypeLabels") or investment_archetype_labels(archetypes))
        if str(item or "").strip()
    ]
    position_intent = str(facts.get("instrumentPositionIntent") or "").strip()
    if not (profile_label or archetypes):
        return
    policy_bits = []
    if facts.get("allowAddOnStrength"):
        policy_bits.append("오르는 구간의 소액 추가매수 검토 가능")
    if facts.get("trimOnTrendBreak"):
        policy_bits.append("평균 가격 이탈 시 분할축소 점검")
    if facts.get("avoidAveragingDown"):
        policy_bits.append("손실 구간 물타기 회피")
    summary = (profile_label or "종목 프로필") + "입니다."
    if archetype_labels:
        summary += " 세부 성격은 " + ", ".join(archetype_labels[:3]) + "입니다."
    if position_intent:
        summary += " " + str(facts.get("instrumentPositionIntentDescription") or position_intent_sentence(position_intent)).strip()
    if policy_bits:
        summary += " 관리 원칙은 " + ", ".join(policy_bits[:3]) + "입니다."
    _append_driver(
        rows, seen, "instrumentProfile", "neutral", "종목 성격", summary,
        ["instrumentProfile", "instrumentArchetypes", "instrumentArchetypeLabels", "instrumentPositionIntentLabel", "instrumentPolicies"],
        "TBox instrument profile",
    )


def decision_drivers_from_relation_context(
    facts: Dict[str, object],
    decision: Dict[str, object],
    matches: List[OntologyRuleMatch],
) -> List[Dict[str, object]]:
    """Render raw observations and direction only from TypeDB relations."""
    facts = facts or {}
    rows: List[Dict[str, object]] = []
    seen = set()
    _instrument_profile_driver(facts, rows, seen)

    if facts.get("isHolding") and facts.get("profitLossRate") not in (None, ""):
        _append_driver(
            rows, seen, "position", "neutral", "손익 위치",
            "보유 수익률 " + _signed_pct(facts.get("profitLossRate")) + ", 현재가 " + _plain_number(facts.get("currentPrice")) + ", 평균매입가 " + _plain_number(facts.get("averagePrice")) + "입니다.",
            ["profitLossRate", "averagePrice", "currentPrice"], "ABox raw observation",
        )

    trend_rows = []
    for key, label in [("ma5Distance", "5일선"), ("ma20Distance", "20일선"), ("ma60Distance", "60일선")]:
        if facts.get(key) not in (None, "") and facts.get(key) != 0:
            trend_rows.append(label + " 대비 " + _signed_pct(facts.get(key)))
    if trend_rows:
        _append_driver(
            rows, seen, "trend", "neutral", "평균 가격 위치",
            "현재가의 평균 가격 대비 원시 괴리값은 " + ", ".join(trend_rows) + "입니다.",
            ["currentPrice", "ma5Distance", "ma20Distance", "ma60Distance", "ma20Slope", "ma60Slope"],
            "ABox raw observation",
        )

    volume_text = _volume_pace_phrase(facts)
    flow_rows = []
    if volume_text:
        flow_rows.append("거래량 " + volume_text)
    if facts.get("tradeStrength") not in (None, "", 0, 0.0):
        flow_rows.append("체결강도 " + _plain_number(facts.get("tradeStrength")))
    if facts.get("bidAskImbalance") not in (None, "", 0, 0.0):
        flow_rows.append("호가불균형 " + _signed_pct(facts.get("bidAskImbalance")))
    if flow_rows:
        _append_driver(
            rows, seen, "flow", "neutral", "거래·호가 원시 관측", ", ".join(flow_rows) + "입니다.",
            ["volume", "volumeRatio", "rawVolumeRatio", "timeAdjustedVolumeRatio", "expectedVolumeRatioNow", "tradeStrength", "bidAskImbalance"],
            "ABox raw observation",
        )

    investor_rows = []
    for key, label in [("foreignNetVolume", "외국인"), ("institutionNetVolume", "기관"), ("individualNetVolume", "개인")]:
        if facts.get(key) not in (None, ""):
            investor_rows.append(label + " 순매수 " + _plain_number(facts.get(key)) + "주")
    if investor_rows:
        _append_driver(
            rows, seen, "investorFlow", "neutral", "투자자별 수급 원시 관측", ", ".join(investor_rows) + "입니다.",
            ["foreignNetVolume", "institutionNetVolume", "individualNetVolume", "smartMoneyNetVolume"],
            "ABox raw observation",
        )

    if facts.get("macroDgs10") not in (None, "", 0, 0.0) or facts.get("usdKrwRate") not in (None, "", 0, 0.0):
        macro_rows = []
        if facts.get("macroDgs10") not in (None, "", 0, 0.0):
            macro_rows.append("미국 10년 금리 " + _plain_number(facts.get("macroDgs10")) + "%")
        if facts.get("usdKrwRate") not in (None, "", 0, 0.0):
            macro_rows.append("USD/KRW " + _plain_number(facts.get("usdKrwRate")))
        _append_driver(
            rows, seen, "macro", "neutral", "거시 원시 관측", ", ".join(macro_rows) + "입니다.",
            ["macroDgs10", "macroDgs2", "macroYieldSpread10y2y", "usdKrwRate", "fxExposureRatio"],
            "ABox raw observation",
        )

    for match in _active_matches(matches):
        role = str(match.evidence_role or "context")
        _append_driver(
            rows, seen, "ruleboxInference", role,
            str(match.decision_label or match.label or match.rule_id),
            "TypeDB RuleBox가 '" + str(match.label or match.rule_id) + "' 관계를 추론했습니다.",
            ["activeRules", "matchedRules"], "TypeDB InferenceBox",
        )

    missing_notes = []
    for item in facts.get("missingData") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("label") or item.get("key") or "").strip()
        effect = str(item.get("effect") or item.get("reason") or "").strip()
        if name:
            missing_notes.append(name + (": " + effect if effect else ""))
    if missing_notes:
        _append_driver(
            rows, seen, "dataQuality", "counter", "부족 데이터",
            "데이터 확인 한계: " + " / ".join(missing_notes[:4]),
            ["missingData", "dataAvailability"], "ABox data quality",
        )

    order = {"blocking": 0, "risk": 1, "counter": 2, "support": 3, "neutral": 4, "context": 4}
    return sorted(rows, key=lambda item: (order.get(str(item.get("direction") or "neutral"), 5), str(item.get("category") or "")))[:12]


def _list_of_strings(value: object) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item or "").strip()]
    if value in (None, ""):
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _metadata_from_matches(matches: List[OntologyRuleMatch], name: str) -> List[str]:
    result: List[str] = []
    for item in _active_matches(matches):
        for value in list(getattr(item, name, []) or []):
            _append_unique(result, value)
    return result


def _watchlist_action_guard(
    target_role: str,
    primary_action: str,
    primary_label: str,
    candidate_action: str,
    candidate_action_provided: bool,
    allowed_action_codes: List[str],
    blocked_action_codes: List[str],
    blocked_actions: List[str],
    next_checks: List[str],
) -> Dict[str, object]:
    """Enforce an explicit action-policy constraint, not an investment view."""
    if target_role != WATCHLIST_TARGET_ROLE:
        return {"primaryAction": primary_action, "primaryActionLabel": primary_label, "blockedActions": blocked_actions, "nextChecks": next_checks}
    _append_unique(blocked_actions, "관심종목에는 보유 수량 축소·매도 명령을 적용하지 않음")
    allowed = {str(item or "").strip().upper() for item in allowed_action_codes if str(item or "").strip()}
    blocked = {str(item or "").strip().upper() for item in blocked_action_codes if str(item or "").strip()}
    candidate = str(candidate_action or "").strip().upper()
    candidate_allowed = bool(candidate and candidate not in blocked and (not allowed or candidate in allowed))
    # Old InferenceBox generations can lack a RuleBox candidate action.  Do
    # not decode their workflow key into a trading instruction; render a safe
    # entry wait until the relation is rematerialized.
    if not candidate_action_provided or candidate == "HOLD":
        primary_action = "WAIT_FOR_ENTRY_CONFIRMATION"
        primary_label = "관심 유지, 다음 진입 조건 확인"
    elif not candidate_allowed or candidate == "AVOID":
        primary_action = "AVOID_OR_WAIT"
        primary_label = "신규 진입 회피/대기, 회복 조건 확인"
    return {"primaryAction": primary_action, "primaryActionLabel": primary_label, "blockedActions": blocked_actions, "nextChecks": next_checks}


def execution_plan_from_relation_context(
    facts: Dict[str, object],
    decision: Dict[str, object],
    matches: List[OntologyRuleMatch],
) -> Dict[str, object]:
    facts = facts or {}
    decision = decision or {}
    target_role = str(decision.get("targetRole") or ("watchlist" if facts.get("isWatchlist") else "") or "").strip()
    action_policy = str(decision.get("actionPolicy") or (WATCHLIST_ACTION_POLICY if target_role == WATCHLIST_TARGET_ROLE else "") or "").strip()
    allowed_action_codes = _list_of_strings(decision.get("allowedActions"))
    blocked_action_codes = _list_of_strings(decision.get("blockedActions"))
    if target_role == WATCHLIST_TARGET_ROLE:
        allowed_action_codes = allowed_action_codes or list(WATCHLIST_ALLOWED_ACTIONS)
        blocked_action_codes = blocked_action_codes or list(WATCHLIST_BLOCKED_ACTIONS)

    primary_action = str(decision.get("primaryAction") or "HOLD").strip()
    primary_label = str(decision.get("primaryActionLabel") or decision.get("label") or "관계 결과 확인").strip()
    raw_candidate_action = decision.get("candidateAction")
    candidate_action_provided = raw_candidate_action not in (None, "")
    candidate_action = str(raw_candidate_action or "HOLD").strip().upper()
    candidate_action_label = str(decision.get("candidateActionLabel") or "").strip()
    blocked_actions = _list_of_strings(decision.get("blockedActionLabels")) + _metadata_from_matches(matches, "blocked_action_labels")
    strengthen_conditions = _list_of_strings(decision.get("strengthenConditions")) + _metadata_from_matches(matches, "strengthen_conditions")
    weaken_conditions = _list_of_strings(decision.get("weakenConditions")) + _metadata_from_matches(matches, "weaken_conditions")
    next_checks = _list_of_strings(decision.get("nextChecks")) + _metadata_from_matches(matches, "next_checks")
    for rows in [blocked_actions, strengthen_conditions, weaken_conditions, next_checks]:
        deduped: List[str] = []
        for item in rows:
            _append_unique(deduped, item)
        rows[:] = deduped
    guarded = _watchlist_action_guard(
        target_role,
        primary_action,
        primary_label,
        candidate_action,
        candidate_action_provided,
        allowed_action_codes,
        blocked_action_codes,
        blocked_actions,
        next_checks,
    )
    primary_action = str(guarded["primaryAction"])
    primary_label = str(guarded["primaryActionLabel"])
    blocked_actions = list(guarded["blockedActions"])
    next_checks = list(guarded["nextChecks"])

    risk_signals: List[str] = []
    support_signals: List[str] = []
    counter_signals: List[str] = []
    for item in _active_matches(matches):
        label = str(item.decision_label or item.label or item.rule_id)
        if item.evidence_role in {"risk", "blocking"}:
            _append_unique(risk_signals, label)
        elif item.evidence_role == "support":
            _append_unique(support_signals, label)
        elif item.evidence_role == "counter":
            _append_unique(counter_signals, label)

    missing_impact = []
    for item in facts.get("missingData") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("label") or item.get("key") or "").strip()
        effect = str(item.get("effect") or item.get("reason") or "").strip()
        if name:
            missing_impact.append(name + (": " + effect if effect else "는 판단 강도를 낮춥니다."))

    add_buy_assessment = _add_buy_assessment_from_matches(facts, matches)
    source_keys = [
        "currentPrice", "averagePrice", "profitLossRate", "ma5Distance", "ma20Distance", "ma60Distance",
        "volume", "volumeRatio", "rawVolumeRatio", "timeAdjustedVolumeRatio", "expectedVolumeRatioNow",
        "tradeStrength", "bidAskImbalance", "sellableQuantity", "foreignBuyVolume", "foreignSellVolume",
        "foreignNetVolume", "institutionBuyVolume", "institutionSellVolume", "institutionNetVolume",
        "individualBuyVolume", "individualSellVolume", "individualNetVolume", "smartMoneyNetVolume",
        "investmentStrategyProfile", "investmentStrategyProfileLabel", "strategyLossTolerancePct",
        "strategyProfitProtectionPct", "strategyMaxPositionWeightPct", "strategyMaxSectorWeightPct",
        "positionWeight", "positionAccountWeight", "positionToTradingValuePct", "exitDaysAtTenPctADV",
        "priceDeltaFromPreviousPct", "profitLossRateDeltaPct", "researchEvidenceCount", "directNewsCount",
        "directRiskNewsCount", "directSupportNewsCount", "peerNewsCount", "sectorNewsCount", "marketNewsCount",
        "newsRelevanceState", "newsSourceTrustState", "newsMaterialityState", "newsEvidenceState",
        "newsConflictState", "newsReviewLevel", "newsDataState", "macroDgs10", "macroDgs2", "macroDff",
        "macroYieldSpread10y2y", "rateRegime", "yieldCurveRegime", "fxRatePair", "fxRateToKrw",
        "usdKrwRate", "fxExposureRatio", "strategyFxExposureReviewPct", "fxRegime",
    ]
    source_facts = {key: facts.get(key) for key in source_keys}
    source_facts.update({
        "targetRole": target_role,
        "actionPolicy": action_policy,
        "topNewsTitles": list(facts.get("topNewsTitles") or [])[:5],
    })

    return {
        "engineVersion": "typedb-inferencebox-execution-plan-v2",
        "tboxClass": "ExecutionPlan",
        "subject": {"symbol": facts.get("symbol"), "name": facts.get("name"), "market": facts.get("market"), "source": facts.get("source")},
        "decisionStage": decision.get("decisionStage"),
        "targetRole": target_role,
        "actionPolicy": action_policy,
        "allowedActions": allowed_action_codes,
        "blockedActionCodes": blocked_action_codes,
        "actionGroup": decision.get("actionGroup"),
        "actionLevel": decision.get("actionLevel"),
        "decisionLabel": decision.get("label"),
        "primaryAction": primary_action,
        "primaryActionLabel": primary_label,
        "candidateAction": candidate_action,
        "candidateActionLabel": candidate_action_label,
        "blockedActions": blocked_actions[:5],
        "riskSignals": risk_signals[:7],
        "supportSignals": support_signals[:5],
        "counterSignals": counter_signals[:5],
        "strengthenConditions": strengthen_conditions[:5],
        "weakenConditions": weaken_conditions[:5],
        "nextChecks": next_checks[:5],
        "missingDataImpact": missing_impact[:5],
        "notificationCategory": str(decision.get("notificationCategory") or "relationshipChange"),
        "notificationSeverity": str(decision.get("notificationSeverity") or ""),
        "decisionDrivers": decision_drivers_from_relation_context(facts, decision, matches),
        "addBuyAssessment": add_buy_assessment,
        "sourceFacts": source_facts,
    }
