"""Application service that projects immutable market facts into valuation evidence."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping

from digital_twin.modules.portfolio.domain.valuation.historical_multiples import (
    build_historical_forward_multiple_observations,
    normalize_current_consensus_contract,
)


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
