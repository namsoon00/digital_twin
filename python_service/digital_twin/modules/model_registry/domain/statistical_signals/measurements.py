"""Exact, bounded measurements behind one market hypothesis contract.

These receipts describe predicates already evaluated by the governed scorer.
They neither reevaluate predicates nor grant investment actions.
"""

from copy import deepcopy
from datetime import date, datetime
from digital_twin.modules.model_registry.domain.statistical_signals.contracts import payload_hash
import math


MEASUREMENT_VERSION = "rule-source-measurements-v1"
CONTEXT_FIELDS = (
    "windowKey", "comparisonWindow", "startAt", "endAt", "sampleCount",
    "hasSufficientHistory", "currency", "proxySymbol", "stockChangeRate",
    "proxyChangeRate", "relativeReturnPct", "proxySourceAsOf", "timestampsAligned",
    "stockEvidenceUsable", "proxyEvidenceUsable", "rateSeriesId", "observationDate",
    "reportingPeriod", "comparisonPeriod", "comparisonBasis", "unit",
    "priceChangePct", "recentPriceChangePct", "drawdownFromPeakPct",
    "reboundFromTroughPct", "priceVelocityChangePct",
    "field", "levelType",
)
PROVENANCE_FIELDS = (
    "sourceAsOf", "observedAt", "source", "provider", "quoteSource",
    "freshnessStatus", "judgementEvidenceUsable", "dataState",
)


def _scalar(value):
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _value(value):
    if _scalar(value):
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 24 and all(_scalar(v) for v in value):
        return list(value)
    return None


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else None
    except (ValueError, TypeError):
        return None


def _known_by(value, cutoff):
    # Daily macro series retain their date precision; midnight is not invented.
    if isinstance(value, str) and len(value) == 10:
        try:
            return date.fromisoformat(value) <= cutoff.date()
        except ValueError:
            return False
    at = _stamp(value)
    return at is not None and at <= cutoff


def _hash(value):
    return payload_hash(value)


def condition_measurements(subject, condition, evidence_ids, index, filter_parts):
    """Copy the exact witness chosen by the scorer, not another matching row."""
    result = []
    for evidence_id in sorted(set(evidence_ids)):
        parts = evidence_id.split("|")
        if condition.kind == "subject_property":
            properties = dict(subject.properties or {})
            fields = [(condition.field, condition.operator, condition.value)]
            target_kind = "stock"
        elif len(parts) == 3:
            target_id = parts[0] if str(condition.direction or "out") == "in" else parts[2]
            target = (index.get("entities") or {}).get(target_id)
            if target is None:
                continue
            properties = dict(target.properties or {})
            fields = [filter_parts(k, v) for k, v in (condition.target_property_filters or {}).items()]
            target_kind = target.kind
            # Relation predicates are independent measurements of the edge.
            for relation in (index.get("relations") or {}).get(
                    (subject.entity_id, str(condition.direction or "out"), condition.relation_type), []):
                if "|".join((relation.source, relation.relation_type, relation.target)) != evidence_id:
                    continue
                edge = {**(relation.properties or {}), "weight": relation.weight}
                for key, expected in (condition.relation_property_filters or {}).items():
                    field, operator, expected = filter_parts(key, expected)
                    result.append(_row(condition, evidence_id, target_kind, "relation:" + field,
                                       operator, expected, edge.get(field), edge))
                break
        else:
            continue
        for field, operator, expected in fields:
            result.append(_row(condition, evidence_id, target_kind, field, operator,
                               expected, properties.get(field), properties))
    return result


def _row(condition, evidence_id, kind, field, operator, expected, actual, properties):
    expected = expected.get("value") if isinstance(expected, dict) else expected
    context = {k: deepcopy(properties[k]) for k in CONTEXT_FIELDS if k in properties and _scalar(properties[k])}
    provenance = {k: deepcopy(properties[k]) for k in PROVENANCE_FIELDS if k in properties and _scalar(properties[k])}
    if kind == "stock" and field in {
        "foreignNetVolume", "institutionNetVolume", "individualNetVolume",
        "smartMoneyNetVolume", "tradeStrength", "bidAskImbalance",
    }:
        # Stock-level quote clocks are not clocks for flow/orderbook samples.
        # Their separate relation witnesses carry the original provenance.
        provenance = {"clockStatus": "field-clock-not-preserved"}
    return {
        "conditionId": condition.condition_id, "conditionLabel": condition.description,
        "role": condition.role, "evidenceId": evidence_id, "targetKind": kind,
        "field": field, "operator": operator, "expectedValue": _value(expected),
        "observedValue": _value(actual), "context": context, "provenance": provenance,
    }


def measurement_packet(rule_id, symbol, release_id, feature_snapshot_id, cutoff, rows):
    ordered = sorted(rows, key=lambda r: (r["conditionId"], r["evidenceId"], r["field"]))
    # Refuse to certify a partial receipt; the original scorer result survives.
    complete = len(ordered) <= 128
    captured = deepcopy(ordered[:128])
    roots = sorted({r["evidenceId"] for r in ordered})
    return {
        "version": MEASUREMENT_VERSION, "hypothesisContractId": rule_id,
        "symbol": symbol, "modelReleaseId": release_id,
        "sourceFeatureSnapshotId": feature_snapshot_id, "knowledgeCutoffAt": cutoff,
        "complete": complete, "omittedMeasurementCount": max(0, len(ordered) - 128),
        "measurements": captured, "rootEvidenceIds": roots,
        # Same source observation shared by two contracts is not two samples.
        "sourceFingerprint": _hash({"symbol": symbol, "snapshot": feature_snapshot_id,
                                    "roots": roots}),
        "measurementFingerprint": _hash(captured),
    }


def verified_measurement_packet(packet, *, rule_id, symbol, release_id, feature_snapshot_id, cutoff):
    """Validate a frozen receipt without consulting current settings or data."""
    if not isinstance(packet, dict) or packet.get("version") != MEASUREMENT_VERSION:
        return {}
    expected = {"hypothesisContractId": rule_id, "symbol": symbol,
                "modelReleaseId": release_id, "sourceFeatureSnapshotId": feature_snapshot_id}
    if not all(expected.values()) or any(packet.get(k) != v for k, v in expected.items()):
        return {}
    at, packet_at = _stamp(cutoff), _stamp(packet.get("knowledgeCutoffAt"))
    if at is None or packet_at is None or at != packet_at or packet.get("complete") is not True:
        return {}
    rows = packet.get("measurements")
    if not isinstance(rows, list) or not rows or len(rows) > 128:
        return {}
    if any(not isinstance(r, dict) or not isinstance(r.get("evidenceId"), str) for r in rows):
        return {}
    roots = sorted({r["evidenceId"] for r in rows})
    if not roots or "" in roots or roots != packet.get("rootEvidenceIds"):
        return {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("conditionId") or not row.get("field"):
            return {}
        if (_value(row.get("observedValue")) != row.get("observedValue")
                or _value(row.get("expectedValue")) != row.get("expectedValue")):
            return {}
        identity = row.get("evidenceId", "")
        if not isinstance(identity, str):
            return {}
        if not (identity.startswith("stock:" + symbol + "#") or identity.startswith("stock:" + symbol + "|")
                or identity.endswith("|stock:" + symbol)):
            return {}
        provenance = row.get("provenance") or {}
        if not isinstance(provenance, dict) or not isinstance(row.get("context") or {}, dict):
            return {}
        observed = provenance.get("sourceAsOf") or provenance.get("observedAt")
        if observed and not _known_by(observed, at):
            return {}
        context = row.get("context") or {}
        other_at = context.get("proxySourceAsOf")
        if other_at and not _known_by(other_at, at):
            return {}
        if provenance.get("judgementEvidenceUsable") is False:
            return {}
    if packet.get("measurementFingerprint") != _hash(rows):
        return {}
    if packet.get("sourceFingerprint") != _hash({"symbol": symbol, "snapshot": feature_snapshot_id, "roots": roots}):
        return {}
    return deepcopy(packet)
