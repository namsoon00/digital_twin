from typing import Dict

from .market_data import number


def decision_stage_from_action(action_group: str, action_level: str) -> str:
    group = str(action_group or "")
    level = str(action_level or "")
    if group == "lossControl":
        return "LOSS_CUT" if level in {"action", "urgent"} else "LOSS_REDUCE"
    if group == "profitTake":
        return "PROFIT_SPLIT" if level in {"action", "urgent"} else "PROFIT_PARTIAL"
    if group == "entry":
        return "ENTRY_READY" if level in {"action", "urgent"} else "ENTRY_SPLIT_BUY" if level == "review" else "ENTRY_WATCH"
    if group == "addBuy":
        return "ADD_BUY_REVIEW" if level in {"review", "action", "urgent"} else "ADD_BUY_WATCH"
    if group == "entryWait":
        return "ENTRY_WAIT" if level in {"review", "action", "urgent"} else "ENTRY_WATCH"
    if group == "entryRisk":
        return "ADD_BUY_BLOCKED"
    if group == "factorRisk":
        return "FACTOR_CROWDING"
    if group == "rebalance":
        return "REBALANCE_ACTION" if level in {"action", "urgent"} else "REBALANCE_REVIEW"
    if group == "dataQuality":
        return "DATA_CONFLICT"
    if group == "executionRisk":
        return "LIQUIDITY_ACTION" if level in {"action", "urgent"} else "LIQUIDITY_REVIEW"
    if group == "alertReview":
        return "RELATION_WATCH"
    return ""


def relation_stage_priority(relation: Dict[str, object]) -> int:
    explicit = number((relation or {}).get("stagePriority"))
    if explicit:
        return int(round(explicit))
    stage = str((relation or {}).get("decisionStage") or "").strip()
    if not stage:
        stage = decision_stage_from_action(
            str((relation or {}).get("actionGroup") or ""),
            str((relation or {}).get("actionLevel") or ""),
        )
    base = {
        "LOSS_CUT": 46,
        "LOSS_REDUCE": 40,
        "EXECUTION_OK": 18,
        "LIQUIDITY_ACTION": 38,
        "LIQUIDITY_REVIEW": 34,
        "PROFIT_SPLIT": 37,
        "PROFIT_PARTIAL": 33,
        "ADD_BUY_BLOCKED": 36,
        "FACTOR_CROWDING": 32,
        "REBALANCE_ACTION": 39,
        "REBALANCE_REVIEW": 34,
        "DATA_CONFLICT": 34,
        "RECOVERY_CONFIRM": 30,
        "NEWS_RISK": 36,
        "NEWS_CONFIRMATION": 31,
        "FLOW_DEFENSE": 35,
        "ADD_BUY_REVIEW": 35,
        "ADD_BUY_WATCH": 24,
        "ENTRY_READY": 35,
        "ENTRY_SPLIT_BUY": 30,
        "ENTRY_WAIT": 26,
        "ENTRY_WATCH": 22,
        "RELATION_WATCH": 24,
    }.get(stage, 10)
    impact = max(number((relation or {}).get("riskImpact")), number((relation or {}).get("supportImpact")))
    return int(round(min(50, base + min(6, impact / 4 if impact else 0))))
