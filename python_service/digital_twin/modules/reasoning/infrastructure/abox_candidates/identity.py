"""Canonical graph-row storage identities and material fingerprints."""

import hashlib
import json
from typing import Dict

from digital_twin.domain.ontology_projection_fingerprint import stable_value
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object


def relation_row_id(row: Dict[str, object]) -> str:
    seed = "|".join([
        str(row.get("source") or ""),
        str(row.get("type") or ""),
        str(row.get("target") or ""),
        str(row.get("ontologyBox") or ""),
        str(row.get("worldId") or ""),
        str(row.get("snapshotId") or row.get("aboxSnapshotId") or ""),
        str(row.get("ruleId") or ""),
    ])
    return "ontology-assertion:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


CURRENT_STATE_STORAGE_ONLY_KEYS = {
    "aboxSnapshotId",
    "logicalScopeGenerationId",
    "manifestId",
    "physicalGenerationId",
    "physicalStateMode",
    "projectionRunId",
    "scopeGenerationId",
    "snapshotId",
    "worldviewManifestId",
}


def ontology_row_content_fingerprint(
    row: Dict[str, object],
    owner_kind: str,
) -> str:
    """Hash rule-visible row content without polling lifecycle metadata.

    Scope and row fingerprints share one materiality boundary. Advancing only
    an observation timestamp or market-session clock keeps the current fact
    reusable, while discrete freshness/data states and business values remain
    material.
    """

    values = dict(row or {})
    properties = json_object(values.get("propertiesJson"))
    payload = stable_value({
        str(key): value
        for key, value in sorted(values.items(), key=lambda pair: str(pair[0]))
        if str(key) not in CURRENT_STATE_STORAGE_ONLY_KEYS
        and str(key) not in {
            "contentFingerprint",
            "propertiesJson",
            "sourceStorageId",
            "targetStorageId",
            "updatedAt",
        }
    })
    payload["properties"] = stable_value(properties)
    payload["ownerKind"] = str(owner_kind or "row")
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def ontology_storage_id(row: Dict[str, object], canonical_id: object, owner_kind: str) -> str:
    """Return a TypeDB-unique persistence identity for one graph row.

    The public ontology ID remains stable across ABox generations. The storage
    identity includes the box and generation so a staging generation can hold
    the same fact beside the active one until promotion is verified.
    """
    payload = "|".join([
        str(owner_kind or "row"),
        str((row or {}).get("ontologyBox") or "ABox"),
        str((row or {}).get("worldId") or ""),
        str((row or {}).get("snapshotId") or (row or {}).get("aboxSnapshotId") or ""),
        str(canonical_id or ""),
    ])
    return "ontology-storage:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
