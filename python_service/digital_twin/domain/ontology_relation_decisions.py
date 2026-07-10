from typing import Dict

from .ontology_relation_contracts import DEFAULT_RELATION_THRESHOLDS, DecisionStageDefinition, ScoreBandDefinition
from .ontology_rule_catalog import DECISION_LABEL_ALIASES, DECISION_STAGE_DEFINITIONS, SCORE_BANDS


def score_band(score: float) -> ScoreBandDefinition:
    value = float(score or 0)
    for band in SCORE_BANDS:
        if band.contains(value):
            return band
    return SCORE_BANDS[-1]


def strength_label(score: float) -> str:
    return score_band(score).label


def relation_score_meaning(score: float) -> str:
    return score_band(score).meaning


def decision_stage_by_key(stage_key: str) -> DecisionStageDefinition:
    return DECISION_STAGE_DEFINITIONS.get(stage_key, DECISION_STAGE_DEFINITIONS["HOLD_KEEP"])


def decision_action_group_for_label(label: object) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    stage_key = DECISION_LABEL_ALIASES.get(text)
    if not stage_key:
        for key, stage in DECISION_STAGE_DEFINITIONS.items():
            if text == stage.label:
                stage_key = key
                break
    if stage_key:
        return decision_stage_by_key(stage_key).action_group
    if "비트코인" in text or "크립토" in text or "민감도" in text:
        return "cryptoSensitivity"
    if any(term in text for term in ["손절", "손실", "분할축소"]):
        return "lossControl"
    if any(term in text for term in ["분할매도", "익절", "수익"]):
        return "profitTake"
    if "리밸런싱" in text:
        return "rebalance"
    if any(term in text for term in ["매수", "진입"]):
        return "entryRisk" if "보류" in text else "entry"
    if "금리" in text:
        return "rateRegime"
    if "환율" in text:
        return "fxRegime"
    if "공시" in text:
        return "disclosure"
    if "뉴스" in text and any(term in text for term in ["리스크", "대응", "부정"]):
        return "eventRisk"
    if "뉴스" in text and any(term in text for term in ["동조", "확인", "우호"]):
        return "eventConfirmation"
    if any(term in text for term in ["섹터", "피어"]):
        return "sectorContext"
    if any(term in text for term in ["보유", "관망", "관찰", "유지"]):
        return "holdWatch"
    return text


def _stage_for_score(review_stage: str, action_stage: str, score: float) -> DecisionStageDefinition:
    return decision_stage_by_key(action_stage if float(score or 0) >= 70 else review_stage)


def resolve_decision_stage(rule_id: str, score: float, facts: Dict[str, object]) -> DecisionStageDefinition:
    value = float(score or 0)
    pnl = float(facts.get("profitLossRate") or 0)
    loss_threshold = float(facts.get("lossThreshold") or DEFAULT_RELATION_THRESHOLDS["lossRateLow"])
    if rule_id == "trend.breakdown_acceleration.v1":
        return decision_stage_by_key("BREAKDOWN_ACCELERATION" if value >= 70 else "LOSS_REDUCE")
    if rule_id == "breakout.failure.v1":
        return decision_stage_by_key("BREAKOUT_FAILURE" if value >= 70 else "LOSS_REDUCE")
    if rule_id == "support.retest.failed.v1":
        return decision_stage_by_key("SUPPORT_RETEST_FAILED")
    if rule_id == "support.retest.confirmed.v1":
        return decision_stage_by_key("SUPPORT_RETEST")
    if rule_id == "trend.support_retest.v1":
        return decision_stage_by_key("SUPPORT_RETEST")
    if rule_id == "trend.recovery_attempt.v1":
        return decision_stage_by_key("RECOVERY_CONFIRM")
    if rule_id == "holding.loss_guard.breakdown.v1":
        if value >= 70 and pnl <= loss_threshold:
            return decision_stage_by_key("LOSS_CUT")
        if value >= 55:
            return decision_stage_by_key("LOSS_REDUCE")
        return decision_stage_by_key("LOSS_WATCH")
    if rule_id == "holding.profit_take.trend_weakness.v1":
        return _stage_for_score("PROFIT_PARTIAL", "PROFIT_SPLIT", value)
    if rule_id == "holding.concentration.rebalance.v1":
        return _stage_for_score("REBALANCE_REVIEW", "REBALANCE_ACTION", value)
    if rule_id == "factor.crowding.v1":
        return decision_stage_by_key("FACTOR_CROWDING")
    if rule_id == "liquidity.exit_capacity.v1":
        return _stage_for_score("LIQUIDITY_REVIEW", "LIQUIDITY_ACTION", value)
    if rule_id == "distribution.detected.v1":
        return decision_stage_by_key("DISTRIBUTION_REVIEW")
    if rule_id == "profit.protection.volatility.v1":
        return decision_stage_by_key("PROFIT_PROTECT")
    if rule_id == "data.conflict.v1":
        return decision_stage_by_key("DATA_CONFLICT")
    if rule_id == "macro.regime.shift.v1":
        return decision_stage_by_key("MACRO_REGIME")
    if rule_id == "rates.interest_rate.sensitivity.v1":
        return _stage_for_score("RATE_REVIEW", "RATE_ACTION", value)
    if rule_id == "fx.usd_krw.exposure.v1":
        return _stage_for_score("FX_REVIEW", "FX_ACTION", value)
    if rule_id == "external.crypto.btc_sensitivity.v1":
        return _stage_for_score("BTC_REVIEW", "BTC_REDUCE", value)
    if rule_id == "disclosure.material_event.v1":
        return decision_stage_by_key("DISCLOSURE_REVIEW")
    if rule_id == "news.direct_risk.price_confirmed.v1":
        return decision_stage_by_key("NEWS_RISK")
    if rule_id == "news.direct_support.price_confirmed.v1":
        return decision_stage_by_key("NEWS_CONFIRMATION")
    if rule_id == "news.sector_peer_context.v1":
        return decision_stage_by_key("SECTOR_NEWS")
    if rule_id == "holding.trend_flow.confirmation.v1":
        return decision_stage_by_key("FLOW_DEFENSE" if value >= 55 else "FLOW_WATCH")
    if rule_id == "entry.pullback.supported.v1":
        if value >= 70:
            return decision_stage_by_key("ENTRY_READY")
        if value >= 55:
            return decision_stage_by_key("ENTRY_SPLIT_BUY")
        return decision_stage_by_key("ENTRY_WATCH")
    if rule_id == "entry.momentum.confirmed.v1":
        if value >= 70:
            return decision_stage_by_key("ENTRY_READY")
        return decision_stage_by_key("ENTRY_SPLIT_BUY")
    if rule_id == "entry.wait_for_confirmation.v1":
        return decision_stage_by_key("ENTRY_WAIT")
    if rule_id == "entry.add_buy.blocked.v1":
        return decision_stage_by_key("ADD_BUY_BLOCKED")
    if rule_id == "averaging_down.block.v1":
        return decision_stage_by_key("ADD_BUY_BLOCKED")
    return decision_stage_by_key("HOLD_KEEP")


def relation_score_direction_meaning(delta: float) -> str:
    value = float(delta or 0)
    if abs(value) < 0.05:
        return "이전과 같은 수준의 대응 필요 강도"
    if value > 0:
        return "대응 필요 강도가 커졌다는 뜻이며, 가격 상승 예측 점수가 아닙니다"
    return "대응 필요 강도가 완화됐다는 뜻이며, 매수 신호는 아닙니다"
