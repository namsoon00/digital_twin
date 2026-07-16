from typing import Dict

from .market_data import clamp, investor_net_volume, number
from .portfolio import Position


def investor_flow_values_reliable(position: Position) -> bool:
    coverage = position.market_signal_coverage if isinstance(position.market_signal_coverage, dict) else {}
    investor = coverage.get("investor") if isinstance(coverage.get("investor"), dict) else {}
    if not investor:
        return True
    status = str(investor.get("status") or "").strip()
    latency_status = str(investor.get("latencyStatus") or "").strip()
    if status in {"stale", "unknown", "unavailable", "missing", "empty"}:
        return False
    if status == "available" and investor.get("judgementEvidenceUsable") is not False:
        return True
    if investor.get("aiUsableAsStrongEvidence") is False:
        return False
    if number(investor.get("unchangedCount")):
        return False
    if investor.get("realTime") is False or latency_status or str(investor.get("cadence") or "") == "stale-repeat":
        return False
    return True


def investor_flow_psychology(position: Position) -> Dict[str, object]:
    if not investor_flow_values_reliable(position):
        return {
            "available": False,
            "reason": "investor-flow-unreliable",
            "field": "investorFlowUnavailable",
            "polarity": "context",
            "sentimentLabel": "투자자별 수급 신뢰도 낮음",
        }

    foreign_volume = investor_net_volume(position.foreign_net_volume, position.foreign_buy_volume, position.foreign_sell_volume)
    institution_volume = investor_net_volume(position.institution_net_volume, position.institution_buy_volume, position.institution_sell_volume)
    individual_volume = investor_net_volume(position.individual_net_volume, position.individual_buy_volume, position.individual_sell_volume)
    foreign = foreign_volume or number(position.foreign_net_amount)
    institution = institution_volume or number(position.institution_net_amount)
    individual = individual_volume or number(position.individual_net_amount)
    base = abs(foreign) + abs(institution) + abs(individual)
    smart_money = foreign + institution
    score = clamp((smart_money - individual * 0.35) / base * 100.0, -100.0, 100.0) if base else 0.0
    joint_inflow = foreign > 0 and institution > 0
    joint_outflow = foreign < 0 and institution < 0
    price_change = number(position.change_rate)
    pnl = number(position.profit_loss_rate)
    ma20_distance = number(position.ma20_distance)
    ma60_distance = number(position.ma60_distance)
    price_weak = price_change <= -1.0 or pnl < 0 or ma20_distance < 0 or ma60_distance < 0
    price_strong = price_change >= 1.0 and ma20_distance >= 0 and ma60_distance >= 0

    field = "mixedInvestorPsychology"
    polarity = "context"
    sentiment = "투자자별 수급 혼조"
    tbox_class = "InvestorFlowSentiment"
    tbox_classes = ["Observation", "FlowObservation", "InvestorFlowSentiment"]
    support_impact = 0.0
    risk_impact = 0.0
    weight = 0.62

    if joint_inflow and individual < 0:
        field = "smartMoneyDipAbsorption" if price_weak else "smartMoneyAccumulation"
        polarity = "support"
        sentiment = "외국인·기관이 사고 개인이 파는 큰 자금 매집"
        tbox_class = "SmartMoneyAccumulation"
        tbox_classes.extend(["SmartMoneyFlow", "SmartMoneyAccumulation"])
        support_impact = 8.5 if price_weak else 7.5
        weight = 0.88
    elif joint_inflow:
        field = "broadInflowConfirmation"
        polarity = "support"
        sentiment = "외국인·기관 동반 순매수"
        tbox_class = "SmartMoneyJointInflow"
        tbox_classes.extend(["SmartMoneyFlow", "SmartMoneyJointInflow"])
        support_impact = 7.0 if price_strong else 6.0
        weight = 0.82
    elif joint_outflow and individual > 0:
        field = "retailDipBuyingRisk"
        polarity = "risk"
        sentiment = "외국인·기관 매도 물량을 개인이 받아내는 흐름"
        tbox_class = "RetailDipBuyingRisk"
        tbox_classes.extend(["SmartMoneyFlow", "RetailFlowPsychology", "AveragingDownRisk"])
        risk_impact = 9.0 if price_weak else 7.0
        weight = 0.88
    elif joint_outflow:
        field = "broadOutflowRisk"
        polarity = "risk"
        sentiment = "외국인·기관 동반 순매도"
        tbox_class = "SmartMoneyJointOutflow"
        tbox_classes.extend(["SmartMoneyFlow", "SmartMoneyJointOutflow"])
        risk_impact = 8.0
        weight = 0.84
    elif smart_money > 0:
        field = "partialSmartMoneySupport"
        polarity = "support"
        sentiment = "외국인·기관 합산 순매수"
        tbox_class = "PartialSmartMoneySupport"
        tbox_classes.extend(["SmartMoneyFlow", "PartialSmartMoneySupport"])
        support_impact = 4.5
        weight = 0.70
    elif smart_money < 0:
        field = "partialSmartMoneyRisk"
        polarity = "risk"
        sentiment = "외국인·기관 합산 순매도"
        tbox_class = "PartialSmartMoneyRisk"
        tbox_classes.extend(["SmartMoneyFlow", "PartialSmartMoneyRisk"])
        risk_impact = 4.5
        weight = 0.70

    return {
        "available": bool(base),
        "field": field,
        "value": round(score, 2),
        "valueNumber": round(score, 2),
        "polarity": polarity,
        "sentimentLabel": sentiment,
        "tboxClass": tbox_class,
        "tboxClasses": tbox_classes,
        "foreignNetVolume": round(foreign_volume, 2),
        "institutionNetVolume": round(institution_volume, 2),
        "individualNetVolume": round(individual_volume, 2),
        "foreignNetAmount": round(number(position.foreign_net_amount), 2),
        "institutionNetAmount": round(number(position.institution_net_amount), 2),
        "individualNetAmount": round(number(position.individual_net_amount), 2),
        "smartMoneyNetVolume": round(smart_money, 2),
        "investorFlowBase": round(base, 2),
        "investorFlowScore": round(score, 2),
        "jointSmartMoneyInflow": joint_inflow,
        "jointSmartMoneyOutflow": joint_outflow,
        "smartMoneyDirection": "joint_inflow" if joint_inflow else "joint_outflow" if joint_outflow else "mixed",
        "priceContext": "weakness" if price_weak else "strength" if price_strong else "neutral",
        "supportImpact": support_impact,
        "riskImpact": risk_impact,
        "weight": weight,
    }
