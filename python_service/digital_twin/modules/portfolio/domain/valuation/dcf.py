"""Pure, driver-based FCFF valuation.

The calculator accepts a fully versioned input bundle and returns calculation
facts.  It performs no I/O and never emits an investment action.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Dict, Mapping


DRIVER_DCF_VERSION = "driver-fcff-dcf-v2"
DRIVER_DCF_SENSITIVITY_VERSION = "driver-fcff-dcf-sensitivity-v2"
SUPPORTED_APPLICABILITY = {"non-financial-company", "operating-company"}


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _finite(value: object):
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_references(values) -> list[Dict[str, object]]:
    result = {}
    for item in values or []:
        if not isinstance(item, Mapping):
            continue
        dataset_id = _text(item.get("datasetId"))
        revision_id = _text(item.get("revisionId"))
        if not dataset_id or not revision_id:
            continue
        result[(dataset_id, revision_id)] = {
            key: item.get(key)
            for key in (
                "datasetId", "providerId", "subjectKey", "revisionId", "providerRevision",
                "payloadHash", "sourceAsOf", "fetchedAt",
            )
            if item.get(key) not in (None, "")
        }
    return [result[key] for key in sorted(result)]


def _blocked(inputs: Mapping[str, object], reasons: list[str], warnings: list[str] = None) -> Dict[str, object]:
    material = {
        "contractVersion": DRIVER_DCF_VERSION,
        "status": "blocked",
        "symbol": _text(inputs.get("symbol")).upper(),
        "blockedReasons": sorted(set(reasons)),
        "warnings": sorted(set(warnings or [])),
    }
    digest = _digest({"inputs": inputs, "result": material})
    return {
        **material,
        "assessmentId": "driver-dcf-assessment:" + digest[:32],
        "materialFingerprint": "driver-dcf-material:" + digest,
        "valuationDecisionEligible": False,
        "referenceOnly": True,
        "scenarios": [],
        "formulaTrace": {},
    }


def calculate_driver_dcf(inputs: Mapping[str, object]) -> Dict[str, object]:
    """Calculate an end-of-period FCFF DCF from explicit yearly drivers."""

    source = dict(inputs or {})
    reasons = []
    warnings = []
    symbol = _text(source.get("symbol")).upper()
    currency = _text(source.get("currency")).upper()
    applicability = _text(source.get("modelApplicability")).lower()
    wacc_pct = _finite(source.get("waccPct"))
    terminal_growth_pct = _finite(source.get("terminalGrowthPct"))
    shares = _finite(source.get("dilutedShares"))
    cash = _finite(source.get("cash")) or 0.0
    non_operating_assets = _finite(source.get("nonOperatingAssets")) or 0.0
    debt = _finite(source.get("debt")) or 0.0
    preferred = _finite(source.get("preferredEquity")) or 0.0
    non_controlling = _finite(source.get("nonControllingInterest")) or 0.0
    sbc_policy = _text(source.get("sbcPolicy"))
    projection_rows = [dict(item) for item in source.get("projectionYears") or [] if isinstance(item, Mapping)]
    references = _source_references(source.get("sourceReferences") or [])
    financial_evidence = dict(source.get("financialEvidence") or {}) if isinstance(source.get("financialEvidence"), Mapping) else {}
    exposure_readiness = dict(source.get("exposureReadiness") or {}) if isinstance(source.get("exposureReadiness"), Mapping) else {}
    assumption_review = dict(source.get("assumptionReview") or {}) if isinstance(source.get("assumptionReview"), Mapping) else {}

    if not symbol:
        reasons.append("symbol-missing")
    if not currency:
        reasons.append("valuation-currency-missing")
    if applicability not in SUPPORTED_APPLICABILITY:
        reasons.append("model-inapplicable")
    if wacc_pct is None or wacc_pct <= 0 or wacc_pct >= 100:
        reasons.append("invalid-wacc")
    if terminal_growth_pct is None:
        reasons.append("terminal-growth-missing")
    elif terminal_growth_pct <= -100:
        reasons.append("invalid-terminal-growth")
    if wacc_pct is not None and terminal_growth_pct is not None and wacc_pct <= terminal_growth_pct:
        reasons.append("wacc-not-greater-than-terminal-growth")
    if shares is None or shares <= 0:
        reasons.append("invalid-diluted-shares")
    if not 1 <= len(projection_rows) <= 15:
        reasons.append("projection-horizon-invalid")
    if not sbc_policy:
        reasons.append("sbc-policy-missing")
    if not references:
        warnings.append("exact-source-revisions-missing")
    if not financial_evidence.get("officialDecisionReady"):
        warnings.append("official-financial-evidence-incomplete")

    normalized_rows = []
    for index, row in enumerate(projection_rows, start=1):
        year = int(_finite(row.get("year")) or index)
        revenue = _finite(row.get("revenue"))
        margin = _finite(row.get("ebitMarginPct"))
        tax = _finite(row.get("taxRatePct"))
        depreciation = _finite(row.get("depreciationAmortization"))
        capex = _finite(row.get("capitalExpenditure"))
        delta_nwc = _finite(row.get("changeInWorkingCapital"))
        period_fraction = _finite(row.get("periodFraction"))
        period_fraction = 1.0 if period_fraction is None else period_fraction
        row_currency = _text(row.get("currency") or currency).upper()
        if revenue is None or revenue < 0:
            reasons.append("year-" + str(year) + "-invalid-revenue")
        if margin is None:
            reasons.append("year-" + str(year) + "-ebit-margin-missing")
        if tax is None or tax < 0 or tax > 100:
            reasons.append("year-" + str(year) + "-invalid-tax-rate")
        if depreciation is None:
            reasons.append("year-" + str(year) + "-depreciation-missing")
        if capex is None:
            reasons.append("year-" + str(year) + "-capex-missing")
        if delta_nwc is None:
            reasons.append("year-" + str(year) + "-working-capital-change-missing")
        if period_fraction <= 0 or period_fraction > 1:
            reasons.append("year-" + str(year) + "-invalid-period-fraction")
        if row_currency != currency:
            reasons.append("year-" + str(year) + "-currency-mismatch")
        normalized_rows.append({
            "year": year,
            "revenue": revenue,
            "ebitMarginPct": margin,
            "taxRatePct": tax,
            "depreciationAmortization": depreciation,
            "capitalExpenditure": capex,
            "changeInWorkingCapital": delta_nwc,
            "periodFraction": period_fraction,
            "currency": row_currency,
        })

    if reasons:
        return _blocked(source, reasons, warnings)

    wacc = wacc_pct / 100.0
    terminal_growth = terminal_growth_pct / 100.0
    cumulative_period = 0.0
    explicit_pv = 0.0
    trace_rows = []
    for row in normalized_rows:
        cumulative_period += row["periodFraction"]
        ebit = row["revenue"] * row["ebitMarginPct"] / 100.0
        nopat = ebit * (1.0 - row["taxRatePct"] / 100.0)
        fcff = nopat + row["depreciationAmortization"] - row["capitalExpenditure"] - row["changeInWorkingCapital"]
        discount_factor = (1.0 + wacc) ** cumulative_period
        present_value = fcff / discount_factor
        explicit_pv += present_value
        trace_rows.append({
            **row,
            "ebit": round(ebit, 8),
            "nopat": round(nopat, 8),
            "fcff": round(fcff, 8),
            "discountExponent": round(cumulative_period, 8),
            "discountFactor": round(discount_factor, 10),
            "presentValue": round(present_value, 8),
        })

    terminal_fcff = trace_rows[-1]["fcff"] * (1.0 + terminal_growth)
    terminal_value = terminal_fcff / (wacc - terminal_growth)
    terminal_present_value = terminal_value / ((1.0 + wacc) ** cumulative_period)
    enterprise_value = explicit_pv + terminal_present_value
    equity_value = enterprise_value + cash + non_operating_assets - debt - preferred - non_controlling
    per_share = equity_value / shares
    terminal_share_pct = terminal_present_value / enterprise_value * 100.0 if enterprise_value else 0.0
    if equity_value <= 0 or per_share <= 0:
        reasons.append("non-positive-equity-value")
    max_terminal_share = _finite(source.get("maxTerminalValueSharePct"))
    max_terminal_share = 80.0 if max_terminal_share is None else max_terminal_share
    if terminal_share_pct > max_terminal_share:
        warnings.append("terminal-value-dependence-exceeds-policy")

    assumptions = [dict(item) for item in source.get("assumptions") or [] if isinstance(item, Mapping)]
    unapproved_assumptions = [
        _text(item.get("id")) or "unnamed-assumption"
        for item in assumptions
        if _text(item.get("status")).lower() not in {"observed", "verified", "approved", "user-approved"}
    ]
    if unapproved_assumptions:
        warnings.append("unapproved-assumptions-present")
    approval = _text(source.get("modelApprovalState")).lower()
    decision_eligible = bool(
        not reasons
        and references
        and financial_evidence.get("officialDecisionReady") is True
        and not unapproved_assumptions
        and approval in {"qualified", "approved", "limited-approved"}
        and terminal_share_pct <= max_terminal_share
    )
    status = "calculated" if not reasons else "blocked"
    material = {
        "contractVersion": DRIVER_DCF_VERSION,
        "status": status,
        "symbol": symbol,
        "currency": currency,
        "valuationAt": _text(source.get("valuationAt")),
        "knowledgeCutoffAt": _text(source.get("knowledgeCutoffAt")),
        "modelApplicability": applicability,
        "modelApprovalState": approval or "unapproved",
        "waccPct": round(wacc_pct, 8),
        "terminalGrowthPct": round(terminal_growth_pct, 8),
        "explicitForecastPresentValue": round(explicit_pv, 8),
        "terminalFcff": round(terminal_fcff, 8),
        "terminalValue": round(terminal_value, 8),
        "terminalPresentValue": round(terminal_present_value, 8),
        "terminalValueSharePct": round(terminal_share_pct, 4),
        "enterpriseValue": round(enterprise_value, 8),
        "equityBridge": {
            "cash": cash,
            "nonOperatingAssets": non_operating_assets,
            "debt": debt,
            "preferredEquity": preferred,
            "nonControllingInterest": non_controlling,
        },
        "equityValue": round(equity_value, 8),
        "dilutedShares": shares,
        "valuePerShare": round(per_share, 8),
        "sbcPolicy": sbc_policy,
        "sourceReferences": references,
        "financialEvidence": financial_evidence,
        "exposureReadiness": exposure_readiness,
        "assumptionReview": assumption_review,
        "assumptions": assumptions,
        "projectionYears": trace_rows,
        "blockedReasons": sorted(set(reasons)),
        "warnings": sorted(set(warnings)),
        "valuationDecisionEligible": decision_eligible,
        "referenceOnly": not decision_eligible,
    }
    digest = _digest(material)
    return {
        **material,
        "assessmentId": "driver-dcf-assessment:" + digest[:32],
        "materialFingerprint": "driver-dcf-material:" + digest,
        "scenarios": [{
            "scenarioId": _text(source.get("scenarioId")) or "base",
            "valuePerShare": round(per_share, 8),
            "currency": currency,
            "assumptionIds": [_text(item.get("id")) for item in assumptions if _text(item.get("id"))],
        }] if not reasons else [],
        "formulaTrace": {
            "formula": "FCFF = EBIT * (1 - tax rate) + D&A - capex - change in NWC",
            "discountConvention": "end-of-period-with-explicit-stub-fractions",
            "projectionYears": trace_rows,
            "terminalFormula": "next-period FCFF / (WACC - terminal growth)",
            "equityBridge": material["equityBridge"],
        },
    }


def calculate_driver_dcf_sensitivity(
    inputs: Mapping[str, object],
    *,
    wacc_deltas_pct=(-1.0, 0.0, 1.0),
    terminal_growth_deltas_pct=(-1.0, 0.0, 1.0),
) -> Dict[str, object]:
    """Evaluate a bounded WACC/terminal-growth surface with the same DCF core."""

    source = dict(inputs or {})
    base_wacc = _finite(source.get("waccPct"))
    base_terminal = _finite(source.get("terminalGrowthPct"))
    rows = []
    reasons = []
    if base_wacc is None:
        reasons.append("wacc-missing")
    if base_terminal is None:
        reasons.append("terminal-growth-missing")
    if reasons:
        material = {
            "contractVersion": DRIVER_DCF_SENSITIVITY_VERSION,
            "status": "blocked",
            "blockedReasons": reasons,
            "independentEvidence": False,
        }
        return {**material, "sensitivityId": "driver-dcf-sensitivity:" + _digest(material)[:32]}

    for wacc_delta in wacc_deltas_pct:
        for terminal_delta in terminal_growth_deltas_pct:
            wacc = base_wacc + float(wacc_delta)
            terminal_growth = base_terminal + float(terminal_delta)
            result = calculate_driver_dcf({
                **source,
                "waccPct": wacc,
                "terminalGrowthPct": terminal_growth,
            })
            rows.append({
                "waccPct": round(wacc, 8),
                "terminalGrowthPct": round(terminal_growth, 8),
                "valuePerShare": result.get("valuePerShare") if result.get("status") == "calculated" else None,
                "terminalValueSharePct": result.get("terminalValueSharePct") if result.get("status") == "calculated" else None,
                "status": result.get("status"),
                "blockedReasons": list(result.get("blockedReasons") or []),
                "isBase": math.isclose(float(wacc_delta), 0.0) and math.isclose(float(terminal_delta), 0.0),
            })
    valid_values = [float(item["valuePerShare"]) for item in rows if item.get("valuePerShare") is not None]
    material = {
        "contractVersion": DRIVER_DCF_SENSITIVITY_VERSION,
        "status": "calculated" if valid_values else "blocked",
        "baseWaccPct": round(base_wacc, 8),
        "baseTerminalGrowthPct": round(base_terminal, 8),
        "waccDeltasPct": [float(item) for item in wacc_deltas_pct],
        "terminalGrowthDeltasPct": [float(item) for item in terminal_growth_deltas_pct],
        "rows": rows,
        "validScenarioCount": len(valid_values),
        "valueRange": {
            "low": round(min(valid_values), 8) if valid_values else None,
            "high": round(max(valid_values), 8) if valid_values else None,
            "currency": _text(source.get("currency")).upper(),
        },
        "interpretation": "WACC와 장기성장률만 바꾼 조건부 민감도이며 독립적인 적정가 근거가 아닙니다.",
        "independentEvidence": False,
    }
    digest = _digest(material)
    return {
        **material,
        "sensitivityId": "driver-dcf-sensitivity:" + digest[:32],
        "materialFingerprint": "driver-dcf-sensitivity-material:" + digest,
    }


def driver_dcf_valuation_row(position, external_signals: Mapping[str, object], settings: Mapping[str, object]) -> Dict[str, object]:
    """Registry adapter; only emits a row for an explicit symbol input bundle."""

    del settings
    symbol = _text(getattr(position, "symbol", "")).upper()
    bundles = external_signals.get("driverDcfInputs") if isinstance(external_signals.get("driverDcfInputs"), Mapping) else {}
    source = bundles.get(symbol) if isinstance(bundles.get(symbol), Mapping) else {}
    if not source:
        return {}
    result = calculate_driver_dcf({**source, "symbol": symbol})
    if result.get("status") != "calculated":
        return {
            "assumptionKey": symbol + ":driver-dcf",
            "symbol": symbol,
            "label": (getattr(position, "name", "") or symbol) + " 사업 변수 DCF",
            "provider": "Orbit Alpha deterministic DCF",
            "source": "driver-dcf",
            "valuationMethod": "driver-fcff-dcf",
            "formula": "FCFF의 명시적 기간과 terminal value를 WACC로 할인",
            "modelVersion": DRIVER_DCF_VERSION,
            "valuationModelId": "driver-fcff-dcf",
            "valuationModelFamily": "driver-dcf",
            "valuationCurrency": _text(source.get("currency") or getattr(position, "currency", "")),
            "valuationInputState": "unavailable",
            "valuationDataState": "unavailable",
            "valuationReliabilityState": "unavailable",
            "valuationDecisionEligible": False,
            "valuationReferenceOnly": True,
            "missingInputs": list(result.get("blockedReasons") or []),
            "modelExclusionReasons": list(result.get("blockedReasons") or []),
            "assumptionReview": dict(source.get("assumptionReview") or {}),
            "financialEvidence": dict(source.get("financialEvidence") or {}),
            "exposureReadiness": dict(source.get("exposureReadiness") or {}),
            "dcfAssessment": result,
        }
    value = float(result["valuePerShare"])
    current = _finite(getattr(position, "current_price", 0.0)) or 0.0
    sensitivity = calculate_driver_dcf_sensitivity(source)
    if current > 0:
        from digital_twin.modules.portfolio.domain.valuation.reverse_dcf import solve_implied_revenue_growth

        bracket = source.get("reverseGrowthSearchBracketPct")
        lower = bracket[0] if isinstance(bracket, list) and len(bracket) == 2 else -50.0
        upper = bracket[1] if isinstance(bracket, list) and len(bracket) == 2 else 100.0
        implied_expectations = solve_implied_revenue_growth(
            source,
            target_price=current,
            lower_growth_pct=lower,
            upper_growth_pct=upper,
        )
    else:
        implied_expectations = {
            "contractVersion": "reverse-dcf-growth-solver-v1",
            "status": "blocked",
            "blockedReasons": ["invalid-target-price"],
        }
    assumption_review = dict(source.get("assumptionReview") or {}) if isinstance(source.get("assumptionReview"), Mapping) else {}
    assumption_review_state = _text(assumption_review.get("state")) or ("complete" if result["valuationDecisionEligible"] else "required")
    implied_expectations = {
        **implied_expectations,
        "inputBundleId": source.get("inputBundleId"),
        "dcfAssessmentId": result.get("assessmentId"),
        "modelApprovalState": result.get("modelApprovalState"),
        "assumptionReviewState": assumption_review_state,
        "assumptionReview": assumption_review,
        "financialEvidence": dict(result.get("financialEvidence") or {}),
        "exposureReadiness": dict(result.get("exposureReadiness") or {}),
        "sourceBacked": bool(result.get("sourceReferences")),
        "officialFinancialsReady": bool((result.get("financialEvidence") or {}).get("officialDecisionReady")),
    }
    dcf_assessment = {
        **result,
        "sensitivity": sensitivity,
        "impliedExpectations": implied_expectations,
    }
    return {
        "assumptionKey": symbol + ":driver-dcf",
        "symbol": symbol,
        "label": (getattr(position, "name", "") or symbol) + " 사업 변수 DCF",
        "provider": "Orbit Alpha deterministic DCF",
        "source": "driver-dcf",
        "valuationMethod": "driver-fcff-dcf",
        "formula": "FCFF의 명시적 기간과 terminal value를 WACC로 할인",
        "modelVersion": DRIVER_DCF_VERSION,
        "valuationModelId": "driver-fcff-dcf",
        "valuationModelFamily": "driver-dcf",
        "valuationCurrency": result["currency"],
        "valuationAsOf": result.get("valuationAt") or source.get("valuationAt"),
        "valuationFreshnessStatus": "fresh" if result.get("valuationAt") else "unknown",
        "currentPrice": current,
        "fairValueLow": value,
        "fairValue": value,
        "fairValueBase": value,
        "fairValueHigh": value,
        "valuationInputState": "sufficient",
        "valuationDataState": "sufficient" if result["valuationDecisionEligible"] else "partial",
        "valuationReliabilityState": "sufficient" if result["valuationDecisionEligible"] else "partial",
        "valuationDecisionEligible": bool(result["valuationDecisionEligible"]),
        "valuationReferenceOnly": not bool(result["valuationDecisionEligible"]),
        "valuationReferenceReason": "" if result["valuationDecisionEligible"] else "DCF 입력 또는 모델 승격 조건이 충족되지 않아 참고용입니다.",
        "approvalStatus": result.get("modelApprovalState"),
        "assumptionVersion": source.get("assumptionVersion"),
        "assumptionReviewState": assumption_review_state,
        "assumptionReview": assumption_review,
        "financialEvidence": dict(result.get("financialEvidence") or {}),
        "exposureReadiness": dict(result.get("exposureReadiness") or {}),
        "officialFinancialsReady": bool((result.get("financialEvidence") or {}).get("officialDecisionReady")),
        "sourceBacked": bool(result.get("sourceReferences")),
        "inputBundleId": source.get("inputBundleId"),
        "modelWarnings": list(result.get("warnings") or []),
        "periodCompatible": True,
        "perShare": True,
        "inputObservations": [
            {
                "observationId": "dcf-year:" + str(item["year"]),
                "metric": "fcff",
                "value": item["fcff"],
                "period": str(item["year"]),
                "currency": result["currency"],
                "sourceReferences": result["sourceReferences"],
                "validationState": (
                    "verified-official"
                    if (result.get("financialEvidence") or {}).get("officialDecisionReady")
                    else "verified-secondary" if result["sourceReferences"] else "unverified"
                ),
            }
            for item in result["projectionYears"]
        ],
        "sourceReferences": result["sourceReferences"],
        "assumptions": result["assumptions"],
        "formulaTrace": result["formulaTrace"],
        "sensitivity": sensitivity,
        "impliedExpectations": implied_expectations,
        "dcfAssessment": dcf_assessment,
        "sourceReason": "사업 변수에서 계산한 FCFF와 명시적 WACC·terminal 가정의 조건부 가치입니다.",
        "preferredValuationMetric": "사업 변수 FCFF DCF",
        "minimumMarginOfSafetyPct": 15.0,
        "marginOfSafetyPct": round((value / current - 1.0) * 100.0, 2) if current else 0.0,
    }


__all__ = [
    "DRIVER_DCF_SENSITIVITY_VERSION",
    "DRIVER_DCF_VERSION",
    "calculate_driver_dcf",
    "calculate_driver_dcf_sensitivity",
    "driver_dcf_valuation_row",
]
