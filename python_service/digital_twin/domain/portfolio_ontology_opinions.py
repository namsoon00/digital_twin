from typing import Dict, List

from .market_data import clamp, number
from .ontology_contracts import OntologyOpinion
from .portfolio import PortfolioSummary, Position
from .portfolio_ontology_market_concepts import (
    data_quality_score,
    smart_money_score,
    symbol_key,
    trend_score,
)


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

def sector_ratio(portfolio: PortfolioSummary, sector: str) -> float:
    for item in portfolio.sectors:
        if item.get("sector") == sector:
            return number(item.get("ratio"))
    return 0.0

def position_weight(position: Position, portfolio: PortfolioSummary) -> float:
    base = number(portfolio.total) or number(portfolio.invested)
    return (number(position.market_value) / base) * 100 if base else 0.0

def evidence_id(symbol: str, kind: str) -> str:
    return "evidence:" + str(symbol or "portfolio").upper() + ":" + kind

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
    foreign_net = number(position.foreign_net_volume) or number(position.foreign_buy_volume) - number(position.foreign_sell_volume)
    institution_net = number(position.institution_net_volume) or number(position.institution_buy_volume) - number(position.institution_sell_volume)
    joint_inflow = foreign_net > 0 and institution_net > 0
    quality = data_quality_score(position)
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
    if pnl < 0 and joint_inflow:
        supporting.append("손실 구간에서 외국인·기관 동반 순매수가 매도 강도를 낮추는 반대 근거")
        opportunities.append("추가매수는 가격·거래 회복 확인 후 조건부 검토")
    if quality < 60:
        contradictions.append("핵심 데이터가 부족해 AI 판단 신뢰도가 낮음")

    risk_score = 18.0
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
            "role": "not-used-for-scoring",
            "reason": "최종 점수는 온톨로지 관계 규칙과 관계 사실만 사용합니다.",
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
