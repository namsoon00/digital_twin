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
