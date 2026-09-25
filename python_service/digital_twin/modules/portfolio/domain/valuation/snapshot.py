"""Deterministic valuation input and assessment snapshots.

The valuation calculator is deliberately pure.  This module binds its inputs
to the source clocks and revisions that were available to the caller, then
assigns stable identities to both the input bundle and the calculated result.
It does not read a database and it does not authorize an investment action.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping, Sequence

from digital_twin.modules.market_data.contracts import number


VALUATION_INPUT_BUNDLE_VERSION = "valuation-input-bundle-v1"
VALUATION_ASSESSMENT_VERSION = "valuation-assessment-v1"
VALUATION_NORMALIZATION_VERSION = "valuation-normalization-v1"
VALUATION_ASSUMPTION_VERSION = "valuation-assumptions-v1"


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _iso(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10] + "T00:00:00+00:00")
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if item not in (None, "")
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        rows = [_canonical(item) for item in value]
        if isinstance(value, (set, frozenset)):
            rows.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return rows
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 12)
    return value


def _fingerprint(prefix: str, value: object) -> str:
    payload = json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_references(row: Mapping[str, object]) -> list[Dict[str, object]]:
    references: Dict[tuple[str, str, str], Dict[str, object]] = {}

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            dataset_id = _text(value.get("datasetId"))
            revision_id = _text(value.get("revisionId"))
            observation_id = _text(value.get("observationId"))
            if dataset_id or revision_id:
                normalized = {
                    key: value.get(key)
                    for key in (
                        "datasetId", "providerId", "provider", "upstreamOrigin", "subjectKey",
                        "revisionId", "providerRevision", "payloadHash", "sourceSchemaVersion",
                        "sourceAsOf", "publishedAt", "availableAt", "fetchedAt", "observationId",
                    )
                    if value.get(key) not in (None, "")
                }
                key = (dataset_id, revision_id, observation_id)
                references[key] = normalized
            for item in value.values():
                if isinstance(item, (Mapping, list, tuple)):
                    visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(row.get("inputObservations") or [])
    visit(row.get("epsScenario") or {})
    visit(row.get("multipleBand") or {})
    visit(row.get("formulaTrace") or {})
    visit(row.get("sourceReferences") or [])
    return [references[key] for key in sorted(references)]


def _source_revision_vector(references: Iterable[Mapping[str, object]]) -> list[Dict[str, object]]:
    rows = []
    for reference in references:
        rows.append({
            "datasetId": _text(reference.get("datasetId")),
            "subjectKey": _text(reference.get("subjectKey")),
            "revisionId": _text(reference.get("revisionId")),
            "providerRevision": _text(reference.get("providerRevision")),
            "payloadHash": _text(reference.get("payloadHash")),
        })
    return sorted(rows, key=lambda item: (
        item["datasetId"], item["subjectKey"], item["revisionId"], item["providerRevision"], item["payloadHash"],
    ))


def _normalized_observations(row: Mapping[str, object]) -> list[Dict[str, object]]:
    result = []
    for item in row.get("inputObservations") or []:
        if not isinstance(item, Mapping):
            continue
        result.append({
            key: item.get(key)
            for key in (
                "observationId", "metric", "value", "base", "low", "high", "unit", "currency",
                "period", "horizon", "targetPeriodStart", "targetPeriodEnd", "basis", "epsBasis",
                "accountingBasis", "securityLine", "issuer", "priceAsOf", "earningsAsOf", "asOf",
                "provider", "upstreamOrigin", "validationState", "comparabilityState", "freshnessState",
            )
            if item.get(key) not in (None, "")
        })
    return sorted(result, key=lambda item: _text(item.get("observationId")))


def _assumptions(row: Mapping[str, object]) -> list[Dict[str, object]]:
    values = []
    explicit = row.get("assumptions") if isinstance(row.get("assumptions"), list) else []
    for item in explicit:
        if isinstance(item, Mapping):
            values.append(dict(item))
    if not values:
        multiple = row.get("multipleBand") if isinstance(row.get("multipleBand"), Mapping) else {}
        for key in ("low", "base", "high"):
            if number(multiple.get(key)):
                values.append({
                    "id": "target-multiple:" + key,
                    "value": number(multiple.get(key)),
                    "unit": "multiple",
                    "status": "observed" if multiple.get("evidenceBacked") else "reference-only",
                    "provenance": _text(multiple.get("basis")),
                })
    return sorted(values, key=lambda item: _text(item.get("id")))


def bind_valuation_snapshot(
    row: Mapping[str, object],
    *,
    symbol: object,
    security_line: object,
    valuation_currency: object,
    quote_value: object,
    quote_as_of: object,
    valuation_at: object,
    knowledge_cutoff_at: object,
    model_release_id: object,
    source_snapshot_id: object = "",
) -> Dict[str, object]:
    """Return ``row`` with deterministic bundle and assessment identities."""

    result = dict(row or {})
    references = _source_references(result)
    revision_vector = _source_revision_vector(references)
    observations = _normalized_observations(result)
    assumptions = _assumptions(result)
    exclusions = [
        dict(item) if isinstance(item, Mapping) else {"reason": _text(item)}
        for item in (
            result.get("modelExclusionReasons")
            or (result.get("formulaTrace") or {}).get("excludedObservations")
            or []
        )
        if item not in (None, "")
    ]
    valuation_clock = _iso(valuation_at or quote_as_of or result.get("valuationAsOf"))
    cutoff_clock = _iso(knowledge_cutoff_at or result.get("valuationAsOf") or quote_as_of)
    quote_clock = _iso(quote_as_of)
    missing_reproducibility = []
    if not valuation_clock:
        missing_reproducibility.append("valuation-clock-missing")
    if not cutoff_clock:
        missing_reproducibility.append("knowledge-cutoff-missing")
    if not quote_clock:
        missing_reproducibility.append("quote-clock-missing")
    if not observations:
        missing_reproducibility.append("normalized-observations-missing")
    if observations and not revision_vector:
        missing_reproducibility.append("exact-source-revisions-missing")

    material = {
        "contractVersion": VALUATION_INPUT_BUNDLE_VERSION,
        "subjectId": _text(symbol).upper(),
        "securityLine": _text(security_line or symbol).upper(),
        "valuationCurrency": _text(valuation_currency).upper(),
        "valuationAt": valuation_clock,
        "knowledgeCutoffAt": cutoff_clock,
        "quote": {"value": number(quote_value), "asOf": quote_clock},
        "normalizedObservations": observations,
        "exclusions": exclusions,
        "assumptions": assumptions,
        "modelReleaseId": _text(model_release_id),
        "calculationVersion": _text(result.get("modelVersion")),
        "normalizationVersion": VALUATION_NORMALIZATION_VERSION,
        "assumptionVersion": VALUATION_ASSUMPTION_VERSION,
    }
    material_fingerprint = _fingerprint("valuation-material:", material)
    audit = {
        **material,
        "sourceSnapshotId": _text(source_snapshot_id),
        "sourceRevisionVector": revision_vector,
        "sourceReferences": references,
        "reproducibilityGaps": sorted(set(missing_reproducibility)),
    }
    audit_fingerprint = _fingerprint("valuation-audit:", audit)
    bundle_id = "valuation-input-bundle:" + material_fingerprint.rsplit(":", 1)[-1][:32]
    reproducibility_state = "reproducible" if not missing_reproducibility else "partial"
    bundle = {
        **audit,
        "bundleId": bundle_id,
        "auditFingerprint": audit_fingerprint,
        "materialFingerprint": material_fingerprint,
        "reproducibilityState": reproducibility_state,
    }

    scenarios = [
        {"scenarioId": "downside", "valuePerShare": number(result.get("fairValueLow")), "currency": material["valuationCurrency"]},
        {"scenarioId": "base", "valuePerShare": number(result.get("fairValue") or result.get("fairValueBase")), "currency": material["valuationCurrency"]},
        {"scenarioId": "upside", "valuePerShare": number(result.get("fairValueHigh")), "currency": material["valuationCurrency"]},
    ]
    scenarios = [item for item in scenarios if item["valuePerShare"] > 0]
    quality_issues = [
        dict(item) for item in result.get("valuationQualityIssues") or [] if isinstance(item, Mapping)
    ]
    blocked_reasons = sorted(set(
        [_text(item.get("code") or item.get("reason")) for item in quality_issues]
        + [_text(item.get("reason")) for item in exclusions]
        + missing_reproducibility
    ) - {""})
    assessment_material = {
        "contractVersion": VALUATION_ASSESSMENT_VERSION,
        "bundleId": bundle_id,
        "modelId": _text(result.get("valuationModelId") or result.get("valuationMethod")),
        "modelVersion": _text(result.get("modelVersion")),
        "assumptionVersion": VALUATION_ASSUMPTION_VERSION,
        "calculationStatus": "calculated" if scenarios else "blocked",
        "inputEligibility": _text(result.get("valuationInputState")),
        "modelApprovalState": _text(result.get("approvalStatus")),
        "valuationDecisionEligible": bool(result.get("valuationDecisionEligible")) and reproducibility_state == "reproducible",
        "blockedReasons": blocked_reasons,
        "scenarios": scenarios,
        "formulaTrace": result.get("formulaTrace") if isinstance(result.get("formulaTrace"), Mapping) else {},
    }
    assessment_fingerprint = _fingerprint("valuation-assessment-material:", assessment_material)
    assessment = {
        **assessment_material,
        "assessmentId": "valuation-assessment:" + assessment_fingerprint.rsplit(":", 1)[-1][:32],
        "materialFingerprint": assessment_fingerprint,
        "inputEvidenceIds": sorted({
            _text(item.get("observationId")) for item in observations if _text(item.get("observationId"))
        }),
    }
    result.update({
        "valuationBundle": bundle,
        "valuationAssessment": assessment,
        "valuationBundleId": bundle_id,
        "valuationAssessmentId": assessment["assessmentId"],
        "valuationAuditFingerprint": audit_fingerprint,
        "valuationMaterialFingerprint": material_fingerprint,
        "valuationReproducibilityState": reproducibility_state,
        "valuationReproducibilityGaps": sorted(set(missing_reproducibility)),
        "valuationDecisionEligible": assessment["valuationDecisionEligible"],
    })
    return result


def valuation_snapshot_delta(previous: Mapping[str, object], current: Mapping[str, object]) -> Dict[str, object]:
    """Classify audit-only and material valuation changes without recomputing."""

    previous_material = _text(previous.get("valuationMaterialFingerprint"))
    current_material = _text(current.get("valuationMaterialFingerprint"))
    previous_audit = _text(previous.get("valuationAuditFingerprint"))
    current_audit = _text(current.get("valuationAuditFingerprint"))
    if not previous_material:
        state = "initial"
    elif previous_material != current_material:
        state = "material-change"
    elif previous_audit != current_audit:
        state = "lineage-only-change"
    else:
        state = "unchanged"
    return {
        "contractVersion": "valuation-snapshot-delta-v1",
        "state": state,
        "materialChange": state in {"initial", "material-change"},
        "auditChange": state != "unchanged",
        "previousAssessmentId": _text(previous.get("valuationAssessmentId")),
        "currentAssessmentId": _text(current.get("valuationAssessmentId")),
        "previousMaterialFingerprint": previous_material,
        "currentMaterialFingerprint": current_material,
    }


__all__ = [
    "VALUATION_ASSESSMENT_VERSION",
    "VALUATION_INPUT_BUNDLE_VERSION",
    "bind_valuation_snapshot",
    "valuation_snapshot_delta",
]
