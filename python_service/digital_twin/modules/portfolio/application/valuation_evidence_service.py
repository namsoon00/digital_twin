"""Application service that projects immutable market facts into valuation evidence."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping

from digital_twin.modules.news_intelligence.contracts import (
    build_company_driver_map,
    company_knowledge_by_symbol,
    merge_company_knowledge_rows,
)
from digital_twin.modules.portfolio.domain.valuation.dcf_inputs import build_driver_dcf_input_bundle
from digital_twin.modules.portfolio.domain.valuation.historical_multiples import (
    build_historical_forward_multiple_observations,
    normalize_current_consensus_contract,
)


def _float_setting(settings: Mapping[str, object], key: str, fallback: float) -> float:
    try:
        return float(settings.get(key))
    except (TypeError, ValueError):
        return fallback


class HistoricalMultipleEvidenceService:
    def __init__(self, fact_store, settings: Mapping[str, object] = None):
        self.fact_store = fact_store
        self.settings = dict(settings or {})

    def enrich(self, signals: Dict[str, object], symbols: Iterable[object]) -> Dict[str, object]:
        result = dict(signals or {})
        requested = sorted({str(item or "").upper().strip() for item in symbols or [] if str(item or "").strip()})
        if not requested or not hasattr(self.fact_store, "list_revisions"):
            return result
        revisions = self.fact_store.list_revisions(
            dataset_ids=["yfinance.fundamental", "yfinance.analyst"],
            subject_keys=requested,
            limit=max(100, len(requested) * 240),
        )
        grouped = {}
        for row in revisions or []:
            if not isinstance(row, Mapping):
                continue
            key = (str(row.get("subjectKey") or "").upper().strip(), str(row.get("datasetId") or ""))
            grouped.setdefault(key, []).append(dict(row))
        overviews = {
            str(symbol): dict(value) if isinstance(value, Mapping) else value
            for symbol, value in dict(result.get("companyOverviews") or {}).items()
        }
        yfinance_data = result.get("yfinanceData") if isinstance(result.get("yfinanceData"), Mapping) else {}
        audit = {}
        for symbol in requested:
            overview = overviews.get(symbol) if isinstance(overviews.get(symbol), Mapping) else {}
            yf = yfinance_data.get(symbol) if isinstance(yfinance_data.get(symbol), Mapping) else {}
            security_line = str(yf.get("querySymbol") or overview.get("securityLine") or symbol).upper().strip()
            overview = normalize_current_consensus_contract(overview, security_line=security_line)
            observations = build_historical_forward_multiple_observations(
                symbol,
                grouped.get((symbol, "yfinance.fundamental"), []),
                grouped.get((symbol, "yfinance.analyst"), []),
                max_analyst_age_days=int(self.settings.get("valuationHistoricalMultipleMaxAnalystAgeDays") or 14),
                max_samples=int(self.settings.get("valuationHistoricalMultipleMaxSamples") or 12),
            )
            existing = [dict(item) for item in overview.get("multipleObservations") or [] if isinstance(item, Mapping)]
            if observations:
                by_id = {str(item.get("observationId") or ""): item for item in [*existing, *observations]}
                overview["multipleObservations"] = [by_id[key] for key in sorted(by_id)]
            if overview:
                overviews[symbol] = overview
            audit[symbol] = {
                "contractVersion": "historical-multiple-evidence-feed-v1",
                "status": "available" if observations else "insufficient-history",
                "sampleCount": len(observations),
                "minimumDecisionSamples": 3,
                "decisionSampleThresholdMet": len(observations) >= 3,
                "sampling": "latest-observation-per-iso-week",
                "lookAheadGuard": "analyst-observed-at-or-before-price",
            }
        result["companyOverviews"] = overviews
        result["valuationEvidenceFeeds"] = audit
        return result


class DriverDcfEvidenceService:
    """Create a source-backed shadow DCF bundle for a bounded pilot set."""

    def __init__(self, settings: Mapping[str, object] = None):
        self.settings = dict(settings or {})

    def _pilot_symbols(self) -> set[str]:
        configured = self.settings.get("valuationDriverDcfPilotSymbols")
        if isinstance(configured, (list, tuple, set)):
            values = configured
        else:
            values = str(configured or "NVDA,000660").split(",")
        return {str(item or "").upper().strip() for item in values if str(item or "").strip()}

    def enrich(self, signals: Dict[str, object], symbols: Iterable[object]) -> Dict[str, object]:
        result = dict(signals or {})
        requested = sorted({str(item or "").upper().strip() for item in symbols or [] if str(item or "").strip()})
        selected = [symbol for symbol in requested if symbol in self._pilot_symbols()]
        if not selected:
            return result

        generated = company_knowledge_by_symbol(result, selected)
        existing = result.get("companyKnowledge") if isinstance(result.get("companyKnowledge"), Mapping) else {}
        company_knowledge = {str(key): value for key, value in existing.items()}
        for symbol in selected:
            merged = merge_company_knowledge_rows(
                company_knowledge.get(symbol) if isinstance(company_knowledge.get(symbol), Mapping) else {},
                generated.get(symbol) if isinstance(generated.get(symbol), Mapping) else {},
            )
            if merged:
                company_knowledge[symbol] = merged
        if company_knowledge:
            result["companyKnowledge"] = company_knowledge

        overviews = result.get("companyOverviews") if isinstance(result.get("companyOverviews"), Mapping) else {}
        yfinance_data = result.get("yfinanceData") if isinstance(result.get("yfinanceData"), Mapping) else {}
        lineage = result.get("externalDataLineage") if isinstance(result.get("externalDataLineage"), Mapping) else {}
        macro = result.get("macro") if isinstance(result.get("macro"), Mapping) else {}
        bundles = dict(result.get("driverDcfInputs") or {}) if isinstance(result.get("driverDcfInputs"), Mapping) else {}
        readiness = dict(result.get("driverDcfReadiness") or {}) if isinstance(result.get("driverDcfReadiness"), Mapping) else {}
        generic_erp = _float_setting(self.settings, "valuationDriverDcfEquityRiskPremiumPct", 5.0)
        generic_terminal_growth = _float_setting(self.settings, "valuationDriverDcfTerminalGrowthPct", 2.5)
        for symbol in selected:
            company = company_knowledge.get(symbol) if isinstance(company_knowledge.get(symbol), Mapping) else {}
            financial_candidates = []
            for source_company in (
                existing.get(symbol) if isinstance(existing.get(symbol), Mapping) else {},
                generated.get(symbol) if isinstance(generated.get(symbol), Mapping) else {},
                company,
            ):
                financials = source_company.get("financials") if isinstance(source_company.get("financials"), Mapping) else {}
                financial_candidates.extend(
                    dict(item) for item in financials.get("annual") or [] if isinstance(item, Mapping)
                )
            driver_map = build_company_driver_map(
                symbol,
                company,
                macro_context=macro,
                fx_rates=result.get("fxRates") if isinstance(result.get("fxRates"), Mapping) else {},
            )
            built = build_driver_dcf_input_bundle(
                symbol,
                {**company, "valuationFinancialCandidates": financial_candidates},
                overview=overviews.get(symbol) if isinstance(overviews.get(symbol), Mapping) else {},
                yfinance=yfinance_data.get(symbol) if isinstance(yfinance_data.get(symbol), Mapping) else {},
                macro=macro,
                lineage=lineage,
                exposure_readiness=driver_map.get("exposureReadiness") if isinstance(driver_map, Mapping) else {},
                valuation_at=result.get("fetchedAt"),
                equity_risk_premium_pct=generic_erp,
                terminal_growth_pct=generic_terminal_growth,
                equity_risk_premium_pct_by_currency={
                    "USD": _float_setting(self.settings, "valuationDriverDcfUsEquityRiskPremiumPct", generic_erp),
                    "KRW": _float_setting(self.settings, "valuationDriverDcfKrEquityRiskPremiumPct", generic_erp),
                },
                terminal_growth_pct_by_currency={
                    "USD": _float_setting(self.settings, "valuationDriverDcfUsTerminalGrowthPct", generic_terminal_growth),
                    "KRW": _float_setting(self.settings, "valuationDriverDcfKrTerminalGrowthPct", generic_terminal_growth),
                },
            )
            readiness[symbol] = {key: value for key, value in built.items() if key != "input"}
            if isinstance(built.get("input"), Mapping):
                bundles[symbol] = dict(built["input"])
        result["driverDcfInputs"] = bundles
        result["driverDcfReadiness"] = readiness
        return result
