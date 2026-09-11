"""TypeDB static-seed restore owner; no facade or runtime construction."""

from .artifact import graph_box_entity_counts, ontology_seed_graph_from_artifact
from .restore_ports import RestoreStore
from digital_twin.modules.model_registry.contracts import rulebox_rules_hash
from digital_twin.modules.reasoning.domain.ontology_schema import normalize_tbox_metadata
from digital_twin.modules.reasoning.domain.ontology_semantics import SEMANTIC_STORAGE_CONTRACT_VERSION
from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_from_payload
from typing import Dict


def seed_release_artifact(
    _store: RestoreStore, payload: Dict[str, object]
) -> Dict[str, object]:
    """Restore an immutable release from its durable static graph artifact."""
    artifact = dict(payload or {})
    if (
        str(artifact.get("version") or "") != "ontology-release-seed-artifact-v2"
        or str(artifact.get("semanticStorageContractVersion") or "")
        != SEMANTIC_STORAGE_CONTRACT_VERSION
        or (not dict(artifact.get("releaseBundle") or {}))
    ):
        return {
            "configured": True,
            "saved": False,
            "status": "unsupported-release-artifact-contract",
            "artifactVersion": str(artifact.get("version") or ""),
            "semanticStorageContractVersion": str(
                artifact.get("semanticStorageContractVersion") or ""
            ),
        }
    authored_rules_payload = [
        dict(item)
        for item in list(artifact.get("rules") or [])
        if isinstance(item, dict)
    ]
    try:
        rules = rulebox_rules_from_payload(
            {"rules": authored_rules_payload}, strict_governance=True
        )
    except ValueError as error:
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-release-artifact",
            "reason": str(error)[:240],
        }
    expected_rulebox_fingerprint = str(artifact.get("ruleboxFingerprint") or "").strip()
    # Hash authored bytes, not the current parser's normalized rule shape.
    actual_rulebox_fingerprint = rulebox_rules_hash(authored_rules_payload)
    tbox_metadata = normalize_tbox_metadata(dict(artifact.get("tboxMetadata") or {}))
    expected_tbox_fingerprint = str(artifact.get("tboxFingerprint") or "").strip()
    if (
        not expected_rulebox_fingerprint
        or expected_rulebox_fingerprint != actual_rulebox_fingerprint
        or (not expected_tbox_fingerprint)
        or (expected_tbox_fingerprint != str(tbox_metadata.get("fingerprint") or ""))
    ):
        return {
            "configured": True,
            "saved": False,
            "status": "release-artifact-fingerprint-mismatch",
            "expectedRuleboxFingerprint": expected_rulebox_fingerprint,
            "actualRuleboxFingerprint": actual_rulebox_fingerprint,
            "expectedTboxFingerprint": expected_tbox_fingerprint,
            "actualTboxFingerprint": str(tbox_metadata.get("fingerprint") or ""),
        }
    graph = ontology_seed_graph_from_artifact(artifact)
    box_counts = graph_box_entity_counts(graph)
    missing_boxes = [
        box
        for box in _store.seed_static_box_names()
        if int(box_counts.get(box) or 0) <= 0
    ]
    if missing_boxes:
        return {
            "configured": True,
            "saved": False,
            "status": "release-artifact-static-box-missing",
            "missingBoxes": missing_boxes,
        }
    schema_sync = _store.sync_base_schema_contract()
    if not bool(schema_sync.get("saved")):
        return {
            "configured": True,
            "saved": False,
            "status": "release-artifact-schema-sync-failed",
            "schemaSync": schema_sync,
            "reason": str(schema_sync.get("reason") or "")[:240],
        }
    _store._last_rules = list(rules)
    static_write = _store.save_static_seed_boxes(
        graph,
        _store.seed_static_box_names(),
        rules_payload=authored_rules_payload,
        schema_prepared=True,
        tbox_metadata=tbox_metadata,
    )
    if not bool(static_write.get("saved")):
        return {
            "configured": True,
            "saved": False,
            "status": "release-artifact-static-write-failed",
            "staticWrite": static_write,
        }
    manifest = _store.save_seed_static_manifest(
        graph, authored_rules_payload, schema_prepared=True, tbox_metadata=tbox_metadata
    )
    if not bool(manifest.get("saved")):
        return {
            "configured": True,
            "saved": False,
            "status": "release-artifact-manifest-write-failed",
            "staticWrite": static_write,
            "manifest": manifest,
        }
    _store.clear_rulebox_snapshot_cache()
    restored_rulebox = dict(_store.rulebox_snapshot() or {})
    restored_tbox = dict(_store.active_tbox_metadata() or {})
    # Executable readback has a separate identity from the frozen artifact.
    restored_runtime_rulebox_fingerprint = str(
        restored_rulebox.get("sourceRulesHash")
        or restored_rulebox.get("ruleboxRulesHash")
        or restored_rulebox.get("rulesHash")
        or ""
    ).strip()
    restored_tbox_fingerprint = str(restored_tbox.get("fingerprint") or "").strip()
    restored_manifest = dict(_store.read_seed_static_manifest() or {})
    restored_manifest_metadata = dict(restored_manifest.get("metadata") or {})
    restored_artifact_rulebox_fingerprint = str(
        restored_manifest_metadata.get("ruleboxRulesHash") or ""
    ).strip()
    ready = bool(
        str(restored_rulebox.get("status") or "") == "ok"
        and restored_runtime_rulebox_fingerprint
        and (str(restored_manifest.get("status") or "") == "ok")
        and (restored_artifact_rulebox_fingerprint == expected_rulebox_fingerprint)
        and (str(restored_tbox.get("status") or "") == "ok")
        and (restored_tbox_fingerprint == expected_tbox_fingerprint)
    )
    return {
        "configured": True,
        "saved": ready,
        "status": "restored" if ready else "release-artifact-readback-mismatch",
        "ruleCount": len(authored_rules_payload),
        "ruleboxFingerprint": restored_runtime_rulebox_fingerprint,
        "runtimeRuleboxFingerprint": restored_runtime_rulebox_fingerprint,
        "artifactRuleboxFingerprint": restored_artifact_rulebox_fingerprint,
        "expectedArtifactRuleboxFingerprint": expected_rulebox_fingerprint,
        "tboxFingerprint": restored_tbox_fingerprint,
        "schemaSync": schema_sync,
        "staticWrite": static_write,
        "manifest": manifest,
        "manifestReadback": {
            "status": str(restored_manifest.get("status") or ""),
            "ruleboxFingerprint": restored_artifact_rulebox_fingerprint,
            "tboxFingerprint": str(
                restored_manifest_metadata.get("tboxFingerprint") or ""
            ),
        },
    }
