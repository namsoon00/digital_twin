"""Pure stored TBox metadata decoding; no seed or rule-catalog dependencies."""

from typing import Dict

from digital_twin.modules.reasoning.domain.ontology_schema import default_tbox_metadata, normalize_tbox_metadata


def active_tbox_metadata_unavailable(status: str, reason: str, source: str) -> Dict[str, object]:
    metadata = default_tbox_metadata()
    metadata.update(
        {
            "configured": True,
            "status": status,
            "source": "code-fallback",
            "storeSource": source,
            "reason": reason,
        }
    )
    return metadata


def active_tbox_metadata_from_rows(rowsets: Dict[str, list], source: str) -> Dict[str, object]:
    entity_row = (rowsets.get("entities") or [{}])[0]
    relation_row = (rowsets.get("relations") or [{}])[0]
    entity_count = int(entity_row.get("entityCount") or 0)
    if entity_count <= 0:
        metadata = default_tbox_metadata()
        metadata.update(
            {
                "configured": True,
                "status": "code-fallback",
                "source": "code-fallback",
                "storeSource": source,
                "reason": "저장된 TBox 노드가 없어 코드 TBox 메타데이터를 사용합니다.",
            }
        )
        return metadata
    metadata = normalize_tbox_metadata(
        {
            "source": source,
            "version": entity_row.get("version") or default_tbox_metadata()["version"],
            "fingerprint": entity_row.get("fingerprint") or default_tbox_metadata()["fingerprint"],
            "entityCount": entity_count,
            "relationCount": int(relation_row.get("relationCount") or 0),
            "status": "ok",
        }
    )
    metadata.update(
        {
            "configured": True,
            "storeSource": source,
            "updatedAt": str(entity_row.get("updatedAt") or ""),
        }
    )
    return metadata
