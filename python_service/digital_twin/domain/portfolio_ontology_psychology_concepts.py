from typing import Dict

from .market_psychology import market_psychology_snapshot, psychology_policy_from_settings
from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation
from .portfolio import Position


PSYCHOLOGY_COMPONENT_TBOX = {
    "behavior": "BehaviorSentiment",
    "investorFlow": "InvestorFlowSentiment",
    "positioning": "PositioningSentiment",
    "news": "NewsSentiment",
    "crowd": "CrowdSentiment",
}

PSYCHOLOGY_STATE_TBOX = {
    "optimistic": "OptimisticPsychologyState",
    "mixed": "MixedPsychologyState",
    "cautious": "CautiousPsychologyState",
    "neutral": "NeutralPsychologyState",
    "insufficient": "InsufficientPsychologyEvidence",
}


def add_market_psychology_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    external_signals: Dict[str, object],
    runtime_context: Dict[str, object],
    observation_profiles: Dict[str, Dict[str, object]],
) -> Dict[str, object]:
    settings = runtime_context.get("settings") if isinstance((runtime_context or {}).get("settings"), dict) else {}
    policy = psychology_policy_from_settings(settings)
    if not policy.enabled:
        return {}
    snapshot = market_psychology_snapshot(
        position,
        external_signals=external_signals,
        observation_profiles=observation_profiles,
        settings=settings,
        observed_at=str((runtime_context or {}).get("asOf") or ""),
    )
    payload = snapshot.to_dict()
    symbol = str(position.symbol or "").upper().strip()
    component_ids = []
    for component in payload.get("components") or []:
        if not isinstance(component, dict):
            continue
        key = str(component.get("key") or "unknown")
        component_id = add_entity(graph, "psychology-observation", symbol + ":" + key, str(component.get("label") or key), {
            "tboxClass": PSYCHOLOGY_COMPONENT_TBOX.get(key, "PsychologyObservation"),
            "tboxClasses": [
                "Observation",
                "PsychologyObservation",
                PSYCHOLOGY_COMPONENT_TBOX.get(key, "PsychologyObservation"),
            ],
            "symbol": symbol,
            "field": key,
            "psychologyState": str(component.get("state") or "insufficient"),
            "psychologyStateLabel": str(component.get("stateLabel") or "자료 부족"),
            "reviewLevel": str(component.get("reviewLevel") or "blocked"),
            "dataState": str(component.get("dataState") or "unavailable"),
            "evidenceRole": str(component.get("evidenceRole") or "blocking"),
            "available": bool(component.get("available")),
            "freshnessStatus": str(component.get("freshnessStatus") or "unknown"),
            "source": str(component.get("source") or ""),
            "sourceAsOf": str(component.get("sourceAsOf") or ""),
            "sourceTimestampPresent": bool(component.get("sourceAsOf")),
            "judgementEvidenceUsable": bool(component.get("available")) and str(component.get("freshnessStatus") or "") == "fresh",
            "reason": str(component.get("reason") or ""),
            "evidence": list(component.get("evidence") or []),
            "shadowOnly": True,
            "decisionImpactApplied": False,
            "observationDomain": "psychology",
        })
        component_ids.append(component_id)
        add_relation(graph, stock_id, component_id, "HAS_PSYCHOLOGY_OBSERVATION", weight=1.0, properties={
            "source": str(component.get("source") or "psychology-shadow"),
            "signalGroup": "marketPsychology",
            "polarity": "context",
            "shadowOnly": True,
            "decisionImpactApplied": False,
            "freshnessStatus": str(component.get("freshnessStatus") or "unknown"),
        })

    state = str(payload.get("state") or "insufficient")
    state_class = PSYCHOLOGY_STATE_TBOX.get(state, "MarketPsychologyState")
    state_id = add_entity(graph, "market-psychology-state", symbol, str(payload.get("stateLabel") or "시장 심리"), {
        "tboxClass": state_class,
        "tboxClasses": ["Signal", "MarketPsychologyState", state_class, "PsychologyShadowState"],
        "symbol": symbol,
        "field": state,
        "psychologyState": state,
        "psychologyStateLabel": str(payload.get("stateLabel") or ""),
        "reviewLevel": str(payload.get("reviewLevel") or "blocked"),
        "dataState": str(payload.get("dataState") or "unavailable"),
        "conflictState": str(payload.get("conflictState") or "context-only"),
        "availableComponentCount": int(payload.get("availableComponentCount") or 0),
        "freshnessStatus": str(payload.get("freshnessStatus") or "unknown"),
        "sourceAsOf": str(payload.get("sourceAsOf") or ""),
        "sourceTimestampPresent": bool(payload.get("sourceAsOf")),
        "judgementEvidenceUsable": state != "insufficient",
        "summary": str(payload.get("summary") or ""),
        "contradiction": str(payload.get("contradiction") or ""),
        "decisionImpactApplied": False,
        "shadowOnly": True,
        "shadowMode": "shadow",
        "policy": policy.to_dict(),
    })
    add_relation(graph, stock_id, state_id, "HAS_MARKET_PSYCHOLOGY_STATE", weight=1.0, properties={
        "source": "market-psychology-shadow-v2",
        "signalGroup": "marketPsychology",
        "polarity": "context",
        "shadowOnly": True,
        "decisionImpactApplied": False,
        "freshnessStatus": str(payload.get("freshnessStatus") or "unknown"),
        "evidenceUsableForJudgement": state != "insufficient",
    })
    for component_id in component_ids:
        add_relation(graph, state_id, component_id, "COMPOSED_FROM_PSYCHOLOGY", weight=1.0, properties={
            "source": "market-psychology-shadow-v2",
            "shadowOnly": True,
        })
    return payload
