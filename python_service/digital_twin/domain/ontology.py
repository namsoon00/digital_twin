import json
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

from .investment_research import build_active_investment_opinion, research_evidence_from_external_signals, research_evidence_from_facts
from .market_data import clamp, number
from .ontology_tbox import (
    BOUNDED_CONTEXTS,
    TBOX_CLASSES,
    TBOX_REASONING_RULES,
    TBOX_RELATION_TYPES,
    bounded_contexts_payload,
    class_definitions_payload,
    relation_definitions_payload,
    rule_definitions_payload,
    tbox_class_def,
    tbox_relation_def,
)
from .ontology_rules import evaluate_position_relation_rules
from .portfolio import PortfolioSummary, Position


ONTOLOGY_PROMPT_VERSION = "ontology-investment-v2-tbox-abox"

SENSITIVE_SETTING_TOKENS = ("secret", "token", "password", "clientid", "client_id", "accountseq", "account_seq", "chatid", "chat_id", "key")

METRIC_CONCEPTS = [
    ("quantity", "수량", "Position", "HAS_POSITION", "position", "quantity"),
    ("sellable_quantity", "매도 가능 수량", "Position", "HAS_POSITION", "position", "sellableQuantity"),
    ("average_price", "평단", "PriceMetric", "HAS_PRICE", "price", "averagePrice"),
    ("current_price", "현재가", "PriceMetric", "HAS_PRICE", "price", "currentPrice"),
    ("market_value", "평가액", "PriceMetric", "HAS_PRICE", "price", "marketValue"),
    ("profit_loss", "평가손익", "PriceMetric", "HAS_PRICE", "price", "profitLoss"),
    ("profit_loss_rate", "손익률", "PriceMetric", "HAS_PRICE", "price", "profitLossRate"),
    ("change_rate", "가격 변화율", "PriceMetric", "HAS_PRICE", "price", "changeRate"),
    ("volume", "거래량", "TradeFlow", "HAS_TRADE_FLOW", "flow", "volume"),
    ("volume_ratio", "거래량 배율", "TradeFlow", "HAS_TRADE_FLOW", "flow", "volumeRatio"),
    ("trade_strength", "체결강도", "TradeFlow", "HAS_TRADE_FLOW", "flow", "tradeStrength"),
    ("trading_value", "거래대금", "TradeFlow", "HAS_TRADE_FLOW", "flow", "tradingValue"),
    ("buy_volume", "매수 체결량", "TradeFlow", "HAS_TRADE_FLOW", "flow", "buyVolume"),
    ("sell_volume", "매도 체결량", "TradeFlow", "HAS_TRADE_FLOW", "flow", "sellVolume"),
    ("orderbook_bid_volume", "매수호가 잔량", "TradeFlow", "HAS_TRADE_FLOW", "flow", "orderbookBidVolume"),
    ("orderbook_ask_volume", "매도호가 잔량", "TradeFlow", "HAS_TRADE_FLOW", "flow", "orderbookAskVolume"),
    ("bid_ask_imbalance", "호가 불균형", "TradeFlow", "HAS_TRADE_FLOW", "flow", "bidAskImbalance"),
    ("foreign_net_volume", "외국인 순매수", "TradeFlow", "HAS_TRADE_FLOW", "flow", "foreignNetVolume"),
    ("foreign_net_amount", "외국인 순매수 금액", "TradeFlow", "HAS_TRADE_FLOW", "flow", "foreignNetAmount"),
    ("institution_net_volume", "기관 순매수", "TradeFlow", "HAS_TRADE_FLOW", "flow", "institutionNetVolume"),
    ("institution_net_amount", "기관 순매수 금액", "TradeFlow", "HAS_TRADE_FLOW", "flow", "institutionNetAmount"),
    ("individual_net_volume", "개인 순매수", "TradeFlow", "HAS_TRADE_FLOW", "flow", "individualNetVolume"),
    ("individual_net_amount", "개인 순매수 금액", "TradeFlow", "HAS_TRADE_FLOW", "flow", "individualNetAmount"),
    ("ma5", "5일선", "TechnicalIndicator", "HAS_TECHNICAL_INDICATOR", "technical", "ma5"),
    ("ma20", "20일선", "TechnicalIndicator", "HAS_TECHNICAL_INDICATOR", "technical", "ma20"),
    ("ma60", "60일선", "TechnicalIndicator", "HAS_TECHNICAL_INDICATOR", "technical", "ma60"),
    ("ma120", "120일선", "TechnicalIndicator", "HAS_TECHNICAL_INDICATOR", "technical", "ma120"),
    ("ma200", "200일선", "TechnicalIndicator", "HAS_TECHNICAL_INDICATOR", "technical", "ma200"),
    ("ma20_slope", "20일선 기울기", "TechnicalIndicator", "HAS_TECHNICAL_INDICATOR", "technical", "ma20Slope"),
    ("ma60_slope", "60일선 기울기", "TechnicalIndicator", "HAS_TECHNICAL_INDICATOR", "technical", "ma60Slope"),
    ("ma20_distance", "20일선 괴리", "TechnicalIndicator", "HAS_TECHNICAL_INDICATOR", "technical", "ma20Distance"),
    ("ma60_distance", "60일선 괴리", "TechnicalIndicator", "HAS_TECHNICAL_INDICATOR", "technical", "ma60Distance"),
]

SETTING_CONCEPT_TYPES = {
    "ontologyRelationRules": ("AlertRule", "HAS_ALERT_RULE"),
    "aiPromptTemplates": ("PromptTemplate", "HAS_PROMPT_TEMPLATE"),
    "aiPromptPolicy": ("NotificationPolicy", "HAS_NOTIFICATION_POLICY"),
    "alertThresholds": ("Threshold", "HAS_THRESHOLD"),
    "relationRuleThresholds": ("Threshold", "HAS_THRESHOLD"),
    "modelDecisionThresholds": ("Threshold", "HAS_THRESHOLD"),
    "formulaWeights": ("Threshold", "HAS_THRESHOLD"),
    "alertCadenceMinutes": ("CooldownPolicy", "HAS_COOLDOWN_POLICY"),
    "externalApiFetchIntervalMinutes": ("CollectionSchedule", "RUNS_ON_SCHEDULE"),
    "marketSnapshotIntervalMinutes": ("CollectionSchedule", "RUNS_ON_SCHEDULE"),
    "watchlistSnapshotIntervalMinutes": ("CollectionSchedule", "RUNS_ON_SCHEDULE"),
    "externalSignalsIntervalMinutes": ("CollectionSchedule", "RUNS_ON_SCHEDULE"),
    "notificationNoveltyThreshold": ("NoveltyPolicy", "HAS_NOVELTY_POLICY"),
    "notificationCooldownMinutes": ("CooldownPolicy", "HAS_COOLDOWN_POLICY"),
    "buyScoreFormula": ("StrategySignal", "CONFIGURES"),
    "sellScoreFormula": ("StrategySignal", "CONFIGURES"),
    "customBuyModelFormula": ("StrategySignal", "CONFIGURES"),
    "customSellModelFormula": ("StrategySignal", "CONFIGURES"),
    "profitTakeScoreFormula": ("StrategySignal", "CONFIGURES"),
    "lossCutScoreFormula": ("StrategySignal", "CONFIGURES"),
    "notificationScoreFormula": ("NotificationPolicy", "HAS_NOTIFICATION_POLICY"),
}

OPERATIONAL_PIPELINES = [
    {
        "key": "marketSnapshot",
        "label": "marketSnapshot",
        "tboxClasses": ["DataPipeline", "MarketSnapshot"],
        "sourceKey": "account-market-data",
        "sourceLabel": "계좌·보유·시세 데이터",
        "scheduleKey": "marketSnapshotIntervalMinutes",
        "defaultMinutes": 3,
        "dataKinds": ["portfolio", "positions", "quotes", "cash"],
        "description": "보유 잔고, 현재가, 현금, 포트폴리오 노출을 갱신합니다.",
    },
    {
        "key": "watchlistSnapshot",
        "label": "watchlistSnapshot",
        "tboxClasses": ["DataPipeline", "WatchlistSnapshot"],
        "sourceKey": "watchlist-market-data",
        "sourceLabel": "관심종목 시세 데이터",
        "scheduleKey": "watchlistSnapshotIntervalMinutes",
        "defaultMinutes": 5,
        "dataKinds": ["watchlist", "quotes", "technicalIndicators"],
        "description": "관심 종목의 시세, 추세, 진입 관찰 데이터를 갱신합니다.",
    },
    {
        "key": "externalSignals",
        "label": "externalSignals",
        "tboxClasses": ["DataPipeline", "ExternalSignalCollection"],
        "sourceKey": "external-signal-data",
        "sourceLabel": "뉴스·공시·거시·크립토 외부 신호",
        "scheduleKey": "externalSignalsIntervalMinutes",
        "fallbackSettingKey": "externalApiFetchIntervalMinutes",
        "defaultMinutes": 30,
        "dataKinds": ["news", "disclosures", "macro", "crypto", "foreignMarket"],
        "description": "외부 신호를 수집해 종목과 포트폴리오 관계에 연결합니다.",
    },
]

INSIGHT_TYPES = [
    ("riskIncrease", "리스크 증가"),
    ("opportunityDetected", "기회 포착"),
    ("contradictionDetected", "관계 충돌"),
    ("dataQualityWarning", "데이터 품질 경고"),
    ("portfolioExposureShift", "포트폴리오 노출 변화"),
    ("watchlistEntrySignal", "관심종목 진입 관찰"),
]

FACTOR_BENCHMARKS = {
    "KR": ("benchmark:KOSPI", "KOSPI"),
    "KOSPI": ("benchmark:KOSPI", "KOSPI"),
    "KOSDAQ": ("benchmark:KOSDAQ", "KOSDAQ"),
    "US": ("benchmark:NASDAQ", "NASDAQ/US Growth"),
    "USA": ("benchmark:NASDAQ", "NASDAQ/US Growth"),
    "NASDAQ": ("benchmark:NASDAQ", "NASDAQ/US Growth"),
    "NYSE": ("benchmark:SP500", "S&P 500"),
    "CRYPTO": ("benchmark:BTC", "Bitcoin/Crypto Liquidity"),
}

SECTOR_FACTORS = {
    "반도체": ["AI 인프라", "반도체 사이클", "수출 민감"],
    "AI/플랫폼": ["AI/플랫폼 성장", "금리 민감 성장주", "광고·클라우드 수요"],
    "모빌리티": ["전기차 수요", "경기 민감 소비재"],
    "디지털자산": ["크립토 유동성", "위험자산 심리"],
}


def unique_list(values: Iterable[str]) -> List[str]:
    seen = set()
    rows: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows


def bounded_number(value: object, lower: float = 0.0, upper: float = 100.0) -> float:
    return clamp(number(value), lower, upper)


def prior_monitor_state(runtime_context: Dict[str, object]) -> Dict[str, object]:
    metadata = runtime_context.get("metadata") if isinstance(runtime_context, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    previous = {}
    if isinstance(runtime_context, dict):
        previous = metadata.get("previousMonitorState") or metadata.get("previousState") or runtime_context.get("previousMonitorState") or {}
    return previous if isinstance(previous, dict) else {}


def previous_position_state(runtime_context: Dict[str, object], symbol: str, source: str = "holding") -> Dict[str, object]:
    previous = prior_monitor_state(runtime_context)
    container_key = "watchlist" if source == "watchlist" else "positions"
    rows = previous.get(container_key) if isinstance(previous.get(container_key), dict) else {}
    item = rows.get(str(symbol or "").upper()) if isinstance(rows, dict) else {}
    return item if isinstance(item, dict) else {}


def previous_decision_state(runtime_context: Dict[str, object], symbol: str) -> Dict[str, object]:
    previous = prior_monitor_state(runtime_context)
    rows = previous.get("decisions") if isinstance(previous.get("decisions"), dict) else {}
    item = rows.get(str(symbol or "").upper()) if isinstance(rows, dict) else {}
    return item if isinstance(item, dict) else {}


def factor_labels_for_position(position: Position) -> List[str]:
    labels = []
    sector = str(position.sector or "").strip()
    labels.extend(SECTOR_FACTORS.get(sector, []))
    currency = str(position.currency or "").upper().strip()
    market = str(position.market or "").upper().strip()
    symbol = str(position.symbol or "").upper().strip()
    if currency and currency != "KRW":
        labels.append(currency + " 환율")
    if market in {"US", "USA", "NASDAQ", "NYSE"}:
        labels.append("미국 주식 베타")
    if market in {"KR", "KOSPI", "KOSDAQ"} or currency == "KRW":
        labels.append("한국 시장 베타")
    if symbol in {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}:
        labels.append("비트코인 민감도")
    return unique_list(labels)


def benchmark_for_position(position: Position) -> (str, str):
    market = str(position.market or "").upper().strip()
    return FACTOR_BENCHMARKS.get(market, ("benchmark:MARKET", "시장 벤치마크"))


def event_tbox_classes(item: object) -> List[str]:
    kind = str(getattr(item, "kind", "") or "").lower()
    classes = ["Observation", "ExternalObservation", "ResearchEvidence", "ExternalSignal", "Evidence"]
    if "news" in kind:
        classes.extend(["NewsEvent", "NewsArticle", "EventRisk"])
    elif "disclosure" in kind or "filing" in kind:
        classes.extend(["DisclosureEvent", "DisclosureFiling", "EventRisk"])
    elif "financial" in kind or "earning" in kind:
        classes.extend(["FundamentalObservation", "EarningsEvent", "ValuationSignal"])
    elif "market" in kind:
        classes.extend(["PriceObservation", "PriceSignal"])
    return unique_list(classes)


def event_relation_properties(item: object) -> Dict[str, object]:
    polarity = str(getattr(item, "polarity", "") or "context")
    impact = number(getattr(item, "impact_score", 0))
    props = {
        "source": "research-evidence",
        "polarity": polarity,
        "impactScore": round(impact, 2),
        "confidence": round(number(getattr(item, "confidence", 0)), 2),
        "aiInfluenceLabel": str(getattr(item, "title", "") or getattr(item, "kind", "") or "리서치 근거"),
    }
    if polarity == "risk":
        props["opinionImpact"] = min(18.0, max(4.0, impact))
    elif polarity == "support":
        props["supportImpact"] = min(14.0, max(3.0, impact))
    return props


def metric_tbox_classes(tbox_class: str, field_name: str) -> List[str]:
    if tbox_class == "PriceMetric":
        return ["Observation", "PriceObservation", "PriceMetric"]
    if tbox_class == "TechnicalIndicator":
        return ["Observation", "TechnicalObservation", "TechnicalIndicator", "TrendSignal"]
    if tbox_class == "TradeFlow":
        if field_name == "volume":
            return ["Observation", "VolumeObservation", "TradeFlow", "FlowSignal"]
        return ["Observation", "FlowObservation", "TradeFlow", "FlowSignal"]
    if tbox_class == "DataQuality":
        return ["Observation", "DataQuality", "DataQualitySignal"]
    return [tbox_class]


def instrument_tbox_classes(position: Position) -> List[str]:
    market = str(position.market or "").lower()
    symbol = str(position.symbol or "").upper()
    if market in {"crypto", "coin"} or symbol in {"BTC", "ETH", "SOL"}:
        return ["Instrument", "CryptoAsset"]
    if "etf" in str(position.name or "").lower():
        return ["Instrument", "ETF"]
    return ["Instrument", "Equity", "Stock"]


def external_signal_classes(group: str) -> List[str]:
    text = str(group or "").lower()
    classes = ["Observation", "ExternalObservation", "ExternalSignal", "Signal"]
    if "dart" in text or "disclosure" in text or "filing" in text:
        classes.extend(["DisclosureEvent", "DisclosureSignal", "EventRisk"])
    if "news" in text or "headline" in text:
        classes.extend(["NewsEvent", "EventRisk"])
    if "macro" in text or "rate" in text or "yield" in text:
        classes.extend(["MacroIndicator", "MacroSignal", "RateSignal", "RegimeRisk"])
    if "credit" in text or "spread" in text:
        classes.extend(["CreditSpreadSignal", "MacroSignal", "RegimeRisk"])
    if "crypto" in text or "coin" in text or "btc" in text:
        classes.extend(["CryptoMarketSignal", "CryptoSignal"])
    if "earning" in text or "result" in text:
        classes.extend(["EarningsEvent", "ValuationSignal"])
    if "regulat" in text or "policy" in text:
        classes.extend(["RegulatoryEvent", "EventRisk"])
    return unique_list(classes)


def risk_tbox_classes(label: str) -> List[str]:
    text = str(label or "")
    classes = ["Risk"]
    if any(token in text for token in ["비중", "노출", "집중", "단일 종목"]):
        classes.append("ConcentrationRisk")
    if any(token in text for token in ["수급", "유동성", "거래량", "체결"]):
        classes.append("LiquidityRisk")
    if any(token in text for token in ["추세", "손실", "수익", "가격"]):
        classes.append("MarketRisk")
    if any(token in text for token in ["데이터", "부족", "품질"]):
        classes.append("DataQualityRisk")
    if any(token in text for token in ["기존", "모델", "점수"]):
        classes.append("ModelRisk")
    if any(token in text for token in ["공시", "뉴스", "규제", "이벤트"]):
        classes.append("EventRisk")
    return unique_list(classes)


@dataclass
class OntologyEntity:
    entity_id: str
    label: str
    kind: str
    properties: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["id"] = payload.pop("entity_id")
        return payload


@dataclass
class OntologyRelation:
    source: str
    target: str
    relation_type: str
    weight: float = 1.0
    evidence_ids: List[str] = field(default_factory=list)
    properties: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["type"] = payload.pop("relation_type")
        return payload


@dataclass
class OntologyEvidence:
    evidence_id: str
    subject: str
    kind: str
    source: str
    summary: str
    value: Dict[str, object] = field(default_factory=dict)
    confidence: float = 0.7

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["id"] = payload.pop("evidence_id")
        return payload


@dataclass
class OntologyBelief:
    belief_id: str
    subject: str
    label: str
    polarity: str
    confidence: float
    evidence_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["id"] = payload.pop("belief_id")
        return payload


@dataclass
class OntologyOpinion:
    symbol: str
    action: str
    tone: str
    conviction: float
    ontology_pressure: float
    thesis: str
    supporting_beliefs: List[str] = field(default_factory=list)
    contradictions: List[str] = field(default_factory=list)
    dominant_risks: List[str] = field(default_factory=list)
    opportunities: List[str] = field(default_factory=list)
    legacy_model: Dict[str, object] = field(default_factory=dict)
    evidence_ids: List[str] = field(default_factory=list)
    relation_influences: List[Dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class PortfolioOntology:
    portfolio_id: str
    entities: List[OntologyEntity] = field(default_factory=list)
    relations: List[OntologyRelation] = field(default_factory=list)
    evidence: List[OntologyEvidence] = field(default_factory=list)
    beliefs: List[OntologyBelief] = field(default_factory=list)
    opinions: List[OntologyOpinion] = field(default_factory=list)
    reasoning_cards: List[Dict[str, object]] = field(default_factory=list)
    worldview: Dict[str, object] = field(default_factory=dict)
    prompt: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "portfolioId": self.portfolio_id,
            "tbox": ontology_tbox(),
            "abox": ontology_abox(self),
            "entities": [item.to_dict() for item in self.entities],
            "relations": [item.to_dict() for item in self.relations],
            "evidence": [item.to_dict() for item in self.evidence],
            "beliefs": [item.to_dict() for item in self.beliefs],
            "opinions": [item.to_dict() for item in self.opinions],
            "reasoningCards": list(self.reasoning_cards),
            "activeInvestmentOpinions": [
                dict((item.properties or {}).get("activeInvestmentOpinion") or {})
                for item in self.entities
                if item.kind == "active-opinion"
            ],
            "executionPlans": [
                dict((item.properties or {}).get("executionPlan") or {})
                for item in self.entities
                if item.kind == "execution-plan"
            ],
            "aiInferencePacket": build_ai_inference_packet(self),
            "worldview": dict(self.worldview or {}),
            "prompt": self.prompt,
            "promptVersion": ONTOLOGY_PROMPT_VERSION,
        }

    def opinion_for_symbol(self, symbol: str) -> Optional[OntologyOpinion]:
        target = str(symbol or "").upper()
        for opinion in self.opinions:
            if opinion.symbol.upper() == target:
                return opinion
        return None


def entity_id(kind: str, value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9가-힣_.:-]+", "-", str(value or "").strip())
    return kind + ":" + (normalized or "unknown")


def ontology_tbox() -> Dict[str, object]:
    return {
        "box": "TBox",
        "description": "Investment ontology schema: bounded contexts, classes, relation types, and reasoning rules.",
        "boundedContexts": bounded_contexts_payload(),
        "classes": list(TBOX_CLASSES),
        "classDefinitions": class_definitions_payload(),
        "relationTypes": list(TBOX_RELATION_TYPES),
        "relationDefinitions": relation_definitions_payload(),
        "reasoningRules": list(TBOX_REASONING_RULES),
        "reasoningRuleDefinitions": rule_definitions_payload(),
    }


def ontology_abox(graph: PortfolioOntology) -> Dict[str, object]:
    return {
        "box": "ABox",
        "description": "Runtime portfolio assertions: holdings, evidence, beliefs, and opinions.",
        "portfolioId": graph.portfolio_id,
        "entityCount": len([item for item in graph.entities if item.properties.get("ontologyBox") != "TBox"]),
        "relationCount": len([item for item in graph.relations if item.properties.get("ontologyBox") != "TBox"]),
        "evidenceCount": len(graph.evidence),
        "beliefCount": len(graph.beliefs),
        "opinionCount": len(graph.opinions),
    }


def tbox_entities() -> List[OntologyEntity]:
    entities = [
        OntologyEntity(entity_id("ontology-box", "TBox"), "TBox", "ontology-box", {
            "ontologyBox": "TBox",
            "description": "Schema layer for investment ontology concepts.",
        }),
        OntologyEntity(entity_id("ontology-box", "ABox"), "ABox", "ontology-box", {
            "ontologyBox": "TBox",
            "description": "Assertion layer for runtime portfolio facts.",
        }),
    ]
    for context in BOUNDED_CONTEXTS:
        entities.append(OntologyEntity(entity_id("bounded-context", context.key), context.label, "bounded-context", {
            "ontologyBox": "TBox",
            "box": "TBox",
            "boundedContext": context.key,
            "label": context.label,
            "description": context.description,
        }))
    for name in TBOX_CLASSES:
        definition = tbox_class_def(name)
        entities.append(OntologyEntity(entity_id("tbox-class", name), name, "tbox-class", {
            "ontologyBox": "TBox",
            "box": "TBox",
            "className": name,
            "boundedContext": definition.bounded_context if definition else "",
            "label": definition.label if definition else name,
            "parentClass": definition.parent if definition else "",
            "description": definition.description if definition else "",
        }))
    for name in TBOX_RELATION_TYPES:
        definition = tbox_relation_def(name)
        entities.append(OntologyEntity(entity_id("tbox-relation", name), name, "tbox-relation", {
            "ontologyBox": "TBox",
            "box": "TBox",
            "relationType": name,
            "boundedContext": definition.bounded_context if definition else "",
            "sourceContext": definition.source_context if definition else "",
            "targetContext": definition.target_context if definition else "",
            "description": definition.description if definition else "",
        }))
    return entities


def tbox_relations() -> List[OntologyRelation]:
    relations: List[OntologyRelation] = []
    tbox_id = entity_id("ontology-box", "TBox")
    abox_id = entity_id("ontology-box", "ABox")
    for context in BOUNDED_CONTEXTS:
        context_id = entity_id("bounded-context", context.key)
        relations.append(OntologyRelation(tbox_id, context_id, "DEFINES_BOUNDED_CONTEXT", properties={
            "ontologyBox": "TBox",
            "boundedContext": context.key,
        }))
    for name in TBOX_CLASSES:
        definition = tbox_class_def(name)
        class_id = entity_id("tbox-class", name)
        owner_id = entity_id("bounded-context", definition.bounded_context) if definition else tbox_id
        relations.append(OntologyRelation(owner_id, class_id, "DEFINES_CLASS", properties={
            "ontologyBox": "TBox",
            "boundedContext": definition.bounded_context if definition else "",
        }))
        if definition and definition.parent:
            relations.append(OntologyRelation(class_id, entity_id("tbox-class", definition.parent), "IS_A", properties={
                "ontologyBox": "TBox",
                "boundedContext": definition.bounded_context,
            }))
    for name in TBOX_RELATION_TYPES:
        definition = tbox_relation_def(name)
        owner_id = entity_id("bounded-context", definition.bounded_context) if definition else tbox_id
        relations.append(OntologyRelation(owner_id, entity_id("tbox-relation", name), "DEFINES_RELATION", properties={
            "ontologyBox": "TBox",
            "boundedContext": definition.bounded_context if definition else "",
            "sourceContext": definition.source_context if definition else "",
            "targetContext": definition.target_context if definition else "",
        }))
    relations.append(OntologyRelation(tbox_id, abox_id, "CONSTRAINS_ASSERTIONS", properties={"ontologyBox": "TBox"}))
    return relations


def abox_properties(properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = dict(properties or {})
    payload.setdefault("ontologyBox", "ABox")
    payload.setdefault("box", "ABox")
    if not payload.get("boundedContext"):
        class_names = []
        if payload.get("tboxClass"):
            class_names.append(str(payload.get("tboxClass")))
        class_names.extend(str(value) for value in payload.get("tboxClasses") or [] if value)
        for class_name in class_names:
            definition = tbox_class_def(class_name)
            if definition:
                payload["boundedContext"] = definition.bounded_context
                break
    return payload


def abox_relation_properties(relation_type: str, properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = abox_properties(properties or {})
    definition = tbox_relation_def(relation_type)
    if definition and not payload.get("boundedContext"):
        payload["boundedContext"] = definition.bounded_context
    if definition:
        payload.setdefault("sourceContext", definition.source_context)
        payload.setdefault("targetContext", definition.target_context)
    return payload


def add_entity(graph: PortfolioOntology, kind: str, value: str, label: str, properties: Dict[str, object] = None) -> str:
    item_id = entity_id(kind, value)
    graph.entities.append(OntologyEntity(item_id, label or str(value or item_id), kind, abox_properties(properties or {})))
    return item_id


def add_relation(
    graph: PortfolioOntology,
    source: str,
    target: str,
    relation_type: str,
    weight: float = 1.0,
    evidence_ids: List[str] = None,
    properties: Dict[str, object] = None,
) -> None:
    graph.relations.append(OntologyRelation(
        source,
        target,
        relation_type,
        weight=weight,
        evidence_ids=list(evidence_ids or []),
        properties=abox_relation_properties(relation_type, properties or {}),
    ))


def compact_string_rows(values: object, limit: int = 5) -> List[str]:
    if not isinstance(values, list):
        return []
    rows: List[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        if text and text not in rows:
            rows.append(text[:180])
        if len(rows) >= limit:
            break
    return rows


def add_execution_plan_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    active_opinion_id: str,
    symbol: str,
    source: str,
    execution_plan: Dict[str, object],
) -> str:
    if not isinstance(execution_plan, dict) or not execution_plan:
        return ""
    plan_id = add_entity(graph, "execution-plan", symbol + ":" + source, "실행 계획 " + symbol, {
        "tboxClass": "ExecutionPlan",
        "tboxClasses": ["ExecutionPlan", "ReasoningCard"],
        "symbol": symbol,
        "source": source,
        "primaryAction": execution_plan.get("primaryAction"),
        "primaryActionLabel": execution_plan.get("primaryActionLabel"),
        "decisionStage": execution_plan.get("decisionStage"),
        "actionGroup": execution_plan.get("actionGroup"),
        "actionLevel": execution_plan.get("actionLevel"),
        "executionPlan": dict(execution_plan),
    })
    add_relation(graph, stock_id, plan_id, "HAS_EXECUTION_PLAN", weight=0.92, properties={"source": "ontology-execution-plan"})
    add_relation(graph, active_opinion_id, plan_id, "HAS_EXECUTION_PLAN", weight=0.95, properties={"source": "active-investment-opinion"})
    primary_label = str(execution_plan.get("primaryActionLabel") or execution_plan.get("primaryAction") or "").strip()
    if primary_label:
        action_id = add_entity(graph, "action-candidate", symbol + ":" + str(execution_plan.get("primaryAction") or primary_label), primary_label, {
            "tboxClass": "ActionCandidate",
            "symbol": symbol,
            "action": execution_plan.get("primaryAction"),
            "label": primary_label,
        })
        add_relation(graph, plan_id, action_id, "HAS_PRIMARY_ACTION", weight=0.95, properties={"source": "ontology-execution-plan"})
    for index, item in enumerate(compact_string_rows(execution_plan.get("blockedActions"), 5)):
        blocked_id = add_entity(graph, "blocked-action", symbol + ":" + str(index) + ":" + item, item, {
            "tboxClass": "BlockedAction",
            "symbol": symbol,
            "label": item,
        })
        add_relation(graph, plan_id, blocked_id, "BLOCKS_ACTION", weight=0.9, properties={"source": "ontology-execution-plan"})
    for index, item in enumerate(compact_string_rows(execution_plan.get("strengthenConditions"), 5)):
        condition_id = add_entity(graph, "execution-condition", symbol + ":strengthen:" + str(index) + ":" + item, item, {
            "tboxClass": "InvalidationCondition",
            "symbol": symbol,
            "conditionType": "strengthen",
            "label": item,
        })
        add_relation(graph, plan_id, condition_id, "STRENGTHENS_ACTION_IF", weight=0.82, properties={"source": "ontology-execution-plan"})
    for index, item in enumerate(compact_string_rows(execution_plan.get("weakenConditions"), 5)):
        condition_id = add_entity(graph, "execution-condition", symbol + ":weaken:" + str(index) + ":" + item, item, {
            "tboxClass": "InvalidationCondition",
            "symbol": symbol,
            "conditionType": "weaken",
            "label": item,
        })
        add_relation(graph, plan_id, condition_id, "WEAKENS_ACTION_IF", weight=0.82, properties={"source": "ontology-execution-plan"})
    for index, item in enumerate(compact_string_rows(execution_plan.get("nextChecks"), 5)):
        check_id = add_entity(graph, "next-check", symbol + ":" + str(index) + ":" + item, item, {
            "tboxClass": "NextCheck",
            "symbol": symbol,
            "label": item,
        })
        add_relation(graph, plan_id, check_id, "REQUIRES_NEXT_CHECK", weight=0.85, properties={"source": "ontology-execution-plan"})
    return plan_id


def safe_setting_value(key: str, value: object) -> object:
    lowered = str(key or "").replace("-", "").replace("_", "").lower()
    if any(token.replace("_", "") in lowered for token in SENSITIVE_SETTING_TOKENS):
        return "configured" if value not in (None, "", False) else ""
    text = str(value or "")
    return text[:1200] if len(text) > 1200 else value


def metric_value(position: Position, field_name: str) -> float:
    return number(getattr(position, field_name, 0))


def metric_relation_properties(field_name: str, value: float, source: str) -> Dict[str, object]:
    properties: Dict[str, object] = {"field": field_name, "source": source}
    if field_name == "profit_loss_rate":
        if value <= -8:
            properties.update({"polarity": "risk", "opinionImpact": min(18.0, abs(value)), "aiInfluenceLabel": "손실률이 관계 리스크를 높임"})
        elif value >= 20:
            properties.update({"polarity": "risk", "opinionImpact": 8.0, "aiInfluenceLabel": "큰 수익 구간이 이익 보호 필요성을 높임"})
        elif value > 0:
            properties.update({"polarity": "support", "supportImpact": min(8.0, value / 3), "aiInfluenceLabel": "수익 구간이 보유 근거를 보강"})
    elif field_name in {"ma20_distance", "ma60_distance", "ma20_slope", "ma60_slope"}:
        if value <= -5:
            properties.update({"polarity": "risk", "opinionImpact": min(14.0, abs(value)), "aiInfluenceLabel": "추세 지표 약화"})
        elif value >= 5:
            properties.update({"polarity": "support", "supportImpact": min(10.0, value), "aiInfluenceLabel": "추세 지표 우호"})
    elif field_name in {"foreign_net_volume", "institution_net_volume", "foreign_net_amount", "institution_net_amount"}:
        if value < 0:
            properties.update({"polarity": "risk", "opinionImpact": 5.0, "aiInfluenceLabel": "주요 수급 순매도"})
        elif value > 0:
            properties.update({"polarity": "support", "supportImpact": 5.0, "aiInfluenceLabel": "주요 수급 순매수"})
    elif field_name in {"volume_ratio", "trade_strength", "bid_ask_imbalance"}:
        if value:
            properties.update({"polarity": "context", "aiInfluenceLabel": "단기 수급 맥락"})
    return properties


def relation_relation_label(relation: OntologyRelation, labels: Dict[str, str]) -> str:
    properties = relation.properties or {}
    explicit = str(properties.get("aiInfluenceLabel") or properties.get("label") or "").strip()
    if explicit:
        return explicit
    source = labels.get(relation.source, relation.source)
    target = labels.get(relation.target, relation.target)
    return source + " " + relation.relation_type + " " + target


def symbol_key(position: Position) -> str:
    return str(position.symbol or position.name or "").upper().strip()


def position_source(position: Position) -> str:
    return str(getattr(position, "source", "") or "holding").strip().lower() or "holding"


def is_watchlist_position(position: Position) -> bool:
    return position_source(position) == "watchlist"


def is_holding_position(position: Position) -> bool:
    return not is_watchlist_position(position) and (number(position.market_value) > 0 or number(position.quantity) > 0)


def observable_position(position: Position) -> bool:
    return not position.is_cash() and bool(symbol_key(position))


def sector_ratio(portfolio: PortfolioSummary, sector: str) -> float:
    for item in portfolio.sectors:
        if item.get("sector") == sector:
            return number(item.get("ratio"))
    return 0.0


def position_weight(position: Position, portfolio: PortfolioSummary) -> float:
    base = number(portfolio.total) or number(portfolio.invested)
    return (number(position.market_value) / base) * 100 if base else 0.0


def smart_money_score(position: Position) -> float:
    foreign_net = number(position.foreign_net_volume) or number(position.foreign_buy_volume) - number(position.foreign_sell_volume)
    institution_net = number(position.institution_net_volume) or number(position.institution_buy_volume) - number(position.institution_sell_volume)
    base = abs(foreign_net) + abs(institution_net)
    return clamp(((foreign_net + institution_net) / base) * 100, -100.0, 100.0) if base else 0.0


def trend_score(position: Position) -> float:
    return clamp(
        number(position.ma20_distance) * 0.45
        + number(position.ma60_distance) * 0.25
        + number(position.ma20_slope) * 4
        + number(position.ma60_slope) * 3
        + number(position.change_rate) * 0.4,
        -35.0,
        35.0,
    )


def trend_dynamic_facts(position: Position) -> Dict[str, object]:
    ma20_distance = number(position.ma20_distance)
    ma60_distance = number(position.ma60_distance)
    ma20_slope = number(position.ma20_slope)
    ma60_slope = number(position.ma60_slope)
    price_change = number(position.change_rate)
    trend_curve = ma20_slope - ma60_slope
    has_ma20_context = bool(number(position.ma20) or ma20_distance)
    has_ma60_context = bool(number(position.ma60) or ma60_distance)
    short_term_breakdown = has_ma20_context and ma20_distance <= -5.0
    medium_term_support = has_ma60_context and ma60_distance >= 0.0
    support_retest = short_term_breakdown and has_ma60_context and ma60_distance >= -1.0
    recovery_attempt = (
        (ma20_distance < 0 or number(position.profit_loss_rate) < 0)
        and has_ma60_context
        and ma60_distance >= -1.0
        and (price_change >= 1.0 or ma20_slope >= 0.3 or trend_curve >= 0.5)
    )
    breakdown_acceleration = (
        short_term_breakdown
        and (
            price_change <= -2.0
            or ma20_slope <= -1.0
            or trend_curve <= -1.0
            or (ma60_distance <= -5.0 and ma20_slope < 0 and ma60_slope < 0)
        )
    )
    if breakdown_acceleration:
        state = "하락 가속"
        polarity = "risk"
        impact = 16.0
    elif support_retest:
        state = "60일선 지지 재확인"
        polarity = "context"
        impact = 7.0
    elif recovery_attempt:
        state = "회복 시도"
        polarity = "support"
        impact = 7.0
    elif has_ma20_context and has_ma60_context and ma20_distance >= 0 and ma60_distance >= 0:
        state = "상승 추세 유지"
        polarity = "support"
        impact = 6.0
    elif short_term_breakdown:
        state = "단기 추세 훼손"
        polarity = "risk"
        impact = 10.0
    else:
        state = "중립 추세"
        polarity = "context"
        impact = 0.0
    risk_score = clamp(
        abs(min(0.0, ma20_distance)) * 2.0
        + abs(min(0.0, ma60_distance)) * 1.4
        + abs(min(0.0, price_change)) * 3.0
        + abs(min(0.0, ma20_slope)) * 6.0
        + abs(min(0.0, trend_curve)) * 4.0
        - (10.0 if support_retest or recovery_attempt else 0.0),
        0.0,
        100.0,
    )
    return {
        "state": state,
        "priceChangeRate": round(price_change, 2),
        "ma20Distance": round(ma20_distance, 2),
        "ma60Distance": round(ma60_distance, 2),
        "ma20Slope": round(ma20_slope, 2),
        "ma60Slope": round(ma60_slope, 2),
        "trendCurve": round(trend_curve, 2),
        "shortTermBreakdown": short_term_breakdown,
        "mediumTermSupport": medium_term_support,
        "supportRetest": support_retest,
        "recoveryAttempt": recovery_attempt,
        "breakdownAcceleration": breakdown_acceleration,
        "dynamicRiskScore": round(risk_score, 1),
        "polarity": polarity,
        "opinionImpact": impact,
    }


def data_quality_score(position: Position) -> float:
    missing = 0
    for value in [
        position.current_price,
        position.market_value,
        position.quantity,
        position.profit_loss_rate,
        position.ma20,
        position.ma60,
    ]:
        if value in (None, "", 0):
            missing += 1
    return clamp(100 - missing * 14, 15.0, 100.0)


def evidence_id(symbol: str, kind: str) -> str:
    return "evidence:" + str(symbol or "portfolio").upper() + ":" + kind


def ontology_action_label(pressure: float, pnl: float, contradictions: List[str], risks: List[str]) -> (str, str):
    if pressure >= 72:
        if pnl < 0:
            return "관계 판단: 손실 구간 보유 이유 재확인", "danger"
        return "관계 판단: 일부 이익 보호", "danger"
    if pressure >= 55:
        if contradictions:
            return "관계 판단: 보유 이유와 반대 신호 점검", "caution"
        return "관계 판단: 비중 축소 후보", "caution"
    if pressure >= 38:
        return "관계 판단: 조건부 보유", "hold"
    if risks:
        return "관계 판단: 보유 이유 유지", "watch"
    return "관계 판단: 보유 유지", "watch"


def build_position_opinion(
    position: Position,
    portfolio: PortfolioSummary,
    legacy_model: Dict[str, object],
) -> OntologyOpinion:
    symbol = symbol_key(position)
    pnl = number(position.profit_loss_rate)
    weight = position_weight(position, portfolio)
    sector_weight = sector_ratio(portfolio, position.sector)
    trend = trend_score(position)
    flow = smart_money_score(position)
    quality = data_quality_score(position)
    legacy_pressure = number(legacy_model.get("exitPressure") or legacy_model.get("exit_pressure"))
    profit_take = number(legacy_model.get("profitTakePressure") or legacy_model.get("profit_take_pressure"))
    loss_cut = number(legacy_model.get("lossCutPressure") or legacy_model.get("loss_cut_pressure"))

    supporting: List[str] = []
    contradictions: List[str] = []
    risks: List[str] = []
    opportunities: List[str] = []

    if sector_weight >= 50:
        risks.append(position.sector + " 관련 종목 비중이 매우 높음")
    elif sector_weight >= 35:
        risks.append(position.sector + " 노출이 높은 편")
    if weight >= 30:
        risks.append("단일 종목 비중이 큼")
    if pnl <= -8:
        risks.append("손실이 보유 이유를 다시 확인할 구간")
    elif pnl >= 20:
        risks.append("큰 수익 구간으로 이익 보호 필요")
    if trend <= -8:
        risks.append("추세 관계가 약화")
    elif trend >= 8:
        supporting.append("추세 흐름이 보유 이유를 뒷받침")
        opportunities.append("가격 추세가 우호적")
    if flow <= -25:
        risks.append("외국인·기관 수급 관계가 부정적")
    elif flow >= 25:
        supporting.append("외국인·기관 수급 관계가 우호적")
        opportunities.append("외국인·기관 수급이 보유 이유를 뒷받침")
    if quality < 60:
        contradictions.append("핵심 데이터가 부족해 AI 판단 신뢰도가 낮음")
    if legacy_pressure >= 70 and (trend >= 8 or flow >= 25):
        contradictions.append("기존 매도 압력은 높지만 추세와 수급은 우호적")
    if legacy_pressure < 45 and (sector_weight >= 50 or pnl <= -8):
        contradictions.append("기존 압력은 낮지만 포트폴리오 관계 리스크가 큼")

    risk_score = 18.0
    risk_score += clamp((legacy_pressure - 40) * 0.35, -10.0, 22.0)
    risk_score += 15.0 if sector_weight >= 50 else 8.0 if sector_weight >= 35 else 0.0
    risk_score += 12.0 if weight >= 30 else 6.0 if weight >= 20 else 0.0
    risk_score += 18.0 if pnl <= -15 else 11.0 if pnl <= -8 else 8.0 if pnl >= 20 else 0.0
    risk_score += clamp(-trend * 0.45, -8.0, 16.0)
    risk_score += clamp(-flow * 0.12, -6.0, 10.0)
    risk_score += clamp((70 - quality) * 0.25, 0.0, 12.0)
    risk_score += min(10.0, len(contradictions) * 5.0)
    ontology_pressure = clamp(risk_score, 0.0, 100.0)
    action, tone = ontology_action_label(ontology_pressure, pnl, contradictions, risks)
    evidence_ids = [
        evidence_id(symbol, "legacy-model"),
        evidence_id(symbol, "portfolio-exposure"),
        evidence_id(symbol, "trend"),
        evidence_id(symbol, "flow"),
        evidence_id(symbol, "data-quality"),
    ]
    thesis_parts = []
    if supporting:
        thesis_parts.append("지지: " + ", ".join(supporting[:2]))
    if risks:
        thesis_parts.append("리스크: " + ", ".join(risks[:2]))
    if contradictions:
        thesis_parts.append("충돌: " + ", ".join(contradictions[:1]))
    thesis = "; ".join(thesis_parts) or "관계 분석에서 강한 반대 신호는 없고 보유 이유를 유지합니다."
    confidence = clamp(quality * 0.006 + len(evidence_ids) * 0.06 - len(contradictions) * 0.08, 0.2, 0.92)
    return OntologyOpinion(
        symbol=symbol,
        action=action,
        tone=tone,
        conviction=round(confidence * 100, 1),
        ontology_pressure=round(ontology_pressure, 1),
        thesis=thesis,
        supporting_beliefs=supporting[:4],
        contradictions=contradictions[:4],
        dominant_risks=risks[:5],
        opportunities=opportunities[:4],
        legacy_model={
            "exitPressure": round(legacy_pressure, 1),
            "profitTakePressure": round(profit_take, 1),
            "lossCutPressure": round(loss_cut, 1),
            "decisionBasis": legacy_model.get("decisionBasis") or legacy_model.get("decision_basis") or "",
        },
        evidence_ids=evidence_ids,
    )


def build_watchlist_opinion(position: Position, legacy_model: Dict[str, object]) -> OntologyOpinion:
    symbol = symbol_key(position)
    trend = trend_score(position)
    flow = smart_money_score(position)
    quality = data_quality_score(position)
    risks: List[str] = []
    supporting: List[str] = []
    opportunities: List[str] = []
    contradictions: List[str] = []

    if quality < 60:
        contradictions.append("관심 종목 판단에 필요한 가격·추세 데이터가 부족함")
    if trend <= -8:
        risks.append("진입 후보로 보기에는 추세 관계가 약함")
    elif trend >= 8:
        supporting.append("추세가 진입 관찰 근거를 뒷받침")
        opportunities.append("가격 추세가 우호적")
    if flow <= -25:
        risks.append("외국인·기관 수급 관계가 부정적")
    elif flow >= 25:
        supporting.append("외국인·기관 수급 관계가 우호적")
        opportunities.append("수급이 진입 관찰 근거를 보강")
    if not number(position.current_price):
        contradictions.append("현재가가 없어 가격 기준을 확정할 수 없음")

    observation_pressure = 26.0
    observation_pressure += clamp(trend * 0.35, -10.0, 18.0)
    observation_pressure += clamp(flow * 0.08, -8.0, 12.0)
    observation_pressure += clamp((quality - 55) * 0.18, -8.0, 10.0)
    observation_pressure = clamp(observation_pressure, 0.0, 100.0)
    if observation_pressure >= 55 and not contradictions:
        action = "관심 종목: 관계 우호 관찰"
        tone = "watch"
    elif risks or contradictions:
        action = "관심 종목: 진입 조건 재확인"
        tone = "hold"
    else:
        action = "관심 종목: 진입 기준 대기"
        tone = "hold"
    evidence_ids = [
        evidence_id(symbol, "market-observation"),
        evidence_id(symbol, "trend"),
        evidence_id(symbol, "flow"),
        evidence_id(symbol, "data-quality"),
    ]
    thesis_parts = []
    if supporting:
        thesis_parts.append("지지: " + ", ".join(supporting[:2]))
    if risks:
        thesis_parts.append("리스크: " + ", ".join(risks[:2]))
    if contradictions:
        thesis_parts.append("공백: " + ", ".join(contradictions[:1]))
    thesis = "; ".join(thesis_parts) or "보유가 아닌 관심 종목이므로 현재가, 추세, 수급이 채워질 때 진입 기준을 확인합니다."
    confidence = clamp(quality * 0.006 + len(evidence_ids) * 0.05 - len(contradictions) * 0.08, 0.2, 0.88)
    return OntologyOpinion(
        symbol=symbol,
        action=action,
        tone=tone,
        conviction=round(confidence * 100, 1),
        ontology_pressure=round(observation_pressure, 1),
        thesis=thesis,
        supporting_beliefs=supporting[:4],
        contradictions=contradictions[:4],
        dominant_risks=risks[:5],
        opportunities=opportunities[:4],
        legacy_model={
            "exitPressure": round(number(legacy_model.get("exitPressure")), 1),
            "decisionBasis": legacy_model.get("decisionBasis") or "watchlist-observation",
        },
        evidence_ids=evidence_ids,
    )


def add_market_exposure_concepts(graph: PortfolioOntology, portfolio_node_id: str, portfolio: PortfolioSummary) -> None:
    for market in portfolio.markets:
        key = str(market.get("key") or market.get("market") or market.get("label") or "").strip()
        if not key:
            continue
        label = str(market.get("label") or key)
        market_id = add_entity(graph, "market", key, label, {"tboxClass": "Market"})
        exposure_id = add_entity(graph, "market-exposure", graph.portfolio_id + ":" + key, label + " 시장 노출", {
            "tboxClass": "MarketExposure",
            "market": key,
            "invested": number(market.get("invested")),
            "cash": number(market.get("cash")),
            "total": number(market.get("total")),
            "cashRatio": number(market.get("cashRatio")),
        })
        add_relation(graph, portfolio_node_id, exposure_id, "HAS_MARKET_EXPOSURE", weight=1.0, properties={"basis": "portfolio-market-summary"})
        add_relation(graph, exposure_id, market_id, "AFFECTS", weight=1.0, properties={"polarity": "context", "aiInfluenceLabel": label + " 시장 노출"})


def add_portfolio_factor_exposure_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    portfolio: PortfolioSummary,
    observed_positions: List[Position],
) -> None:
    total = number(portfolio.total) or number(portfolio.invested)
    if not total:
        return
    currency_exposure: Dict[str, float] = {}
    raw_position_total = sum(number(position.market_value) for position in observed_positions if is_holding_position(position))
    sector_positions: Dict[str, int] = {}
    for position in observed_positions:
        if not is_holding_position(position):
            continue
        currency = str(position.currency or "").upper().strip()
        sector = str(position.sector or "기타").strip() or "기타"
        if currency:
            currency_exposure[currency] = currency_exposure.get(currency, 0.0) + number(position.market_value)
        sector_positions[sector] = sector_positions.get(sector, 0) + 1
    for currency, value in sorted(currency_exposure.items()):
        ratio = (value / raw_position_total) * 100 if raw_position_total else 0.0
        if currency in {"KRW", ""} or ratio < 10:
            continue
        fx_id = add_entity(graph, "fx-pair", "KRW:" + currency, "KRW/" + currency + " 환율 노출", {
            "tboxClass": "FXPair",
            "currency": currency,
            "exposureValue": round(value, 2),
            "exposureRatio": round(ratio, 2),
        })
        risk_id = add_entity(graph, "risk", currency + "-currency-risk", currency + " 통화 리스크", {
            "tboxClass": "Risk",
            "tboxClasses": ["Risk", "CurrencyRisk"],
            "currency": currency,
            "exposureRatio": round(ratio, 2),
        })
        add_relation(graph, portfolio_node_id, fx_id, "HAS_MARKET_EXPOSURE", weight=round(ratio / 100, 4), properties={"source": "currency-exposure", "aiInfluenceLabel": currency + " 환율 노출"})
        add_relation(graph, portfolio_node_id, risk_id, "EXPOSED_TO", weight=round(ratio / 100, 4), properties={"source": "currency-exposure", "polarity": "context", "aiInfluenceLabel": currency + " 통화 리스크"})
        add_relation(graph, fx_id, risk_id, "AMPLIFIES_RISK", weight=round(ratio / 100, 4), properties={"source": "currency-exposure", "polarity": "context", "aiInfluenceLabel": currency + " 환율 민감도"})
    for sector in portfolio.sectors:
        label = str(sector.get("sector") or "기타")
        ratio = number(sector.get("ratio"))
        if ratio < 35 and sector_positions.get(label, 0) < 2:
            continue
        risk_id = add_entity(graph, "risk", label + "-correlation-risk", label + " 상관 리스크", {
            "tboxClass": "Risk",
            "tboxClasses": ["Risk", "ConcentrationRisk", "CorrelationRisk"],
            "sector": label,
            "sectorRatio": round(ratio, 2),
            "positionCount": sector_positions.get(label, 0),
        })
        add_relation(graph, portfolio_node_id, risk_id, "EXPOSED_TO", weight=round(ratio / 100, 4), properties={"source": "sector-correlation", "polarity": "context", "aiInfluenceLabel": label + " 섹터 상관 리스크"})


def add_metric_concepts(graph: PortfolioOntology, stock_id: str, position: Position, source: str) -> None:
    symbol = symbol_key(position)
    for field_name, label, tbox_class, relation_type, kind, public_key in METRIC_CONCEPTS:
        value = metric_value(position, field_name)
        if value in (None, "", 0):
            continue
        metric_id = add_entity(graph, kind + "-metric", symbol + ":" + public_key, label, {
            "tboxClass": tbox_class,
            "tboxClasses": metric_tbox_classes(tbox_class, field_name),
            "field": public_key,
            "value": round(value, 4),
            "source": source,
        })
        properties = metric_relation_properties(field_name, value, source)
        add_relation(
            graph,
            stock_id,
            metric_id,
            "HAS_OBSERVATION",
            weight=1.0,
            properties={**properties, "observationField": public_key},
        )
        add_relation(
            graph,
            stock_id,
            metric_id,
            relation_type,
            weight=1.0,
            properties=properties,
        )
    trend_dynamic = trend_dynamic_facts(position)
    scenario_id = add_entity(graph, "trend-scenario", symbol, str(trend_dynamic.get("state") or "추세 시나리오"), {
        "tboxClass": "TrendSignal",
        "tboxClasses": ["Observation", "TechnicalObservation", "TrendSignal", "Scenario"],
        "source": source,
        **trend_dynamic,
    })
    trend_properties = {
        "source": source,
        "polarity": str(trend_dynamic.get("polarity") or "context"),
        "opinionImpact": number(trend_dynamic.get("opinionImpact")),
        "aiInfluenceLabel": "추세 동역학: " + str(trend_dynamic.get("state") or "중립 추세"),
    }
    add_relation(graph, stock_id, scenario_id, "HAS_OBSERVATION", weight=1.0, properties=trend_properties)
    add_relation(graph, stock_id, scenario_id, "HAS_TECHNICAL_INDICATOR", weight=1.0, properties=trend_properties)
    quality = data_quality_score(position)
    quality_id = add_entity(graph, "data-quality", symbol, "데이터 품질", {
        "tboxClass": "DataQuality",
        "tboxClasses": metric_tbox_classes("DataQuality", "dataQuality"),
        "qualityScore": round(quality, 2),
        "dataQuality": position.data_quality,
        "quoteStatus": position.quote_status,
    })
    quality_properties = {"field": "dataQuality", "source": source, "aiInfluenceLabel": "데이터 품질"}
    if quality < 60:
        quality_properties.update({"polarity": "risk", "opinionImpact": round((60 - quality) * 0.2, 2)})
    add_relation(graph, stock_id, quality_id, "HAS_OBSERVATION", weight=round(quality / 100, 4), properties=quality_properties)
    add_relation(graph, stock_id, quality_id, "HAS_DATA_QUALITY", weight=round(quality / 100, 4), properties=quality_properties)


def add_data_source_concept(graph: PortfolioOntology, stock_id: str, position: Position, source: str) -> None:
    label = str(position.quote_source or position.data_quality or source or "runtime-data")
    quality = data_quality_score(position)
    source_id = add_entity(graph, "data-source", label, label, {
        "tboxClass": "DataSource",
        "tboxClasses": ["DataSource", "Provenance"],
        "quoteStatus": position.quote_status,
        "quoteMessage": position.quote_message,
        "dataQuality": position.data_quality,
    })
    add_relation(graph, stock_id, source_id, "OBSERVED_FROM", weight=1.0, properties={"source": source, "basis": "quote-source"})
    add_relation(graph, stock_id, source_id, "HAS_PROVENANCE", weight=1.0, properties={"source": source, "basis": "quote-source"})
    reliability_id = add_entity(graph, "source-reliability", label, label + " 신뢰도", {
        "tboxClass": "SourceReliability",
        "tboxClasses": ["Provenance", "SourceReliability", "DataQuality"],
        "qualityScore": round(quality, 2),
        "quoteStatus": position.quote_status,
        "quoteMessage": position.quote_message,
    })
    props = {"source": source, "aiInfluenceLabel": label + " 신뢰도", "confidence": round(quality / 100, 3)}
    if quality < 60:
        props.update({"polarity": "risk", "opinionImpact": round((60 - quality) * 0.18, 2)})
    add_relation(graph, source_id, reliability_id, "HAS_SOURCE_RELIABILITY", weight=round(quality / 100, 4), properties=props)
    add_relation(graph, stock_id, reliability_id, "WEIGHTED_BY_CONFIDENCE", weight=round(quality / 100, 4), properties=props)


def add_legacy_model_score_concepts(graph: PortfolioOntology, stock_id: str, symbol: str, legacy: Dict[str, object]) -> None:
    score_rows = [
        ("exitPressure", "기존 매도 압력", number(legacy.get("exitPressure") or legacy.get("exit_pressure")), "risk"),
        ("profitTakePressure", "익절 압력", number(legacy.get("profitTakePressure") or legacy.get("profit_take_pressure")), "risk"),
        ("lossCutPressure", "손실 관리 압력", number(legacy.get("lossCutPressure") or legacy.get("loss_cut_pressure")), "risk"),
        ("buyScore", "매수 점수", number(legacy.get("buyScore") or legacy.get("modelBuyScore")), "support"),
        ("sellScore", "매도 점수", number(legacy.get("sellScore") or legacy.get("modelSellScore")), "risk"),
    ]
    for key, label, value, polarity in score_rows:
        if not value:
            continue
        score_id = add_entity(graph, "model-score", symbol + ":" + key, label, {
            "tboxClass": "ModelScore",
            "tboxClasses": ["Signal", "StrategySignal", "ModelScore"],
            "field": key,
            "value": round(value, 2),
            "modelRole": "supporting-evidence",
        })
        properties = {"field": key, "polarity": polarity, "aiInfluenceLabel": label, "source": "legacy-model"}
        if polarity == "risk" and value >= 55:
            properties["opinionImpact"] = min(18.0, (value - 45) * 0.35)
        if polarity == "support" and value >= 55:
            properties["supportImpact"] = min(14.0, (value - 45) * 0.35)
        add_relation(graph, stock_id, score_id, "HAS_MODEL_SCORE", weight=round(value / 100, 4), properties=properties)
        add_relation(graph, score_id, stock_id, "USED_AS_EVIDENCE", weight=round(value / 100, 4), properties={**properties, "source": "legacy-model"})


def add_price_level_and_liquidity_concepts(graph: PortfolioOntology, stock_id: str, position: Position, source: str) -> None:
    symbol = symbol_key(position)
    current_price = number(position.current_price)
    if current_price:
        bar_id = add_entity(graph, "price-bar", symbol + ":latest", (position.name or symbol) + " 현재 가격 봉", {
            "tboxClass": "PriceBar",
            "tboxClasses": ["Observation", "PriceObservation", "PriceBar"],
            "symbol": symbol,
            "close": round(current_price, 4),
            "changeRate": round(number(position.change_rate), 2),
            "volume": round(number(position.volume), 2),
            "volumeRatio": round(number(position.volume_ratio), 3),
            "observedAt": position.updated_at,
        })
        add_relation(graph, stock_id, bar_id, "HAS_PRICE", weight=1.0, properties={"source": source, "aiInfluenceLabel": "현재 가격 봉"})
        add_relation(graph, stock_id, bar_id, "HAS_OBSERVATION", weight=1.0, properties={"source": source, "aiInfluenceLabel": "현재 가격 봉"})
    level_rows = [
        ("ma20", "20일선", number(position.ma20), number(position.ma20_distance), "SupportLevel" if number(position.ma20_distance) >= -1 else "ResistanceLevel"),
        ("ma60", "60일선", number(position.ma60), number(position.ma60_distance), "SupportLevel" if number(position.ma60_distance) >= -1 else "ResistanceLevel"),
        ("average", "평단가", number(position.average_price), pct_distance_safe(current_price, number(position.average_price)), "KeyLevel"),
    ]
    for key, label, level, distance, tbox_class in level_rows:
        if not level:
            continue
        level_id = add_entity(graph, "key-level", symbol + ":" + key, label + " " + compact_price(level), {
            "tboxClass": tbox_class,
            "tboxClasses": ["Observation", "TechnicalObservation", "KeyLevel", tbox_class],
            "symbol": symbol,
            "levelType": key,
            "price": round(level, 4),
            "distancePct": round(distance, 2),
        })
        add_relation(graph, stock_id, level_id, "HAS_TECHNICAL_INDICATOR", weight=1.0, properties={"source": source, "aiInfluenceLabel": label + " 위치"})
        if -1.0 <= distance <= 1.5:
            add_relation(graph, stock_id, level_id, "RETESTS_LEVEL", weight=0.82, properties={"source": source, "polarity": "context", "aiInfluenceLabel": label + " 재시험"})
        elif distance <= -5.0:
            add_relation(graph, stock_id, level_id, "BREAKS_LEVEL", weight=0.88, properties={"source": source, "polarity": "risk", "opinionImpact": min(14.0, abs(distance)), "aiInfluenceLabel": label + " 이탈"})
        elif distance >= 0 and number(position.change_rate) > 0:
            add_relation(graph, stock_id, level_id, "RECLAIMS_LEVEL", weight=0.74, properties={"source": source, "polarity": "support", "supportImpact": min(8.0, distance + 2), "aiInfluenceLabel": label + " 회복"})
    liquidity = liquidity_profile(position)
    liquidity_id = add_entity(graph, "liquidity-profile", symbol, (position.name or symbol) + " 유동성 프로파일", {
        "tboxClass": "LiquidityProfile",
        "tboxClasses": ["Risk", "LiquidityRisk", "LiquidityProfile"],
        **liquidity,
    })
    risk_props = {"source": source, "aiInfluenceLabel": "유동성/실행 가능성"}
    if number(liquidity.get("liquidityRiskScore")) >= 55:
        risk_props.update({"polarity": "risk", "opinionImpact": min(16.0, number(liquidity.get("liquidityRiskScore")) * 0.18)})
    add_relation(graph, stock_id, liquidity_id, "LIMITED_BY_LIQUIDITY", weight=round(number(liquidity.get("liquidityRiskScore")) / 100, 4), properties=risk_props)
    capacity_id = add_entity(graph, "exit-capacity", symbol, (position.name or symbol) + " 청산 가능 용량", {
        "tboxClass": "ExitCapacity",
        "tboxClasses": ["Risk", "LiquidityRisk", "ExitCapacity"],
        "sellableQuantity": round(number(position.sellable_quantity), 4),
        "positionValue": round(number(position.market_value), 2),
        "tradingValue": round(number(position.trading_value), 2),
        "exitDaysAtTenPctADV": liquidity.get("exitDaysAtTenPctADV"),
    })
    add_relation(graph, stock_id, capacity_id, "HAS_EXIT_CAPACITY", weight=1.0, properties={"source": source, "aiInfluenceLabel": "청산 가능 용량"})
    slippage_id = add_entity(graph, "slippage-estimate", symbol, (position.name or symbol) + " 슬리피지 추정", {
        "tboxClass": "SlippageEstimate",
        "tboxClasses": ["Risk", "ExecutionRisk", "SlippageEstimate"],
        "slippageRiskScore": liquidity.get("slippageRiskScore"),
        "bidAskImbalance": round(number(position.bid_ask_imbalance), 2),
        "volumeRatio": round(number(position.volume_ratio), 3),
    })
    add_relation(graph, stock_id, slippage_id, "HAS_SLIPPAGE_RISK", weight=round(number(liquidity.get("slippageRiskScore")) / 100, 4), properties=risk_props)


def pct_distance_safe(value: float, reference: float) -> float:
    return ((number(value) / number(reference)) - 1) * 100 if number(value) and number(reference) else 0.0


def compact_price(value: object) -> str:
    numeric = number(value)
    if numeric >= 1000:
        return str(int(round(numeric)))
    return str(round(numeric, 4)).rstrip("0").rstrip(".")


def liquidity_profile(position: Position) -> Dict[str, object]:
    market_value = number(position.market_value)
    trading_value = number(position.trading_value)
    volume_ratio = number(position.volume_ratio)
    ask_pressure = max(0.0, -number(position.bid_ask_imbalance))
    sellable_quantity = number(position.sellable_quantity)
    quantity = number(position.quantity)
    exit_days = market_value / max(1.0, trading_value * 0.1) if market_value and trading_value else 0.0
    position_to_value = (market_value / trading_value) * 100 if market_value and trading_value else 0.0
    sellable_gap = 100.0 if quantity and sellable_quantity <= 0 else 0.0
    liquidity_risk = clamp(position_to_value * 2.0 + max(0.0, 1.0 - volume_ratio) * 18.0 + ask_pressure * 0.25 + sellable_gap * 0.25, 0.0, 100.0)
    slippage_risk = clamp(position_to_value * 1.4 + ask_pressure * 0.35 + max(0.0, 0.8 - volume_ratio) * 20.0, 0.0, 100.0)
    return {
        "positionToTradingValuePct": round(position_to_value, 2),
        "exitDaysAtTenPctADV": round(exit_days, 2),
        "liquidityRiskScore": round(liquidity_risk, 1),
        "slippageRiskScore": round(slippage_risk, 1),
        "volumeRatio": round(volume_ratio, 3),
        "bidAskImbalance": round(number(position.bid_ask_imbalance), 2),
    }


def add_position_factor_concepts(graph: PortfolioOntology, stock_id: str, portfolio_node_id: str, position: Position, portfolio: PortfolioSummary) -> None:
    symbol = symbol_key(position)
    benchmark_id, benchmark_label = benchmark_for_position(position)
    benchmark_entity_id = add_entity(graph, "benchmark-index", benchmark_id, benchmark_label, {
        "tboxClass": "BenchmarkIndex",
        "tboxClasses": ["BenchmarkIndex", "Factor"],
        "market": position.market,
    })
    add_relation(graph, stock_id, benchmark_entity_id, "HAS_BETA_TO", weight=0.6, properties={"source": "factor-map", "polarity": "context", "aiInfluenceLabel": benchmark_label + " 베타"})
    for label in factor_labels_for_position(position):
        factor_id = add_entity(graph, "factor", label, label, {
            "tboxClass": "Factor",
            "tboxClasses": ["Factor", "FactorExposure"],
            "label": label,
        })
        weight = round(position_weight(position, portfolio) / 100, 4) if is_holding_position(position) else 0.18
        props = {"source": "factor-map", "polarity": "context", "aiInfluenceLabel": label + " 팩터 노출"}
        add_relation(graph, stock_id, factor_id, "HAS_FACTOR_EXPOSURE", weight=weight or 0.18, properties=props)
        add_relation(graph, portfolio_node_id, factor_id, "HAS_FACTOR_EXPOSURE", weight=weight or 0.18, properties=props)


def add_research_evidence_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    thesis_id: str,
    active_opinion_id: str,
    symbol: str,
    facts: Dict[str, object],
    external_signals: Dict[str, object],
) -> None:
    evidence_by_id = {}
    for item in research_evidence_from_facts(symbol, facts or {}) + research_evidence_from_external_signals(symbol, external_signals or {}):
        evidence_by_id[item.evidence_id] = item
    for item in evidence_by_id.values():
        event_id = add_entity(graph, "research-evidence", item.evidence_id, item.title, {
            "tboxClass": "ResearchEvidence",
            "tboxClasses": event_tbox_classes(item),
            "symbol": item.symbol,
            "kind": item.kind,
            "source": item.source,
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
            "publishedAt": item.published_at,
            "observedAt": item.observed_at,
            "polarity": item.polarity,
            "impactScore": round(number(item.impact_score), 1),
            "confidence": round(number(item.confidence), 2),
        })
        graph.evidence.append(OntologyEvidence(
            item.evidence_id,
            stock_id,
            item.kind,
            item.source,
            item.title,
            item.to_dict(),
            item.confidence,
        ))
        props = event_relation_properties(item)
        add_relation(graph, stock_id, event_id, "HAS_OBSERVATION", weight=round(number(item.confidence), 4), evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, stock_id, event_id, "HAS_EXTERNAL_SIGNAL", weight=round(number(item.confidence), 4), evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, event_id, stock_id, "MENTIONS_INSTRUMENT", weight=round(number(item.confidence), 4), evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, event_id, thesis_id, "MATERIAL_TO", weight=round((number(item.impact_score) or 2) / 20, 4), evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, event_id, active_opinion_id, "IMPACTS_OPINION", weight=round((number(item.impact_score) or 2) / 20, 4), evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, event_id, event_id, "DECAYS_AFTER", weight=1.0, evidence_ids=[item.evidence_id], properties={
            "source": "research-evidence",
            "decayPolicy": "materiality-decay",
            "defaultDays": 3 if item.kind in {"news", "market-move"} else 14,
            "aiInfluenceLabel": "이벤트 영향 시간 감쇠",
        })


def add_relation_state_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    symbol: str,
    position: Position,
    source: str,
    runtime_context: Dict[str, object],
    relation_context: Dict[str, object],
) -> None:
    previous_position = previous_position_state(runtime_context, symbol, source)
    previous_decision = previous_decision_state(runtime_context, symbol)
    current_decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    facts = relation_context.get("facts") if isinstance(relation_context.get("facts"), dict) else {}
    current_score = number(current_decision.get("score") or relation_context.get("signalStrength"))
    previous_pressure = number(previous_decision.get("exit_pressure") or previous_decision.get("exitPressure"))
    current_price = number(position.current_price)
    previous_price = number(previous_position.get("current_price") or previous_position.get("currentPrice"))
    previous_pnl = number(previous_position.get("profit_loss_rate") or previous_position.get("profitLossRate"))
    current_pnl = number(position.profit_loss_rate)
    previous_ma20_distance = number(previous_position.get("ma20_distance") or previous_position.get("ma20Distance"))
    current_ma20_distance = number(position.ma20_distance)
    state_id = add_entity(graph, "relation-state", symbol + ":current", (position.name or symbol) + " 현재 관계 상태", {
        "tboxClass": "RelationStateSnapshot",
        "tboxClasses": ["RelationStateSnapshot", "ReasoningCard"],
        "symbol": symbol,
        "source": source,
        "decisionLabel": current_decision.get("label"),
        "relationScore": round(current_score, 1),
        "price": round(current_price, 4),
        "profitLossRate": round(current_pnl, 2),
        "ma20Distance": round(current_ma20_distance, 2),
        "activeRuleIds": [
            str(item.get("ruleId") or item.get("rule_id") or "")
            for item in relation_context.get("activeRules") or []
            if isinstance(item, dict)
        ][:8],
    })
    add_relation(graph, stock_id, state_id, "HAS_REASONING_CARD", weight=round(current_score / 100, 4), properties={"source": "relation-state", "aiInfluenceLabel": "현재 관계 상태"})
    if not previous_position and not previous_decision:
        return
    previous_state_id = add_entity(graph, "relation-state", symbol + ":previous", (position.name or symbol) + " 이전 관계 상태", {
        "tboxClass": "PreviousInsight",
        "tboxClasses": ["PreviousInsight", "RelationStateSnapshot"],
        "symbol": symbol,
        "source": source,
        "decisionLabel": previous_decision.get("decision"),
        "relationScore": round(previous_pressure, 1),
        "price": round(previous_price, 4),
        "profitLossRate": round(previous_pnl, 2),
        "ma20Distance": round(previous_ma20_distance, 2),
    })
    add_relation(graph, state_id, previous_state_id, "CHANGED_FROM", weight=1.0, properties={"source": "previous-monitor-state", "aiInfluenceLabel": "이전 상태 대비 변화"})
    transition_score = 0.0
    transition_labels: List[str] = []
    price_delta = pct_distance_safe(current_price, previous_price)
    pnl_delta = current_pnl - previous_pnl if previous_position else 0.0
    ma20_delta = current_ma20_distance - previous_ma20_distance if previous_position else 0.0
    if abs(price_delta) >= 1.5:
        transition_score += min(20.0, abs(price_delta) * 4.0)
        transition_labels.append("가격 " + signed_pct_text(price_delta))
    if abs(pnl_delta) >= 1.0:
        transition_score += min(18.0, abs(pnl_delta) * 5.0)
        transition_labels.append("손익률 " + signed_pct_text(pnl_delta, suffix="%p"))
    if abs(ma20_delta) >= 2.0:
        transition_score += min(16.0, abs(ma20_delta) * 3.0)
        transition_labels.append("20일선 괴리 " + signed_pct_text(ma20_delta, suffix="%p"))
    selected_rule = str(current_decision.get("selectedRuleId") or "")
    if selected_rule and selected_rule != str(previous_decision.get("selectedRuleId") or ""):
        transition_score += 18.0
        transition_labels.append("선택 규칙 변화 " + selected_rule)
    if facts.get("breakdownAcceleration"):
        transition_score += 14.0
    transition_score = clamp(transition_score, 0.0, 100.0)
    if transition_score <= 0:
        return
    transition_id = add_entity(graph, "signal-transition", symbol + ":state-change", "관계 상태 변화", {
        "tboxClass": "SignalTransition",
        "symbol": symbol,
        "changeScore": round(transition_score, 1),
        "priceDeltaPct": round(price_delta, 2),
        "profitLossRateDeltaPct": round(pnl_delta, 2),
        "ma20DistanceDeltaPct": round(ma20_delta, 2),
        "labels": transition_labels[:6],
    })
    props = {"source": "previous-monitor-state", "aiInfluenceLabel": "관계 상태 변화", "polarity": "context"}
    if current_score >= 55 and (pnl_delta < 0 or ma20_delta < 0 or facts.get("breakdownAcceleration")):
        props.update({"polarity": "risk", "opinionImpact": min(16.0, transition_score * 0.22)})
    elif current_score < 55 and (price_delta > 0 or ma20_delta > 0):
        props.update({"polarity": "support", "supportImpact": min(10.0, transition_score * 0.16)})
    add_relation(graph, stock_id, transition_id, "HAS_OBSERVATION", weight=round(transition_score / 100, 4), properties=props)
    add_relation(graph, transition_id, state_id, "CONFIRMED_OVER", weight=round(transition_score / 100, 4), properties=props)
    if selected_rule and any(token in selected_rule for token in ["breakdown", "blocked", "loss"]):
        add_relation(graph, transition_id, previous_state_id, "FAILED_AFTER", weight=round(transition_score / 100, 4), properties=props)


def signed_pct_text(value: object, suffix: str = "%") -> str:
    numeric = round(number(value), 2)
    return ("+" if numeric > 0 else "") + str(numeric).rstrip("0").rstrip(".") + suffix


def add_runtime_setting_concepts(graph: PortfolioOntology, portfolio_node_id: str, runtime_context: Dict[str, object]) -> None:
    settings = runtime_context.get("settings") if isinstance(runtime_context, dict) else {}
    if not isinstance(settings, dict):
        return
    for key, value in sorted(settings.items()):
        if value in (None, "", False):
            continue
        concept = SETTING_CONCEPT_TYPES.get(str(key))
        tbox_class, relation_type = concept if concept else ("RuntimeSetting", "HAS_RUNTIME_SETTING")
        setting_id = add_entity(graph, "runtime-setting", key, str(key), {
            "tboxClass": tbox_class,
            "key": str(key),
            "value": safe_setting_value(str(key), value),
        })
        add_relation(graph, portfolio_node_id, setting_id, relation_type, weight=1.0, properties={"source": "runtime-settings", "aiInfluenceLabel": str(key)})


def add_runtime_metadata_concepts(graph: PortfolioOntology, portfolio_node_id: str, runtime_context: Dict[str, object]) -> None:
    metadata = runtime_context.get("metadata") if isinstance(runtime_context, dict) else {}
    if not isinstance(metadata, dict):
        return
    for key, value in sorted(metadata.items()):
        if value in (None, "", False):
            continue
        metadata_id = add_entity(graph, "runtime-metadata", key, "metadata:" + str(key), {
            "tboxClass": "RuntimeSetting",
            "key": str(key),
            "value": safe_setting_value(str(key), value),
        })
        add_relation(graph, portfolio_node_id, metadata_id, "HAS_RUNTIME_SETTING", weight=1.0, properties={"source": "runtime-metadata", "aiInfluenceLabel": "metadata:" + str(key)})


def runtime_settings(runtime_context: Dict[str, object]) -> Dict[str, object]:
    settings = runtime_context.get("settings") if isinstance(runtime_context, dict) else {}
    return settings if isinstance(settings, dict) else {}


def configured_minutes(settings: Dict[str, object], primary_key: str, fallback: float, secondary_key: str = "") -> float:
    raw = settings.get(primary_key)
    if raw in (None, "") and secondary_key:
        raw = settings.get(secondary_key)
    value = number(raw)
    return value if value > 0 else number(fallback)


def add_operational_world_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    runtime_context: Dict[str, object],
    observed_positions: List[Position],
) -> None:
    settings = runtime_settings(runtime_context)
    collection_policy_id = add_entity(graph, "collection-policy", "adaptive-polling", "적응형 데이터 수집 정책", {
        "tboxClass": "CollectionPolicy",
        "mode": "adaptive",
        "description": "데이터는 성격별 목표 주기로 갱신하고, 알림은 의미 변화가 있을 때만 검토합니다.",
    })
    market_session_id = add_entity(graph, "market-session", "runtime-market-session", "현재 시장 세션", {
        "tboxClass": "MarketSession",
        "mode": str(runtime_context.get("mode") or ""),
        "positionCount": len([item for item in observed_positions if is_holding_position(item)]),
        "watchlistCount": len([item for item in observed_positions if is_watchlist_position(item)]),
    })
    reasoning_id = add_entity(graph, "reasoning-cycle", "ontologyReasoning", "ontologyReasoning", {
        "tboxClass": "ReasoningCycle",
        "trigger": "every-data-update",
        "description": "데이터 갱신 직후 관계 영향과 인사이트를 재계산합니다.",
    })
    strategy_analysis_id = add_entity(graph, "analysis-job", "strategyAnalysis", "전략 분석", {
        "tboxClass": "AnalysisJob",
        "role": "supporting-analysis",
        "description": "기존 모델 점수와 관계 규칙을 보조 분석으로 유지합니다.",
    })
    insight_policy_id = add_entity(graph, "insight-policy", "meaningful-change", "의미 변화 인사이트 정책", {
        "tboxClass": "InsightPolicy",
        "mode": "meaningful-change",
        "minimumNovelty": number(settings.get("notificationNoveltyThreshold")) or 0.65,
        "minimumConfidence": number(settings.get("notificationConfidenceThreshold")) or 0.55,
    })
    novelty_policy_id = add_entity(graph, "novelty-policy", "relation-novelty", "관계 신규성 정책", {
        "tboxClass": "NoveltyPolicy",
        "minimumNovelty": number(settings.get("notificationNoveltyThreshold")) or 0.65,
    })
    cooldown_policy_id = add_entity(graph, "cooldown-policy", "insight-cooldown", "인사이트 발송 쿨다운", {
        "tboxClass": "CooldownPolicy",
        "fallbackMinutes": number(settings.get("notificationCooldownMinutes")) or 10,
        "legacyAlertCadence": safe_setting_value("alertCadenceMinutes", settings.get("alertCadenceMinutes") or ""),
    })
    suppression_policy_id = add_entity(graph, "suppression-policy", "duplicate-insight", "중복 인사이트 억제 정책", {
        "tboxClass": "SuppressionPolicy",
        "basis": "same-subject-same-insight-type-without-material-relation-change",
    })
    dispatch_id = add_entity(graph, "notification-dispatch", "investmentInsight", "investmentInsight 디스패치", {
        "tboxClass": "NotificationDispatch",
        "mode": "insight-driven-only",
        "legacyAlertTypesRole": "presentation-and-compatibility",
        "description": "투자 알림은 알림 타입별 폴링이 아니라 온톨로지 인사이트를 전달합니다.",
    })
    add_relation(graph, portfolio_node_id, collection_policy_id, "USES_COLLECTION_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, portfolio_node_id, market_session_id, "OBSERVES_MARKET_SESSION", properties={"source": "operational-ontology"})
    add_relation(graph, portfolio_node_id, reasoning_id, "HAS_REASONING_CYCLE", properties={"source": "operational-ontology"})
    add_relation(graph, portfolio_node_id, dispatch_id, "HAS_NOTIFICATION_DISPATCH", properties={"source": "operational-ontology"})
    add_relation(graph, dispatch_id, insight_policy_id, "USES_INSIGHT_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, dispatch_id, cooldown_policy_id, "HAS_COOLDOWN_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, dispatch_id, novelty_policy_id, "HAS_NOVELTY_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, dispatch_id, suppression_policy_id, "SUPPRESSED_BY_POLICY", properties={"source": "operational-ontology"})
    add_relation(graph, reasoning_id, strategy_analysis_id, "SCHEDULES_ANALYSIS", properties={"source": "operational-ontology"})
    for key, label in INSIGHT_TYPES:
        add_entity(graph, "insight-type", key, label, {"tboxClass": "InsightType", "key": key})
    for pipeline in OPERATIONAL_PIPELINES:
        key = str(pipeline["key"])
        fallback_key = str(pipeline.get("fallbackSettingKey") or "")
        target_minutes = number(pipeline.get("defaultMinutes"))
        minutes = configured_minutes(settings, str(pipeline["scheduleKey"]), target_minutes, fallback_key)
        pipeline_id = add_entity(graph, "data-pipeline", key, str(pipeline["label"]), {
            "tboxClass": "DataPipeline",
            "tboxClasses": list(pipeline.get("tboxClasses") or ["DataPipeline"]),
            "key": key,
            "dataKinds": list(pipeline.get("dataKinds") or []),
            "targetMinutes": target_minutes,
            "configuredMinutes": minutes,
            "description": str(pipeline.get("description") or ""),
        })
        source_id = add_entity(graph, "data-source", str(pipeline["sourceKey"]), str(pipeline["sourceLabel"]), {
            "tboxClass": "DataSource",
            "dataKinds": list(pipeline.get("dataKinds") or []),
        })
        schedule_id = add_entity(graph, "collection-schedule", key + ":" + str(int(minutes)), str(pipeline["label"]) + " " + str(int(minutes)) + "분", {
            "tboxClass": "CollectionSchedule",
            "pipeline": key,
            "targetMinutes": target_minutes,
            "configuredMinutes": minutes,
            "settingKey": str(pipeline.get("scheduleKey") or ""),
            "fallbackSettingKey": fallback_key,
        })
        freshness_id = add_entity(graph, "data-freshness", key, str(pipeline["label"]) + " freshness", {
            "tboxClass": "DataFreshness",
            "targetMinutes": target_minutes,
            "configuredMinutes": minutes,
            "freshnessRole": "ai-confidence-input",
        })
        add_relation(graph, portfolio_node_id, pipeline_id, "HAS_PIPELINE", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, source_id, "COLLECTS_DATA_FROM", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, schedule_id, "RUNS_ON_SCHEDULE", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, freshness_id, "HAS_DATA_FRESHNESS", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, collection_policy_id, "USES_COLLECTION_POLICY", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, portfolio_node_id, "UPDATES_GRAPH", properties={"source": "operational-ontology"})
        add_relation(graph, pipeline_id, reasoning_id, "TRIGGERS_REASONING", properties={"source": "operational-ontology"})


def add_strategy_world_concepts(
    graph: PortfolioOntology,
    portfolio_node_id: str,
    runtime_context: Dict[str, object],
) -> str:
    settings = runtime_settings(runtime_context)
    strategy_id = add_entity(graph, "strategy", "ontology-first-investment-strategy", "온톨로지 투자전략", {
        "tboxClass": "Strategy",
        "mode": "ontology-first",
        "description": "계산 점수보다 TBox/ABox 관계, 근거 충돌, 데이터 품질, 운영 정책을 먼저 읽습니다.",
    })
    thesis_id = add_entity(graph, "investment-thesis", "portfolio-relation-thesis", "포트폴리오 관계 투자 가설", {
        "tboxClass": "InvestmentThesis",
        "scope": "portfolio",
        "thesis": "실세계 관측값과 포트폴리오 노출이 투자 의견의 주 근거이며 기존 점수는 보조 근거입니다.",
    })
    entry_id = add_entity(graph, "entry-condition", "evidence-confirmed-entry", "근거 확인 진입 조건", {
        "tboxClass": "EntryCondition",
        "requires": ["price-observation", "trend-signal", "flow-signal", "data-quality"],
    })
    exit_id = add_entity(graph, "exit-condition", "risk-invalidates-thesis", "가설 약화 청산 조건", {
        "tboxClass": "ExitCondition",
        "requires": ["risk-amplification", "contradiction", "position-sizing-check"],
    })
    risk_rule_id = add_entity(graph, "risk-management-rule", "relation-risk-first", "관계 리스크 우선 규칙", {
        "tboxClass": "RiskManagementRule",
        "minimumDataQuality": 60,
        "riskPressureThreshold": 55,
    })
    sizing_id = add_entity(graph, "position-sizing-rule", "exposure-aware-sizing", "노출 기반 비중 규칙", {
        "tboxClass": "PositionSizingRule",
        "uses": ["positionWeight", "sectorWeight", "cashRatio"],
    })
    rebalance_id = add_entity(graph, "rebalancing-rule", "meaningful-exposure-change", "의미 있는 노출 변화 리밸런싱", {
        "tboxClass": "RebalancingRule",
        "noveltyThreshold": number(settings.get("notificationNoveltyThreshold")) or 0.65,
    })
    add_relation(graph, portfolio_node_id, strategy_id, "USES_STRATEGY", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, thesis_id, "BASED_ON_THESIS", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, entry_id, "HAS_ENTRY_CONDITION", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, exit_id, "HAS_EXIT_CONDITION", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, risk_rule_id, "HAS_RISK_MANAGEMENT_RULE", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, sizing_id, "HAS_POSITION_SIZING_RULE", properties={"source": "strategy-ontology"})
    add_relation(graph, strategy_id, rebalance_id, "HAS_REBALANCING_RULE", properties={"source": "strategy-ontology"})
    return strategy_id


def add_decision_item_concepts(graph: PortfolioOntology, runtime_context: Dict[str, object]) -> None:
    items = runtime_context.get("decisionItems") if isinstance(runtime_context, dict) else []
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        stock_id = entity_id("stock", symbol)
        signal_id = add_entity(graph, "strategy-signal", symbol + ":" + str(item.get("decision") or "decision"), str(item.get("decision") or "전략 판단"), {
            "tboxClass": "StrategySignal",
            "tboxClasses": ["Signal", "StrategySignal"],
            "source": str(item.get("source") or ""),
            "tone": str(item.get("tone") or ""),
            "priority": number(item.get("priority")),
            "exitPressure": number(item.get("exitPressure")),
            "reasons": list(item.get("reasons") or [])[:5],
            "triggers": list(item.get("triggers") or [])[:8],
        })
        properties = {"source": "decision-item", "aiInfluenceLabel": str(item.get("decision") or "전략 판단")}
        if number(item.get("exitPressure")) >= 55:
            properties.update({"polarity": "risk", "opinionImpact": min(16.0, (number(item.get("exitPressure")) - 45) * 0.3)})
        add_relation(graph, stock_id, signal_id, "DERIVES", weight=round(number(item.get("exitPressure")) / 100, 4), properties=properties)
        add_relation(graph, signal_id, stock_id, "USED_AS_EVIDENCE", weight=0.55, properties={"source": "decision-item"})


def add_external_signal_concepts(graph: PortfolioOntology, portfolio_node_id: str, external_signals: Dict[str, object]) -> None:
    if not isinstance(external_signals, dict):
        return
    add_external_signal_quality_concepts(graph, portfolio_node_id, external_signals)
    add_portfolio_macro_and_cross_asset_concepts(graph, portfolio_node_id, external_signals)
    for key, value in sorted(external_signals.items()):
        if key in {"quality", "freshness", "provenance"}:
            continue
        signal_id = add_entity(graph, "external-signal", key, str(key), {
            "tboxClass": "ExternalSignal",
            "tboxClasses": external_signal_classes(str(key)),
            "key": str(key),
            "value": safe_setting_value(str(key), value),
        })
        properties = {"source": "external-signals", "aiInfluenceLabel": "외부 신호 " + str(key)}
        if isinstance(value, dict):
            magnitude = max([abs(number(item)) for item in value.values()] + [0.0])
            if magnitude >= 3:
                properties.update({"polarity": "risk", "opinionImpact": min(12.0, magnitude)})
        add_relation(graph, portfolio_node_id, signal_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=properties)
        add_relation(graph, portfolio_node_id, signal_id, "HAS_OBSERVATION", weight=1.0, properties=properties)


def add_portfolio_macro_and_cross_asset_concepts(graph: PortfolioOntology, portfolio_node_id: str, external_signals: Dict[str, object]) -> None:
    macro = external_signals.get("macro") if isinstance(external_signals.get("macro"), dict) else {}
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    for series_id, item in sorted(series.items()):
        if not isinstance(item, dict):
            continue
        value = number(item.get("value"))
        macro_id = add_entity(graph, "macro-print", str(series_id), "FRED " + str(series_id), {
            "tboxClass": "MacroPrint",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "MacroIndicator", "MacroPrint", "MacroSignal", "RegimeRisk"],
            "seriesId": str(series_id),
            "provider": str(item.get("provider") or "FRED"),
            "date": str(item.get("date") or ""),
            "value": round(value, 4),
        })
        props = {"source": "macro", "polarity": "context", "aiInfluenceLabel": "거시 지표 " + str(series_id)}
        if str(series_id).upper() in {"DGS10", "DGS2", "DFF"}:
            props.update({"polarity": "risk" if value >= 4.5 else "context", "opinionImpact": 5.0 if value >= 4.5 else 0.0})
        add_relation(graph, portfolio_node_id, macro_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        add_relation(graph, macro_id, portfolio_node_id, "AFFECTS", weight=0.65, properties=props)
    if "yieldSpread10y2y" in macro:
        spread = number(macro.get("yieldSpread10y2y"))
        spread_id = add_entity(graph, "macro-print", "yieldSpread10y2y", "10Y-2Y 금리 스프레드", {
            "tboxClass": "MacroPrint",
            "tboxClasses": ["Observation", "ExternalObservation", "MacroIndicator", "MacroPrint", "CreditSpreadSignal", "RegimeRisk"],
            "value": round(spread, 4),
        })
        props = {"source": "macro", "polarity": "risk" if spread < 0 else "context", "opinionImpact": 6.0 if spread < 0 else 0.0, "aiInfluenceLabel": "금리 스프레드 레짐"}
        add_relation(graph, portfolio_node_id, spread_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        add_relation(graph, spread_id, portfolio_node_id, "AFFECTS", weight=0.72, properties=props)
    crypto = external_signals.get("cryptoMarkets") if isinstance(external_signals.get("cryptoMarkets"), dict) else {}
    for coin_id, item in sorted(crypto.items()):
        if not isinstance(item, dict):
            continue
        change24h = number(item.get("change24h"))
        change7d = number(item.get("change7d"))
        crypto_id = add_entity(graph, "crypto-market-signal", str(coin_id), str(item.get("name") or coin_id), {
            "tboxClass": "CryptoMarketSignal",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "CryptoMarketSignal", "CryptoSignal"],
            "provider": str(item.get("provider") or "CoinGecko"),
            "symbol": str(item.get("symbol") or "").upper(),
            "price": round(number(item.get("price")), 4),
            "change24h": round(change24h, 2),
            "change7d": round(change7d, 2),
            "volume24h": round(number(item.get("volume24h")), 2),
        })
        magnitude = max(abs(change24h), abs(change7d))
        props = {"source": "cryptoMarkets", "polarity": "risk" if magnitude >= 4 else "context", "opinionImpact": min(12.0, magnitude) if magnitude >= 4 else 0.0, "aiInfluenceLabel": str(item.get("name") or coin_id) + " 변동성"}
        add_relation(graph, portfolio_node_id, crypto_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        add_relation(graph, crypto_id, portfolio_node_id, "AFFECTS", weight=min(1.0, magnitude / 10), properties=props)


def add_external_signal_quality_concepts(graph: PortfolioOntology, portfolio_node_id: str, external_signals: Dict[str, object]) -> None:
    quality = external_signals.get("quality") if isinstance(external_signals.get("quality"), dict) else {}
    freshness = external_signals.get("freshness") if isinstance(external_signals.get("freshness"), dict) else {}
    provenance = external_signals.get("provenance") if isinstance(external_signals.get("provenance"), dict) else {}
    if quality:
        quality_id = add_entity(graph, "data-quality", "externalSignals", "외부 신호 품질", {
            "tboxClass": "DataQuality",
            "tboxClasses": ["Observation", "DataQuality", "DataQualitySignal", "Provenance"],
            "qualityScore": number(quality.get("score")),
            "coverageScore": number(quality.get("coverageScore")),
            "sourceHealthScore": number(quality.get("sourceHealthScore")),
            "errorCount": number(quality.get("errorCount")),
            "symbolCoverage": quality.get("symbolCoverage") if isinstance(quality.get("symbolCoverage"), dict) else {},
        })
        relation_props = {"source": "external-signal-quality", "aiInfluenceLabel": "외부 신호 품질"}
        if number(quality.get("score")) < 60:
            relation_props.update({"polarity": "risk", "opinionImpact": round((60 - number(quality.get("score"))) * 0.25, 2)})
        add_relation(graph, portfolio_node_id, quality_id, "HAS_DATA_QUALITY", weight=round(number(quality.get("score")) / 100, 4), properties=relation_props)
        add_relation(graph, portfolio_node_id, quality_id, "HAS_OBSERVATION", weight=round(number(quality.get("score")) / 100, 4), properties=relation_props)
    if freshness:
        freshness_id = add_entity(graph, "data-freshness", "externalSignals-runtime", "외부 신호 신선도", {
            "tboxClass": "DataFreshness",
            "fetchedAt": str(freshness.get("fetchedAt") or ""),
            "ageMinutes": number(freshness.get("ageMinutes")),
            "status": str(freshness.get("status") or ""),
        })
        relation_props = {"source": "external-signal-freshness", "aiInfluenceLabel": "외부 신호 신선도"}
        if str(freshness.get("status") or "") == "stale":
            relation_props.update({"polarity": "risk", "opinionImpact": 8.0})
        add_relation(graph, portfolio_node_id, freshness_id, "HAS_DATA_FRESHNESS", weight=1.0, properties=relation_props)
    if provenance:
        provenance_id = add_entity(graph, "provenance", "externalSignals", "외부 신호 출처", {
            "tboxClass": "Provenance",
            "sources": list(provenance.get("sources") or [])[:12],
            "unavailableSources": list(provenance.get("unavailableSources") or [])[:12],
        })
        add_relation(graph, portfolio_node_id, provenance_id, "HAS_PROVENANCE", weight=1.0, properties={"source": "external-signal-provenance", "aiInfluenceLabel": "외부 신호 출처"})


def symbol_external_signal_items(external_signals: Dict[str, object], symbol: str) -> List[Dict[str, object]]:
    if not isinstance(external_signals, dict):
        return []
    candidates = {str(symbol or "").upper(), str(symbol or "").lower(), str(symbol or "")}
    rows: List[Dict[str, object]] = []
    for group_key, group_value in sorted(external_signals.items()):
        if not isinstance(group_value, dict):
            continue
        matched_key = next((key for key in candidates if key in group_value), "")
        if not matched_key:
            continue
        rows.append({
            "group": str(group_key),
            "symbolKey": matched_key,
            "value": group_value.get(matched_key),
        })
    return rows


def external_signal_relation_properties(group: str, value: object) -> Dict[str, object]:
    properties = {"source": "external-signals", "signalGroup": group, "aiInfluenceLabel": "외부 신호 " + group}
    if isinstance(value, dict):
        count = number(value.get("count"))
        sentiment = number(value.get("sentiment") or value.get("score") or value.get("riskScore"))
        if count and group in {"newsHeadlines", "secFilings", "dartDisclosures"}:
            properties.update({"polarity": "context", "aiInfluenceLabel": group + " " + str(int(count)) + "건"})
        if sentiment < 0:
            properties.update({"polarity": "risk", "opinionImpact": min(12.0, abs(sentiment)), "aiInfluenceLabel": group + " 부정 신호"})
        elif sentiment > 0:
            properties.update({"polarity": "support", "supportImpact": min(10.0, sentiment), "aiInfluenceLabel": group + " 긍정 신호"})
    return properties


def add_symbol_external_signal_concepts(graph: PortfolioOntology, stock_id: str, symbol: str, external_signals: Dict[str, object]) -> None:
    for row in symbol_external_signal_items(external_signals, symbol):
        group = str(row.get("group") or "")
        signal_id = add_entity(graph, "external-signal", symbol + ":" + group, group + " 외부 신호", {
            "tboxClass": "ExternalSignal",
            "tboxClasses": external_signal_classes(group),
            "symbol": symbol,
            "group": group,
            "value": safe_setting_value(group, row.get("value")),
        })
        add_relation(
            graph,
            stock_id,
            signal_id,
            "HAS_OBSERVATION",
            weight=1.0,
            properties=external_signal_relation_properties(group, row.get("value")),
        )
        add_relation(
            graph,
            stock_id,
            signal_id,
            "HAS_EXTERNAL_SIGNAL",
            weight=1.0,
            properties=external_signal_relation_properties(group, row.get("value")),
        )
        add_symbol_fundamental_event_concepts(graph, stock_id, symbol, group, row.get("value"))


def add_symbol_fundamental_event_concepts(graph: PortfolioOntology, stock_id: str, symbol: str, group: str, value: object) -> None:
    if not isinstance(value, dict) or group not in {"secFilings", "dartDisclosures"}:
        return
    latest = value.get("latestFiling") if isinstance(value.get("latestFiling"), dict) else {}
    facts = value.get("facts") if isinstance(value.get("facts"), dict) else {}
    label = "펀더멘털 이벤트"
    if group == "dartDisclosures":
        label = str(value.get("reportName") or "DART 공시 이벤트")
    elif latest:
        label = "SEC " + str(latest.get("form") or "filing") + " 이벤트"
    event_id = add_entity(graph, "fundamental-event", symbol + ":" + group, label, {
        "tboxClass": "FundamentalObservation",
        "tboxClasses": ["Observation", "ExternalObservation", "FundamentalObservation", "ExternalSignal", "DisclosureEvent", "EarningsEvent", "ValuationSignal"],
        "symbol": symbol,
        "group": group,
        "provider": str(value.get("provider") or ""),
        "latestFiling": latest,
        "facts": facts,
    })
    add_relation(graph, stock_id, event_id, "HAS_OBSERVATION", weight=1.0, properties={"source": group, "aiInfluenceLabel": label})
    add_relation(graph, stock_id, event_id, "HAS_VALUATION", weight=0.7, properties={"source": group, "polarity": "context", "aiInfluenceLabel": label})


def build_portfolio_ontology(
    positions: Iterable[Position],
    portfolio: PortfolioSummary,
    legacy_by_symbol: Dict[str, Dict[str, object]] = None,
    external_signals: Dict[str, object] = None,
    portfolio_id: str = "portfolio",
    runtime_context: Dict[str, object] = None,
) -> PortfolioOntology:
    legacy_by_symbol = legacy_by_symbol or {}
    external_signals = external_signals or {}
    runtime_context = runtime_context or {}
    observed_by_symbol: Dict[str, Position] = {}
    for item in positions:
        if not observable_position(item):
            continue
        key = symbol_key(item)
        previous = observed_by_symbol.get(key)
        if previous is None or (is_watchlist_position(previous) and is_holding_position(item)):
            observed_by_symbol[key] = item
    observed_positions = list(observed_by_symbol.values())
    graph = PortfolioOntology(portfolio_id=portfolio_id)
    graph.entities.extend(tbox_entities())
    graph.relations.extend(tbox_relations())
    portfolio_node_id = entity_id("portfolio", portfolio_id)
    account_context = runtime_context.get("account") if isinstance(runtime_context, dict) else {}
    account_context = account_context if isinstance(account_context, dict) else {}
    account_value = str(account_context.get("accountId") or account_context.get("id") or portfolio_id or "account")
    account_label = str(account_context.get("accountLabel") or account_context.get("label") or account_value or "투자 계좌")
    account_id_value = add_entity(graph, "account", account_value, account_label, {
        "tboxClass": "Account",
        "provider": account_context.get("provider") or (runtime_context.get("provider") if isinstance(runtime_context, dict) else ""),
        "mode": account_context.get("mode") or (runtime_context.get("mode") if isinstance(runtime_context, dict) else ""),
        "status": account_context.get("status") or "",
    })
    graph.entities.append(OntologyEntity(portfolio_node_id, "투자 포트폴리오", "portfolio", abox_properties({
        "total": number(portfolio.total),
        "invested": number(portfolio.invested),
        "cash": number(portfolio.cash),
        "concentration": number(portfolio.concentration),
        "tboxClass": "Portfolio",
    })))
    add_relation(graph, account_id_value, portfolio_node_id, "MANAGES_PORTFOLIO", weight=1.0, properties={"source": "account-context"})
    graph.entities.append(OntologyEntity(entity_id("concept", "legacy-score-model"), "기존 점수 모델", "model", abox_properties({
        "role": "supporting-evidence",
        "tboxClass": "LegacyScoreModel",
    })))
    graph.entities.append(OntologyEntity(entity_id("concept", "ai-investment-review"), "AI 투자 의견", "ai-review", abox_properties({
        "promptVersion": ONTOLOGY_PROMPT_VERSION,
        "tboxClass": "AIReview",
    })))
    if portfolio.cash:
        graph.entities.append(OntologyEntity(entity_id("asset", "cash"), "대기 현금", "cash", abox_properties({
            "value": number(portfolio.cash),
            "cashRatio": round((number(portfolio.cash) / number(portfolio.total)) * 100, 2) if number(portfolio.total) else 0,
            "tboxClass": "Cash",
        })))
        graph.relations.append(OntologyRelation(
            portfolio_node_id,
            entity_id("asset", "cash"),
            "HOLDS_CASH",
            weight=1.0,
            properties=abox_properties(),
        ))
    add_market_exposure_concepts(graph, portfolio_node_id, portfolio)
    add_portfolio_factor_exposure_concepts(graph, portfolio_node_id, portfolio, observed_positions)
    add_runtime_setting_concepts(graph, portfolio_node_id, runtime_context)
    add_runtime_metadata_concepts(graph, portfolio_node_id, runtime_context)
    add_operational_world_concepts(graph, portfolio_node_id, runtime_context, observed_positions)
    strategy_id = add_strategy_world_concepts(graph, portfolio_node_id, runtime_context)
    add_external_signal_concepts(graph, portfolio_node_id, external_signals)
    watchlist_id = ""
    if any(is_watchlist_position(item) for item in observed_positions):
        watchlist_id = add_entity(graph, "watchlist", portfolio_id, "관심 종목 목록", {
            "tboxClass": "Watchlist",
            "candidateCount": len([item for item in observed_positions if is_watchlist_position(item)]),
        })
        add_relation(graph, portfolio_node_id, watchlist_id, "HAS_WATCHLIST", weight=1.0, properties={"source": "watchlist"})
    sector_weights: Dict[str, float] = {}
    for sector in portfolio.sectors:
        label = str(sector.get("sector") or "기타")
        sector_weights[label] = number(sector.get("ratio"))
        graph.entities.append(OntologyEntity(entity_id("sector", label), label, "sector", abox_properties({**dict(sector), "tboxClass": "Sector"})))
        graph.relations.append(OntologyRelation(
            portfolio_node_id,
            entity_id("sector", label),
            "EXPOSED_TO",
            weight=round(number(sector.get("ratio")) / 100, 4),
            properties=abox_properties({"basis": "sector-weight"}),
        ))
    for position in observed_positions:
        symbol = symbol_key(position)
        stock_id = entity_id("stock", symbol)
        source = "watchlist" if is_watchlist_position(position) else "holding"
        holding = is_holding_position(position)
        stock_tbox_classes = instrument_tbox_classes(position) + (["WatchlistCandidate"] if source == "watchlist" else [])
        graph.entities.append(OntologyEntity(stock_id, position.name or symbol, "stock", abox_properties({
            "symbol": symbol,
            "market": position.market,
            "currency": position.currency,
            "sector": position.sector,
            "source": source,
            "marketValue": number(position.market_value),
            "profitLossRate": number(position.profit_loss_rate),
            "tboxClass": "Stock",
            "tboxClasses": stock_tbox_classes,
        })))
        position_id = add_entity(graph, "position", portfolio_id + ":" + symbol, (position.name or symbol) + (" 관심 행" if source == "watchlist" else " 보유 행"), {
            "tboxClass": "Position",
            "tboxClasses": ["Position"] + (["WatchlistCandidate"] if source == "watchlist" else []),
            "symbol": symbol,
            "source": source,
            "quantity": number(position.quantity),
            "marketValue": number(position.market_value),
            "profitLossRate": number(position.profit_loss_rate),
            "updatedAt": position.updated_at,
        })
        if holding:
            add_relation(graph, portfolio_node_id, position_id, "HAS_POSITION", weight=round(position_weight(position, portfolio) / 100, 4), properties={"source": source})
        elif watchlist_id:
            add_relation(graph, watchlist_id, position_id, "HAS_POSITION", weight=0.15, properties={"source": source})
        add_relation(graph, position_id, stock_id, "REPRESENTS_STOCK", weight=1.0, properties={"source": source})
        for kind, label in [("market", position.market or "unknown"), ("currency", position.currency or "unknown")]:
            tbox_class = "Market" if kind == "market" else "Currency"
            graph.entities.append(OntologyEntity(entity_id(kind, label), label, kind, abox_properties({"tboxClass": tbox_class})))
        graph.relations.append(OntologyRelation(
            portfolio_node_id,
            stock_id,
            "HOLDS" if holding else "WATCHES",
            weight=round(position_weight(position, portfolio) / 100, 4) if holding else 0.15,
            properties=abox_properties({"source": source, "basis": "portfolio-position" if holding else "watchlist"}),
        ))
        graph.relations.extend([
            OntologyRelation(stock_id, entity_id("sector", position.sector or "기타"), "BELONGS_TO", weight=1.0, properties=abox_properties({"source": source})),
            OntologyRelation(stock_id, entity_id("market", position.market or "unknown"), "TRADED_IN", weight=1.0, properties=abox_properties({"source": source})),
            OntologyRelation(stock_id, entity_id("currency", position.currency or "unknown"), "DENOMINATED_IN", weight=1.0, properties=abox_properties({"source": source})),
            OntologyRelation(stock_id, entity_id("concept", "ai-investment-review"), "REQUESTS_OPINION_FROM", weight=1.0, properties=abox_properties({"source": source})),
        ])
        if holding:
            graph.relations.append(OntologyRelation(
                stock_id,
                entity_id("concept", "legacy-score-model"),
                "USES_EVIDENCE_FROM",
                weight=0.55,
                properties=abox_properties({"source": source}),
            ))
        legacy = legacy_by_symbol.get(symbol) or legacy_by_symbol.get(position.symbol) or {}
        add_data_source_concept(graph, stock_id, position, source)
        add_metric_concepts(graph, stock_id, position, source)
        add_price_level_and_liquidity_concepts(graph, stock_id, position, source)
        add_legacy_model_score_concepts(graph, stock_id, symbol, legacy)
        add_symbol_external_signal_concepts(graph, stock_id, symbol, external_signals)
        add_position_factor_concepts(graph, stock_id, portfolio_node_id, position, portfolio)
        opinion = build_position_opinion(position, portfolio, legacy) if holding else build_watchlist_opinion(position, legacy)
        graph.opinions.append(opinion)
        thesis_id = add_entity(graph, "investment-thesis", symbol, (position.name or symbol) + " 투자 가설", {
            "tboxClass": "InvestmentThesis",
            "symbol": symbol,
            "source": source,
            "thesis": opinion.thesis,
            "action": opinion.action,
            "confidence": number(opinion.conviction),
            "ontologyPressure": number(opinion.ontology_pressure),
        })
        active_relation_context = evaluate_position_relation_rules(
            position,
            portfolio,
            external_signals=external_signals,
            settings=runtime_context.get("settings") if isinstance(runtime_context.get("settings"), dict) else {},
            legacy_model=legacy,
            previous_state=previous_position_state(runtime_context, symbol, source),
            previous_decision=previous_decision_state(runtime_context, symbol),
        )
        active_opinion_payload = build_active_investment_opinion(
            position,
            relation_context=active_relation_context,
            ontology_opinion=opinion.to_dict(),
            legacy_model=legacy,
            external_signals=external_signals,
        ).to_dict()
        active_opinion_id = add_entity(graph, "active-opinion", symbol, (position.name or symbol) + " 적극 투자 의견", {
            "tboxClass": "Opinion",
            "tboxClasses": ["Opinion", "ActiveInvestmentOpinion", "AIReview", "Insight"],
            "symbol": symbol,
            "source": source,
            "action": active_opinion_payload.get("action"),
            "actionLabel": active_opinion_payload.get("actionLabel"),
            "conviction": active_opinion_payload.get("conviction"),
            "activeInvestmentOpinion": active_opinion_payload,
        })
        execution_plan_payload = active_opinion_payload.get("executionPlan") if isinstance(active_opinion_payload.get("executionPlan"), dict) else {}
        execution_plan_id = add_execution_plan_concepts(
            graph,
            stock_id,
            active_opinion_id,
            symbol,
            source,
            execution_plan_payload,
        )
        add_research_evidence_concepts(
            graph,
            stock_id,
            thesis_id,
            active_opinion_id,
            symbol,
            active_relation_context.get("facts") if isinstance(active_relation_context.get("facts"), dict) else {},
            external_signals,
        )
        add_relation_state_concepts(
            graph,
            stock_id,
            symbol,
            position,
            source,
            runtime_context,
            active_relation_context,
        )
        horizon_id = add_entity(graph, "signal-horizon", symbol + ":" + source, "보유 점검 기간" if holding else "관심 관찰 기간", {
            "tboxClass": "SignalHorizon",
            "symbol": symbol,
            "source": source,
            "horizon": "position-risk-review" if holding else "watchlist-entry-check",
            "validity": "until-next-data-update",
        })
        add_relation(graph, stock_id, thesis_id, "BASED_ON_THESIS", weight=round(number(opinion.conviction) / 100, 4), properties={"source": "ontology-opinion"})
        add_relation(graph, strategy_id, thesis_id, "BASED_ON_THESIS", weight=round(number(opinion.conviction) / 100, 4), properties={"source": "ontology-opinion"})
        add_relation(graph, stock_id, active_opinion_id, "HAS_OPINION", weight=round(number(active_opinion_payload.get("conviction")) / 100, 4), properties={
            "source": "active-investment-opinion",
            "polarity": "context",
            "aiInfluenceLabel": str(active_opinion_payload.get("actionLabel") or active_opinion_payload.get("action") or "적극 투자 의견"),
        })
        add_relation(graph, active_opinion_id, thesis_id, "IMPACTS_OPINION", weight=round(number(active_opinion_payload.get("conviction")) / 100, 4), properties={
            "source": "active-investment-opinion",
            "opinionImpact": number(active_opinion_payload.get("conviction")) / 10,
            "aiInfluenceLabel": str(active_opinion_payload.get("thesis") or "적극 투자 의견"),
        })
        if execution_plan_id:
            add_relation(graph, execution_plan_id, thesis_id, "IMPACTS_OPINION", weight=0.86, properties={
                "source": "ontology-execution-plan",
                "opinionImpact": number(active_opinion_payload.get("conviction")) / 12,
                "aiInfluenceLabel": str(execution_plan_payload.get("primaryActionLabel") or "실행 계획"),
            })
        add_relation(graph, stock_id, horizon_id, "HAS_TIME_HORIZON", weight=1.0, properties={"source": "ontology-opinion"})
        add_relation(graph, thesis_id, horizon_id, "APPLIES_TO_HORIZON", weight=1.0, properties={"source": "ontology-opinion"})
        weight = position_weight(position, portfolio)
        trend = trend_score(position)
        trend_dynamic = trend_dynamic_facts(position)
        flow = smart_money_score(position)
        quality = data_quality_score(position)
        if holding:
            evidence_rows = [
                ("legacy-model", "legacyModel", "기존 점수 모델을 보조 근거로 사용", opinion.legacy_model, 0.75),
                ("portfolio-exposure", "portfolio", "포트폴리오/섹터 노출 관계", {
                    "positionWeight": round(weight, 2),
                    "sectorWeight": round(sector_weights.get(position.sector, 0.0), 2),
                }, 0.85),
                ("trend", "market-data", "이동평균과 가격 추세 관계", {"trendScore": round(trend, 2), "trendDynamics": trend_dynamic}, 0.65),
                ("flow", "market-data", "외국인·기관 수급 관계", {"smartMoneyScore": round(flow, 2)}, 0.6),
                ("data-quality", "data-quality", "AI 판단에 투입할 데이터 완성도", {"qualityScore": round(quality, 2)}, 0.7),
            ]
        else:
            evidence_rows = [
                ("market-observation", "watchlist", "관심 종목 현재가와 관찰 상태", {
                    "currentPrice": round(number(position.current_price), 4),
                    "market": position.market,
                    "currency": position.currency,
                }, 0.62),
                ("trend", "market-data", "관심 종목 이동평균과 가격 추세 관계", {"trendScore": round(trend, 2), "trendDynamics": trend_dynamic}, 0.55),
                ("flow", "market-data", "관심 종목 외국인·기관 수급 관계", {"smartMoneyScore": round(flow, 2)}, 0.5),
                ("data-quality", "data-quality", "진입 관찰에 투입할 데이터 완성도", {"qualityScore": round(quality, 2)}, 0.65),
            ]
        for kind, source, summary, value, confidence in evidence_rows:
            graph.evidence.append(OntologyEvidence(
                evidence_id(symbol, kind),
                stock_id,
                kind,
                source,
                summary,
                value,
                confidence,
            ))
        for index, label in enumerate(opinion.supporting_beliefs):
            graph.beliefs.append(OntologyBelief("belief:" + symbol + ":support:" + str(index), stock_id, label, "support", 0.72, opinion.evidence_ids))
        for index, label in enumerate(opinion.dominant_risks):
            graph.beliefs.append(OntologyBelief("belief:" + symbol + ":risk:" + str(index), stock_id, label, "risk", 0.7, opinion.evidence_ids))
        for risk in opinion.dominant_risks:
            risk_id = entity_id("risk", risk)
            graph.entities.append(OntologyEntity(risk_id, risk, "risk", abox_properties({
                "tboxClass": "Risk",
                "tboxClasses": risk_tbox_classes(risk),
            })))
            graph.relations.append(OntologyRelation(stock_id, risk_id, "EXPOSED_TO", weight=0.75, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("EXPOSED_TO")))
            graph.relations.append(OntologyRelation(risk_id, thesis_id, "WEAKENS_THESIS", weight=0.72, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("WEAKENS_THESIS", {
                "polarity": "context",
                "aiInfluenceLabel": risk,
            })))
            graph.relations.append(OntologyRelation(risk_id, stock_id, "AMPLIFIES_RISK", weight=0.62, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("AMPLIFIES_RISK", {
                "polarity": "context",
                "aiInfluenceLabel": risk,
            })))
        if opinion.opportunities:
            opportunity_id = entity_id("opportunity", opinion.opportunities[0])
            graph.entities.append(OntologyEntity(opportunity_id, opinion.opportunities[0], "opportunity", abox_properties({"tboxClass": "Opportunity"})))
            graph.relations.append(OntologyRelation(stock_id, opportunity_id, "SUPPORTED_BY", weight=0.65, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("SUPPORTED_BY")))
            graph.relations.append(OntologyRelation(opportunity_id, thesis_id, "SUPPORTS_THESIS", weight=0.62, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("SUPPORTS_THESIS", {
                "polarity": "context",
                "aiInfluenceLabel": opinion.opportunities[0],
            })))
        if opinion.contradictions:
            contradiction_id = entity_id("contradiction", opinion.contradictions[0])
            graph.entities.append(OntologyEntity(contradiction_id, opinion.contradictions[0], "contradiction", abox_properties({"tboxClass": "Contradiction"})))
            graph.relations.append(OntologyRelation(stock_id, contradiction_id, "CONTRADICTS", weight=0.8, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("CONTRADICTS")))
            graph.relations.append(OntologyRelation(contradiction_id, thesis_id, "INVALIDATES_THESIS", weight=0.7, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("INVALIDATES_THESIS", {
                "polarity": "context",
                "aiInfluenceLabel": opinion.contradictions[0],
            })))
    add_decision_item_concepts(graph, runtime_context)
    graph.entities = dedupe_entities(graph.entities)
    graph.relations = dedupe_relations(graph.relations)
    graph.evidence = dedupe_evidence(graph.evidence)
    apply_relation_driven_opinions(graph)
    add_ontology_insight_concepts(graph)
    graph.entities = dedupe_entities(graph.entities)
    graph.relations = dedupe_relations(graph.relations)
    graph.reasoning_cards = build_reasoning_cards(graph)
    graph.worldview = portfolio_worldview(graph, portfolio, external_signals)
    graph.prompt = build_investment_opinion_prompt(graph)
    return graph


def dedupe_entities(items: List[OntologyEntity]) -> List[OntologyEntity]:
    merged: Dict[str, OntologyEntity] = {}
    for item in items:
        if item.entity_id in merged:
            merged[item.entity_id].properties.update(item.properties or {})
            continue
        merged[item.entity_id] = item
    return list(merged.values())


def dedupe_relations(items: List[OntologyRelation]) -> List[OntologyRelation]:
    merged: Dict[str, OntologyRelation] = {}
    for item in items:
        if (item.properties or {}).get("ontologyBox") != "TBox":
            item.properties = abox_relation_properties(item.relation_type, item.properties or {})
        key = "|".join([item.source, item.relation_type, item.target])
        if key in merged:
            merged[key].weight = max(number(merged[key].weight), number(item.weight))
            merged[key].evidence_ids = sorted(set(merged[key].evidence_ids + item.evidence_ids))
            merged[key].properties.update(item.properties or {})
            continue
        merged[key] = item
    return list(merged.values())


def dedupe_evidence(items: List[OntologyEvidence]) -> List[OntologyEvidence]:
    merged: Dict[str, OntologyEvidence] = {}
    for item in items:
        merged[item.evidence_id] = item
    return list(merged.values())


def relation_influence_score(relation: OntologyRelation) -> (float, float):
    properties = relation.properties or {}
    polarity = str(properties.get("polarity") or properties.get("signalPolarity") or "").lower()
    if polarity == "context":
        return 0.0, 0.0
    risk = number(properties.get("opinionImpact") or properties.get("riskImpact") or properties.get("impactScore"))
    support = number(properties.get("supportImpact"))
    if not risk and polarity == "risk":
        risk = number(relation.weight) * 8
    if not support and polarity == "support":
        support = number(relation.weight) * 8
    if relation.relation_type in {"CONTRADICTS", "EXPOSED_TO"} and not support:
        risk = max(risk, number(relation.weight) * (12 if relation.relation_type == "CONTRADICTS" else 8))
    if relation.relation_type == "SUPPORTED_BY" and not risk:
        support = max(support, number(relation.weight) * 8)
    return max(0.0, risk), max(0.0, support)


def relation_influence_rows(graph: PortfolioOntology, stock_id: str) -> List[Dict[str, object]]:
    labels = entity_label_map(graph)
    portfolio_id = entity_id("portfolio", graph.portfolio_id)
    neighbor_ids = {
        relation.source if relation.target == stock_id else relation.target
        for relation in graph.relations
        if relation.source == stock_id or relation.target == stock_id
    }
    rows: List[Dict[str, object]] = []
    for relation in graph.relations:
        if relation.properties.get("ontologyBox") == "TBox":
            continue
        direct = relation.source == stock_id or relation.target == stock_id
        neighbor = relation.source in neighbor_ids or relation.target in neighbor_ids
        portfolio_scope = relation.source == portfolio_id or relation.target == portfolio_id
        if not direct and not neighbor and not portfolio_scope:
            continue
        risk, support = relation_influence_score(relation)
        if not risk and not support:
            continue
        rows.append({
            "relationId": relation_key(relation),
            "scope": "direct" if direct else "neighbor" if neighbor else "portfolio",
            "type": relation.relation_type,
            "source": relation.source,
            "sourceLabel": labels.get(relation.source, relation.source),
            "target": relation.target,
            "targetLabel": labels.get(relation.target, relation.target),
            "riskImpact": round(risk, 2),
            "supportImpact": round(support, 2),
            "label": relation_relation_label(relation, labels),
            "properties": dict(relation.properties or {}),
        })
    return rows


def opinion_action_from_relation_pressure(opinion: OntologyOpinion, source: str, pressure: float) -> (str, str):
    if source == "watchlist":
        if pressure >= 65:
            return "관심 종목: 리스크 관계 우선 점검", "caution"
        if pressure >= 45:
            return "관심 종목: 진입 조건 재확인", "hold"
        return "관심 종목: 진입 기준 대기", "hold"
    return ontology_action_label(pressure, number((opinion.legacy_model or {}).get("profitLossRate")), opinion.contradictions, opinion.dominant_risks)


def apply_relation_driven_opinions(graph: PortfolioOntology) -> None:
    stock_entities = {
        str((item.properties or {}).get("symbol") or "").upper(): item
        for item in graph.entities
        if item.kind == "stock"
    }
    for opinion in graph.opinions:
        stock = stock_entities.get(str(opinion.symbol or "").upper())
        if not stock:
            continue
        properties = stock.properties or {}
        source = str(properties.get("source") or "holding")
        influences = relation_influence_rows(graph, stock.entity_id)
        base_pressure = number((opinion.legacy_model or {}).get("baseOntologyPressure") or opinion.ontology_pressure)
        base_thesis = str((opinion.legacy_model or {}).get("baseThesis") or opinion.thesis or "")
        opinion.legacy_model.setdefault("baseOntologyPressure", round(base_pressure, 1))
        opinion.legacy_model.setdefault("baseThesis", base_thesis)
        opinion.legacy_model.setdefault("profitLossRate", properties.get("profitLossRate", 0))
        risk_impact = sum(number(item.get("riskImpact")) for item in influences)
        support_impact = sum(number(item.get("supportImpact")) for item in influences)
        opinion.relation_influences = influences
        opinion.ontology_pressure = round(clamp(base_pressure + risk_impact - min(18.0, support_impact * 0.65), 0.0, 100.0), 1)
        action, tone = opinion_action_from_relation_pressure(opinion, source, opinion.ontology_pressure)
        opinion.action = action
        opinion.tone = tone
        risk_labels = [item["label"] for item in influences if number(item.get("riskImpact")) > 0]
        support_labels = [item["label"] for item in influences if number(item.get("supportImpact")) > 0]
        for label in risk_labels[:4]:
            if label not in opinion.dominant_risks:
                opinion.dominant_risks.append(label)
        for label in support_labels[:4]:
            if label not in opinion.supporting_beliefs:
                opinion.supporting_beliefs.append(label)
        relation_summary = []
        if risk_labels:
            relation_summary.append("관계 리스크: " + ", ".join(risk_labels[:2]))
        if support_labels:
            relation_summary.append("관계 지지: " + ", ".join(support_labels[:2]))
        opinion.thesis = "; ".join([item for item in [base_thesis] + relation_summary if item])


def insight_type_for_opinion(opinion: OntologyOpinion, stock_source: str) -> str:
    if opinion.contradictions:
        return "contradictionDetected"
    if any("데이터" in str(item) or "부족" in str(item) for item in opinion.dominant_risks + opinion.contradictions):
        return "dataQualityWarning"
    if stock_source == "watchlist":
        return "watchlistEntrySignal"
    if opinion.ontology_pressure >= 55 or opinion.tone in {"danger", "caution"}:
        return "riskIncrease"
    if opinion.opportunities or opinion.supporting_beliefs:
        return "opportunityDetected"
    return "portfolioExposureShift"


def add_ontology_insight_concepts(graph: PortfolioOntology) -> None:
    stock_entities = {
        str((item.properties or {}).get("symbol") or "").upper(): item
        for item in graph.entities
        if item.kind == "stock"
    }
    reasoning_id = entity_id("reasoning-cycle", "ontologyReasoning")
    dispatch_id = entity_id("notification-dispatch", "investmentInsight")
    insight_policy_id = entity_id("insight-policy", "meaningful-change")
    ai_review_id = entity_id("concept", "ai-investment-review")
    for opinion in graph.opinions:
        stock = stock_entities.get(str(opinion.symbol or "").upper())
        if not stock:
            continue
        source = str((stock.properties or {}).get("source") or "holding")
        insight_type = insight_type_for_opinion(opinion, source)
        insight_id = add_entity(graph, "insight", opinion.symbol + ":" + insight_type, stock.label + " " + opinion.action, {
            "tboxClass": "Insight",
            "symbol": opinion.symbol,
            "insightType": insight_type,
            "severity": opinion.tone,
            "score": number(opinion.ontology_pressure),
            "confidence": number(opinion.conviction),
            "thesis": opinion.thesis,
            "relationInfluenceCount": len(opinion.relation_influences or []),
            "dispatchCandidate": bool(opinion.ontology_pressure >= 55 or opinion.contradictions or source == "watchlist"),
        })
        add_relation(graph, reasoning_id, insight_id, "PRODUCES_INSIGHT", weight=round(number(opinion.conviction) / 100, 4), properties={"source": "ontology-reasoning"})
        add_relation(graph, stock.entity_id, insight_id, "CREATED_FROM_RELATION", weight=round(number(opinion.ontology_pressure) / 100, 4), properties={"source": "ontology-reasoning"})
        add_relation(graph, insight_id, entity_id("insight-type", insight_type), "HAS_INSIGHT_TYPE", weight=1.0, properties={"source": "ontology-reasoning"})
        add_relation(graph, insight_id, insight_policy_id, "EVALUATED_BY", weight=1.0, properties={"source": "ontology-reasoning"})
        add_relation(graph, insight_id, dispatch_id, "DISPATCHED_BY", weight=1.0, properties={"source": "ontology-reasoning", "mode": "insight-driven-only"})
        add_relation(graph, insight_id, ai_review_id, "REQUESTS_OPINION_FROM", weight=1.0, properties={"source": "ontology-reasoning"})


def relation_key(item: OntologyRelation) -> str:
    return "|".join([item.source, item.relation_type, item.target])


def entity_label_map(graph: PortfolioOntology) -> Dict[str, str]:
    return {item.entity_id: item.label for item in graph.entities}


def reasoning_card_data_gaps(evidence_rows: List[OntologyEvidence]) -> List[str]:
    gaps: List[str] = []
    for item in evidence_rows:
        value = item.value or {}
        if item.kind == "data-quality" and number(value.get("qualityScore")) < 60:
            gaps.append("가격·이동평균·수급 데이터 일부 부족")
        if item.kind == "market-observation" and not number(value.get("currentPrice")):
            gaps.append("현재가 미확인")
    return sorted(set(gaps))


def compact_evidence_row(item: OntologyEvidence) -> Dict[str, object]:
    payload = item.to_dict()
    payload["confidence"] = round(number(payload.get("confidence")), 2)
    return payload


def compact_relation_row(item: OntologyRelation, labels: Dict[str, str]) -> Dict[str, object]:
    return {
        "id": relation_key(item),
        "source": item.source,
        "sourceLabel": labels.get(item.source, item.source),
        "target": item.target,
        "targetLabel": labels.get(item.target, item.target),
        "type": item.relation_type,
        "weight": round(number(item.weight), 4),
        "evidenceIds": list(item.evidence_ids or []),
        "properties": dict(item.properties or {}),
    }


def build_reasoning_cards(graph: PortfolioOntology) -> List[Dict[str, object]]:
    labels = entity_label_map(graph)
    entities = {item.entity_id: item for item in graph.entities}
    stocks = [
        item
        for item in graph.entities
        if item.kind == "stock" and (item.properties or {}).get("ontologyBox") != "TBox"
    ]
    cards: List[Dict[str, object]] = []
    for stock in sorted(stocks, key=lambda item: item.label):
        properties = dict(stock.properties or {})
        symbol = str(properties.get("symbol") or stock.label or "").upper()
        if not symbol:
            continue
        relations = [
            item
            for item in graph.relations
            if item.source == stock.entity_id or item.target == stock.entity_id
        ]
        evidence_rows = [item for item in graph.evidence if item.subject == stock.entity_id]
        belief_rows = [item for item in graph.beliefs if item.subject == stock.entity_id]
        opinion = graph.opinion_for_symbol(symbol)
        opinion_payload = opinion.to_dict() if opinion else {}
        neighbor_ids = sorted(set(
            [stock.entity_id]
            + [item.source for item in relations]
            + [item.target for item in relations]
        ))
        tbox_classes = sorted(set(
            [
                str(value)
                for entity_id_value in neighbor_ids
                for value in (
                    (entities.get(entity_id_value).properties or {}).get("tboxClasses")
                    or [(entities.get(entity_id_value).properties or {}).get("tboxClass")]
                    if entities.get(entity_id_value)
                    else []
                )
                if value
            ]
            + ["Evidence", "Belief", "Opinion", "AIReview"]
        ))
        bounded_contexts = sorted(set(
            [
                str((entities.get(entity_id_value).properties or {}).get("boundedContext") or "")
                for entity_id_value in neighbor_ids
                if entities.get(entity_id_value) and (entities.get(entity_id_value).properties or {}).get("boundedContext")
            ]
            + [
                str((relation.properties or {}).get("boundedContext") or "")
                for relation in relations
                if (relation.properties or {}).get("boundedContext")
            ]
        ))
        gaps = reasoning_card_data_gaps(evidence_rows)
        source = str(properties.get("source") or "holding")
        portfolio_relation = next((
            item.relation_type
            for item in relations
            if item.target == stock.entity_id and item.relation_type in {"HOLDS", "WATCHES"}
        ), "HOLDS" if source != "watchlist" else "WATCHES")
        execution_plans = [
            dict((entities.get(item.target).properties or {}).get("executionPlan") or {})
            for item in relations
            if item.relation_type == "HAS_EXECUTION_PLAN"
            and entities.get(item.target)
            and entities.get(item.target).kind == "execution-plan"
        ]
        cards.append({
            "id": "reasoning-card:" + symbol,
            "symbol": symbol,
            "companyName": stock.label,
            "displayName": stock.label,
            "source": source,
            "portfolioRelation": portfolio_relation,
            "status": "needsData" if gaps else "readyForAiReview",
            "finalOpinion": {
                "action": opinion_payload.get("action") or "",
                "tone": opinion_payload.get("tone") or "",
                "ontologyPressure": opinion_payload.get("ontology_pressure") or opinion_payload.get("ontologyPressure") or 0,
                "conviction": opinion_payload.get("conviction") or 0,
                "thesis": opinion_payload.get("thesis") or "",
            },
            "legacyModel": dict(opinion_payload.get("legacy_model") or opinion_payload.get("legacyModel") or {}),
            "relationInfluences": list(opinion_payload.get("relation_influences") or opinion_payload.get("relationInfluences") or []),
            "executionPlans": execution_plans,
            "strategyEvidence": [compact_evidence_row(item) for item in evidence_rows],
            "relationEvidence": [compact_relation_row(item, labels) for item in relations],
            "beliefs": [item.to_dict() for item in belief_rows],
            "dataGaps": gaps,
            "graphContext": {
                "stockEntityId": stock.entity_id,
                "boundedContexts": bounded_contexts,
                "tboxClasses": tbox_classes,
                "aboxEntityIds": neighbor_ids,
                "relationIds": [relation_key(item) for item in relations],
                "evidenceIds": [item.evidence_id for item in evidence_rows],
                "beliefIds": [item.belief_id for item in belief_rows],
                "opinionId": "opinion:" + symbol,
            },
            "aiInference": {
                "role": "ontology-first-investment-opinion",
                "promptVersion": ONTOLOGY_PROMPT_VERSION,
                "legacyModelRole": "supporting-evidence",
                "question": "전략 근거와 관계 근거를 함께 읽고 보유/관심 상태에 맞는 투자 의견, 반대 신호, 다음 검증 순서를 설명합니다.",
            },
        })
    return cards


def build_ai_inference_packet(graph: PortfolioOntology) -> Dict[str, object]:
    pipeline_count = len([item for item in graph.entities if item.kind == "data-pipeline"])
    insight_count = len([item for item in graph.entities if item.kind == "insight"])
    active_opinion_count = len([item for item in graph.entities if item.kind == "active-opinion"])
    execution_plan_count = len([item for item in graph.entities if item.kind == "execution-plan"])
    return {
        "contract": "investment-ontology-ai-inference-v1",
        "promptVersion": ONTOLOGY_PROMPT_VERSION,
        "role": "ontology-first-investment-opinion",
        "legacyModelRole": "supporting-evidence",
        "notificationRole": "insight-driven-dispatch",
        "inputOrder": ["tbox", "boundedContexts", "abox", "operationalOntology", "reasoningCards", "relationInfluences", "researchEvidence", "signalTransitions", "factorExposure", "liquidityConstraints", "insights", "activeInvestmentOpinions", "executionPlans", "relations", "evidence", "beliefs", "opinions"],
        "reasoningCardCount": len(graph.reasoning_cards),
        "reasoningCardIds": [item.get("id") for item in graph.reasoning_cards],
        "graphInputs": {
            "boundedContextCount": len(BOUNDED_CONTEXTS),
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "evidenceCount": len(graph.evidence),
            "beliefCount": len(graph.beliefs),
            "opinionCount": len(graph.opinions),
            "pipelineCount": pipeline_count,
            "insightCount": insight_count,
            "activeOpinionCount": active_opinion_count,
            "executionPlanCount": execution_plan_count,
        },
        "outputSchema": {
            "portfolioView": "string",
            "relationThesis": "string",
            "companyOpinions": ["symbol", "action", "thesis", "relationInfluences", "executionPlan", "contradictions", "nextChecks"],
            "activeInvestmentOpinions": ["symbol", "action", "conviction", "evidence", "counterEvidence", "executionPlan", "invalidationCondition"],
            "executionPlans": ["symbol", "primaryAction", "blockedActions", "riskSignals", "supportSignals", "counterSignals", "strengthenConditions", "weakenConditions", "nextChecks"],
            "insightDispatch": ["subject", "insightType", "novelty", "confidence", "dispatchDecision"],
            "missingDataImpact": ["string"],
        },
        "guardrails": [
            "제공된 TBox, ABox, reasoning card, 관계 행만 사용합니다.",
            "보유 종목 HOLDS와 관심 종목 WATCHES를 다른 판단 단계로 설명합니다.",
            "기존 점수 모델은 보조 근거로만 사용합니다.",
            "알림 타입 이름보다 온톨로지 인사이트, 신규성, 쿨다운, 억제 정책을 우선합니다.",
            "BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견을 고르되 자동 주문 지시로 표현하지 않습니다.",
            "뉴스·공시·SEC/OpenDART 출처와 반대 근거, 무효화 조건을 함께 제시합니다.",
            "이전 상태와 현재 상태의 SignalTransition을 읽고 새 변화인지 반복 상태인지 구분합니다.",
            "팩터/상관/유동성/슬리피지 제약이 있으면 투자 의견과 실행 계획을 분리해 설명합니다.",
        ],
    }


def portfolio_worldview(
    graph: PortfolioOntology,
    portfolio: PortfolioSummary,
    external_signals: Dict[str, object],
) -> Dict[str, object]:
    risk_count = len([item for item in graph.beliefs if item.polarity == "risk"])
    support_count = len([item for item in graph.beliefs if item.polarity == "support"])
    contradictions = sum(len(item.contradictions) for item in graph.opinions)
    high_pressure = [item.symbol for item in graph.opinions if item.ontology_pressure >= 55]
    relation_influence_count = sum(len(item.relation_influences or []) for item in graph.opinions)
    pipeline_nodes = [item for item in graph.entities if item.kind == "data-pipeline"]
    insight_nodes = [item for item in graph.entities if item.kind == "insight"]
    dispatch_nodes = [item for item in graph.entities if item.kind == "notification-dispatch"]
    bounded_context_counts: Dict[str, int] = {}
    for item in graph.entities:
        context = str((item.properties or {}).get("boundedContext") or "")
        if context and (item.properties or {}).get("ontologyBox") != "TBox":
            bounded_context_counts[context] = bounded_context_counts.get(context, 0) + 1
    top_sector = portfolio.sectors[0] if portfolio.sectors else {}
    return {
        "model": "ontology-first",
        "ontologyBoxes": {
            "tbox": ontology_tbox(),
            "abox": ontology_abox(graph),
        },
        "boundedContexts": bounded_contexts_payload(),
        "aboxBoundedContextCounts": bounded_context_counts,
        "legacyModelRole": "supporting-evidence",
        "dominantSector": top_sector.get("sector") or "",
        "dominantSectorRatio": number(top_sector.get("ratio")) if top_sector else 0.0,
        "cash": number(portfolio.cash),
        "riskBeliefCount": risk_count,
        "supportBeliefCount": support_count,
        "contradictionCount": contradictions,
        "relationInfluenceCount": relation_influence_count,
        "operationalOntology": {
            "collectionPipelineCount": len(pipeline_nodes),
            "insightCount": len(insight_nodes),
            "dispatchMode": str((dispatch_nodes[0].properties or {}).get("mode") or "insight-driven-only") if dispatch_nodes else "",
            "pipelines": [
                {
                    "key": str((item.properties or {}).get("key") or item.entity_id),
                    "targetMinutes": number((item.properties or {}).get("targetMinutes")),
                    "configuredMinutes": number((item.properties or {}).get("configuredMinutes")),
                }
                for item in pipeline_nodes
            ],
        },
        "highPressureSymbols": high_pressure,
        "externalSignalKeys": sorted(str(key) for key in external_signals.keys()) if isinstance(external_signals, dict) else [],
    }


def prompt_relation_priority(item: OntologyRelation) -> float:
    if (item.properties or {}).get("ontologyBox") == "TBox":
        return -100.0
    risk, support = relation_influence_score(item)
    properties = item.properties or {}
    priority = max(risk, support) + number(item.weight) * 10
    if item.relation_type in {
        "CHANGED_FROM",
        "CONFIRMED_OVER",
        "FAILED_AFTER",
        "MATERIAL_TO",
        "LIMITED_BY_LIQUIDITY",
        "HAS_EXIT_CAPACITY",
        "HAS_SLIPPAGE_RISK",
        "BREAKS_LEVEL",
        "RECLAIMS_LEVEL",
        "RETESTS_LEVEL",
    }:
        priority += 18
    if properties.get("polarity") in {"risk", "support"}:
        priority += 8
    return priority


def prompt_evidence_priority(item: OntologyEvidence) -> float:
    payload = item.value or {}
    impact = number(payload.get("impactScore") or payload.get("impact_score"))
    polarity = str(payload.get("polarity") or "")
    priority = number(item.confidence) * 20 + impact
    if polarity in {"risk", "support"}:
        priority += 10
    if item.kind in {"disclosure", "filing", "news", "market-move", "financial-fact"}:
        priority += 8
    return priority


def prompt_belief_priority(item: OntologyBelief) -> float:
    priority = number(item.confidence) * 20
    if item.polarity == "risk":
        priority += 8
    if item.polarity == "support":
        priority += 4
    return priority


def prompt_payload(graph: PortfolioOntology) -> Dict[str, object]:
    relations = sorted(graph.relations, key=prompt_relation_priority, reverse=True)
    evidence = sorted(graph.evidence, key=prompt_evidence_priority, reverse=True)
    beliefs = sorted(graph.beliefs, key=prompt_belief_priority, reverse=True)
    return {
        "tbox": ontology_tbox(),
        "boundedContexts": bounded_contexts_payload(),
        "abox": ontology_abox(graph),
        "worldview": graph.worldview,
        "aiInferencePacket": build_ai_inference_packet(graph),
        "reasoningCards": list(graph.reasoning_cards),
        "insights": [item.to_dict() for item in graph.entities if item.kind == "insight"],
        "activeInvestmentOpinions": [
            dict((item.properties or {}).get("activeInvestmentOpinion") or {})
            for item in graph.entities
            if item.kind == "active-opinion"
        ],
        "executionPlans": [
            dict((item.properties or {}).get("executionPlan") or {})
            for item in graph.entities
            if item.kind == "execution-plan"
        ],
        "operationalOntology": dict((graph.worldview or {}).get("operationalOntology") or {}),
        "opinions": [item.to_dict() for item in graph.opinions],
        "relations": [item.to_dict() for item in relations[:120]],
        "evidence": [item.to_dict() for item in evidence[:120]],
        "beliefs": [item.to_dict() for item in beliefs[:100]],
    }


def build_investment_opinion_prompt(graph: PortfolioOntology) -> str:
    payload = json.dumps(prompt_payload(graph), ensure_ascii=False, sort_keys=True)
    return "\n".join([
        "너는 투자전략 관계 분석 데이터를 읽는 AI 투자 의견 리뷰어다.",
        "규칙 구조는 투자 핵심, 관측 데이터, 전략 가설, 리스크, 추론 인사이트, 운영/알림 바운디드 컨텍스트로 나뉜 세계관이다.",
        "현재 데이터는 계좌의 실제 보유, 근거, 판단 근거, 운영 정책, 의견 기록이다.",
        "제공된 근거 안에서 BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견을 반드시 고르되 자동 주문 지시로 표현하지 마라.",
        "기존 점수 모델은 보조 데이터로만 사용하고, 최종 판단은 관계 규칙과 근거 충돌을 기준으로 설명해라.",
        "뉴스, 공시, SEC/OpenDART 근거와 출처 URL을 적극적으로 반영하고, 반대 근거와 무효화 조건을 함께 제시해라.",
        "새 관측값이나 관계가 추가되면 어떤 투자 가설, 리스크, 인사이트, 알림 정책에 영향을 주는지 먼저 추론해라.",
        "알림은 알림 타입별 주기가 아니라 온톨로지 인사이트의 신규성, 신뢰도, 쿨다운, 억제 정책으로 설명해라.",
        "계좌번호, API 키, 토큰, 개인 식별정보를 추정하거나 요청하지 마라.",
        "응답 섹션은 반드시 투자 관점, 핵심 관계, 보유 이유와 반대 신호, 종목별 의견, 다음 검증 순서로 작성해라.",
        "",
        "프롬프트 버전: " + ONTOLOGY_PROMPT_VERSION,
        "관계 분석 데이터 JSON:",
        payload,
    ])
