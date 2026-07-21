"""Build investment decisions from TypeDB inference state.

Raw prices, returns, moving averages and flow observations remain numeric.
This module intentionally contains no aggregate buy, sell or exit score engine.
"""

from typing import Dict, Iterable, List

from .investment_research import build_active_investment_opinion
from .market_data import number
from .ontology_prompting import ONTOLOGY_PROMPT_VERSION
from .portfolio import DecisionItem, PortfolioSummary, Position
from .portfolio_ontology_builder import build_portfolio_ontology


ONTOLOGY_INFERENCE_REQUIRED_BASIS = "ontologyInferenceRequired"
TYPEDB_INFERENCE_BASIS = "typedbInferenceBox"
GRAPH_STORE_INFERENCE_BASIS = TYPEDB_INFERENCE_BASIS
LEGACY_GRAPH_STORE_INFERENCE_BASIS = "graphStoreInferenceBox"
GRAPH_INFERENCE_BASES = {
    TYPEDB_INFERENCE_BASIS,
    LEGACY_GRAPH_STORE_INFERENCE_BASIS,
}


class StrategyModel:
    """Compatibility container for account settings used by ontology reasoning."""

    def __init__(self, settings: Dict[str, str]):
        self.settings = dict(settings or {})


def baseline_position_payload(position: Position) -> Dict[str, object]:
    return {
        "symbol": position.symbol,
        "name": position.name,
        "sector": position.sector,
        "market": position.market,
        "currency": position.currency,
        "marketValue": position.market_value,
        "profitLoss": position.profit_loss,
        "profitLossRate": round(number(position.profit_loss_rate), 2),
        "decision": "온톨로지 추론 대기",
        "tone": "hold",
        "reviewLevel": "blocked",
        "dataState": "unavailable",
        "changeState": "unchanged",
        "conflictState": "context-only",
        "validationState": "blocked",
        "decisionBasis": ONTOLOGY_INFERENCE_REQUIRED_BASIS,
    }


def is_graph_inference_context(relation_context: Dict[str, object]) -> bool:
    if not isinstance(relation_context, dict) or not relation_context:
        return False
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    return (
        str(relation_context.get("source") or "") in GRAPH_INFERENCE_BASES
        and str(decision.get("basis") or "") in GRAPH_INFERENCE_BASES
        and bool(relation_context.get("graphStoreUsed"))
        and not bool(relation_context.get("fallbackUsed"))
    )


def inference_required_relation_context(position: Position, reason: str = "") -> Dict[str, object]:
    return {
        "engineVersion": "ontology-inference-required-v1",
        "source": "ontologyInferenceGate",
        "graphStoreUsed": False,
        "fallbackUsed": False,
        "blocked": True,
        "reason": reason or "TypeDB InferenceBox 관계가 없어 투자 판단을 만들지 않았습니다.",
        "subject": {
            "symbol": position.symbol,
            "name": position.name,
            "market": position.market,
            "sector": position.sector,
        },
        "facts": {
            "symbol": position.symbol,
            "name": position.name,
            "market": position.market,
            "currency": position.currency,
            "profitLossRate": round(number(position.profit_loss_rate), 2),
            "currentPrice": number(position.current_price),
            "ma20Distance": round(number(position.ma20_distance), 2),
            "ma60Distance": round(number(position.ma60_distance), 2),
            "volumeRatio": round(number(position.volume_ratio), 3),
        },
        "matchedRules": [],
        "activeRules": [],
        "referenceRules": [],
        "missingData": [{
            "key": GRAPH_STORE_INFERENCE_BASIS,
            "label": "TypeDB InferenceBox 추론 결과",
            "effect": "추론 결과가 없으면 매수·매도 판단을 만들지 않습니다.",
        }],
        "dominantSignals": [],
        "reviewLevel": "blocked",
        "reviewLevelLabel": "판단 보류",
        "dataState": "unavailable",
        "dataStateLabel": "자료 사용 불가",
        "changeState": "unchanged",
        "changeStateLabel": "이전과 같은 상태",
        "conflictState": "context-only",
        "conflictStateLabel": "방향을 정하기 어려운 참고 근거",
        "validationState": "blocked",
        "validationStateLabel": "판단 보류",
        "decision": {
            "label": "온톨로지 추론 대기",
            "tone": "hold",
            "basis": ONTOLOGY_INFERENCE_REQUIRED_BASIS,
            "selectedRuleId": "",
            "decisionStage": "ONTOLOGY_INFERENCE_REQUIRED",
            "actionGroup": "inferenceRequired",
            "actionLevel": "blocked",
            "reviewLevel": "blocked",
            "dataState": "unavailable",
            "changeState": "unchanged",
            "conflictState": "context-only",
            "validationState": "blocked",
        },
        "executionPlan": {
            "engineVersion": "ontology-inference-required-v1",
            "primaryAction": "WAIT_FOR_ONTOLOGY_INFERENCE",
            "primaryActionLabel": "온톨로지 추론 완료 전 투자 판단 보류",
            "blockedActions": [
                "InferenceBox 없는 매수 판단",
                "InferenceBox 없는 매도 판단",
                "Python 관계 규칙 fallback",
            ],
            "nextChecks": [
                "TypeDB native rule 저장 상태 확인",
                "InferenceBox 관계 생성 여부 확인",
                "온톨로지 자료 상태 확인",
            ],
            "missingDataImpact": ["온톨로지 추론 결과가 없어 판단을 보류했습니다."],
            "sourceFacts": {},
        },
        "promptContext": {
            "promptVersion": ONTOLOGY_PROMPT_VERSION,
            "promptId": "ontologyInferenceRequired",
            "missingData": [{
                "key": GRAPH_STORE_INFERENCE_BASIS,
                "label": "TypeDB InferenceBox 추론 결과",
            }],
            "guardrails": ["InferenceBox 없이 매수·매도 판단을 만들지 않습니다."],
        },
    }


def inference_required_decision_for_position(
    position: Position,
    ontology_opinion=None,
    ontology_worldview: Dict[str, object] = None,
    reason: str = "",
) -> DecisionItem:
    relation_context = inference_required_relation_context(position, reason)
    opinion_payload = ontology_opinion.to_dict() if ontology_opinion else {}
    return DecisionItem(
        symbol=position.symbol,
        name=position.name,
        sector=position.sector,
        market=position.market,
        currency=position.currency,
        market_value=position.market_value,
        profit_loss=position.profit_loss,
        profit_loss_rate=number(position.profit_loss_rate),
        decision="온톨로지 추론 대기",
        tone="hold",
        review_level="blocked",
        data_state="unavailable",
        change_state="unchanged",
        conflict_state="context-only",
        validation_state="blocked",
        decision_basis=ONTOLOGY_INFERENCE_REQUIRED_BASIS,
        ontology_opinion=opinion_payload,
        ontology_worldview=dict(ontology_worldview or {}),
        relation_rule_context=relation_context,
        ai_prompt_context=dict(relation_context.get("promptContext") or {}),
        active_investment_opinion={},
        ai_context={
            "promptVersion": ONTOLOGY_PROMPT_VERSION,
            "role": "ontology-inference-required",
            "stateContract": "review-data-change-evidence-validation",
            "relationRuleContext": relation_context,
            "blockedReason": relation_context["reason"],
        },
    )


def decision_for_position(
    position: Position,
    portfolio: PortfolioSummary,
    strategy_model: StrategyModel = None,
    legacy_payload: Dict[str, object] = None,
    ontology_opinion=None,
    ontology_worldview: Dict[str, object] = None,
    ontology_prompt: str = "",
    relation_context: Dict[str, object] = None,
    external_signals: Dict[str, object] = None,
    require_inference_context: bool = True,
) -> DecisionItem:
    del portfolio, strategy_model, legacy_payload, require_inference_context
    payload = baseline_position_payload(position)
    if not is_graph_inference_context(relation_context):
        return inference_required_decision_for_position(
            position,
            ontology_opinion=ontology_opinion,
            ontology_worldview=ontology_worldview,
            reason="TypeDB InferenceBox 결과가 없어 Python 관계 규칙 fallback을 차단했습니다.",
        )

    relation_decision = relation_context.get("decision") if isinstance(relation_context, dict) else {}
    if not isinstance(relation_decision, dict):
        relation_decision = {}
    prompt_context = relation_context.get("promptContext") if isinstance(relation_context, dict) else {}
    if not isinstance(prompt_context, dict):
        prompt_context = {}

    decision_label = str(relation_decision.get("label") or payload.get("decision") or "")
    decision_tone = str(relation_decision.get("tone") or payload.get("tone") or "")
    decision_basis = str(relation_decision.get("basis") or TYPEDB_INFERENCE_BASIS)
    review_level = str(relation_decision.get("reviewLevel") or relation_context.get("reviewLevel") or "check")
    data_state = str(relation_decision.get("dataState") or relation_context.get("dataState") or "partial")
    change_state = str(relation_decision.get("changeState") or relation_context.get("changeState") or "unchanged")
    conflict_state = str(relation_decision.get("conflictState") or relation_context.get("conflictState") or "context-only")
    validation_state = str(relation_decision.get("validationState") or relation_context.get("validationState") or "conditional")
    opinion_payload = ontology_opinion.to_dict() if ontology_opinion else {}
    worldview = dict(ontology_worldview or {})
    active_opinion = build_active_investment_opinion(
        position,
        relation_context=relation_context,
        ontology_opinion=opinion_payload,
        legacy_model={},
        external_signals=external_signals or {},
    ).to_dict()
    return DecisionItem(
        symbol=position.symbol,
        name=position.name,
        sector=position.sector,
        market=position.market,
        currency=position.currency,
        market_value=position.market_value,
        profit_loss=position.profit_loss,
        profit_loss_rate=float(payload.get("profitLossRate") or 0),
        decision=decision_label,
        tone=decision_tone,
        review_level=review_level,
        data_state=data_state,
        change_state=change_state,
        conflict_state=conflict_state,
        validation_state=validation_state,
        decision_basis=decision_basis,
        ontology_opinion=opinion_payload,
        ontology_worldview=worldview,
        relation_rule_context=relation_context,
        ai_prompt_context=prompt_context,
        active_investment_opinion=active_opinion,
        ai_context={
            "promptVersion": prompt_context.get("promptVersion") or ONTOLOGY_PROMPT_VERSION,
            "role": "ontology-relation-rule-ai-review",
            "stateContract": "review-data-change-evidence-validation",
            "worldview": worldview,
            "opinion": opinion_payload,
            "activeInvestmentOpinion": active_opinion,
            "prompt": ontology_prompt,
            "relationRuleContext": relation_context,
            "promptContext": prompt_context,
            "promptTemplate": prompt_context.get("promptTemplate") if isinstance(prompt_context, dict) else {},
        },
    )


def decisions_for_positions(
    positions: Iterable[Position],
    portfolio: PortfolioSummary,
    strategy_model: StrategyModel = None,
    external_signals: Dict[str, object] = None,
    relation_contexts_by_symbol: Dict[str, Dict[str, object]] = None,
    runtime_context: Dict[str, object] = None,
    require_inference_context: bool = True,
) -> List[DecisionItem]:
    active_positions = [item for item in positions if not item.is_cash() and item.market_value > 0]
    relation_contexts_by_symbol = relation_contexts_by_symbol or {}
    decision_runtime_context = dict(runtime_context or {})
    decision_runtime_context["settings"] = (
        getattr(strategy_model, "settings", {})
        if strategy_model
        else dict(decision_runtime_context.get("settings") or {})
    )
    decision_runtime_context["decisionItems"] = []
    ontology = build_portfolio_ontology(
        active_positions,
        portfolio,
        legacy_by_symbol={},
        external_signals=external_signals or {},
        runtime_context=decision_runtime_context,
    )
    decisions = [
        decision_for_position(
            item,
            portfolio,
            strategy_model,
            ontology_opinion=None,
            ontology_worldview=ontology.worldview,
            ontology_prompt=ontology.prompt,
            relation_context=relation_contexts_by_symbol.get(item.symbol.upper()),
            external_signals=external_signals or {},
            require_inference_context=require_inference_context,
        )
        for item in active_positions
    ]
    review_order = {
        "immediate": 0,
        "act": 1,
        "check": 2,
        "observe": 3,
        "normal": 4,
        "blocked": 5,
    }
    return sorted(decisions, key=lambda item: (review_order.get(item.review_level, 4), item.symbol))
