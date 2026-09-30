"""Expose existing normalized reports to research without losing period identity."""

from typing import Dict, List

from digital_twin.modules.market_data.contracts import parse_datetime
from .financial_reporting import financial_report_contract_assessment
from .company_report_evidence import METRICS, _financial_row
from .investment_research import ResearchEvidence


REPORTED_METRICS = tuple(field for field, _label in METRICS)


def financial_research_evidence(symbol: str, company: Dict[str, object]) -> List[ResearchEvidence]:
    """Use only source-bound annual, quarterly and cumulative report contracts.

    A report period is never substituted for a publication or ingestion time.
    Missing disclosure clocks remain explicit; these observations support
    current research, not a claim of historical point-in-time availability.
    """
    financials = company.get("financials") if isinstance(company.get("financials"), dict) else {}
    results = []
    for frequency in ("annual", "quarterly", "interim"):
        for row in (financials.get(frequency) or [])[:4]:
            if not isinstance(row, dict) or not financial_report_contract_assessment(row, frequency).get("eligible"):
                continue
            contract = row["reportContract"]
            metrics = [metric for metric in _financial_row(row, company).get("metrics") or [] if metric.get("sourceReferences")]
            reference_ids = {(ref["datasetId"], ref["revisionId"]) for metric in metrics for ref in metric["sourceReferences"]}
            references = [ref for ref in contract.get("sourceReferences") or []
                          if (ref.get("datasetId"), ref.get("revisionId")) in reference_ids]
            if any(str(ref.get("subjectKey") or "").upper() != symbol.upper() for ref in references):
                continue
            observed = [parse_datetime(ref.get("fetchedAt")) for ref in references if isinstance(ref, dict)]
            if not observed or any(stamp is None for stamp in observed):
                continue
            observed_at = max(observed).isoformat().replace("+00:00", "Z")
            freshness_observed_at = min(observed).isoformat().replace("+00:00", "Z")
            if parse_datetime(contract["periodEnd"]) > max(observed):
                continue
            values = {metric["key"]: metric["value"] for metric in metrics}
            if not values:
                continue
            period = contract["periodEnd"]
            currencies = list(contract.get("currencies") or [])
            metric_units = {metric["key"]: metric["currency"] for metric in metrics}
            metric_provenance = {metric["key"]: {key: metric.get(key) for key in (
                "provider", "currency", "scope", "durationBasis", "period", "sourceUrl", "derived", "sourceReferences",
            )} for metric in metrics}
            title = str(company.get("name") or symbol) + " " + period + " " + frequency + " 재무 보고"
            results.append(ResearchEvidence(
                evidence_id="research:" + symbol + ":financial-report:" + contract["observationId"],
                symbol=symbol, kind="financial-fact", source=contract["provider"],
                title=title,
                summary=title + ": " + ", ".join(key + "=" + str(value) + " " + metric_units[key] for key, value in values.items()),
                observed_at=observed_at, published_at=str(contract.get("publishedAt") or ""),
                raw_payload={
                    "relationScope": "direct", "dataState": "sufficient", "validationState": "ready",
                    "periodEnd": period, "frequency": frequency,
                    "reportedValues": values, "metricUnits": metric_units,
                    "metricProvenance": metric_provenance,
                    "currencies": currencies, "durationBases": list(contract.get("durationBases") or []),
                    "reportObservationId": contract["observationId"],
                    "financialReport": dict(row), "sourceReferences": references,
                    "sourceRevision": ",".join(str(ref["revisionId"]) for ref in references),
                    "freshnessBasis": "source-observation", "historicalReport": True,
                    "freshnessObservedAt": freshness_observed_at,
                    "publicationTimeKnown": bool(contract.get("publishedAt")),
                },
            ))
    return results
