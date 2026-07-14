from typing import Dict, List

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
    text = ("%.1f" % amount).rstrip("0").rstrip(".")
    return text or "0"


def _volume_pace_phrase(facts: Dict[str, object]) -> str:
    raw_ratio = _float_value(facts.get("rawVolumeRatio") if facts.get("rawVolumeRatio") is not None else facts.get("volumeRatio"))
    adjusted_ratio = _float_value(facts.get("timeAdjustedVolumeRatio"))
    expected_ratio = _float_value(facts.get("expectedVolumeRatioNow"))
    elapsed_pct = _float_value(facts.get("volumePaceElapsedPct"))
    session_label = str(facts.get("volumePaceSessionLabel") or "").strip()
    pieces = []
    if raw_ratio:
        pieces.append("원본 " + _plain_number(raw_ratio) + "배")
    if adjusted_ratio:
        pieces.append("시간 보정 " + _plain_number(adjusted_ratio) + "배")
    if session_label and elapsed_pct:
        pieces.append(session_label + " " + _plain_number(elapsed_pct) + "% 경과")
    if expected_ratio:
        pieces.append("현시점 기대 누적 " + _plain_number(expected_ratio) + "배")
    return ", ".join(pieces)


def _append_unique(rows: List[str], value: object) -> None:
    text = str(value or "").strip()
    if text and text not in rows:
        rows.append(text)


def _active_rule_labels(matches: List[OntologyRuleMatch]) -> List[str]:
    return [
        item.label
        for item in matches or []
        if item.matched and not item.reference_only and str(item.label or "").strip()
    ]


def _append_driver(
    rows: List[Dict[str, object]],
    seen: set,
    category: str,
    direction: str,
    label: str,
    summary: object,
    importance: float,
    data_keys: List[str],
) -> None:
    text = str(summary or "").strip()
    if not text:
        return
    key = (category, direction, label, text)
    if key in seen:
        return
    seen.add(key)
    rows.append({
        "category": category,
        "direction": direction,
        "label": label,
        "summary": text,
        "importance": round(max(0.0, min(100.0, _float_value(importance))), 1),
        "dataKeys": [str(item) for item in data_keys or [] if str(item or "").strip()],
        "source": "ABox facts + relation rules",
    })


def _first_text(values: List[object]) -> str:
    for value in values or []:
        text = " ".join(str(value or "").split())
        if text:
            return text[:180]
    return ""


def decision_drivers_from_relation_context(
    facts: Dict[str, object],
    decision: Dict[str, object],
    matches: List[OntologyRuleMatch],
) -> List[Dict[str, object]]:
    facts = facts or {}
    decision = decision or {}
    action_group = str(decision.get("actionGroup") or "")
    rows: List[Dict[str, object]] = []
    seen = set()

    pnl = _float_value(facts.get("profitLossRate"))
    if facts.get("isHolding") and pnl < 0:
        _append_driver(
            rows,
            seen,
            "position",
            "risk",
            "손익 위치",
            "보유 수익률이 " + _signed_pct(pnl) + "라 손실 관리 기준을 먼저 봐야 합니다.",
            62 + min(30, abs(pnl) * 1.8),
            ["profitLossRate", "averagePrice", "currentPrice"],
        )
    elif facts.get("isHolding") and pnl > 0:
        _append_driver(
            rows,
            seen,
            "position",
            "support" if action_group not in {"profitTake", "rebalance"} else "counter",
            "손익 위치",
            "보유 수익률이 " + _signed_pct(pnl) + "라 손실 구간은 아니며 수익 보호 기준을 함께 봅니다.",
            55 + min(24, pnl * 1.2),
            ["profitLossRate", "averagePrice", "currentPrice"],
        )

    ma5_distance = _float_value(facts.get("ma5Distance"))
    ma20_distance = _float_value(facts.get("ma20Distance"))
    ma60_distance = _float_value(facts.get("ma60Distance"))
    if facts.get("ma5") and (abs(ma5_distance) >= 0.8 or action_group in {"entry", "entryWait"}):
        _append_driver(
            rows,
            seen,
            "trend",
            "support" if ma5_distance >= 0 else "risk",
            "5일 가격 위치",
            "현재가가 5일 평균보다 " + ("높아" if ma5_distance >= 0 else "낮아") + " 아주 짧은 가격 흐름은 " + ("살아 있습니다." if ma5_distance >= 0 else "약합니다."),
            48 + min(22, abs(ma5_distance) * 3.0),
            ["currentPrice", "ma5", "ma5Distance"],
        )
    if facts.get("ma20") or facts.get("ma60"):
        if ma20_distance < 0 and ma60_distance < 0:
            summary = (
                "현재가가 20일 평균보다 " + _plain_number(abs(ma20_distance)) + "% 낮고 "
                "60일 평균보다 " + _plain_number(abs(ma60_distance)) + "% 낮아 단기와 중기 가격 흐름이 모두 약합니다."
            )
            direction = "risk"
        elif ma20_distance < 0 and ma60_distance >= 0:
            summary = (
                "현재가가 20일 평균보다 " + _plain_number(abs(ma20_distance)) + "% 낮지만 "
                "60일 평균보다 " + _plain_number(abs(ma60_distance)) + "% 높아 중기 방어선은 아직 남아 있습니다."
            )
            direction = "counter"
        elif ma20_distance >= 0 and ma60_distance < 0:
            summary = (
                "현재가가 20일 평균보다 " + _plain_number(abs(ma20_distance)) + "% 높지만 "
                "60일 평균보다 " + _plain_number(abs(ma60_distance)) + "% 낮아 중기 회복 확인이 더 필요합니다."
            )
            direction = "counter"
        else:
            summary = (
                "현재가가 20일 평균과 60일 평균 위에 있어 가격 흐름은 우호적으로 봅니다."
            )
            direction = "support"
        _append_driver(
            rows,
            seen,
            "trend",
            direction,
            "20일·60일 가격 위치",
            summary,
            58 + min(32, abs(ma20_distance) * 1.5 + abs(ma60_distance)),
            ["currentPrice", "ma20", "ma20Distance", "ma60", "ma60Distance"],
        )

    raw_volume_ratio = _float_value(facts.get("rawVolumeRatio") if facts.get("rawVolumeRatio") is not None else facts.get("volumeRatio"))
    adjusted_volume_ratio = _float_value(facts.get("timeAdjustedVolumeRatio"))
    volume_ratio = adjusted_volume_ratio or raw_volume_ratio
    volume_pace_phrase = _volume_pace_phrase(facts)
    trade_strength = _float_value(facts.get("tradeStrength"))
    bid_ask_imbalance = _float_value(facts.get("bidAskImbalance"))
    if volume_ratio:
        if volume_ratio < 0.7:
            summary = "거래량은 " + (volume_pace_phrase or _plain_number(volume_ratio) + "배") + " 기준이라 강한 매수세나 투매를 단정하기 어렵습니다."
            direction = "counter"
        elif volume_ratio >= 1.5:
            summary = "거래량은 " + (volume_pace_phrase or _plain_number(volume_ratio) + "배") + " 기준으로 늘어 가격 신호의 확인 강도가 커졌습니다."
            direction = "support" if ma20_distance >= 0 else "risk"
        else:
            summary = "거래량은 " + (volume_pace_phrase or _plain_number(volume_ratio) + "배") + " 기준이라 가격 흐름을 보조 근거로 봅니다."
            direction = "neutral"
        _append_driver(
            rows,
            seen,
            "liquidity",
            direction,
            "거래량 확인",
            summary,
            52 + min(28, abs(volume_ratio - 1) * 12),
            ["volumeRatio", "rawVolumeRatio", "timeAdjustedVolumeRatio", "expectedVolumeRatioNow", "volumePaceElapsedPct", "volume"],
        )
    if trade_strength:
        _append_driver(
            rows,
            seen,
            "liquidity",
            "support" if trade_strength >= 100 else "risk",
            "체결강도",
            "체결강도 " + _plain_number(trade_strength) + "로 당일 체결 흐름은 " + ("매수 쪽이 우세합니다." if trade_strength >= 100 else "매수 우위가 약합니다."),
            55 + min(25, abs(trade_strength - 100) * 0.6),
            ["tradeStrength"],
        )
    if bid_ask_imbalance:
        _append_driver(
            rows,
            seen,
            "liquidity",
            "support" if bid_ask_imbalance > 0 else "risk",
            "호가 잔량",
            "호가 잔량은 " + ("매수" if bid_ask_imbalance > 0 else "매도") + " 쪽이 " + _plain_number(abs(bid_ask_imbalance)) + "% 많습니다.",
            50 + min(25, abs(bid_ask_imbalance) * 0.4),
            ["bidAskImbalance", "orderbookBidVolume", "orderbookAskVolume"],
        )

    smart_money = _float_value(facts.get("smartMoneyNetVolume"))
    if smart_money:
        _append_driver(
            rows,
            seen,
            "investorFlow",
            "support" if smart_money > 0 else "risk",
            "외국인·기관 흐름",
            "외국인과 기관 합산 흐름은 " + ("순매수 " if smart_money > 0 else "순매도 ") + _plain_number(abs(smart_money)) + "주입니다.",
            58 + min(24, abs(_float_value(facts.get("investorFlowScore"))) * 0.25),
            ["foreignNetVolume", "institutionNetVolume", "smartMoneyNetVolume", "investorFlowScore"],
        )

    btc24 = _float_value(facts.get("btcChange24h"))
    btc7 = _float_value(facts.get("btcChange7d"))
    if facts.get("isBtcSensitive") and (btc24 or btc7):
        _append_driver(
            rows,
            seen,
            "crossAsset",
            "risk" if abs(btc24) >= 3 or abs(btc7) >= 4 else "neutral",
            "비트코인 민감도",
            "비트코인 민감 종목입니다. BTC 24시간 " + _signed_pct(btc24) + ", 7일 " + _signed_pct(btc7) + "와 종목 가격 반응을 같이 확인해야 합니다.",
            56 + min(28, abs(btc24) * 2.0 + abs(btc7)),
            ["isBtcSensitive", "btcChange24h", "btcChange7d", "btcPrice"],
        )

    if _float_value(facts.get("macroDgs10")):
        _append_driver(
            rows,
            seen,
            "macro",
            "risk" if _float_value(facts.get("macroDgs10")) >= 4 else "neutral",
            "금리 환경",
            "미국 10년 금리 " + _plain_number(facts.get("macroDgs10")) + "% 환경이라 성장주·테마주에는 부담 여부를 봅니다.",
            52 + min(25, max(0.0, _float_value(facts.get("macroDgs10")) - 3.5) * 10),
            ["macroDgs10", "macroDgs2", "macroYieldSpread10y2y", "rateRegime"],
        )
    if _float_value(facts.get("usdKrwRate") or facts.get("fxRateToKrw")) and _float_value(facts.get("fxExposureRatio")):
        _append_driver(
            rows,
            seen,
            "macro",
            "neutral",
            "환율 영향",
            "외화 노출이 있어 원화 평가금액은 환율과 현지 주가를 나눠서 봐야 합니다.",
            50 + min(20, _float_value(facts.get("fxExposureRatio")) * 0.25),
            ["usdKrwRate", "fxRateToKrw", "fxExposureRatio", "fxRegime"],
        )

    risk_title = _first_text(list(facts.get("directRiskNewsTitles") or []))
    support_title = _first_text(list(facts.get("directSupportNewsTitles") or []))
    top_title = _first_text(list(facts.get("topNewsTitles") or []))
    if _float_value(facts.get("directRiskNewsCount")):
        _append_driver(rows, seen, "research", "risk", "위험 뉴스", "직접 위험 뉴스가 확인됐습니다: " + (risk_title or top_title), 72, ["directRiskNewsCount", "directRiskNewsTitles", "researchEvidence"])
    if _float_value(facts.get("directSupportNewsCount")):
        _append_driver(rows, seen, "research", "support", "우호 뉴스", "직접 우호 뉴스가 확인됐습니다: " + (support_title or top_title), 68, ["directSupportNewsCount", "directSupportNewsTitles", "researchEvidence"])
    if _float_value(facts.get("directNewsCount")) and not (_float_value(facts.get("directRiskNewsCount")) or _float_value(facts.get("directSupportNewsCount"))):
        _append_driver(rows, seen, "research", "neutral", "새 직접 뉴스", "새 직접 뉴스가 확인됐습니다. 방향이 명확하지 않아 원문과 가격 반응을 함께 확인해야 합니다: " + top_title, 62, ["directNewsCount", "topNewsTitles", "researchEvidence"])
    if facts.get("dartDisclosure"):
        disclosure = facts.get("dartDisclosure") if isinstance(facts.get("dartDisclosure"), dict) else {}
        _append_driver(
            rows,
            seen,
            "research",
            "risk",
            "공시 이벤트",
            "새 공시가 있어 원문 조건과 가격 반응을 함께 확인해야 합니다: " + str(disclosure.get("reportName") or "공시"),
            64,
            ["dartDisclosure"],
        )

    missing_labels = [str(item.get("label") or item.get("key") or "") for item in facts.get("missingData") or [] if isinstance(item, dict)]
    if missing_labels:
        _append_driver(
            rows,
            seen,
            "dataQuality",
            "counter",
            "부족 데이터",
            "부족 데이터가 있어 판단 강도를 낮춥니다: " + ", ".join(missing_labels[:4]),
            66,
            ["missingData", "dataAvailability"],
        )

    quality_warning_labels = [str(item.get("label") or item.get("key") or "") for item in facts.get("dataQualityWarnings") or [] if isinstance(item, dict)]
    if quality_warning_labels:
        _append_driver(
            rows,
            seen,
            "dataQualityWarning",
            "counter",
            "데이터 품질 주의",
            "실시간 확정값이 아닌 근거가 있어 판단 강도를 낮춥니다: " + ", ".join(quality_warning_labels[:4]),
            58,
            ["dataQualityWarnings", "dataAvailability"],
        )

    active_labels = _active_rule_labels(matches)
    if active_labels:
        _append_driver(
            rows,
            seen,
            "relationRule",
            "neutral",
            "성립 규칙",
            "관계 규칙은 " + " · ".join(active_labels[:3]) + "를 핵심 근거로 봅니다.",
            60,
            ["activeRules", "matchedRules"],
        )

    return sorted(rows, key=lambda item: float(item.get("importance") or 0), reverse=True)[:10]


def execution_plan_from_relation_context(
    facts: Dict[str, object],
    decision: Dict[str, object],
    matches: List[OntologyRuleMatch],
) -> Dict[str, object]:
    facts = facts or {}
    decision = decision or {}
    action_group = str(decision.get("actionGroup") or "")
    action_level = str(decision.get("actionLevel") or "")
    label = str(decision.get("label") or "")
    target_role = str(decision.get("targetRole") or ("watchlist" if facts.get("isWatchlist") else "") or "").strip()
    action_policy = str(decision.get("actionPolicy") or (WATCHLIST_ACTION_POLICY if target_role == WATCHLIST_TARGET_ROLE else "") or "").strip()
    allowed_action_codes = [str(item) for item in (decision.get("allowedActions") or []) if str(item or "").strip()]
    blocked_action_codes = [str(item) for item in (decision.get("blockedActions") or []) if str(item or "").strip()]
    if target_role == WATCHLIST_TARGET_ROLE:
        allowed_action_codes = allowed_action_codes or list(WATCHLIST_ALLOWED_ACTIONS)
        blocked_action_codes = blocked_action_codes or list(WATCHLIST_BLOCKED_ACTIONS)
    pnl = float(facts.get("profitLossRate") or 0)
    ma20_distance = float(facts.get("ma20Distance") or 0)
    ma60_distance = float(facts.get("ma60Distance") or 0)
    raw_volume_ratio = _float_value(facts.get("rawVolumeRatio") if facts.get("rawVolumeRatio") is not None else facts.get("volumeRatio"))
    adjusted_volume_ratio = _float_value(facts.get("timeAdjustedVolumeRatio"))
    volume_ratio = adjusted_volume_ratio or raw_volume_ratio
    volume_phrase = _volume_pace_phrase(facts) or (("%.1f" % volume_ratio) + "x" if volume_ratio else "")
    trade_strength = float(facts.get("tradeStrength") or 0)
    bid_ask_imbalance = float(facts.get("bidAskImbalance") or 0)
    primary_action = "HOLD"
    primary_label = "보유 유지, 다음 데이터 확인"
    blocked_actions: List[str] = []
    risk_signals: List[str] = []
    support_signals: List[str] = []
    counter_signals: List[str] = []
    strengthen_conditions: List[str] = []
    weaken_conditions: List[str] = []
    next_checks: List[str] = []

    if action_group == "lossControl":
        primary_action = "TRIM_OR_SELL_REVIEW" if action_level in {"action", "urgent"} else "LOSS_CONTROL_REVIEW"
        primary_label = "추가매수 보류, 분할축소/매도 기준 검토"
        blocked_actions.append("20일선 회복 전 추가매수")
        _append_unique(risk_signals, "수익률 " + ("%.1f" % pnl) + "%")
        if ma20_distance < 0:
            _append_unique(risk_signals, "20일선보다 " + ("%.1f" % abs(ma20_distance)) + "% 낮음")
        if ma60_distance < 0:
            _append_unique(risk_signals, "60일선보다 " + ("%.1f" % abs(ma60_distance)) + "% 낮음")
            _append_unique(strengthen_conditions, "60일선 아래 상태가 유지되면 손절·축소 강도를 높임")
        else:
            _append_unique(counter_signals, "60일선보다 " + ("%.1f" % abs(ma60_distance)) + "% 높아 중기 지지는 남아 있음")
            _append_unique(strengthen_conditions, "60일선 아래로 내려가면 손절·축소 강도 상향")
        if volume_ratio and volume_ratio < 1:
            _append_unique(counter_signals, "거래량 " + volume_phrase + " 기준으로 평균 이하라 투매 확정은 아님")
        elif volume_ratio >= 1:
            _append_unique(risk_signals, "거래량 " + volume_phrase + " 기준으로 하락 확인 강도 상승")
        if trade_strength and trade_strength < 100:
            _append_unique(risk_signals, "체결강도 " + ("%.1f" % trade_strength) + "로 매수 체결 우위 부족")
        elif trade_strength >= 100:
            _append_unique(counter_signals, "체결강도 " + ("%.1f" % trade_strength) + "로 단기 매수 체결은 확인됨")
        if bid_ask_imbalance > 0:
            _append_unique(counter_signals, "호가잔량 매수 우위 " + ("%.1f" % bid_ask_imbalance) + "%")
        elif bid_ask_imbalance < 0:
            _append_unique(risk_signals, "호가잔량 매도 우위 " + ("%.1f" % abs(bid_ask_imbalance)) + "%")
        _append_unique(weaken_conditions, "20일선 회복과 거래량 동반 반등이 확인되면 축소 강도 완화")
        _append_unique(next_checks, "매도 가능 수량과 손절/분할축소 기준 확인")
        _append_unique(next_checks, "다음 조회에서도 손실 관리 규칙이 유지되는지 확인")
    elif action_group == "profitTake":
        primary_action = "TRIM_REVIEW"
        primary_label = "분할매도/수익 보호 기준 검토"
        _append_unique(risk_signals, "수익 구간에서 추세 약화")
        _append_unique(blocked_actions, "목표·추세 확인 없는 일괄 매도")
        _append_unique(strengthen_conditions, "20일선 회복 실패와 거래량 증가가 이어지면 분할매도 강도 상향")
        _append_unique(weaken_conditions, "20일선 회복과 수급 개선이 확인되면 보유 유지로 완화")
        _append_unique(next_checks, "목표 수익률, 분할매도 수량, 재진입 조건 확인")
    elif action_group == "rebalance":
        primary_action = "REBALANCE_REVIEW"
        primary_label = "초과 비중 축소와 리밸런싱 검토"
        _append_unique(blocked_actions, "같은 섹터 추가매수")
        _append_unique(risk_signals, "포트폴리오 집중도 확대")
        _append_unique(next_checks, "섹터 비중과 단일 종목 비중 한도 확인")
    elif action_group == "entry":
        primary_action = "SPLIT_BUY_REVIEW"
        primary_label = "소액 분할매수 조건 검토"
        _append_unique(blocked_actions, "확인 없는 일괄 매수")
        _append_unique(support_signals, "5일선 타이밍, 20/60일선, 거래량, 금리·환율 조건이 함께 통과")
        _append_unique(strengthen_conditions, "5일선 위 유지, 거래량 증가, 20/60일선 회복이 함께 유지되면 진입 강도 상향")
        _append_unique(weaken_conditions, "5일선 재이탈, 60일선 이탈, 금리·환율 부담 확대 또는 부정 공시가 나오면 매수 후보 해제")
        _append_unique(next_checks, "첫 진입 가격, 손절 기준, 추가매수 조건, 환율 기준 확인")
    elif action_group == "entryWait":
        primary_action = "WAIT_FOR_ENTRY_CONFIRMATION"
        primary_label = "신규 진입 대기, 조건 재확인"
        _append_unique(blocked_actions, "5일선·60일선·거래량·금리·환율 확인 전 신규 매수")
        for reason in list(facts.get("entryBlockReasons") or [])[:4]:
            _append_unique(risk_signals, reason)
        if ma20_distance >= 0:
            _append_unique(support_signals, "20일선 위라 단기 반등 관찰 근거는 있음")
        _append_unique(strengthen_conditions, "5일선 위 유지, 60일선 회복, 거래량 증가, 금리·환율 부담 완화가 같이 나오면 소액 진입 검토")
        _append_unique(weaken_conditions, "20일선 아래로 다시 내려가거나 거래량이 붙지 않으면 대기 유지")
        _append_unique(next_checks, "5일선·20일선·60일선 위치, 거래량 배율, 미국 10년 금리, USD/KRW를 다음 조회에서 확인")
    elif action_group == "entryRisk":
        primary_action = "AVOID_OR_WAIT"
        primary_label = "신규 진입 보류, 회복 조건 대기" if target_role == WATCHLIST_TARGET_ROLE else "추가매수 보류, 회복 조건 대기"
        _append_unique(blocked_actions, "추세 회복 전 " + ("신규 진입" if target_role == WATCHLIST_TARGET_ROLE else "추가매수"))
        _append_unique(risk_signals, ("관심종목의 가격 흐름 약화 또는 이벤트 리스크" if target_role == WATCHLIST_TARGET_ROLE else "보유 종목의 추세 훼손 또는 이벤트 리스크"))
        _append_unique(weaken_conditions, "20일선 회복과 부정 이벤트 해소 시 보류 강도 완화")
        _append_unique(next_checks, "회복 조건과 비중 한도 확인")
    elif action_group == "disclosure":
        primary_action = "EVENT_RISK_REVIEW"
        primary_label = "공시 원문과 가격 반응 확인"
        _append_unique(blocked_actions, "공시 성격 확인 전 비중 확대")
        _append_unique(risk_signals, "신규 공시가 보유 판단에 연결됨")
        _append_unique(strengthen_conditions, "공시 이후 거래량 증가와 20일선 이탈이 겹치면 방어 강도 상향")
        _append_unique(weaken_conditions, "공시 영향이 제한적이고 가격·수급이 회복되면 경계 강도 완화")
        _append_unique(next_checks, "공시 원문, 접수번호, 후속 정정 여부 확인")
    elif action_group == "eventRisk":
        primary_action = "EVENT_RISK_REVIEW"
        primary_label = "뉴스 리스크와 가격·수급 동조 확인"
        _append_unique(blocked_actions, "뉴스 영향 확인 전 추가매수")
        _append_unique(risk_signals, "직접 부정 뉴스가 가격·수급 반응과 연결됨")
        _append_unique(strengthen_conditions, "후속 보도와 거래량 증가, 20일선 이탈이 이어지면 방어 강도 상향")
        _append_unique(weaken_conditions, "뉴스 반박 또는 가격·수급 회복이 확인되면 경계 강도 완화")
        _append_unique(next_checks, "기사 원문, 출처 신뢰도, 후속 보도, 가격·거래량 동조 여부 확인")
    elif action_group == "eventConfirmation":
        primary_action = "CONFIRMATION_REVIEW"
        primary_label = "우호 뉴스의 가격·수급 확인"
        _append_unique(blocked_actions, "확인 없는 추격 매수")
        _append_unique(support_signals, "직접 우호 뉴스와 가격·수급 확인 신호가 연결됨")
        _append_unique(counter_signals, "우호 뉴스도 추세 재이탈 시 무효화될 수 있음")
        _append_unique(strengthen_conditions, "거래량 증가와 20일선 회복이 유지되면 우호 강도 상향")
        _append_unique(weaken_conditions, "가격 반응이 사라지거나 부정 뉴스가 추가되면 우호 강도 완화")
        _append_unique(next_checks, "뉴스 원문, 거래량 지속성, 체결강도, 다음 지지선 유지 확인")
    elif action_group == "sectorContext":
        primary_action = "SECTOR_CONTEXT_REVIEW"
        primary_label = "섹터·피어 뉴스의 전파 가능성 확인"
        _append_unique(blocked_actions, "직접 뉴스처럼 과대 해석")
        _append_unique(counter_signals, "직접 종목 뉴스가 아니라 섹터·피어 맥락")
        _append_unique(next_checks, "피어/섹터 뉴스가 대상 종목 가격·수급에 전파되는지 확인")
    elif action_group == "cryptoSensitivity":
        primary_action = "EXPOSURE_REVIEW"
        primary_label = "비트코인 민감 비중 점검"
        _append_unique(blocked_actions, "크립토 변동 안정 전 민감 종목 비중 확대")
        _append_unique(next_checks, "BTC 변화와 보유 종목 가격 반응의 시차 확인")
    elif action_group == "distributionRisk":
        primary_action = "TRIM_REVIEW"
        primary_label = "분산매도 가능성 점검"
        _append_unique(blocked_actions, "수급 확인 없는 추가매수")
        _append_unique(risk_signals, "가격 버팀과 매도 수급이 충돌")
        _append_unique(strengthen_conditions, "거래량 증가와 20일선 이탈이 겹치면 축소 강도 상향")
        _append_unique(weaken_conditions, "외국인·기관 순매수와 20일선 회복이 확인되면 방어 강도 완화")
        _append_unique(next_checks, "거래량 증가가 매집인지 분산인지 다음 체결/호가에서 확인")
    elif action_group == "executionRisk":
        primary_action = "SPLIT_EXECUTION_REVIEW"
        primary_label = "시장가보다 분할 실행 기준 검토"
        _append_unique(blocked_actions, "유동성 확인 없는 일괄 매도/매수")
        _append_unique(risk_signals, "거래대금 대비 포지션 또는 매도 가능 수량 제약")
        _append_unique(next_checks, "거래대금, 호가잔량, 매도 가능 수량으로 분할 수량을 계산")
    elif action_group == "factorRisk":
        primary_action = "EXPOSURE_REVIEW"
        primary_label = "섹터·팩터 과밀 노출 점검"
        _append_unique(blocked_actions, "같은 팩터 종목 동시 추가매수")
        _append_unique(risk_signals, "개별 종목 리스크가 포트폴리오 팩터 리스크로 전파")
        _append_unique(next_checks, "동일 섹터/팩터 보유 종목의 동방향 신호를 함께 확인")
    elif action_group == "dataQuality":
        primary_action = "DATA_REPAIR_REVIEW"
        primary_label = "데이터 충돌 해소 전 판단 강도 제한"
        _append_unique(blocked_actions, "누락 데이터 추정 기반 매매 판단")
        _append_unique(risk_signals, "출처 품질 또는 신선도 부족")
        _append_unique(next_checks, "가격·수급·외부 피드의 출처와 최신성을 먼저 복구")
    elif action_group == "macroRegime":
        primary_action = "REGIME_EXPOSURE_REVIEW"
        primary_label = "거시 레짐 민감도 점검"
        _append_unique(blocked_actions, "레짐 악화 중 민감 팩터 비중 확대")
        _append_unique(risk_signals, "금리·크립토·통화 레짐이 민감 종목에 전파")
        _append_unique(next_checks, "금리, 환율, BTC, 지수 반응을 다음 데이터에서 함께 확인")
    elif action_group == "rateRegime":
        primary_action = "RATE_EXPOSURE_REVIEW"
        primary_label = "금리 변화가 밸류에이션에 주는 영향 점검"
        _append_unique(blocked_actions, "금리 부담 확인 없이 성장/테마 비중 확대")
        _append_unique(risk_signals, "금리 상승 또는 수익률곡선 변화가 밸류에이션과 위험 선호에 전파")
        _append_unique(next_checks, "미국 10년 금리, 2년 금리, 10Y-2Y 스프레드와 보유 종목 반응을 함께 확인")
        _append_unique(weaken_conditions, "금리가 기준 아래로 내려가거나 종목 가격·수급이 금리 부담을 이겨내면 신호 약화")
    elif action_group == "fxRegime":
        primary_action = "FX_EXPOSURE_REVIEW"
        primary_label = "환율 효과와 실제 주가 흐름 분리"
        _append_unique(blocked_actions, "환율로 오른 평가액을 종목 자체 상승으로 단정")
        _append_unique(risk_signals, "원화 기준 평가액이 환율 변화에 흔들릴 수 있음")
        _append_unique(next_checks, "USD/KRW, 외화 노출 비중, 현지 통화 기준 주가 변화를 나눠 확인")
        _append_unique(weaken_conditions, "환율이 기준 구간으로 돌아오거나 외화 노출이 줄면 신호 약화")

    if target_role == WATCHLIST_TARGET_ROLE:
        _append_unique(blocked_actions, "관심종목에는 추가매수·분할축소·매도 같은 보유종목 행동을 적용하지 않음")
        if primary_action in {
            "TRIM_OR_SELL_REVIEW",
            "LOSS_CONTROL_REVIEW",
            "TRIM_REVIEW",
            "REBALANCE_REVIEW",
            "SPLIT_EXECUTION_REVIEW",
        }:
            primary_action = "AVOID_OR_WAIT"
            primary_label = "신규 진입 회피/대기, 회복 조건 확인"
            _append_unique(risk_signals, "보유 수량이 없는 관심종목이므로 가격 회복 전 신규 진입을 보류합니다.")
            _append_unique(next_checks, "진입 예정 금액, 5일·20일·60일 평균 위치, 거래량 회복 여부 확인")
        elif primary_action == "EXPOSURE_REVIEW" and action_group in {"rebalance", "distributionRisk"}:
            primary_action = "WAIT_FOR_ENTRY_CONFIRMATION"
            primary_label = "신규 진입 대기, 노출 리스크 확인"
        blocked_actions = [
            item.replace("추가매수", "신규 진입").replace("일괄 매도", "성급한 진입").replace("분할매도", "신규 진입")
            for item in blocked_actions
        ]

    for item in _active_rule_labels(matches):
        if any(token in item for token in ["손실", "리스크", "하락", "공시", "집중", "부정"]):
            _append_unique(risk_signals, item)
        elif any(token in item for token in ["지지", "회복", "수급", "매수", "우호", "동조"]):
            _append_unique(support_signals, item)

    missing_impact = []
    for item in facts.get("missingData") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("label") or item.get("key") or "").strip()
        effect = str(item.get("effect") or "").strip()
        if name:
            missing_impact.append(name + (": " + effect if effect else "는 판단 강도를 낮춥니다."))
    decision_drivers = decision_drivers_from_relation_context(facts, decision, matches)

    return {
        "engineVersion": "ontology-execution-plan-v1",
        "tboxClass": "ExecutionPlan",
        "subject": {
            "symbol": facts.get("symbol"),
            "name": facts.get("name"),
            "market": facts.get("market"),
            "source": facts.get("source"),
        },
        "decisionStage": decision.get("decisionStage"),
        "targetRole": target_role,
        "actionPolicy": action_policy,
        "allowedActions": allowed_action_codes,
        "blockedActionCodes": blocked_action_codes,
        "actionGroup": action_group,
        "actionLevel": action_level,
        "decisionLabel": label,
        "primaryAction": primary_action,
        "primaryActionLabel": primary_label,
        "blockedActions": blocked_actions[:5],
        "riskSignals": risk_signals[:7],
        "supportSignals": support_signals[:5],
        "counterSignals": counter_signals[:5],
        "strengthenConditions": strengthen_conditions[:5],
        "weakenConditions": weaken_conditions[:5],
        "nextChecks": next_checks[:5],
        "missingDataImpact": missing_impact[:5],
        "decisionDrivers": decision_drivers,
        "sourceFacts": {
            "currentPrice": facts.get("currentPrice"),
            "targetRole": target_role,
            "actionPolicy": action_policy,
            "averagePrice": facts.get("averagePrice"),
            "profitLossRate": facts.get("profitLossRate"),
            "ma5Distance": round(float(facts.get("ma5Distance") or 0), 2),
            "ma20Distance": round(float(facts.get("ma20Distance") or 0), 2),
            "ma60Distance": round(float(facts.get("ma60Distance") or 0), 2),
            "volumeRatio": facts.get("volumeRatio"),
            "tradeStrength": facts.get("tradeStrength"),
            "bidAskImbalance": facts.get("bidAskImbalance"),
            "sellableQuantity": facts.get("sellableQuantity"),
            "foreignBuyVolume": facts.get("foreignBuyVolume"),
            "foreignSellVolume": facts.get("foreignSellVolume"),
            "foreignNetVolume": facts.get("foreignNetVolume"),
            "institutionBuyVolume": facts.get("institutionBuyVolume"),
            "institutionSellVolume": facts.get("institutionSellVolume"),
            "institutionNetVolume": facts.get("institutionNetVolume"),
            "individualBuyVolume": facts.get("individualBuyVolume"),
            "individualSellVolume": facts.get("individualSellVolume"),
            "individualNetVolume": facts.get("individualNetVolume"),
            "positionToTradingValuePct": round(float(facts.get("positionToTradingValuePct") or 0), 2),
            "exitDaysAtTenPctADV": round(float(facts.get("exitDaysAtTenPctADV") or 0), 2),
            "liquidityRiskScore": round(float(facts.get("liquidityRiskScore") or 0), 1),
            "priceDeltaFromPreviousPct": round(float(facts.get("priceDeltaFromPreviousPct") or 0), 2),
            "profitLossRateDeltaPct": round(float(facts.get("profitLossRateDeltaPct") or 0), 2),
            "researchEvidenceCount": facts.get("researchEvidenceCount"),
            "directNewsCount": facts.get("directNewsCount"),
            "directRiskNewsCount": facts.get("directRiskNewsCount"),
            "directSupportNewsCount": facts.get("directSupportNewsCount"),
            "peerNewsCount": facts.get("peerNewsCount"),
            "sectorNewsCount": facts.get("sectorNewsCount"),
            "marketNewsCount": facts.get("marketNewsCount"),
            "averageNewsRelevanceScore": facts.get("averageNewsRelevanceScore"),
            "averageNewsSourceReliability": facts.get("averageNewsSourceReliability"),
            "topNewsTitles": list(facts.get("topNewsTitles") or [])[:5],
            "macroDgs10": facts.get("macroDgs10"),
            "macroDgs2": facts.get("macroDgs2"),
            "macroDff": facts.get("macroDff"),
            "macroYieldSpread10y2y": facts.get("macroYieldSpread10y2y"),
            "rateRegime": facts.get("rateRegime"),
            "yieldCurveRegime": facts.get("yieldCurveRegime"),
            "fxRatePair": facts.get("fxRatePair"),
            "fxRateToKrw": facts.get("fxRateToKrw"),
            "usdKrwRate": facts.get("usdKrwRate"),
            "fxExposureRatio": round(float(facts.get("fxExposureRatio") or 0), 2),
            "entryMa5TimingOk": facts.get("entryMa5TimingOk"),
            "entryMomentumTrendReady": facts.get("entryMomentumTrendReady"),
            "entrySupportCount": facts.get("entrySupportCount"),
            "entryMacroBlocked": facts.get("entryMacroBlocked"),
            "entryFxBlocked": facts.get("entryFxBlocked"),
            "entryRequiredDataMissing": facts.get("entryRequiredDataMissing"),
            "entryBlockReasons": list(facts.get("entryBlockReasons") or [])[:6],
            "fxRegime": facts.get("fxRegime"),
        },
    }
