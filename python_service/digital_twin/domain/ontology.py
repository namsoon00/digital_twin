import json
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

from .market_data import clamp, number
from .portfolio import PortfolioSummary, Position


ONTOLOGY_PROMPT_VERSION = "ontology-investment-v2-tbox-abox"

TBOX_CLASSES = [
    "Portfolio",
    "Stock",
    "Sector",
    "Market",
    "Currency",
    "Cash",
    "Risk",
    "Opportunity",
    "Contradiction",
    "Evidence",
    "Belief",
    "Opinion",
    "AIReview",
    "LegacyScoreModel",
]

TBOX_RELATION_TYPES = [
    "HOLDS",
    "HOLDS_CASH",
    "BELONGS_TO",
    "TRADED_IN",
    "DENOMINATED_IN",
    "EXPOSED_TO",
    "SUPPORTED_BY",
    "CONTRADICTS",
    "USES_EVIDENCE_FROM",
    "REQUESTS_OPINION_FROM",
    "HAS_EVIDENCE",
    "HAS_BELIEF",
    "HAS_OPINION",
]

TBOX_REASONING_RULES = [
    "sector concentration and position weight create exposure risk beliefs",
    "trend and smart-money flow create support or risk beliefs",
    "legacy score disagreement with trend or flow creates contradiction beliefs",
    "data quality controls AI opinion confidence",
    "legacy score model remains supporting evidence, not the primary decision model",
]


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
        "description": "Investment ontology schema: classes, relation types, and reasoning rules.",
        "classes": list(TBOX_CLASSES),
        "relationTypes": list(TBOX_RELATION_TYPES),
        "reasoningRules": list(TBOX_REASONING_RULES),
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
    for name in TBOX_CLASSES:
        entities.append(OntologyEntity(entity_id("tbox-class", name), name, "tbox-class", {
            "ontologyBox": "TBox",
            "box": "TBox",
        }))
    for name in TBOX_RELATION_TYPES:
        entities.append(OntologyEntity(entity_id("tbox-relation", name), name, "tbox-relation", {
            "ontologyBox": "TBox",
            "box": "TBox",
        }))
    return entities


def tbox_relations() -> List[OntologyRelation]:
    relations: List[OntologyRelation] = []
    tbox_id = entity_id("ontology-box", "TBox")
    abox_id = entity_id("ontology-box", "ABox")
    for name in TBOX_CLASSES:
        relations.append(OntologyRelation(tbox_id, entity_id("tbox-class", name), "DEFINES_CLASS", properties={"ontologyBox": "TBox"}))
    for name in TBOX_RELATION_TYPES:
        relations.append(OntologyRelation(tbox_id, entity_id("tbox-relation", name), "DEFINES_RELATION", properties={"ontologyBox": "TBox"}))
    relations.append(OntologyRelation(tbox_id, abox_id, "CONSTRAINS_ASSERTIONS", properties={"ontologyBox": "TBox"}))
    return relations


def abox_properties(properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = dict(properties or {})
    payload.setdefault("ontologyBox", "ABox")
    payload.setdefault("box", "ABox")
    return payload


def symbol_key(position: Position) -> str:
    return str(position.symbol or position.name or "").upper().strip()


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
        + number(position.ma60_slope) * 3,
        -35.0,
        35.0,
    )


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


def build_portfolio_ontology(
    positions: Iterable[Position],
    portfolio: PortfolioSummary,
    legacy_by_symbol: Dict[str, Dict[str, object]] = None,
    external_signals: Dict[str, object] = None,
    portfolio_id: str = "portfolio",
) -> PortfolioOntology:
    legacy_by_symbol = legacy_by_symbol or {}
    external_signals = external_signals or {}
    clean_positions = [item for item in positions if not item.is_cash() and number(item.market_value) > 0]
    graph = PortfolioOntology(portfolio_id=portfolio_id)
    graph.entities.extend(tbox_entities())
    graph.relations.extend(tbox_relations())
    graph.entities.append(OntologyEntity(entity_id("portfolio", portfolio_id), "투자 포트폴리오", "portfolio", abox_properties({
        "total": number(portfolio.total),
        "invested": number(portfolio.invested),
        "cash": number(portfolio.cash),
        "concentration": number(portfolio.concentration),
    })))
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
            entity_id("portfolio", portfolio_id),
            entity_id("asset", "cash"),
            "HOLDS_CASH",
            weight=1.0,
            properties=abox_properties(),
        ))
    sector_weights: Dict[str, float] = {}
    for sector in portfolio.sectors:
        label = str(sector.get("sector") or "기타")
        sector_weights[label] = number(sector.get("ratio"))
        graph.entities.append(OntologyEntity(entity_id("sector", label), label, "sector", abox_properties({**dict(sector), "tboxClass": "Sector"})))
        graph.relations.append(OntologyRelation(
            entity_id("portfolio", portfolio_id),
            entity_id("sector", label),
            "EXPOSED_TO",
            weight=round(number(sector.get("ratio")) / 100, 4),
            properties=abox_properties({"basis": "sector-weight"}),
        ))
    for position in clean_positions:
        symbol = symbol_key(position)
        stock_id = entity_id("stock", symbol)
        graph.entities.append(OntologyEntity(stock_id, position.name or symbol, "stock", abox_properties({
            "symbol": symbol,
            "market": position.market,
            "currency": position.currency,
            "sector": position.sector,
            "marketValue": number(position.market_value),
            "profitLossRate": number(position.profit_loss_rate),
            "tboxClass": "Stock",
        })))
        for kind, label in [("market", position.market or "unknown"), ("currency", position.currency or "unknown")]:
            tbox_class = "Market" if kind == "market" else "Currency"
            graph.entities.append(OntologyEntity(entity_id(kind, label), label, kind, abox_properties({"tboxClass": tbox_class})))
        graph.relations.extend([
            OntologyRelation(entity_id("portfolio", portfolio_id), stock_id, "HOLDS", weight=round(position_weight(position, portfolio) / 100, 4), properties=abox_properties()),
            OntologyRelation(stock_id, entity_id("sector", position.sector or "기타"), "BELONGS_TO", weight=1.0, properties=abox_properties()),
            OntologyRelation(stock_id, entity_id("market", position.market or "unknown"), "TRADED_IN", weight=1.0, properties=abox_properties()),
            OntologyRelation(stock_id, entity_id("currency", position.currency or "unknown"), "DENOMINATED_IN", weight=1.0, properties=abox_properties()),
            OntologyRelation(stock_id, entity_id("concept", "legacy-score-model"), "USES_EVIDENCE_FROM", weight=0.55, properties=abox_properties()),
            OntologyRelation(stock_id, entity_id("concept", "ai-investment-review"), "REQUESTS_OPINION_FROM", weight=1.0, properties=abox_properties()),
        ])
        legacy = legacy_by_symbol.get(symbol) or legacy_by_symbol.get(position.symbol) or {}
        opinion = build_position_opinion(position, portfolio, legacy)
        graph.opinions.append(opinion)
        weight = position_weight(position, portfolio)
        trend = trend_score(position)
        flow = smart_money_score(position)
        quality = data_quality_score(position)
        evidence_rows = [
            ("legacy-model", "legacyModel", "기존 점수 모델을 보조 근거로 사용", opinion.legacy_model, 0.75),
            ("portfolio-exposure", "portfolio", "포트폴리오/섹터 노출 관계", {
                "positionWeight": round(weight, 2),
                "sectorWeight": round(sector_weights.get(position.sector, 0.0), 2),
            }, 0.85),
            ("trend", "market-data", "이동평균과 가격 추세 관계", {"trendScore": round(trend, 2)}, 0.65),
            ("flow", "market-data", "외국인·기관 수급 관계", {"smartMoneyScore": round(flow, 2)}, 0.6),
            ("data-quality", "data-quality", "AI 판단에 투입할 데이터 완성도", {"qualityScore": round(quality, 2)}, 0.7),
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
            graph.entities.append(OntologyEntity(risk_id, risk, "risk", abox_properties({"tboxClass": "Risk"})))
            graph.relations.append(OntologyRelation(stock_id, risk_id, "EXPOSED_TO", weight=0.75, evidence_ids=opinion.evidence_ids, properties=abox_properties()))
        if opinion.opportunities:
            opportunity_id = entity_id("opportunity", opinion.opportunities[0])
            graph.entities.append(OntologyEntity(opportunity_id, opinion.opportunities[0], "opportunity", abox_properties({"tboxClass": "Opportunity"})))
            graph.relations.append(OntologyRelation(stock_id, opportunity_id, "SUPPORTED_BY", weight=0.65, evidence_ids=opinion.evidence_ids, properties=abox_properties()))
        if opinion.contradictions:
            contradiction_id = entity_id("contradiction", opinion.contradictions[0])
            graph.entities.append(OntologyEntity(contradiction_id, opinion.contradictions[0], "contradiction", abox_properties({"tboxClass": "Contradiction"})))
            graph.relations.append(OntologyRelation(stock_id, contradiction_id, "CONTRADICTS", weight=0.8, evidence_ids=opinion.evidence_ids, properties=abox_properties()))
    graph.entities = dedupe_entities(graph.entities)
    graph.relations = dedupe_relations(graph.relations)
    graph.evidence = dedupe_evidence(graph.evidence)
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


def portfolio_worldview(
    graph: PortfolioOntology,
    portfolio: PortfolioSummary,
    external_signals: Dict[str, object],
) -> Dict[str, object]:
    risk_count = len([item for item in graph.beliefs if item.polarity == "risk"])
    support_count = len([item for item in graph.beliefs if item.polarity == "support"])
    contradictions = sum(len(item.contradictions) for item in graph.opinions)
    high_pressure = [item.symbol for item in graph.opinions if item.ontology_pressure >= 55]
    top_sector = portfolio.sectors[0] if portfolio.sectors else {}
    return {
        "model": "ontology-first",
        "ontologyBoxes": {
            "tbox": ontology_tbox(),
            "abox": ontology_abox(graph),
        },
        "legacyModelRole": "supporting-evidence",
        "dominantSector": top_sector.get("sector") or "",
        "dominantSectorRatio": number(top_sector.get("ratio")) if top_sector else 0.0,
        "cash": number(portfolio.cash),
        "riskBeliefCount": risk_count,
        "supportBeliefCount": support_count,
        "contradictionCount": contradictions,
        "highPressureSymbols": high_pressure,
        "externalSignalKeys": sorted(str(key) for key in external_signals.keys()) if isinstance(external_signals, dict) else [],
    }


def prompt_payload(graph: PortfolioOntology) -> Dict[str, object]:
    return {
        "tbox": ontology_tbox(),
        "abox": ontology_abox(graph),
        "worldview": graph.worldview,
        "opinions": [item.to_dict() for item in graph.opinions],
        "relations": [item.to_dict() for item in graph.relations[:80]],
        "evidence": [item.to_dict() for item in graph.evidence[:80]],
        "beliefs": [item.to_dict() for item in graph.beliefs[:80]],
    }


def build_investment_opinion_prompt(graph: PortfolioOntology) -> str:
    payload = json.dumps(prompt_payload(graph), ensure_ascii=False, sort_keys=True)
    return "\n".join([
        "너는 투자전략 관계 분석 데이터를 읽는 AI 투자 의견 리뷰어다.",
        "규칙 구조는 투자 기준의 분류, 관계 타입, 판단 규칙이고, 현재 데이터는 계좌의 실제 보유, 근거, 판단 근거, 의견 기록이다.",
        "매수/매도 명령을 확정하지 말고, 포트폴리오 투자 관점, 관계, 반대 신호, 데이터 공백을 분석해라.",
        "기존 점수 모델은 보조 데이터로만 사용하고, 최종 판단은 관계 규칙과 근거 충돌을 기준으로 설명해라.",
        "계좌번호, API 키, 토큰, 개인 식별정보를 추정하거나 요청하지 마라.",
        "응답 섹션은 반드시 투자 관점, 핵심 관계, 보유 이유와 반대 신호, 종목별 의견, 다음 검증 순서로 작성해라.",
        "",
        "프롬프트 버전: " + ONTOLOGY_PROMPT_VERSION,
        "관계 분석 데이터 JSON:",
        payload,
    ])
