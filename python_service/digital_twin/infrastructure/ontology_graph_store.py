from typing import Dict, Iterable, List

from .settings import runtime_settings


GRAPH_STORE_MODES = {"neo4j", "typedb", "dual"}


def normalized_graph_store_mode(settings: Dict[str, str] = None) -> str:
    settings = settings or {}
    mode = str(settings.get("ontologyGraphStoreMode") or "neo4j").strip().lower()
    return mode if mode in GRAPH_STORE_MODES else "neo4j"


def ontology_repository_from_settings(settings: Dict[str, str] = None):
    settings = settings or runtime_settings()
    mode = normalized_graph_store_mode(settings)
    if mode == "typedb":
        from .typedb_ontology import typedb_repository_from_settings

        return typedb_repository_from_settings(settings)

    from .neo4j_ontology import neo4j_repository_from_settings

    primary = neo4j_repository_from_settings(settings)
    if mode == "dual":
        from .typedb_ontology import typedb_repository_from_settings

        return CompositeOntologyGraphRepository(
            primary,
            mirrors=[typedb_repository_from_settings(settings)],
        )
    return primary


class CompositeOntologyGraphRepository:
    store_key = "dual"
    store_label = "Dual Graph Store"

    def __init__(self, primary, mirrors: Iterable[object] = None):
        self.primary = primary
        self.mirrors = [item for item in (mirrors or []) if item]

    def active_tbox_metadata(self) -> Dict[str, object]:
        metadata = self.primary.active_tbox_metadata()
        if isinstance(metadata, dict):
            metadata = dict(metadata)
            metadata["graphStoreMode"] = "dual"
            metadata["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
            metadata["mirrorGraphStores"] = [getattr(item, "store_key", "mirror") for item in self.mirrors]
        return metadata

    def save_graph(self, graph) -> Dict[str, object]:
        result = dict(self.primary.save_graph(graph) or {})
        result["graphStoreMode"] = "dual"
        result["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        result["mirrorResults"] = self.mirror_call("save_graph", graph)
        return result

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        result = dict(self.primary.seed_ontology(payload or {}) or {})
        result["graphStoreMode"] = "dual"
        result["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        result["mirrorResults"] = self.mirror_call("seed_ontology", payload or {})
        return result

    def rulebox_snapshot(self) -> Dict[str, object]:
        snapshot = self.primary.rulebox_snapshot()
        if isinstance(snapshot, dict):
            snapshot = dict(snapshot)
            snapshot["graphStoreMode"] = "dual"
            snapshot["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        return snapshot

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        result = dict(self.primary.save_rulebox(payload or {}) or {})
        result["graphStoreMode"] = "dual"
        result["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        result["mirrorResults"] = self.mirror_call("save_rulebox", payload or {})
        return result

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        result = dict(self.primary.run_rulebox(payload or {}) or {})
        result["graphStoreMode"] = "dual"
        result["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        result["mirrorResults"] = self.mirror_call("run_rulebox", payload or {})
        return result

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
        return self.primary.inferencebox_snapshot(symbols=symbols, limit=limit)

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        result = dict(self.primary.save_rule_change_candidates(candidates, context or {}) or {})
        result["graphStoreMode"] = "dual"
        result["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        result["mirrorResults"] = self.mirror_call("save_rule_change_candidates", candidates, context or {})
        return result

    def mirror_call(self, method_name: str, *args) -> Dict[str, object]:
        results: Dict[str, object] = {}
        for mirror in self.mirrors:
            key = getattr(mirror, "store_key", mirror.__class__.__name__)
            method = getattr(mirror, method_name, None)
            if not callable(method):
                results[key] = {"status": "unsupported", "reason": method_name + " is not available."}
                continue
            try:
                results[key] = method(*args)
            except Exception as error:  # noqa: BLE001 - mirror writes must be isolated.
                results[key] = {"status": "error", "reason": str(error)[:180]}
        return results
