"""Canonical business-domain modules layered over the compatibility TBox."""

from dataclasses import replace
from typing import Dict, Iterable, List, Set

from .ontology_tbox_contracts import (
    TBoxBoundedContext,
    TBoxClassDef,
    TBoxRelationDef,
    TBoxRuleDef,
)


ONTOLOGY_DOMAIN_TBOX_VERSION = "investment-domain-tbox-v1"


DOMAIN_BOUNDED_CONTEXTS = [
    TBoxBoundedContext("account-identity", "투자자와 계좌", "투자자, 외부 증권계좌, 포트폴리오 집계 관계를 정의합니다."),
    TBoxBoundedContext("portfolio-ledger", "포트폴리오 원장", "포지션 로트, 현금, 원가, 기업행동과 원장 재구성을 정의합니다."),
    TBoxBoundedContext("investment-mandate", "투자 정책", "손실 예산, 현금 하한, 포지션·섹터·통화 한도와 허용 행동을 정의합니다."),
    TBoxBoundedContext("asset-knowledge", "자산과 기업 지식", "회사, 증권, 상장 라인, 시장, 섹터와 지속 관계를 정의합니다."),
    TBoxBoundedContext("market-observation", "시장 관측", "시세, 수급, 기술·거시 관측과 출처·신선도를 정의합니다."),
    TBoxBoundedContext("research-evidence", "조사와 근거", "뉴스, 공시, 조사 주장, 검증과 반박 근거를 정의합니다."),
    TBoxBoundedContext("risk-exposure", "위험과 노출", "종목·섹터·통화·팩터 노출과 정책 한도 비교를 정의합니다."),
    TBoxBoundedContext("allocation-rebalance", "배분과 리밸런싱", "목표 밴드, 배분 이탈, 리밸런싱 제안과 제약을 정의합니다."),
    TBoxBoundedContext("decision-intelligence", "투자 의사결정", "질문, 가설, 근거 비교, 추론과 판단 에피소드를 정의합니다."),
    TBoxBoundedContext("trade-execution", "주문과 체결", "실행계획, 주문 의도, 체결, 정산과 대사를 정의합니다."),
    TBoxBoundedContext("outcome-learning", "결과와 학습", "관측 결과, 성과 귀속과 판단 리뷰를 정의합니다."),
    TBoxBoundedContext("notification-delivery", "알림 전달", "투자 의미와 분리된 알림 의도, 전달 정책과 결과를 정의합니다."),
    TBoxBoundedContext("operations-audit", "운영과 감사", "파이프라인, 장애, 작업 추적과 운영 감사를 정의합니다."),
]


DOMAIN_CLASS_DEFS = [
    TBoxClassDef("Investor", "account-identity", "투자자"),
    TBoxClassDef("BrokerageAccount", "account-identity", "증권 계좌", parent="Account"),
    TBoxClassDef("PortfolioLedger", "portfolio-ledger", "포트폴리오 원장"),
    TBoxClassDef("PortfolioLedgerEntry", "portfolio-ledger", "포트폴리오 원장 항목"),
    TBoxClassDef("PortfolioReconciliation", "portfolio-ledger", "포트폴리오 잔고 대사"),
    TBoxClassDef("InferredPortfolioActivity", "portfolio-ledger", "실계좌 잔고 변화 추론 활동"),
    TBoxClassDef("PortfolioActivityEpisode", "portfolio-ledger", "동일 스냅샷 잔고 변화 에피소드"),
    TBoxClassDef("PortfolioStateSnapshot", "portfolio-ledger", "원장 기반 파생 포트폴리오 상태"),
    TBoxClassDef("DecisionActionObservation", "outcome-learning", "이전 판단 이후 관측된 계좌 행동"),
    TBoxClassDef("PositionLot", "portfolio-ledger", "포지션 로트"),
    TBoxClassDef("CashBalance", "portfolio-ledger", "현금 잔액"),
    TBoxClassDef("CashMovement", "portfolio-ledger", "현금 이동"),
    TBoxClassDef("InvestmentMandate", "investment-mandate", "투자 정책"),
    TBoxClassDef("MandateVersion", "investment-mandate", "투자 정책 버전"),
    TBoxClassDef("TargetAllocation", "investment-mandate", "목표 배분"),
    TBoxClassDef("AllocationBand", "investment-mandate", "허용 배분 구간"),
    TBoxClassDef("PositionLimit", "investment-mandate", "종목 비중 한도"),
    TBoxClassDef("SectorLimit", "investment-mandate", "섹터 비중 한도"),
    TBoxClassDef("CurrencyLimit", "investment-mandate", "통화 노출 한도"),
    TBoxClassDef("CashFloor", "investment-mandate", "최소 현금 비중"),
    TBoxClassDef("LossBudget", "investment-mandate", "손실 예산"),
    TBoxClassDef("AssetRole", "investment-mandate", "포트폴리오 내 자산 역할"),
    TBoxClassDef("ExposureSnapshot", "risk-exposure", "노출 스냅샷"),
    TBoxClassDef("PositionExposure", "risk-exposure", "종목 노출", parent="ExposureSnapshot"),
    TBoxClassDef("PortfolioRiskSnapshot", "risk-exposure", "포트폴리오 위험 스냅샷"),
    TBoxClassDef("PositionRiskMetric", "risk-exposure", "종목 시계열 위험 지표"),
    TBoxClassDef("PairwiseCorrelation", "risk-exposure", "종목 쌍 상관 지표"),
    TBoxClassDef("RiskBreach", "risk-exposure", "위험 한도 초과", parent="Risk"),
    TBoxClassDef("AllocationDrift", "allocation-rebalance", "배분 이탈"),
    TBoxClassDef("RebalanceProposal", "allocation-rebalance", "리밸런싱 제안"),
    TBoxClassDef("RebalanceLeg", "allocation-rebalance", "리밸런싱 실행 항목"),
    TBoxClassDef("RebalanceScenario", "allocation-rebalance", "리밸런싱 비교 시나리오"),
    TBoxClassDef("PortfolioDecisionCycle", "allocation-rebalance", "계좌 의사결정 후보 주기"),
    TBoxClassDef("PortfolioActionCandidate", "allocation-rebalance", "정책 산술 행동 후보"),
    TBoxClassDef("ActionEnvelope", "trade-execution", "실행 가능 범위"),
    TBoxClassDef("ActionPlan", "trade-execution", "실행 계획"),
    TBoxClassDef("ActionPlanReview", "trade-execution", "실행 계획 승인 감사"),
    TBoxClassDef("OrderIntent", "trade-execution", "주문 의도"),
    TBoxClassDef("Order", "trade-execution", "주문"),
    TBoxClassDef("TradeFill", "trade-execution", "체결"),
    TBoxClassDef("ExecutionEpisode", "trade-execution", "실행 에피소드"),
    TBoxClassDef("ExecutionReconciliation", "trade-execution", "체결 대사"),
    TBoxClassDef("PerformanceAttribution", "outcome-learning", "성과 귀속"),
    TBoxClassDef("DecisionReview", "outcome-learning", "판단 리뷰"),
    TBoxClassDef("DeliveryReceipt", "notification-delivery", "알림 전달 결과"),
]


DOMAIN_RELATION_DEFS = [
    TBoxRelationDef("OWNS_ACCOUNT", "account-identity", "account-identity", "account-identity"),
    TBoxRelationDef("AGGREGATES_ACCOUNT", "account-identity", "portfolio-ledger", "account-identity"),
    TBoxRelationDef("GOVERNED_BY_MANDATE", "investment-mandate", "portfolio-ledger", "investment-mandate"),
    TBoxRelationDef("RECONCILES_PORTFOLIO", "portfolio-ledger", "portfolio-ledger", "portfolio-ledger"),
    TBoxRelationDef("RECORDS_PORTFOLIO_ACTIVITY", "portfolio-ledger", "portfolio-ledger", "portfolio-ledger"),
    TBoxRelationDef("INFERRED_FROM_SNAPSHOT_CHANGE", "portfolio-ledger", "portfolio-ledger", "portfolio-ledger"),
    TBoxRelationDef("GROUPS_LEDGER_ACTIVITY", "portfolio-ledger", "portfolio-ledger", "portfolio-ledger"),
    TBoxRelationDef("HAS_PORTFOLIO_ACTIVITY", "portfolio-ledger", "asset-knowledge", "portfolio-ledger"),
    TBoxRelationDef("HAS_PORTFOLIO_STATE", "portfolio-ledger", "asset-knowledge", "portfolio-ledger"),
    TBoxRelationDef("OBSERVES_ACCOUNT_ACTION", "outcome-learning", "asset-knowledge", "outcome-learning"),
    TBoxRelationDef("OBSERVED_AFTER_DECISION", "outcome-learning", "outcome-learning", "decision-intelligence"),
    TBoxRelationDef("HAS_TARGET_BAND", "investment-mandate", "investment-mandate", "investment-mandate"),
    TBoxRelationDef("HAS_RISK_LIMIT", "investment-mandate", "investment-mandate", "investment-mandate"),
    TBoxRelationDef("HAS_EXPOSURE", "risk-exposure", "portfolio-ledger", "risk-exposure"),
    TBoxRelationDef("HAS_RISK_SNAPSHOT", "risk-exposure", "portfolio-ledger", "risk-exposure"),
    TBoxRelationDef("HAS_POSITION_RISK", "risk-exposure", "asset-knowledge", "risk-exposure"),
    TBoxRelationDef("HAS_CORRELATION_RISK", "risk-exposure", "portfolio-ledger", "risk-exposure"),
    TBoxRelationDef("BREACHES_LIMIT", "risk-exposure", "risk-exposure", "investment-mandate"),
    TBoxRelationDef("DRIFTS_FROM_TARGET", "allocation-rebalance", "risk-exposure", "investment-mandate"),
    TBoxRelationDef("PROPOSES_REBALANCE_LEG", "allocation-rebalance", "allocation-rebalance", "allocation-rebalance"),
    TBoxRelationDef("HAS_REBALANCE_PROPOSAL", "allocation-rebalance", "portfolio-ledger", "allocation-rebalance"),
    TBoxRelationDef("HAS_REBALANCE_SCENARIO", "allocation-rebalance", "allocation-rebalance", "allocation-rebalance"),
    TBoxRelationDef("EVALUATES_PORTFOLIO_CANDIDATE", "allocation-rebalance", "allocation-rebalance", "allocation-rebalance"),
    TBoxRelationDef("OBSERVES_DECISION_CYCLE", "allocation-rebalance", "portfolio-ledger", "allocation-rebalance"),
    TBoxRelationDef("CONSTRAINED_BY_ENVELOPE", "trade-execution", "trade-execution", "trade-execution"),
    TBoxRelationDef("PROPOSES_ACTION_PLAN", "trade-execution", "decision-intelligence", "trade-execution"),
    TBoxRelationDef("EXECUTES_ACTION_PLAN", "trade-execution", "trade-execution", "trade-execution"),
    TBoxRelationDef("REVIEWS_ACTION_PLAN", "trade-execution", "trade-execution", "trade-execution"),
    TBoxRelationDef("FILLS_ORDER", "trade-execution", "trade-execution", "trade-execution"),
    TBoxRelationDef("MATCHES_DECISION", "trade-execution", "trade-execution", "decision-intelligence"),
    TBoxRelationDef("DEVIATES_FROM_PLAN", "trade-execution", "trade-execution", "trade-execution"),
    TBoxRelationDef("PRODUCES_OUTCOME", "outcome-learning", "decision-intelligence", "outcome-learning"),
    TBoxRelationDef("ATTRIBUTED_TO", "outcome-learning", "outcome-learning", "decision-intelligence"),
    TBoxRelationDef("REVIEWS_DECISION", "outcome-learning", "outcome-learning", "decision-intelligence"),
    TBoxRelationDef("DELIVERED_AS", "notification-delivery", "decision-intelligence", "notification-delivery"),
]


DOMAIN_RULE_DEFS = [
    TBoxRuleDef("portfolio policy values are source-backed ABox facts and TypeDB compares exposure deltas with zero", "risk-exposure"),
    TBoxRuleDef("inferred portfolio activity requires two complete account balance observations and never asserts unknown fees or realised profit", "portfolio-ledger"),
    TBoxRuleDef("a decision references one mandate version, one source ABox snapshot, and one inference generation", "decision-intelligence"),
    TBoxRuleDef("an executable action plan must remain inside cash, quantity, and mandate constraints", "trade-execution"),
    TBoxRuleDef("broker fills are immutable and idempotent by provider execution identity", "trade-execution"),
    TBoxRuleDef("notification delivery never changes the investment meaning selected before dispatch", "notification-delivery"),
]


CLASS_CONTEXT_OVERRIDES: Dict[str, str] = {
    "Account": "account-identity",
    "Portfolio": "portfolio-ledger",
    "Position": "portfolio-ledger",
    "Cash": "portfolio-ledger",
    "Watchlist": "investment-mandate",
    "WatchlistCandidate": "investment-mandate",
    "Instrument": "asset-knowledge",
    "Security": "asset-knowledge",
    "Company": "asset-knowledge",
    "InstrumentAnchor": "asset-knowledge",
    "SecurityLine": "asset-knowledge",
    "Sector": "asset-knowledge",
    "Industry": "asset-knowledge",
    "Market": "asset-knowledge",
    "Currency": "asset-knowledge",
    "Observation": "market-observation",
    "PriceObservation": "market-observation",
    "VolumeObservation": "market-observation",
    "TechnicalObservation": "market-observation",
    "FlowObservation": "market-observation",
    "DataQuality": "market-observation",
    "DataSource": "market-observation",
    "DataFreshness": "market-observation",
    "Provenance": "market-observation",
    "NewsEvent": "research-evidence",
    "NewsArticle": "research-evidence",
    "DisclosureEvent": "research-evidence",
    "DisclosureFiling": "research-evidence",
    "ResearchEvidence": "research-evidence",
    "MarketExposure": "risk-exposure",
    "CurrencyExposure": "risk-exposure",
    "SectorExposure": "risk-exposure",
    "FactorExposure": "risk-exposure",
    "InvestmentQuestion": "decision-intelligence",
    "HypothesisSet": "decision-intelligence",
    "DecisionGuardrail": "decision-intelligence",
    "DecisionAbstention": "decision-intelligence",
    "DecisionEpisode": "decision-intelligence",
    "ObservedOutcome": "outcome-learning",
    "NotificationIntent": "notification-delivery",
    "OntologyWorld": "operations-audit",
    "PortfolioWorld": "operations-audit",
    "MarketWorld": "operations-audit",
    "KnowledgeWorld": "operations-audit",
}


def _merge_unique(existing: Iterable[object], additions: Iterable[object], key) -> List[object]:
    result = list(existing or [])
    seen: Set[str] = {str(key(item)) for item in result}
    for item in additions or []:
        value = str(key(item))
        if value not in seen:
            result.append(item)
            seen.add(value)
    return result


def apply_domain_tbox(
    contexts: Iterable[TBoxBoundedContext],
    classes: Iterable[TBoxClassDef],
    relations: Iterable[TBoxRelationDef],
    rules: Iterable[TBoxRuleDef],
):
    merged_contexts = _merge_unique(contexts, DOMAIN_BOUNDED_CONTEXTS, lambda item: item.key)
    remapped_classes = [
        replace(item, bounded_context=CLASS_CONTEXT_OVERRIDES.get(item.name, item.bounded_context))
        for item in classes or []
    ]
    merged_classes = _merge_unique(remapped_classes, DOMAIN_CLASS_DEFS, lambda item: item.name)
    merged_relations = _merge_unique(relations, DOMAIN_RELATION_DEFS, lambda item: item.name.upper())
    merged_rules = _merge_unique(rules, DOMAIN_RULE_DEFS, lambda item: item.text)
    return merged_contexts, merged_classes, merged_relations, merged_rules


def tbox_domain_validation(
    contexts: Iterable[TBoxBoundedContext],
    classes: Iterable[TBoxClassDef],
    relations: Iterable[TBoxRelationDef],
) -> Dict[str, object]:
    context_keys = {item.key for item in contexts or []}
    class_names = {item.name for item in classes or []}
    missing_parent_classes = sorted({item.parent for item in classes or [] if item.parent and item.parent not in class_names})
    missing_class_contexts = sorted({item.bounded_context for item in classes or [] if item.bounded_context not in context_keys})
    missing_relation_contexts = sorted({
        value
        for item in relations or []
        for value in (item.bounded_context, item.source_context, item.target_context)
        if value and value not in context_keys
    })
    return {
        "version": ONTOLOGY_DOMAIN_TBOX_VERSION,
        "valid": not (missing_parent_classes or missing_class_contexts or missing_relation_contexts),
        "missingParentClasses": missing_parent_classes,
        "missingClassContexts": missing_class_contexts,
        "missingRelationContexts": missing_relation_contexts,
        "contextCount": len(context_keys),
        "classCount": len(class_names),
        "relationCount": len({item.name.upper() for item in relations or []}),
    }
