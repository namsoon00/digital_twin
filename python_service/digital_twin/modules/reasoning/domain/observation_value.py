"""Bounded, exact views of graph-authored valuation calculations.

No model, source or approval is created here. A price comparison never renews
the business assumptions or grants an investment action.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math

from .observation_evidence import content_hash, canonical_json, EvidenceContractError

VERSION = "company-value-evidence-v1"
DIAGNOSTIC_VERSION = "company-value-evidence-v2-diagnostics"
KINDS = {"valuation-assessment", "valuation-input-bundle", "fair-value-estimate"}
BASELINE_MAX_BYTES = 32000
PAYLOAD_FIELDS = {
    "valuation-assessment": ("contractVersion", "assessmentId", "bundleId", "modelId", "modelVersion",
        "assumptionVersion", "calculationStatus", "inputEligibility", "modelApprovalState",
        "valuationDecisionEligible", "blockedReasons", "scenarios", "inputEvidenceIds", "sensitivity", "impliedExpectations"),
    "valuation-input-bundle": ("contractVersion", "bundleId", "subjectId", "securityLine", "valuationCurrency",
        "valuationAt", "knowledgeCutoffAt", "modelReleaseId", "calculationVersion", "assumptionVersion",
        "reproducibilityState", "reproducibilityGaps", "assumptions", "normalizedObservations",
        "sourceReferences", "sourceRevisionVector"),
    "fair-value-estimate": (),
}
IMPLIED_FIELDS = ("contractVersion", "status", "solverId", "dcfAssessmentId", "inputBundleId", "freeVariable",
    "impliedEbitMarginPct", "observedEbitMarginPct", "marginExpansionRequiredPctPoints", "forwardValuePerShare",
    "targetPrice", "quoteAsOf", "residual", "tolerance", "sourceBacked", "officialFinancialsReady",
    "independentEvidence", "assumptionReviewState", "modelApprovalState", "interpretation", "fixedAssumptions",
    "exposureReadiness")


def value_view(fact, *, diagnostics=False):
    if fact.get("kind") not in KINDS:
        raise EvidenceContractError("unsupported company value view")
    result = deepcopy(fact)
    payload = result.pop("payload", {})
    result["payload"] = {key: deepcopy(payload[key]) for key in PAYLOAD_FIELDS[fact["kind"]] if key in payload}
    if diagnostics and fact["kind"] == "valuation-input-bundle" and "exclusions" in payload:
        result["payload"]["exclusions"] = deepcopy(payload["exclusions"])
    nested_omissions = {}
    if fact["kind"] == "valuation-assessment" and isinstance(payload.get("impliedExpectations"), dict):
        implied = payload["impliedExpectations"]
        # Solver iterations and duplicated scenario input trees remain in the
        # source artifact, not every AI prompt. Preserve exact results/limits.
        result["payload"]["impliedExpectations"] = {key: deepcopy(implied[key]) for key in IMPLIED_FIELDS if key in implied}
        nested_omissions["impliedExpectations"] = sorted(set(implied) - set(result["payload"]["impliedExpectations"]))
    result["evidenceView"] = {"version": DIAGNOSTIC_VERSION if diagnostics else VERSION, "sourceFactHash": content_hash(fact),
        "omittedPayloadFields": sorted(set(payload) - set(result["payload"])),
        "omittedNestedFields": nested_omissions,
        "authority": "exact-field-projection", "omissionMeaning": "not-selected-not-missing"}
    return result


def value_baseline(facts, max_bytes=BASELINE_MAX_BYTES, *, diagnostics=False):
    """Reserve complete assessment/input/estimate groups, with explicit omissions."""
    rows = sorted(facts, key=lambda f: (f.get("valuationDecisionEligible") is not True, f["id"]))
    selected, views, omitted, used = [], {}, [], 0
    assessments = [f for f in rows if f.get("kind") == "valuation-assessment"]
    for assessment in assessments:
        aid, bid = assessment.get("valuationAssessmentId"), assessment.get("valuationBundleId")
        group = [assessment] + [f for f in rows if
            (bid and f.get("kind") == "valuation-input-bundle" and f.get("valuationBundleId") == bid) or
            (aid and f.get("kind") == "fair-value-estimate" and f.get("valuationAssessmentId") == aid)]
        projected = [value_view(f, diagnostics=diagnostics) for f in group if f["id"] not in views]
        size = sum(len(canonical_json(f).encode()) for f in projected)
        if len(selected) >= 2 or used + size > max_bytes:
            omitted.append({"factId": assessment["id"], "reason": "company-value-budget"})
            continue
        selected.append(assessment["id"])
        views.update((f["id"], f) for f in projected)
        used += size
    return {"version": DIAGNOSTIC_VERSION if diagnostics else VERSION, "factIds": sorted(views), "views": views,
        "omitted": omitted, "bytes": used, "availableAssessments": len(assessments)}


def instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Financial source dates often have day precision; use the source
        # snapshot convention (UTC), never the machine's local timezone.
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def company_value_context(packet):
    facts = [f for f in packet.get("facts", []) if f.get("symbol") == packet.get("symbol")]
    cutoff = instant(packet.get("capturedAt"))
    models = []
    for fact in facts:
        if fact.get("kind") != "valuation-assessment":
            continue
        assessment = fact.get("payload") or {}
        bid = fact.get("valuationBundleId")
        bundle_fact = next((f for f in facts if bid and f.get("kind") == "valuation-input-bundle"
                            and f.get("valuationBundleId") == bid), {})
        bundle = bundle_fact.get("payload") or {}
        estimate = next((f for f in facts if f.get("kind") == "fair-value-estimate"
                         and f.get("valuationAssessmentId") == fact.get("valuationAssessmentId")), {})
        reasons = list(assessment.get("blockedReasons") or [])
        currency = bundle.get("valuationCurrency", "")
        scenarios = assessment.get("scenarios") or []
        by_scenario = {s.get("scenarioId"): s.get("valuePerShare") for s in scenarios}
        values = [by_scenario.get(key) for key in ("downside", "base", "upside")]
        if (len(scenarios) != 3 or not all(positive(v) for v in values)
                or values != sorted(values) or not currency or any(s.get("currency") != currency for s in scenarios)):
            reasons.append("scenario-range-unavailable")
        if not bundle_fact or bundle.get("bundleId") != assessment.get("bundleId") or bundle.get("subjectId") != packet["symbol"]:
            reasons.append("input-lineage-unavailable")
        if bundle.get("reproducibilityState") != "reproducible":
            reasons.append("source-revisions-incomplete")
        clocks = [instant(bundle.get(key)) for key in ("valuationAt", "knowledgeCutoffAt")]
        if not cutoff or not all(clocks) or any(clock > cutoff for clock in clocks if clock):
            reasons.append("valuation-clock-unavailable")
        if (fact.get("valuationDecisionEligible") is not True or assessment.get("valuationDecisionEligible") is not True
                or estimate.get("valuationDecisionEligible") is not True
                or assessment.get("calculationStatus") != "calculated"):
            reasons.append("valuation-not-admitted")
        if any(f.get("judgementEvidenceUsable") is False for f in (fact, bundle_fact, estimate)):
            reasons.append("source-not-admitted")
        if not estimate:
            reasons.append("estimate-lineage-unavailable")
        elif estimate.get("perShare") is not True or estimate.get("valuationCurrency") != currency:
            reasons.append("per-share-currency-unconfirmed")
        for reference in bundle.get("sourceReferences", []):
            if any(instant(reference.get(key)) and cutoff and instant(reference[key]) > cutoff
                   for key in ("publishedAt", "availableAt", "sourceAsOf", "fetchedAt")):
                reasons.append("future-source-reference")
        if estimate and all(positive(v) for v in values) and any(estimate.get(key) != value for key, value in
                zip(("fairValueLow", "fairValueBase", "fairValueHigh"), values)):
            reasons.append("estimate-calculation-mismatch")
        eligible = not reasons
        basis = {"modelId": assessment.get("modelId"), "modelVersion": assessment.get("modelVersion"),
            "currency": currency, "scenarios": scenarios, "assumptions": bundle.get("assumptions", []),
            "inputs": [{k: v for k, v in row.items() if k not in {"observationId", "priceAsOf"}}
                       for row in bundle.get("normalizedObservations", []) if row.get("metric") not in {"currentPrice", "price"}],
            "eligible": eligible, "blockedReasons": sorted(set(reasons))}
        models.append({"assessmentFactId": fact["id"], "inputFactId": bundle_fact.get("id", ""),
            "estimateFactId": estimate.get("id", ""), "modelId": assessment.get("modelId", ""),
            "modelVersion": assessment.get("modelVersion", ""), "status": "available" if eligible else "blocked",
            "currency": currency, "valuationAt": bundle.get("valuationAt", ""),
            "assumptions": [{k: deepcopy(row[k]) for k in ("id", "value", "unit", "status", "reviewState", "evidenceClass") if k in row}
                            for row in bundle.get("assumptions", [])[:12]],
            "omittedAssumptions": max(0, len(bundle.get("assumptions", [])) - 12),
            "knowledgeCutoffAt": bundle.get("knowledgeCutoffAt", ""), "basisFingerprint": content_hash(basis),
            "scenarios": deepcopy(scenarios) if eligible else [], "blockedReasons": sorted(set(reasons))})
    from .observation_evidence import quote_use_assessment
    uses = {r["evidenceId"]: r for r in quote_use_assessment(packet)}
    quote = next((f for f in facts if f.get("kind") == "stock" and positive(f.get("currentPrice"))
                  and uses.get(f["id"], {}).get("datedReferenceEligible")), {})
    for model in models:
        model["priceComparison"] = {"status": "unavailable"}
        if model["status"] != "available" or not quote or quote.get("currency") != model["currency"]:
            continue
        price = quote["currentPrice"]
        low, base, high = [s["valuePerShare"] for key in ("downside", "base", "upside")
                           for s in model["scenarios"] if s["scenarioId"] == key]
        model["priceComparison"] = {"status": "source-dated", "quoteFactId": quote["id"],
            "price": price, "sourceAsOf": quote.get("sourceAsOf") or quote.get("asOf"),
            "baseDifferencePct": round((base / price - 1) * 100, 2),
            "position": "below-range" if price < low else "above-range" if price > high else "within-range"}
    return {"version": VERSION, "models": models, "status": "available" if any(m["status"] == "available" for m in models) else
            "blocked" if models else "unavailable", "authority": "existing-calculation-explanation-only"}
