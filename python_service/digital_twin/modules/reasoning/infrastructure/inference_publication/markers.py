"""Candidate/active marker payloads and world-scoped generation deletion."""

import json
from typing import Callable, Dict, Iterable, List

from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string


def inference_generation_marker_row(
    graph: PortfolioOntology,
    node_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
    publication_status: str = 'candidate',
    *,
    now: Callable[[], str],
) -> Dict[str, object]:
    worldview = dict(graph.worldview or {})
    generation_id = str(worldview.get("inferenceGenerationId") or "").strip()
    generation_at = str(worldview.get("inferenceGenerationAt") or now()).strip()
    properties = {
        **worldview,
        "ontologyBox": "InferenceBox",
        "tboxClass": "ActiveGeneration" if publication_status == "active" else "CandidateGeneration",
        "tboxClasses": ["InferenceGeneration", "ActiveGeneration" if publication_status == "active" else "CandidateGeneration"],
        "publicationStatus": publication_status,
        "candidateCreatedAt": generation_at,
        "activatedAt": generation_at if publication_status == "active" else "",
        "expectedEntityCount": len(list(node_rows or [])),
        "expectedRelationCount": len(list(relation_rows or [])),
        "nativeTypeDbReasoned": True,
    }
    return {
        "id": "inference-generation" + ("" if publication_status == "active" else "-candidate") + ":" + generation_id,
        "label": ("Active" if publication_status == "active" else "Candidate") + " InferenceBox " + generation_id,
        "kind": "inference-generation" if publication_status == "active" else "inference-generation-candidate",
        "nodeType": "ontology-entity",
        "ontologyBox": "InferenceBox",
        # These fields must be promoted to TypeDB attributes, not merely left
        # in ``propertiesJson``. Candidate validation and active-generation
        # lookup are scoped by world id, and an unscoped marker becomes
        # invisible immediately after it is written.
        "worldId": str(worldview.get("worldId") or ""),
        "worldType": str(worldview.get("worldType") or ""),
        "tenantId": str(worldview.get("tenantId") or ""),
        "accountId": str(worldview.get("accountId") or ""),
        "snapshotId": generation_id,
        "aboxSnapshotId": generation_id,
        "tboxClass": "ActiveGeneration" if publication_status == "active" else "CandidateGeneration",
        "propertiesJson": json.dumps(properties, ensure_ascii=False, sort_keys=True),
    }


def inference_generation_delete_queries(generation_id: str, world_id: str = "") -> List[str]:
    generation_literal = typedb_string(str(generation_id or ""))
    world_clause = (
        ", has ontology-world-id " + typedb_string(world_id)
        if str(world_id or "").strip()
        else ""
    )
    return [
        (
            'match $r isa ontology-assertion, has ontology-box "InferenceBox", '
            "has ontology-snapshot-id " + generation_literal + world_clause + "; delete $r;"
        ),
        (
            'match $n isa ontology-node, has ontology-box "InferenceBox", '
            "has ontology-snapshot-id " + generation_literal + world_clause + "; delete $n;"
        ),
    ]
