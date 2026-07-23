import hashlib
import json
from typing import Dict

from .ontology_contracts import PortfolioOntology


VOLATILE_LIFECYCLE_KEYS = {
    "aboxSnapshotId",
    "aboxScopeId",
    "aboxScopeType",
    "activeTboxEntityCount",
    "activeTboxRelationCount",
    "asOf",
    "checkedAt",
    "fetchedAt",
    "firstObservedAt",
    "generatedAt",
    # Durations derived from the polling clock change continuously even when
    # the observed market fact has not changed. The corresponding discrete
    # freshness/data-state remains material and is what TypeDB rules consume.
    "elapsedHours",
    "elapsedMinutes",
    "elapsedSeconds",
    "freshnessAgeMinutes",
    "freshnessGateReason",
    "freshnessReason",
    "indicatorAsOf",
    "indicatorFetchedAt",
    "inferenceGenerationAt",
    "inferenceGenerationId",
    "isCurrent",
    "latencyReason",
    "materialFingerprint",
    "marketObservedAt",
    "marketSession",
    "marketSessionElapsedPct",
    "marketSessionLabel",
    "marketSessionLocalTime",
    "lastObservedAt",
    "lastFailureAt",
    "lastHealthyAt",
    "lastNonZeroAt",
    "lastSuccessAt",
    "lastUpdated",
    "observedAt",
    "projectionRunId",
    "persistenceMode",
    "scopeFingerprints",
    "scopeGenerationId",
    "scopeGenerationIds",
    "scopePlan",
    "scopedAboxManifestVersion",
    "snapshotId",
    "sourceAsOf",
    "sourceFetchedAt",
    "quoteMessage",
    "staleReason",
    "stateSince",
    "updatedAt",
    "validFrom",
    "validUntil",
    "worldviewManifestId",
}

_NORMALIZED_VOLATILE_LIFECYCLE_KEYS = {
    "".join(character for character in str(key).lower() if character.isalnum())
    for key in VOLATILE_LIFECYCLE_KEYS
}

EXCLUDED_VOLATILE_ENTITY_KINDS = {
    "runtime-metadata",
}


def material_graph_fingerprint(graph: PortfolioOntology) -> str:
    excluded_ids = {
        item.entity_id
        for item in graph.entities
        if str(item.kind or "") in EXCLUDED_VOLATILE_ENTITY_KINDS
    }
    payload = {
        "entities": sorted([
            {
                "id": item.entity_id,
                "kind": item.kind,
                "properties": stable_value(item.properties),
            }
            for item in graph.entities
            if item.entity_id not in excluded_ids
        ], key=lambda item: (str(item["kind"]), str(item["id"]))),
        "relations": sorted([
            {
                "source": item.source,
                "target": item.target,
                "type": item.relation_type,
                "weight": stable_value(item.weight),
                "properties": stable_value(item.properties),
            }
            for item in graph.relations
            if item.source not in excluded_ids and item.target not in excluded_ids
        ], key=lambda item: (str(item["type"]), str(item["source"]), str(item["target"]))),
        "evidence": sorted([
            {
                "id": item.evidence_id,
                "subject": item.subject,
                "kind": item.kind,
                "source": item.source,
                "summary": item.summary,
                "value": stable_value(item.value),
                "evidenceRole": stable_value(getattr(item, "evidence_role", "context")),
                "dataState": stable_value(getattr(item, "data_state", "partial")),
            }
            for item in graph.evidence
            if item.subject not in excluded_ids
        ], key=lambda item: (str(item["kind"]), str(item["id"]))),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def apply_material_graph_identity(
    graph: PortfolioOntology,
    account_id: str,
    fingerprint: str,
    world_id: str = "",
) -> str:
    identity_owner = str(world_id or account_id or "account")
    snapshot_id = "abox-material:" + hashlib.sha256(
        (identity_owner + "|" + str(fingerprint or "")).encode("utf-8")
    ).hexdigest()[:20]
    lifecycle = {
        "aboxSnapshotId": snapshot_id,
        "snapshotId": snapshot_id,
        "materialFingerprint": fingerprint,
    }
    for item in graph.entities:
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox":
            item.properties.update(lifecycle)
    for item in graph.relations:
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox":
            item.properties.update(lifecycle)
    for item in graph.evidence:
        if str((item.value or {}).get("ontologyBox") or "ABox") == "ABox":
            item.value.update(lifecycle)
    graph.worldview.update(lifecycle)
    return snapshot_id


def stable_value(value: object):
    if isinstance(value, dict):
        return {
            str(key): stable_value(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
            if not is_volatile_lifecycle_key(key)
        }
    if isinstance(value, (list, tuple, set)):
        rows = [stable_value(item) for item in value]
        return sorted(rows, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    if isinstance(value, float):
        return round(value, 8)
    return value


def is_volatile_lifecycle_key(key: object) -> bool:
    """Return whether a provider/runtime field is observation provenance.

    Providers mix camelCase and snake_case for the same timestamps. Treating
    both forms consistently keeps a polling clock from changing the material
    ABox fingerprint while retaining the timestamp for display and freshness
    checks outside the material payload.
    """

    text = str(key or "").strip()
    if text in VOLATILE_LIFECYCLE_KEYS:
        return True
    normalized = "".join(character for character in text.lower() if character.isalnum())
    return normalized in _NORMALIZED_VOLATILE_LIFECYCLE_KEYS


def active_material_fingerprint(metadata: Dict[str, object]) -> str:
    return str((metadata or {}).get("materialFingerprint") or "").strip()
