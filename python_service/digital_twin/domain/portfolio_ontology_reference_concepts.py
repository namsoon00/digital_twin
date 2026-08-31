"""Bounded official security, benchmark, and corporate-action ABox facts."""

from __future__ import annotations

from typing import Dict, Mapping

from .market_data import number
from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation
from .portfolio import Position


MAX_CORPORATE_ACTIONS = 8


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _symbol(position: Position) -> str:
    return _text(position.symbol or position.name).upper()


def _relation_properties(label: str, **values) -> Dict[str, object]:
    return {
        "source": "data-go-kr-fsc",
        "polarity": "context",
        "evidenceRole": "context",
        "aiInfluenceLabel": label,
        **values,
    }


def add_official_security_reference_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    external_signals: Dict[str, object],
) -> None:
    symbol = _symbol(position)
    masters = external_signals.get("securityMaster") if isinstance(external_signals, dict) else {}
    master = masters.get(symbol) if isinstance(masters, dict) and isinstance(masters.get(symbol), dict) else {}
    if not master:
        return
    company_name = _text(master.get("legalName") or master.get("name") or position.name or symbol)
    company_id = add_entity(graph, "company", symbol, company_name, {
        "tboxClass": "Company",
        "symbol": symbol,
        "companyName": company_name,
        "corporateRegistrationNumber": _text(master.get("corporateRegistrationNumber")),
        "officialIsin": _text(master.get("isin")),
        "officialReferenceBaseDate": _text(master.get("baseDate")),
        "officialReferenceProvider": _text(master.get("provider")),
    })
    source_id = add_entity(graph, "data-source", "public-data-security-master", _text(master.get("provider") or "공공데이터포털"), {
        "tboxClass": "DataSource",
        "tboxClasses": ["DataSource", "Provenance"],
        "sourceUrl": _text(master.get("sourceUrl")),
        "sourceType": _text(master.get("sourceType") or "official-security-master"),
        "officialSource": True,
    })
    market_name = _text(master.get("market") or position.market or "KR")
    market_id = add_entity(graph, "market", market_name, market_name, {
        "tboxClass": "Market",
        "marketName": market_name,
        "country": "KR",
    })
    listing_id = add_entity(graph, "security-listing", symbol, company_name + " 공식 상장 상태", {
        "tboxClass": "SecurityListing",
        "tboxClasses": ["Observation", "FundamentalObservation", "SecurityListing"],
        "symbol": symbol,
        "isin": _text(master.get("isin")),
        "market": market_name,
        "baseDate": _text(master.get("baseDate")),
        "sourceAsOf": _text(master.get("sourceAsOf")),
        "sourceFetchedAt": _text(master.get("fetchedAt")),
        "listingState": "listed",
        "decisionEligibility": "identity-only",
        "officialSource": True,
    })
    props = _relation_properties(
        "공식 법인·증권·상장 식별",
        baseDate=_text(master.get("baseDate")),
        decisionEligibility="identity-only",
    )
    add_relation(graph, stock_id, company_id, "REPRESENTS_COMPANY", weight=1.0, properties=props)
    add_relation(graph, company_id, listing_id, "HAS_LISTING", weight=1.0, properties=props)
    add_relation(graph, stock_id, listing_id, "HAS_LISTING", weight=1.0, properties=props)
    add_relation(graph, listing_id, market_id, "LISTED_ON", weight=1.0, properties=props)
    add_relation(graph, listing_id, source_id, "HAS_PROVENANCE", weight=1.0, properties=props)


def _benchmark_key(position: Position, master: Mapping[str, object]) -> str:
    market = _text(master.get("market") or position.market).upper()
    if "KOSDAQ" in market or "코스닥" in market:
        return "KOSDAQ"
    if "KOSPI" in market or "코스피" in market or market in {"KR", "KOREA"}:
        return "KOSPI"
    return ""


def add_official_market_index_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    external_signals: Dict[str, object],
) -> None:
    indices = external_signals.get("marketIndices") if isinstance(external_signals, dict) else {}
    masters = external_signals.get("securityMaster") if isinstance(external_signals, dict) else {}
    master = masters.get(_symbol(position)) if isinstance(masters, dict) and isinstance(masters.get(_symbol(position)), dict) else {}
    index_key = _benchmark_key(position, master)
    item = indices.get(index_key) if index_key and isinstance(indices, dict) and isinstance(indices.get(index_key), dict) else {}
    if not item or number(item.get("close")) <= 0:
        return
    index_name = _text(item.get("indexName") or index_key)
    index_id = add_entity(graph, "market-index", index_key, index_name, {
        "tboxClass": "Index",
        "tboxClasses": ["Instrument", "Index", "MarketProxyIndex"],
        "indexKey": index_key,
        "indexName": index_name,
        "indexCategory": _text(item.get("indexCategory")),
        "country": "KR",
    })
    source_id = add_entity(graph, "data-source", "public-data-market-index", _text(item.get("provider") or "공공데이터포털"), {
        "tboxClass": "DataSource",
        "tboxClasses": ["DataSource", "Provenance"],
        "sourceUrl": _text(item.get("sourceUrl")),
        "sourceType": _text(item.get("sourceType")),
        "officialSource": True,
    })
    base_date = _text(item.get("baseDate"))
    price_id = add_entity(graph, "price-bar", index_key + ":official-daily:" + (base_date or "latest"), index_name + " 공식 일별 지수", {
        "tboxClass": "PriceBar",
        "tboxClasses": ["Observation", "PriceObservation", "PriceBar"],
        "indexKey": index_key,
        "baseDate": base_date,
        "sourceAsOf": _text(item.get("sourceAsOf")),
        "sourceFetchedAt": _text(item.get("fetchedAt")),
        "open": number(item.get("open")),
        "high": number(item.get("high")),
        "low": number(item.get("low")),
        "close": number(item.get("close")),
        "change": number(item.get("change")),
        "changeRate": number(item.get("changePercent")),
        "volume": number(item.get("volume")),
        "tradingValue": number(item.get("tradingValue")),
        "marketCap": number(item.get("marketCap")),
        "constituentCount": number(item.get("constituentCount")),
        "realTime": False,
        "decisionEligibility": "market-context",
        "evidenceRole": "context",
    })
    props = _relation_properties(
        "공식 시장 기준 지수",
        decisionEligibility="market-context",
        realTime=False,
        baseDate=base_date,
    )
    add_relation(graph, stock_id, index_id, "USES_MARKET_BENCHMARK", weight=1.0, properties=props)
    add_relation(graph, index_id, price_id, "HAS_OBSERVATION", weight=1.0, properties=props)
    add_relation(graph, price_id, source_id, "HAS_PROVENANCE", weight=1.0, properties=props)


def _event_date(event: Mapping[str, object]) -> str:
    return max(
        (
            _text(event.get(field))
            for field in (
                "exerciseStartDate", "releaseDate", "issueDate", "recordDate",
                "cashPaymentDate", "listingDate", "baseDate",
            )
        ),
        default="",
    )


def add_official_corporate_action_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    external_signals: Dict[str, object],
) -> None:
    symbol = _symbol(position)
    groups = external_signals.get("corporateActions") if isinstance(external_signals, dict) else {}
    events = groups.get(symbol) if isinstance(groups, dict) and isinstance(groups.get(symbol), dict) else {}
    if not events:
        return
    company_id = add_entity(graph, "company", symbol, position.name or symbol, {
        "tboxClass": "Company",
        "symbol": symbol,
        "companyName": position.name or symbol,
    })
    ranked = sorted(
        (dict(event) for event in events.values() if isinstance(event, Mapping)),
        key=lambda event: (
            1 if _text(event.get("eventLifecycleState")) in {"upcoming", "active"} else 0,
            _event_date(event),
        ),
        reverse=True,
    )[:MAX_CORPORATE_ACTIONS]
    for event in ranked:
        event_key = _text(event.get("eventId"))
        event_type = _text(event.get("eventType") or "corporate-action")
        if not event_key:
            continue
        tbox_class = _text(event.get("tboxClass") or "CorporateAction")
        financing_or_supply_event = event_type in {
            "equity-issuance",
            "lockup-release",
            "shareholder-right",
        }
        event_decision_eligible = financing_or_supply_event and _text(
            event.get("eventLifecycleState")
        ) in {"upcoming", "active"}
        label = (position.name or symbol) + " " + {
            "dividend": "배당",
            "equity-issuance": "주식 발행",
            "lockup-release": "보호예수 해제",
            "shareholder-right": "주주 권리 일정",
        }.get(event_type, "기업 행동")
        event_id = add_entity(graph, "corporate-action", event_key, label, {
            "tboxClass": tbox_class,
            "tboxClasses": ["Observation", "ExternalSignal", "CorporateAction", tbox_class],
            **{
                key: value
                for key, value in event.items()
                if key not in {"tboxClass"} and value not in (None, "")
            },
            "eventDate": _event_date(event),
            "officialSource": True,
            "eventDecisionEligible": event_decision_eligible,
            "eventDecisionReason": (
                "공식 발행·유통물량·주주권리 일정이 현재 또는 예정 상태입니다."
                if event_decision_eligible
                else "공식 과거 기업행동 참고 사실입니다."
            ),
        })
        source_id = add_entity(graph, "data-source", "public-data:" + event_type, _text(event.get("provider") or "공공데이터포털"), {
            "tboxClass": "DataSource",
            "tboxClasses": ["DataSource", "Provenance"],
            "sourceUrl": _text(event.get("sourceUrl")),
            "officialSource": True,
        })
        props = _relation_properties(
            "공식 " + label,
            eventType=event_type,
            eventLifecycleState=_text(event.get("eventLifecycleState")),
            eventDate=_event_date(event),
        )
        add_relation(graph, company_id, event_id, "HAS_CORPORATE_ACTION", weight=1.0, properties=props)
        add_relation(graph, stock_id, event_id, "HAS_CORPORATE_ACTION", weight=1.0, properties=props)
        add_relation(graph, event_id, stock_id, "APPLIES_TO_SECURITY", weight=1.0, properties=props)
        add_relation(graph, event_id, source_id, "HAS_PROVENANCE", weight=1.0, properties=props)
        if event_decision_eligible:
            add_relation(graph, stock_id, event_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, properties=props)
        if tbox_class in {"EquityIssuanceEvent", "LockupReleaseEvent"}:
            add_relation(graph, company_id, event_id, "HAS_CAPITAL_EVENT", weight=1.0, properties=props)
            add_relation(graph, stock_id, event_id, "HAS_CAPITAL_EVENT", weight=1.0, properties=props)
        share_class_code = _text(event.get("shareClassCode"))
        share_class_name = _text(event.get("shareClassName"))
        if share_class_code or share_class_name:
            share_class_id = add_entity(graph, "share-class", symbol + ":" + (share_class_code or share_class_name), share_class_name or share_class_code, {
                "tboxClass": "ShareClass",
                "shareClassCode": share_class_code,
                "shareClassName": share_class_name,
                "symbol": symbol,
            })
            add_relation(graph, stock_id, share_class_id, "HAS_SHARE_CLASS", weight=1.0, properties=props)
            add_relation(graph, event_id, share_class_id, "APPLIES_TO_SECURITY", weight=1.0, properties=props)


__all__ = [
    "add_official_corporate_action_concepts",
    "add_official_market_index_concepts",
    "add_official_security_reference_concepts",
]
