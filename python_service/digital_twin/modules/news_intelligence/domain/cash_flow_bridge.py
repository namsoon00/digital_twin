"""Reproducible cash-flow arithmetic over source-owned report observations.

The bridge explains an accounting identity, never the economic cause of a
change. Reported free cash flow is kept separate from OCF less capex.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

VERSION = "cash-flow-bridge-v1"
INPUTS = ("operatingCashFlow", "capitalExpenditure")
VALUE_FIELDS = {"operatingCashFlowContribution", "capitalExpenditureContribution", "freeCashFlowChange"}


def cash_flow_bridge_field_usable(fact, field):
    prefix = "derivedMetricEvidence.cashFlowBridge."
    bridge = fact.get("derivedMetricEvidence", {}).get("cashFlowBridge", {})
    if not field.startswith(prefix) or field[len(prefix):] not in VALUE_FIELDS:
        return False
    if bridge.get("version") != VERSION or bridge.get("status") != "calculated":
        return False
    try:
        digest = hashlib.sha256(json.dumps({key: value for key, value in bridge.items() if key != "calculationId"},
                                         sort_keys=True, allow_nan=False).encode()).hexdigest()
        return digest == bridge.get("calculationId") and bool(bridge.get("sourceReferences"))
    except (ValueError, TypeError):
        return False


def _date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and math.isfinite(float(result)) and abs(result) <= Decimal("1e30") else None
    except InvalidOperation:
        return None


def cash_flow_bridge(previous, current):
    """Inputs are normalized financial_research_evidence raw payloads."""
    result = {"version": VERSION, "status": "unavailable", "reasons": [],
              "authority": "accounting-calculation-only", "causalValidation": "not-established"}
    reasons, observations, bases, references, required = [], [], [], [], []
    for report in (previous, current):
        end = _date(report.get("periodEnd"))
        values, provenance = report.get("reportedValues", {}), report.get("metricProvenance", {})
        inputs = {}
        for metric in INPUTS:
            source = provenance.get(metric, {})
            value = _number(values.get(metric))
            start = _date(source.get("periodStart"))
            refs = source.get("sourceReferences") or []
            basis = {key: source.get(key) for key in ("provider", "currency", "scope", "durationBasis")}
            if value is None or not start or not end or start >= end or not all(basis.values()):
                reasons.append("missing-value-or-basis:" + metric)
            if (not refs or any(not ref.get("datasetId") or not ref.get("revisionId")
                                or not ref.get("subjectKey") or not _date(ref.get("fetchedAt")) for ref in refs)):
                reasons.append("missing-source-revision:" + metric)
            missing = (["value"] if value is None else []) + (["periodStart"] if not start else [])
            missing += (["periodEnd"] if not end else []) + [key for key, value in basis.items() if not value]
            if not refs: missing.append("sourceReferences")
            if missing:
                required.append({"reportObservationId": report.get("reportObservationId", ""),
                    "periodEnd": report.get("periodEnd", ""), "frequency": report.get("frequency", ""),
                    "metric": metric, "missingFields": missing})
            references.extend(deepcopy(refs))
            bases.append(basis)
            inputs[metric] = {"value": value, "periodStart": start, "periodEnd": end}
        starts = [item["periodStart"] for item in inputs.values()]
        if starts[0] != starts[1]:
            reasons.append("mixed-statement-periods")
        if not report.get("reportObservationId"):
            reasons.append("missing-report-identity")
        observations.append(inputs)
    if bases and any(basis != bases[0] for basis in bases):
        reasons.append("incompatible-metric-basis")
    subjects = {ref.get("subjectKey") for ref in references}
    if len(subjects) != 1:
        reasons.append("incompatible-subject")
    # A correction first acquired after the current report's source snapshot
    # must not leak into that report's historical calculation.
    clocks = []
    for report in (previous, current):
        raw_clocks = [ref.get("fetchedAt") for metric in INPUTS
                      for ref in report.get("metricProvenance", {}).get(metric, {}).get("sourceReferences", [])]
        try:
            stamps = [datetime.fromisoformat(str(value).replace("Z", "+00:00")) for value in raw_clocks]
            if not stamps or any(stamp.tzinfo is None for stamp in stamps):
                raise ValueError("missing timezone")
            clocks.append(max(stamps))
        except (ValueError, TypeError):
            reasons.append("unknown-source-availability")
    if len(clocks) == 2 and clocks[0] > clocks[1]:
        reasons.append("previous-revision-known-later")
    before, after = _date(previous.get("periodEnd")), _date(current.get("periodEnd"))
    frequency = current.get("frequency")
    expected_duration = {"annual": "annual", "quarterly": "quarterly", "interim": "year-to-date"}.get(frequency)
    if bases and bases[0].get("durationBasis") != expected_duration:
        reasons.append("incompatible-frequency-duration")
    if not before or not after or before >= after or previous.get("frequency") != frequency:
        reasons.append("invalid-period-order")
    elif frequency not in {"annual", "quarterly", "interim"}:
        reasons.append("unsupported-frequency")
    else:
        gap = (after - before).days
        annual = 350 <= gap <= 380
        adjacent = frequency == "quarterly" and 75 <= gap <= 106
        if not (annual or adjacent):
            reasons.append("non-comparable-periods")
        starts = [row["operatingCashFlow"]["periodStart"] for row in observations]
        if all(starts) and (starts[1] <= before or abs((after-starts[1]).days-(before-starts[0]).days) > 8):
            reasons.append("overlapping-or-different-duration")
    if reasons:
        return {**result, "reasons": sorted(set(reasons)), "requiredEvidence": required}
    rows = []
    for report, inputs in zip((previous, current), observations):
        ocf, capex = inputs[INPUTS[0]]["value"], inputs[INPUTS[1]]["value"]
        computed = ocf - abs(capex)
        reported = _number(report.get("reportedValues", {}).get("freeCashFlow"))
        reported_source = report.get("metricProvenance", {}).get("freeCashFlow", {})
        reported_comparable = (reported is not None
            and all(reported_source.get(key) == value for key, value in bases[0].items())
            and _date(reported_source.get("periodStart")) == inputs[INPUTS[0]]["periodStart"]
            and bool(reported_source.get("sourceReferences")))
        # A disagreement is explicit. Never replace a provider's FCF definition.
        rows.append({"reportObservationId": report["reportObservationId"], "periodEnd": report["periodEnd"],
            "periodStart": inputs[INPUTS[0]]["periodStart"].isoformat(),
            "operatingCashFlow": float(ocf), "reportedCapex": float(capex), "capexOutflow": float(abs(capex)),
            "calculatedFreeCashFlow": float(computed),
            "reportedFreeCashFlow": float(reported) if reported_comparable else None,
            "reportedFcfReconciliationState": "comparable" if reported_comparable else "missing-or-incompatible-basis",
            "reconciliationDifference": float(reported-computed) if reported_comparable else None})
    ocf_delta = observations[1][INPUTS[0]]["value"]-observations[0][INPUTS[0]]["value"]
    capex_delta = abs(observations[1][INPUTS[1]]["value"])-abs(observations[0][INPUTS[1]]["value"])
    unique = {json.dumps(ref, sort_keys=True): ref for ref in references}
    result.update(status="calculated", basis=bases[0], previous=rows[0], current=rows[1],
        operatingCashFlowContribution=float(ocf_delta), capitalExpenditureContribution=float(-capex_delta),
        freeCashFlowChange=float(ocf_delta-capex_delta),
        comparison="year-on-year" if (after-before).days >= 350 else "quarter-on-quarter",
        formula="delta(operatingCashFlow) - delta(abs(capitalExpenditure))",
        capexConvention="positive-outflow-magnitude", sourceReferences=[unique[key] for key in sorted(unique)],
        definition="operating-cash-flow-less-capital-expenditure")
    result["calculationId"] = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return result


def attach_cash_flow_bridges(evidence):
    """Add one closest comparable bridge; leave reported numbers untouched."""
    for current in evidence:
        raw = current.raw_payload
        candidates = sorted((item for item in evidence
            if item.raw_payload.get("frequency") == raw.get("frequency")
            and item.raw_payload.get("periodEnd", "") < raw.get("periodEnd", "")),
            key=lambda item: (item.raw_payload["periodEnd"], item.evidence_id), reverse=True)
        bridge = {"version": VERSION, "status": "unavailable", "reasons": ["comparable-report-missing"],
                  "authority": "accounting-calculation-only"}
        for previous in candidates:
            candidate = cash_flow_bridge(previous.raw_payload, raw)
            if bridge.get("reasons") == ["comparable-report-missing"]:
                bridge = candidate
            if candidate["status"] == "calculated":
                bridge = candidate
                break
        raw["derivedMetricEvidence"] = {**deepcopy(raw.get("financialReport", {}).get("derivedMetricEvidence", {})),
                                        "cashFlowBridge": bridge}
    return evidence
