"""Bounded company KnowledgeWorld ABox concepts.

The durable provider archive lives in MySQL.  TypeDB receives only current,
decision-relevant company facts and a small number of reporting periods so a
quote tick can read company context without rewriting an entire statement.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping

from .company_knowledge import COMPANY_VALUATION_CONTEXT_VERSION, latest_source_as_of
from .market_data import number
from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_schema import add_entity, add_relation


COMPANY_ABOX_CONTRACT_VERSION = "company-abox-v2"
FINANCIAL_PERIOD_LIMITS = {"annual": 3, "interim": 2, "quarterly": 3}
MAX_EXECUTIVE_ROLES = 8
MAX_COMPANY_RELATIONSHIPS = 12


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _relation_properties(source: str, label: str, **values) -> Dict[str, object]:
    return {
        "source": source or "company-knowledge",
        "polarity": "context",
        "evidenceRole": "context",
        "aiInfluenceLabel": label,
        **values,
    }


def _period_rows(financials: Mapping[str, object], frequency: str) -> Iterable[Dict[str, object]]:
    values = financials.get(frequency) if isinstance(financials, Mapping) else []
    limit = FINANCIAL_PERIOD_LIMITS.get(frequency, 2)
    return [dict(row) for row in values[:limit] if isinstance(row, Mapping)] if isinstance(values, list) else []


def _period_rank(value: object, frequency: str) -> tuple:
    digits = "".join(character for character in _text(value) if character.isdigit())
    # DART interim labels may be a date range. The final eight digits are the
    # reporting-period end and therefore the relevant recency boundary.
    period_end = int(digits[-8:] or 0)
    frequency_priority = {"annual": 1, "interim": 2, "quarterly": 3}.get(frequency, 0)
    return period_end, frequency_priority


def _financial_properties(row: Mapping[str, object], **extra) -> Dict[str, object]:
    numeric_fields = (
        "revenue", "grossProfit", "operatingIncome", "netIncome", "totalAssets",
        "totalLiabilities", "equity", "cash", "totalDebt", "operatingCashFlow",
        "capitalExpenditure", "freeCashFlow", "sharesOutstanding", "grossMarginPct",
        "operatingMarginPct", "netMarginPct", "cashConversionPct", "freeCashFlowMarginPct",
        "debtToEquityPct", "liabilitiesToAssetsPct", "revenueGrowthPct",
        "operatingIncomeGrowthPct", "netIncomeGrowthPct", "freeCashFlowGrowthPct",
        "sharesOutstandingGrowthPct",
    )
    return {
        "tboxClass": "FinancialState",
        "tboxClasses": ["Observation", "FundamentalObservation", "FinancialFact", "FinancialState"],
        "period": _text(row.get("period")),
        **{
            field: number(row.get(field))
            for field in numeric_fields
            if row.get(field) not in (None, "")
        },
        **extra,
    }


def add_company_knowledge_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    symbol: str,
    external_signals: Dict[str, object],
) -> None:
    groups = external_signals.get("companyKnowledge") if isinstance(external_signals, dict) else {}
    knowledge = groups.get(symbol) if isinstance(groups, dict) and isinstance(groups.get(symbol), dict) else {}
    if not knowledge:
        return
    company_name = _text(knowledge.get("companyName") or symbol)
    revision = _text(knowledge.get("factRevision") or "current")
    profile = knowledge.get("profile") if isinstance(knowledge.get("profile"), dict) else {}
    identifiers = knowledge.get("identifiers") if isinstance(knowledge.get("identifiers"), dict) else {}
    listing = knowledge.get("listing") if isinstance(knowledge.get("listing"), dict) else {}
    coverage = knowledge.get("coverage") if isinstance(knowledge.get("coverage"), dict) else {}
    provenance = knowledge.get("provenance") if isinstance(knowledge.get("provenance"), list) else []
    primary_source = _text((provenance[0] if provenance and isinstance(provenance[0], dict) else {}).get("provider") or "company-knowledge")
    company_id = add_entity(graph, "company", symbol, company_name, {
        "tboxClass": "Company",
        "symbol": symbol,
        "companyName": company_name,
        "corporateRegistrationNumber": _text(identifiers.get("corporateRegistrationNumber")),
        "businessRegistrationNumber": _text(identifiers.get("businessRegistrationNumber")),
        "fssCorporateNumber": _text(identifiers.get("fssCorporateNumber")),
        "officialIsin": _text(identifiers.get("isin")),
        "legalName": _text(profile.get("legalName")),
        "englishName": _text(profile.get("englishName")),
        "ceoName": _text(profile.get("ceoName")),
        "sector": _text(profile.get("sector")),
        "industry": _text(profile.get("industry")),
        "website": _text(profile.get("website")),
        "establishedDate": _text(profile.get("establishedDate")),
        "fiscalYearEndMonth": _text(profile.get("fiscalYearEndMonth")),
        "marketCapitalization": number(profile.get("marketCapitalization")),
        "companyFactRevision": revision,
        "companyDataState": _text(coverage.get("dataState") or "partial"),
        "companyAboxContractVersion": COMPANY_ABOX_CONTRACT_VERSION,
    })
    add_relation(graph, stock_id, company_id, "REPRESENTS_COMPANY", weight=1.0, properties=_relation_properties(primary_source, "회사 실세계 정보"))

    if listing:
        market_name = _text(
            listing.get("marketRegistrationName")
            or listing.get("market")
            or "KR"
        )
        market_id = add_entity(graph, "market", market_name, market_name, {
            "tboxClass": "Market",
            "marketName": market_name,
            "country": "KR",
        })
        listing_id = add_entity(graph, "security-listing", symbol, company_name + " 공식 상장 상태", {
            "tboxClass": "SecurityListing",
            "tboxClasses": ["Observation", "FundamentalObservation", "SecurityListing"],
            "symbol": symbol,
            "isin": _text(identifiers.get("isin")),
            **{key: value for key, value in listing.items() if value not in (None, "")},
            "companyFactRevision": revision,
            "source": primary_source,
            "officialSource": bool(coverage.get("officialSource")),
        })
        listing_props = _relation_properties(primary_source, "공식 상장·주식 종류 정보")
        add_relation(graph, company_id, listing_id, "HAS_LISTING", weight=1.0, properties=listing_props)
        add_relation(graph, stock_id, listing_id, "HAS_LISTING", weight=1.0, properties=listing_props)
        add_relation(graph, listing_id, market_id, "LISTED_ON", weight=1.0, properties=listing_props)
        share_class_code = _text(listing.get("shareClassCode"))
        share_class_name = _text(listing.get("shareClassName"))
        if share_class_code or share_class_name:
            share_class_id = add_entity(
                graph,
                "share-class",
                symbol + ":" + (share_class_code or share_class_name),
                share_class_name or share_class_code,
                {
                    "tboxClass": "ShareClass",
                    "symbol": symbol,
                    "shareClassCode": share_class_code,
                    "shareClassName": share_class_name,
                    "issueForm": _text(listing.get("issueForm")),
                    "parValue": number(listing.get("parValue")),
                },
            )
            add_relation(graph, stock_id, share_class_id, "HAS_SHARE_CLASS", weight=1.0, properties=listing_props)

    relationships = knowledge.get("relationships") if isinstance(knowledge.get("relationships"), dict) else {}
    affiliates = relationships.get("affiliates") if isinstance(relationships.get("affiliates"), list) else []
    for item in affiliates[:MAX_COMPANY_RELATIONSHIPS]:
        if not isinstance(item, Mapping):
            continue
        related_name = _text(item.get("companyName"))
        related_key = _text(item.get("corporateRegistrationNumber")) or related_name
        if not related_key or not related_name:
            continue
        related_id = add_entity(graph, "company", related_key, related_name, {
            "tboxClass": "Company",
            "companyName": related_name,
            "corporateRegistrationNumber": _text(item.get("corporateRegistrationNumber")),
            "listed": _text(item.get("listed")),
            "referenceBaseDate": _text(item.get("baseDate")),
            "source": _text(item.get("provider") or primary_source),
        })
        add_relation(
            graph,
            company_id,
            related_id,
            "AFFILIATED_WITH",
            weight=0.9,
            properties=_relation_properties(
                _text(item.get("provider") or primary_source),
                "공식 계열회사 관계",
                baseDate=_text(item.get("baseDate")),
            ),
        )
    subsidiaries = relationships.get("subsidiaries") if isinstance(relationships.get("subsidiaries"), list) else []
    subsidiaries = sorted(
        [dict(item) for item in subsidiaries if isinstance(item, Mapping)],
        key=lambda item: (
            _text(item.get("materialSubsidiary")) in {"Y", "예", "해당"},
            number(item.get("totalAssets")),
        ),
        reverse=True,
    )[:MAX_COMPANY_RELATIONSHIPS]
    for item in subsidiaries:
        related_name = _text(item.get("companyName"))
        if not related_name:
            continue
        related_id = add_entity(graph, "company", "subsidiary:" + symbol + ":" + related_name, related_name, {
            "tboxClass": "Company",
            "companyName": related_name,
            "establishedDate": _text(item.get("establishedDate")),
            "headOfficeAddress": _text(item.get("address")),
            "mainBusiness": _text(item.get("mainBusiness")),
            "totalAssets": number(item.get("totalAssets")),
            "referenceBaseDate": _text(item.get("baseDate")),
            "source": _text(item.get("provider") or primary_source),
        })
        add_relation(
            graph,
            company_id,
            related_id,
            "CONTROLS",
            weight=1.0,
            properties=_relation_properties(
                _text(item.get("provider") or primary_source),
                "공식 연결대상 종속회사 관계",
                baseDate=_text(item.get("baseDate")),
                controlBasis=_text(item.get("controlBasis")),
                materialSubsidiary=_text(item.get("materialSubsidiary")),
            ),
        )

    financials = knowledge.get("financials") if isinstance(knowledge.get("financials"), dict) else {}
    current_state_candidates = []
    for frequency in ("annual", "interim", "quarterly"):
        for index, row in enumerate(_period_rows(financials, frequency)):
            period = _text(row.get("period") or str(index))
            frequency_label = {"annual": "연간", "interim": "중간", "quarterly": "분기"}.get(frequency, frequency)
            state_id = add_entity(
                graph,
                "company-financial-state",
                symbol + ":" + frequency + ":" + period,
                company_name + " " + period + " " + frequency_label + " 재무 상태",
                _financial_properties(
                    row,
                    symbol=symbol,
                    reportingFrequency=frequency,
                    isLatestPeriod=index == 0,
                    companyFactRevision=revision,
                    dataState=_text(coverage.get("dataState") or "partial"),
                    source=primary_source,
                ),
            )
            props = _relation_properties(
                primary_source,
                ("최신 " if index == 0 else "과거 ") + frequency_label + " 재무 사실",
                reportingFrequency=frequency,
                period=period,
                isLatestPeriod=index == 0,
            )
            add_relation(graph, company_id, state_id, "HAS_FINANCIAL_STATE", weight=1.0 if index == 0 else 0.75, properties=props)
            if index == 0:
                current_state_candidates.append((_period_rank(period, frequency), state_id, props))

    latest_state_id = ""
    current_state_props: Dict[str, object] = {}
    if current_state_candidates:
        _rank, latest_state_id, current_state_props = max(current_state_candidates, key=lambda item: item[0])
        add_relation(
            graph,
            stock_id,
            latest_state_id,
            "HAS_FINANCIAL_STATE",
            weight=1.0,
            properties={**current_state_props, "decisionCurrentState": True},
        )

    valuation = knowledge.get("valuation") if isinstance(knowledge.get("valuation"), dict) else {}
    valuation_id = ""
    if valuation:
        source_as_of_values = [
            _text(item.get("asOf"))
            for item in provenance
            if isinstance(item, Mapping) and _text(item.get("asOf"))
        ]
        source_providers = list(dict.fromkeys(
            _text(item.get("provider"))
            for item in provenance
            if isinstance(item, Mapping) and _text(item.get("provider"))
        ))[:6]
        raw_trailing_eps = valuation.get("trailingEPS")
        trailing_eps = number(raw_trailing_eps)
        pe_ratio = number(valuation.get("peRatio"))
        per_status = (
            "not-meaningful-loss"
            if raw_trailing_eps not in (None, "") and trailing_eps < 0
            else "not-meaningful-zero-earnings" if raw_trailing_eps not in (None, "") and trailing_eps == 0
            else "available" if pe_ratio > 0
            else "missing"
        )
        valuation_id = add_entity(graph, "company-valuation-state", symbol, company_name + " 회사 밸류에이션 상태", {
            "tboxClass": "ValuationSnapshot",
            "tboxClasses": ["Observation", "FundamentalObservation", "ValuationMetric", "ValuationSnapshot"],
            "symbol": symbol,
            "companyFactRevision": revision,
            "dataState": _text(coverage.get("dataState") or "partial"),
            "valuationDataState": _text(coverage.get("dataState") or "partial"),
            "valuationMetricCount": len(valuation),
            "valuationSourceAsOf": latest_source_as_of(source_as_of_values),
            "valuationSourceProviders": source_providers,
            "valuationOfficialSource": bool(coverage.get("officialSource")),
            "valuationPerStatus": per_status,
            "reportingPeriod": _text(current_state_props.get("period")),
            "reportingFrequency": _text(current_state_props.get("reportingFrequency")),
            "valuationContextVersion": COMPANY_VALUATION_CONTEXT_VERSION,
            **{field: number(value) for field, value in valuation.items() if value not in (None, "")},
        })
        props = _relation_properties(primary_source, "PER·PBR·ROE 회사 평가 지표")
        add_relation(graph, company_id, valuation_id, "HAS_VALUATION_METRIC", weight=0.9, properties=props)
        add_relation(graph, stock_id, valuation_id, "HAS_VALUATION_METRIC", weight=0.9, properties=props)
        add_relation(graph, company_id, valuation_id, "HAS_VALUATION_SNAPSHOT", weight=0.95, properties=props)
        add_relation(graph, stock_id, valuation_id, "HAS_VALUATION_SNAPSHOT", weight=0.95, properties=props)
        basis_id = add_entity(graph, "valuation-reporting-basis", symbol, company_name + " 밸류에이션 보고 기준", {
            "tboxClass": "ReportingBasis",
            "tboxClasses": ["Observation", "FundamentalObservation", "ReportingBasis"],
            "symbol": symbol,
            "reportingPeriod": _text(current_state_props.get("period")),
            "reportingFrequency": _text(current_state_props.get("reportingFrequency")),
            "valuationSourceAsOf": latest_source_as_of(source_as_of_values),
            "companyFactRevision": revision,
        })
        add_relation(graph, valuation_id, basis_id, "USES_REPORTING_BASIS", weight=1.0, properties=props)
        quality_id = add_entity(graph, "valuation-data-quality", symbol, company_name + " 밸류에이션 자료 품질", {
            "tboxClass": "ValuationDataQuality",
            "tboxClasses": ["Observation", "DataQuality", "ValuationDataQuality"],
            "symbol": symbol,
            "dataState": _text(coverage.get("dataState") or "partial"),
            "valuationMetricCount": len(valuation),
            "valuationOfficialSource": bool(coverage.get("officialSource")),
            "missingFields": [str(item) for item in (coverage.get("missing") or [])[:8]],
            "valuationSourceAsOf": latest_source_as_of(source_as_of_values),
            "companyFactRevision": revision,
        })
        add_relation(graph, valuation_id, quality_id, "HAS_VALUATION_DATA_QUALITY", weight=1.0, properties=props)
        if latest_state_id:
            add_relation(graph, valuation_id, latest_state_id, "DERIVED_FROM_FINANCIAL_FACT", weight=0.95, properties=props)

    capital = knowledge.get("capital") if isinstance(knowledge.get("capital"), dict) else {}
    if capital:
        capital_id = add_entity(graph, "company-capital-state", symbol, company_name + " 자본 구조 상태", {
            "tboxClass": "CapitalState",
            "tboxClasses": ["Observation", "FundamentalObservation", "CapitalStructureSnapshot", "CapitalState"],
            "symbol": symbol,
            "companyFactRevision": revision,
            "dataState": _text(coverage.get("dataState") or "partial"),
            **{field: number(value) for field, value in capital.items() if value not in (None, "")},
        })
        props = _relation_properties(primary_source, "주식수·현금·부채 자본 구조")
        add_relation(graph, company_id, capital_id, "HAS_CAPITAL_STATE", weight=0.95, properties=props)
        add_relation(graph, stock_id, capital_id, "HAS_CAPITAL_STATE", weight=0.95, properties=props)
        if latest_state_id:
            add_relation(graph, capital_id, latest_state_id, "DERIVED_FROM_FINANCIAL_FACT", weight=0.9, properties=props)

    governance = knowledge.get("governance") if isinstance(knowledge.get("governance"), dict) else {}
    executives = governance.get("executives") if isinstance(governance.get("executives"), list) else []
    governance_id = add_entity(graph, "company-governance-state", symbol, company_name + " 경영진·지배구조 상태", {
        "tboxClass": "GovernanceState",
        "tboxClasses": ["Observation", "FundamentalObservation", "GovernanceState"],
        "symbol": symbol,
        "ceoName": _text(profile.get("ceoName")),
        "executiveCount": len(executives),
        "companyFactRevision": revision,
        "dataState": "available" if executives or profile.get("ceoName") else "missing",
    })
    governance_props = _relation_properties(primary_source, "대표이사·주요 임원 구성")
    add_relation(graph, company_id, governance_id, "HAS_GOVERNANCE_STATE", weight=0.9, properties=governance_props)
    add_relation(graph, stock_id, governance_id, "HAS_GOVERNANCE_STATE", weight=0.9, properties=governance_props)
    for index, executive in enumerate(executives[:MAX_EXECUTIVE_ROLES]):
        if not isinstance(executive, Mapping):
            continue
        name = _text(executive.get("name"))
        title = _text(executive.get("title"))
        if not name:
            continue
        person_id = add_entity(graph, "person", name, name, {
            "tboxClass": "Person",
            "personName": name,
            "source": _text(executive.get("provider") or primary_source),
        })
        role_id = add_entity(graph, "executive-role", symbol + ":" + str(index) + ":" + name + ":" + title, company_name + " " + (title or "임원") + " " + name, {
            "tboxClass": "ExecutiveRole",
            "tboxClasses": ["ExecutiveRole", "GovernanceState"],
            "symbol": symbol,
            "personName": name,
            "executiveTitle": title,
            "executiveResponsibility": _text(executive.get("role")),
            "registeredExecutive": _text(executive.get("registeredExecutive")),
            "tenureEnd": _text(executive.get("tenureEnd")),
            "source": _text(executive.get("provider") or primary_source),
        })
        add_relation(graph, governance_id, role_id, "HAS_EXECUTIVE_ROLE", weight=0.9, properties=governance_props)
        add_relation(graph, role_id, person_id, "ROLE_HELD_BY", weight=1.0, properties=governance_props)

    for item in provenance[:6]:
        if not isinstance(item, Mapping):
            continue
        provider = _text(item.get("provider"))
        if not provider:
            continue
        source_id = add_entity(graph, "data-source", provider, provider, {
            "tboxClass": "DataSource",
            "provider": provider,
            "sourceAsOf": _text(item.get("asOf")),
            "dataScope": _text(item.get("scope") or "company-knowledge"),
        })
        add_relation(graph, company_id, source_id, "HAS_PROVENANCE", weight=1.0, properties=_relation_properties(provider, "회사 정보 출처"))
        if valuation_id:
            add_relation(graph, valuation_id, source_id, "HAS_PROVENANCE", weight=1.0, properties=_relation_properties(provider, "밸류에이션 원천"))

    missing = coverage.get("missing") if isinstance(coverage.get("missing"), list) else []
    if missing:
        missing_id = add_entity(graph, "company-coverage-gap", symbol, company_name + " 회사 정보 부족", {
            "tboxClass": "CoverageGap",
            "tboxClasses": ["Observation", "DataQuality", "CoverageGap", "MissingData"],
            "symbol": symbol,
            "dataScope": "company-knowledge",
            "missingFields": [str(item) for item in missing[:8]],
            "dataState": _text(coverage.get("dataState") or "partial"),
        })
        add_relation(graph, stock_id, missing_id, "HAS_COVERAGE_GAP", weight=1.0, properties=_relation_properties(primary_source, "회사 정보 부족 데이터"))
