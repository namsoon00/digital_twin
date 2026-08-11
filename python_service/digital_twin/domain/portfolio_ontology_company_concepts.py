"""Bounded company KnowledgeWorld ABox concepts.

The durable provider archive lives in MySQL.  TypeDB receives only current,
decision-relevant company facts and a small number of reporting periods so a
quote tick can read company context without rewriting an entire statement.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping

from .market_data import number
from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_schema import add_entity, add_relation


COMPANY_ABOX_CONTRACT_VERSION = "company-abox-v1"
FINANCIAL_PERIOD_LIMITS = {"annual": 3, "interim": 2, "quarterly": 3}
MAX_EXECUTIVE_ROLES = 8


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
    coverage = knowledge.get("coverage") if isinstance(knowledge.get("coverage"), dict) else {}
    provenance = knowledge.get("provenance") if isinstance(knowledge.get("provenance"), list) else []
    primary_source = _text((provenance[0] if provenance and isinstance(provenance[0], dict) else {}).get("provider") or "company-knowledge")
    company_id = add_entity(graph, "company", symbol, company_name, {
        "tboxClass": "Company",
        "symbol": symbol,
        "companyName": company_name,
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
    if valuation:
        valuation_id = add_entity(graph, "company-valuation-state", symbol, company_name + " 회사 밸류에이션 상태", {
            "tboxClass": "ValuationMetric",
            "tboxClasses": ["Observation", "FundamentalObservation", "ValuationMetric"],
            "symbol": symbol,
            "companyFactRevision": revision,
            "dataState": _text(coverage.get("dataState") or "partial"),
            **{field: number(value) for field, value in valuation.items() if value not in (None, "")},
        })
        props = _relation_properties(primary_source, "PER·PBR·ROE 회사 평가 지표")
        add_relation(graph, company_id, valuation_id, "HAS_VALUATION_METRIC", weight=0.9, properties=props)
        add_relation(graph, stock_id, valuation_id, "HAS_VALUATION_METRIC", weight=0.9, properties=props)

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
