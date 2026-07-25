from typing import Dict

from .market_data import investor_net_volume, number
from .portfolio import Position, expects_kr_microstructure_signals


def investor_flow_values_reliable(position: Position) -> bool:
    if not expects_kr_microstructure_signals(position.market, position.currency, position.symbol):
        return False
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


def investor_flow_observation(position: Position) -> Dict[str, object]:
    """Return raw investor-flow facts without assigning investment meaning.

    Whether joint buying or selling supports, blocks, or changes an action is
    authored by a TypeDB RuleBox rule.  Keeping this helper observational
    prevents a future caller from accidentally recreating a Python-side
    sentiment or risk classifier.
    """
    if not investor_flow_values_reliable(position):
        return {
            "available": False,
            "reason": "investor-flow-unreliable",
            "field": "investorFlowUnavailable",
            "polarity": "context",
            "evidenceRole": "blocking",
            "reviewLevel": "blocked",
            "dataState": "unavailable",
            "sentimentLabel": "투자자별 수급 신뢰도 낮음",
            "tboxClass": "InvestorFlowObservation",
            "tboxClasses": ["Observation", "FlowObservation", "InvestorFlowObservation"],
            "foreignNetVolume": 0.0,
            "institutionNetVolume": 0.0,
            "individualNetVolume": 0.0,
            "foreignNetAmount": 0.0,
            "institutionNetAmount": 0.0,
            "individualNetAmount": 0.0,
            "smartMoneyNetVolume": 0.0,
            "investorFlowBase": 0.0,
            "jointSmartMoneyInflow": False,
            "jointSmartMoneyOutflow": False,
        }

    foreign_volume = investor_net_volume(position.foreign_net_volume, position.foreign_buy_volume, position.foreign_sell_volume)
    institution_volume = investor_net_volume(position.institution_net_volume, position.institution_buy_volume, position.institution_sell_volume)
    individual_volume = investor_net_volume(position.individual_net_volume, position.individual_buy_volume, position.individual_sell_volume)
    foreign = foreign_volume or number(position.foreign_net_amount)
    institution = institution_volume or number(position.institution_net_amount)
    individual = individual_volume or number(position.individual_net_amount)
    base = abs(foreign) + abs(institution) + abs(individual)
    smart_money = foreign + institution
    joint_inflow = foreign > 0 and institution > 0
    joint_outflow = foreign < 0 and institution < 0

    return {
        "available": bool(base),
        "field": "investorFlow",
        "value": "raw",
        "polarity": "context",
        "evidenceRole": "context",
        "reviewLevel": "observe",
        "dataState": "sufficient" if base else "insufficient",
        "sentimentLabel": "투자자별 원시 수급 관측",
        "tboxClass": "InvestorFlowObservation",
        "tboxClasses": ["Observation", "FlowObservation", "InvestorFlowObservation"],
        "foreignNetVolume": round(foreign_volume, 2),
        "institutionNetVolume": round(institution_volume, 2),
        "individualNetVolume": round(individual_volume, 2),
        "foreignNetAmount": round(number(position.foreign_net_amount), 2),
        "institutionNetAmount": round(number(position.institution_net_amount), 2),
        "individualNetAmount": round(number(position.individual_net_amount), 2),
        "smartMoneyNetVolume": round(smart_money, 2),
        "investorFlowBase": round(base, 2),
        "jointSmartMoneyInflow": joint_inflow,
        "jointSmartMoneyOutflow": joint_outflow,
    }


def investor_flow_psychology(position: Position) -> Dict[str, object]:
    """Compatibility name for the raw observation projection.

    The former implementation classified market psychology in Python.  The
    name remains temporarily for callers outside this repository, while the
    returned payload is deliberately non-directional.
    """
    return investor_flow_observation(position)
