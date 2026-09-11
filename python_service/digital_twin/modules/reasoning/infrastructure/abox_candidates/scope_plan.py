"""Normalize scoped candidate contracts and bind immutable physical generations."""

from typing import Dict, Iterable, List

from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION, SCOPED_ABOX_PERSISTENCE_MODE
from digital_twin.modules.reasoning.domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE, is_current_state_persistence_mode, copy_on_write_generation_id, next_current_state_slot
from digital_twin.infrastructure.graph_store_payloads import number_or_none


def is_scoped_abox_graph(graph: PortfolioOntology) -> bool:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    persistence_mode = str(worldview.get("persistenceMode") or "")
    return (
        (
            persistence_mode == SCOPED_ABOX_PERSISTENCE_MODE
            or is_current_state_persistence_mode(persistence_mode)
        )
        and str(worldview.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
        and isinstance(worldview.get("scopePlan"), list)
    )


def is_current_state_scoped_abox_graph(graph: PortfolioOntology) -> bool:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    return is_current_state_persistence_mode(
        worldview.get("persistenceMode") or worldview.get("physicalStateMode")
    )


def scoped_abox_plan(graph: PortfolioOntology) -> List[Dict[str, object]]:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    rows = []
    for item in worldview.get("scopePlan") or []:
        if not isinstance(item, dict):
            continue
        scope_id = str(item.get("scopeId") or "").strip()
        generation_id = str(item.get("generationId") or "").strip()
        if not scope_id or not generation_id:
            continue
        rows.append({
            "scopeId": scope_id,
            "scopeType": str(item.get("scopeType") or scope_id.split(":", 1)[0] or "reference"),
            "scopeFamily": str(item.get("scopeFamily") or ""),
            "impactScopeFamilies": [
                str(value or "")
                for value in item.get("impactScopeFamilies") or []
                if str(value or "").strip()
            ],
            "semanticFingerprints": {
                str(family or "").strip(): str(fingerprint or "").strip()
                for family, fingerprint in dict(item.get("semanticFingerprints") or {}).items()
                if str(family or "").strip() and str(fingerprint or "").strip()
            },
            "semanticDependencyFingerprintVersion": str(
                item.get("semanticDependencyFingerprintVersion") or ""
            ).strip(),
            "semanticDependencyFingerprintsPacked": str(
                item.get("semanticDependencyFingerprintsPacked") or ""
            ).strip(),
            **(
                {
                    "semanticDependencyFingerprints": {
                        str(key or "").strip(): str(fingerprint or "").strip()
                        for key, fingerprint in dict(
                            item.get("semanticDependencyFingerprints") or {}
                        ).items()
                        if str(key or "").strip()
                        and str(fingerprint or "").strip()
                    }
                }
                if isinstance(item.get("semanticDependencyFingerprints"), dict)
                else {}
            ),
            "fingerprint": str(item.get("fingerprint") or ""),
            "baseFingerprint": str(item.get("baseFingerprint") or ""),
            "dependencyScopeIds": [
                str(value or "")
                for value in item.get("dependencyScopeIds") or []
                if str(value or "").strip()
            ],
            "nodeInventoryVersion": str(
                item.get("nodeInventoryVersion") or ""
            ).strip(),
            "nodeIds": sorted({
                str(value or "").strip()
                for value in item.get("nodeIds") or []
                if str(value or "").strip()
            }),
            "relationEndpointBindingVersion": str(
                item.get("relationEndpointBindingVersion") or ""
            ).strip(),
            "relationEndpointNodeIdsByScope": {
                str(endpoint_scope_id or "").strip(): sorted({
                    str(value or "").strip()
                    for value in endpoint_node_ids or []
                    if str(value or "").strip()
                })
                for endpoint_scope_id, endpoint_node_ids in dict(
                    item.get("relationEndpointNodeIdsByScope") or {}
                ).items()
                if str(endpoint_scope_id or "").strip()
            },
            "generationId": generation_id,
            "entityCount": int(number_or_none(item.get("entityCount")) or 0),
            "relationCount": int(number_or_none(item.get("relationCount")) or 0),
            "evidenceCount": int(number_or_none(item.get("evidenceCount")) or 0),
            "observedAt": str(item.get("observedAt") or ""),
        })
    return sorted(rows, key=lambda item: str(item.get("scopeId") or ""))


def current_state_physical_scope_plan(logical_scope_plan: Iterable[Dict[str, object]], active_metadata: Dict[str, object], changed_scope_ids: Iterable[str], world_id: str, persistence_mode: str=CURRENT_STATE_ABOX_PERSISTENCE_MODE, transition_id: str='') -> List[Dict[str, object]]:
    """Map logical generations to copy-on-write or legacy physical rows."""

    changed = {
        str(value or "").strip()
        for value in changed_scope_ids or []
        if str(value or "").strip()
    }
    active_generations = dict((active_metadata or {}).get("scopeGenerationIds") or {})
    result: List[Dict[str, object]] = []
    for raw in logical_scope_plan or []:
        item = dict(raw or {})
        scope_id = str(item.get("scopeId") or "").strip()
        logical_generation_id = str(
            item.get("logicalGenerationId") or item.get("generationId") or ""
        ).strip()
        active_generation_id = str(active_generations.get(scope_id) or "").strip()
        physical_generation_changed = bool(
            scope_id in changed or not active_generation_id
        )
        if physical_generation_changed:
            physical_generation_id = (
                copy_on_write_generation_id(
                    world_id,
                    scope_id,
                    logical_generation_id,
                    transition_id,
                )
                if str(persistence_mode or "") == CURRENT_STATE_ABOX_PERSISTENCE_MODE
                else next_current_state_slot(
                    world_id,
                    scope_id,
                    active_generation_id,
                )
            )
        else:
            physical_generation_id = active_generation_id
        item.update({
            "generationId": physical_generation_id,
            "logicalGenerationId": logical_generation_id,
            "physicalGenerationId": physical_generation_id,
            "physicalGenerationChanged": physical_generation_changed,
            "physicalStateMode": str(
                persistence_mode or CURRENT_STATE_ABOX_PERSISTENCE_MODE
            ),
        })
        result.append(item)
    return sorted(result, key=lambda item: str(item.get("scopeId") or ""))
