from typing import Dict

from .ontology_relation_catalog import DECISION_LABEL_ALIASES, DECISION_STAGE_DEFINITIONS
from .ontology_relation_contracts import DEFAULT_RELATION_THRESHOLDS, DecisionStageDefinition


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
        if "추가매수 관찰" in text or "조건부 추가매수" in text:
            return "addBuy"
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


def _stage_for_level(review_stage: str, action_stage: str, action_level: str) -> DecisionStageDefinition:
    return decision_stage_by_key(action_stage if action_level in {"action", "urgent"} else review_stage)


def resolve_decision_stage(
    rule_id: str,
    facts: Dict[str, object],
    action_level: str = "review",
) -> DecisionStageDefinition:
    """Resolve a fallback stage without manufacturing an aggregate score.

    Native TypeDB relations normally provide ``decisionStage`` directly.  This
    mapping is only for old persisted relations that predate that attribute.
    """
    facts = facts or {}
    level = str(action_level or "review").strip().lower()
    try:
        pnl = float(facts.get("profitLossRate") or 0)
    except (TypeError, ValueError):
        pnl = 0.0
    try:
        loss_threshold = float(facts.get("lossThreshold") or DEFAULT_RELATION_THRESHOLDS["lossRateLow"])
    except (TypeError, ValueError):
        loss_threshold = float(DEFAULT_RELATION_THRESHOLDS["lossRateLow"])

    fixed = {
        "trend.breakdown_acceleration.v1": "BREAKDOWN_ACCELERATION",
        "breakout.failure.v1": "BREAKOUT_FAILURE",
        "support.retest.failed.v1": "SUPPORT_RETEST_FAILED",
        "support.retest.confirmed.v1": "SUPPORT_RETEST",
        "trend.support_retest.v1": "SUPPORT_RETEST",
        "trend.recovery_attempt.v1": "RECOVERY_CONFIRM",
        "factor.crowding.v1": "FACTOR_CROWDING",
        "distribution.detected.v1": "DISTRIBUTION_REVIEW",
        "profit.protection.volatility.v1": "PROFIT_PROTECT",
        "data.conflict.v1": "DATA_CONFLICT",
        "macro.regime.shift.v1": "MACRO_REGIME",
        "disclosure.material_event.v1": "DISCLOSURE_REVIEW",
        "news.direct_risk.new_material.v1": "NEWS_RISK",
        "news.direct_risk.price_confirmed.v1": "NEWS_RISK",
        "news.direct_support.new_material.v1": "NEWS_CONFIRMATION",
        "news.direct_support.price_confirmed.v1": "NEWS_CONFIRMATION",
        "news.direct_material.new.v1": "NEWS_CONFIRMATION",
        "news.sector_peer_context.v1": "SECTOR_NEWS",
        "holding.loss_smart_money.defense.v1": "FLOW_DEFENSE",
        "holding.investor_flow.smart_money_accumulation.v1": "FLOW_DEFENSE",
        "graph.investor_flow.smart_money_accumulation.v1": "FLOW_DEFENSE",
        "holding.investor_flow.retail_dip_buying_risk.v1": "ADD_BUY_BLOCKED",
        "graph.investor_flow.retail_dip_buying_risk.v1": "ADD_BUY_BLOCKED",
        "holding.investor_flow.smart_money_outflow_risk.v1": "LIQUIDITY_REVIEW",
        "graph.investor_flow.smart_money_outflow_risk.v1": "LIQUIDITY_REVIEW",
        "holding.loss_smart_money.reversal_watch.v1": "ADD_BUY_WATCH",
        "holding.loss_smart_money.add_buy_review.v1": "ADD_BUY_REVIEW",
        "holding.winner_momentum.add_buy_review.v1": "ADD_BUY_REVIEW",
        "entry.wait_for_confirmation.v1": "ENTRY_WAIT",
        "entry.add_buy.blocked.v1": "ADD_BUY_BLOCKED",
        "averaging_down.block.v1": "ADD_BUY_BLOCKED",
        "holding.averaging_down.risk_guard.v1": "ADD_BUY_BLOCKED",
    }
    if rule_id in fixed:
        return decision_stage_by_key(fixed[rule_id])
    if rule_id == "holding.loss_guard.breakdown.v1":
        if pnl <= loss_threshold and level in {"action", "urgent"}:
            return decision_stage_by_key("LOSS_CUT")
        return decision_stage_by_key("LOSS_REDUCE" if pnl < 0 else "LOSS_WATCH")
    if rule_id == "holding.profit_take.trend_weakness.v1":
        return _stage_for_level("PROFIT_PARTIAL", "PROFIT_SPLIT", level)
    if rule_id == "holding.concentration.rebalance.v1":
        return _stage_for_level("REBALANCE_REVIEW", "REBALANCE_ACTION", level)
    if rule_id == "liquidity.exit_capacity.v1":
        return _stage_for_level("LIQUIDITY_REVIEW", "LIQUIDITY_ACTION", level)
    if rule_id == "rates.interest_rate.sensitivity.v1":
        return _stage_for_level("RATE_REVIEW", "RATE_ACTION", level)
    if rule_id == "fx.usd_krw.exposure.v1":
        return _stage_for_level("FX_REVIEW", "FX_ACTION", level)
    if rule_id == "external.crypto.btc_sensitivity.v1":
        return _stage_for_level("BTC_REVIEW", "BTC_REDUCE", level)
    if rule_id == "holding.trend_flow.confirmation.v1":
        return decision_stage_by_key("FLOW_DEFENSE" if level in {"review", "action", "urgent"} else "FLOW_WATCH")
    if rule_id in {"entry.pullback.supported.v1", "entry.momentum.confirmed.v1"}:
        if level in {"action", "urgent"}:
            return decision_stage_by_key("ENTRY_READY")
        return decision_stage_by_key("ENTRY_SPLIT_BUY" if level == "review" else "ENTRY_WATCH")
    return decision_stage_by_key("HOLD_KEEP")
