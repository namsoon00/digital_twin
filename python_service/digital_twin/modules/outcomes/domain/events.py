from digital_twin.modules.outcomes.domain.event_types import TRADE_EXECUTION_RECORDED, INVESTMENT_DECISION_REVIEWED, INVESTMENT_PERFORMANCE_ATTRIBUTED, INVESTMENT_FOLLOW_UP_TRANSITIONED
from digital_twin.modules.reasoning.contracts import fact_change_contract, follow_up_field_fact_types
from digital_twin.shared_kernel.events import DomainEvent
from typing import Dict, Iterable, List, Mapping
import hashlib










def investment_follow_up_transitioned_event(
    account_id: str,
    transitions: Iterable[Mapping[str, object]],
    observed_at: str,
    *,
    source_snapshot_id: str = "",
) -> DomainEvent:
    """Record verified condition edges that require a fresh graph judgement."""

    clean_transitions = []
    symbols = set()
    for raw in transitions or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        symbol = str(item.get("symbol") or "").upper().strip()
        condition_id = str(item.get("conditionId") or "").strip()
        transition_id = str(item.get("transitionId") or "").strip()
        if not symbol or not condition_id or not transition_id:
            continue
        symbols.add(symbol)
        clean_transitions.append({
            "conditionId": condition_id[:191],
            "sourceConditionId": str(item.get("sourceConditionId") or "")[:191],
            "episodeId": str(item.get("episodeId") or "")[:191],
            "symbol": symbol[:64],
            "field": str(item.get("field") or "")[:96],
            "operator": str(item.get("operator") or "")[:8],
            "threshold": item.get("threshold"),
            "purpose": str(item.get("purpose") or "")[:32],
            "previousValue": item.get("previousValue"),
            "currentValue": item.get("currentValue"),
            "previousStatus": str(item.get("previousStatus") or "pending")[:32],
            "status": str(item.get("status") or "")[:32],
            "transitionKind": str(item.get("transitionKind") or "")[:64],
            "transitionId": transition_id[:191],
            "transitionAt": str(item.get("transitionAt") or observed_at or "")[:40],
            "label": str(item.get("label") or "")[:240],
            "onSatisfied": str(item.get("onSatisfied") or "")[:240],
            "transitionVerified": bool(item.get("transitionVerified")),
        })
        if len(clean_transitions) >= 40:
            break
    transition_ids = sorted(
        str(item.get("transitionId") or "") for item in clean_transitions
    )
    fact_types_by_symbol = {
        symbol: [
            "DecisionFollowUpCondition",
            *follow_up_field_fact_types(
                item.get("field")
                for item in clean_transitions
                if item.get("symbol") == symbol
            ),
        ]
        for symbol in sorted(symbols)
    }
    fact_types = sorted({
        fact_type
        for values in fact_types_by_symbol.values()
        for fact_type in values
    })
    identity = hashlib.sha256("|".join(transition_ids).encode("utf-8")).hexdigest()[:24]
    return DomainEvent(
        name=INVESTMENT_FOLLOW_UP_TRANSITIONED,
        aggregate_id=("investment-follow-up:" + str(account_id or "default") + ":" + identity)[:191],
        payload={
            "accountId": str(account_id or "default")[:191],
            "symbols": sorted(symbols),
            "observedAt": str(observed_at or "")[:40],
            "sourceSnapshotId": str(source_snapshot_id or "")[:191],
            "transitionCount": len(clean_transitions),
            "transitions": clean_transitions,
            "factTypes": fact_types,
            "factTypesBySymbol": fact_types_by_symbol,
            "changedFieldsBySymbol": {
                symbol: sorted({
                    "followUpStatus",
                    *[
                        str(item.get("field") or "")
                        for item in clean_transitions
                        if item.get("symbol") == symbol and str(item.get("field") or "")
                    ],
                })
                for symbol in sorted(symbols)
            },
        },
        correlation_id=(str(source_snapshot_id or "") or identity)[:191],
    )
