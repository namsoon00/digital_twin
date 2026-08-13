from typing import Dict, Optional, Set

from .market_data import number, optional_investor_net_volume
from .portfolio import Position, expects_kr_microstructure_signals


INVESTOR_PARTY_FIELDS = {
    "foreign": {
        "net": "foreignNetVolume",
        "amount": "foreignNetAmount",
        "buy": "foreignBuyVolume",
        "sell": "foreignSellVolume",
        "net_attr": "foreign_net_volume",
        "amount_attr": "foreign_net_amount",
        "buy_attr": "foreign_buy_volume",
        "sell_attr": "foreign_sell_volume",
    },
    "institution": {
        "net": "institutionNetVolume",
        "amount": "institutionNetAmount",
        "buy": "institutionBuyVolume",
        "sell": "institutionSellVolume",
        "net_attr": "institution_net_volume",
        "amount_attr": "institution_net_amount",
        "buy_attr": "institution_buy_volume",
        "sell_attr": "institution_sell_volume",
    },
    "individual": {
        "net": "individualNetVolume",
        "amount": "individualNetAmount",
        "buy": "individualBuyVolume",
        "sell": "individualSellVolume",
        "net_attr": "individual_net_volume",
        "amount_attr": "individual_net_amount",
        "buy_attr": "individual_buy_volume",
        "sell_attr": "individual_sell_volume",
    },
}


def investor_flow_coverage(position: Position) -> Dict[str, object]:
    coverage = position.market_signal_coverage if isinstance(position.market_signal_coverage, dict) else {}
    investor = coverage.get("investor") if isinstance(coverage.get("investor"), dict) else {}
    return dict(investor or {})


def investor_flow_observed_fields(position: Position) -> Set[str]:
    """Return fields actually observed by the provider, including real zeroes."""

    investor = investor_flow_coverage(position)
    if investor:
        return {
            str(field)
            for field in (investor.get("observedFields") or investor.get("fields") or [])
            if str(field or "").strip()
        }

    # Compatibility for legacy snapshots that predate per-field coverage. A
    # non-zero value proves observation; a zero cannot be distinguished from
    # the old numeric default and therefore remains unobserved.
    observed: Set[str] = set()
    for fields in INVESTOR_PARTY_FIELDS.values():
        for public_key, attr_key in [
            (fields["net"], fields["net_attr"]),
            (fields["amount"], fields["amount_attr"]),
            (fields["buy"], fields["buy_attr"]),
            (fields["sell"], fields["sell_attr"]),
        ]:
            if number(getattr(position, attr_key, 0)):
                observed.add(public_key)
    return observed


def investor_flow_party_status(position: Position) -> Dict[str, str]:
    investor = investor_flow_coverage(position)
    explicit = investor.get("participantStatus") if isinstance(investor.get("participantStatus"), dict) else {}
    observed = investor_flow_observed_fields(position)
    stage_status = str(investor.get("status") or "").strip().lower()
    result: Dict[str, str] = {}
    for party, fields in INVESTOR_PARTY_FIELDS.items():
        if explicit.get(party):
            result[party] = str(explicit.get(party))
        elif fields["net"] in observed or fields["buy"] in observed or fields["sell"] in observed:
            result[party] = "available"
        elif not expects_kr_microstructure_signals(position.market, position.currency, position.symbol):
            result[party] = "unsupported"
        elif stage_status in {"stale", "stale-at-dispatch"}:
            result[party] = "stale"
        elif stage_status in {"missing", "empty", "unavailable", "unknown"}:
            result[party] = stage_status
        else:
            result[party] = "missing"
    return result


def investor_flow_party_value(position: Position, party: str) -> Optional[float]:
    fields = INVESTOR_PARTY_FIELDS.get(str(party or ""))
    if not fields:
        return None
    observed = investor_flow_observed_fields(position)
    if not ({fields["net"], fields["buy"], fields["sell"]} & observed):
        return None
    return optional_investor_net_volume(
        getattr(position, fields["net_attr"], None) if fields["net"] in observed else None,
        getattr(position, fields["buy_attr"], None) if fields["buy"] in observed else None,
        getattr(position, fields["sell_attr"], None) if fields["sell"] in observed else None,
    )


def investor_flow_contract(position: Position) -> Dict[str, object]:
    observed = investor_flow_observed_fields(position)
    statuses = investor_flow_party_status(position)
    values = {
        party: investor_flow_party_value(position, party)
        for party in INVESTOR_PARTY_FIELDS
    }
    smart_money_available = values["foreign"] is not None and values["institution"] is not None
    return {
        "observedFields": sorted(observed),
        "participantStatus": statuses,
        "values": values,
        "smartMoneyAvailable": smart_money_available,
        "complete": all(value is not None for value in values.values()),
    }


def investor_flow_values_reliable(position: Position) -> bool:
    if not expects_kr_microstructure_signals(position.market, position.currency, position.symbol):
        return False
    investor = investor_flow_coverage(position)
    if not investor:
        return bool(investor_flow_observed_fields(position))
    status = str(investor.get("status") or "").strip()
    latency_status = str(investor.get("latencyStatus") or "").strip()
    if status in {"stale", "unknown", "unavailable", "missing", "empty", "error"}:
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


def investor_flow_measurement(position: Position) -> Dict[str, object]:
    investor = investor_flow_coverage(position)
    contract = investor_flow_contract(position)
    measurement_type = str(investor.get("measurementType") or "").strip() or "unspecified"
    return {
        "investorFlowMeasurementType": measurement_type,
        "investorFlowIsEstimate": bool(investor.get("isEstimate")) if "isEstimate" in investor else measurement_type == "intraday-estimate",
        "investorFlowSourceAsOf": str(investor.get("sourceAsOf") or ""),
        "investorFlowProviderUpdateSlot": str(investor.get("providerUpdateSlot") or ""),
        "investorFlowFreshnessStatus": str(investor.get("freshnessStatus") or ""),
        "investorFlowObservedFields": list(contract["observedFields"]),
        "investorFlowParticipantStatus": dict(contract["participantStatus"]),
        "investorFlowSmartMoneyAvailable": bool(contract["smartMoneyAvailable"]),
        "investorFlowComplete": bool(contract["complete"]),
    }


def investor_flow_observation(position: Position) -> Dict[str, object]:
    """Return raw investor-flow facts without assigning investment meaning.

    Whether joint buying or selling supports, blocks, or changes an action is
    authored by a TypeDB RuleBox rule.  Keeping this helper observational
    prevents a future caller from accidentally recreating a Python-side
    sentiment or risk classifier.
    """
    measurement = investor_flow_measurement(position)
    contract = investor_flow_contract(position)
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
            "investorFlowBase": 0.0,
            "jointSmartMoneyInflow": False,
            "jointSmartMoneyOutflow": False,
            **measurement,
        }

    values = contract["values"]
    foreign_volume = values["foreign"]
    institution_volume = values["institution"]
    individual_volume = values["individual"]
    directional_values = [value for value in values.values() if value is not None]
    base = sum(abs(value) for value in directional_values)
    smart_money_available = bool(contract["smartMoneyAvailable"])
    smart_money = (foreign_volume + institution_volume) if smart_money_available else None
    joint_inflow = bool(smart_money_available and foreign_volume > 0 and institution_volume > 0)
    joint_outflow = bool(smart_money_available and foreign_volume < 0 and institution_volume < 0)

    is_estimate = bool(measurement.get("investorFlowIsEstimate"))
    result = {
        "available": bool(contract["observedFields"]),
        "field": "investorFlow",
        "value": "raw",
        "polarity": "context",
        "evidenceRole": "context",
        "reviewLevel": "observe",
        "dataState": "estimated" if is_estimate and contract["observedFields"] else "sufficient" if contract["complete"] else "partial" if contract["observedFields"] else "insufficient",
        "sentimentLabel": "투자자별 장중 추정 수급 관측" if is_estimate else "투자자별 확정 수급 관측",
        "tboxClass": "InvestorFlowObservation",
        "tboxClasses": ["Observation", "FlowObservation", "InvestorFlowObservation"],
        "investorFlowBase": round(base, 2),
        "investorFlowDirectional": bool(base),
        "jointSmartMoneyInflow": joint_inflow,
        "jointSmartMoneyOutflow": joint_outflow,
        **measurement,
    }
    for party, fields in INVESTOR_PARTY_FIELDS.items():
        value = values[party]
        if value is not None:
            result[fields["net"]] = round(value, 2)
        if fields["amount"] in contract["observedFields"]:
            result[fields["amount"]] = round(number(getattr(position, fields["amount_attr"], 0)), 2)
    if smart_money is not None:
        result["smartMoneyNetVolume"] = round(smart_money, 2)
    return result


def investor_flow_psychology(position: Position) -> Dict[str, object]:
    """Compatibility name for the raw observation projection.

    The former implementation classified market psychology in Python.  The
    name remains temporarily for callers outside this repository, while the
    returned payload is deliberately non-directional.
    """
    return investor_flow_observation(position)
