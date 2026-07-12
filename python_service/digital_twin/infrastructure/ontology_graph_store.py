import hashlib
import json
from typing import Dict, Iterable, List

from .settings import runtime_settings


GRAPH_STORE_MODES = {"neo4j", "typedb", "dual"}
PARITY_COUNT_KEYS = [
    "entityCount",
    "relationCount",
    "tboxEntityCount",
    "aboxEntityCount",
    "ruleBoxEntityCount",
    "inferenceBoxEntityCount",
    "tboxRelationCount",
    "aboxRelationCount",
    "ruleBoxRelationCount",
    "inferenceBoxRelationCount",
    "ruleCount",
    "conditionCount",
    "derivationCount",
    "statementCount",
    "traceCount",
]


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
        result["graphStoreParity"] = graph_store_parity(result, result["mirrorResults"])
        return result

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        result = dict(self.primary.seed_ontology(payload or {}) or {})
        result["graphStoreMode"] = "dual"
        result["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        result["mirrorResults"] = self.mirror_call("seed_ontology", payload or {})
        result["graphStoreParity"] = graph_store_parity(result, result["mirrorResults"])
        return result

    def rulebox_snapshot(self) -> Dict[str, object]:
        snapshot = self.primary.rulebox_snapshot()
        if isinstance(snapshot, dict):
            snapshot = dict(snapshot)
            snapshot["graphStoreMode"] = "dual"
            snapshot["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
            snapshot["mirrorResults"] = self.mirror_call("rulebox_snapshot")
            snapshot["graphStoreParity"] = graph_store_parity(snapshot, snapshot["mirrorResults"])
        return snapshot

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        result = dict(self.primary.save_rulebox(payload or {}) or {})
        result["graphStoreMode"] = "dual"
        result["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        result["mirrorResults"] = self.mirror_call("save_rulebox", payload or {})
        result["graphStoreParity"] = graph_store_parity(result, result["mirrorResults"])
        return result

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        result = dict(self.primary.run_rulebox(payload or {}) or {})
        result["graphStoreMode"] = "dual"
        result["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        result["mirrorResults"] = self.mirror_call("run_rulebox", payload or {})
        result["graphStoreParity"] = graph_store_parity(result, result["mirrorResults"])
        return result

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
        snapshot = dict(self.primary.inferencebox_snapshot(symbols=symbols, limit=limit) or {})
        snapshot["graphStoreMode"] = "dual"
        snapshot["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        snapshot["mirrorResults"] = self.mirror_call("inferencebox_snapshot", symbols, limit)
        snapshot["graphStoreParity"] = graph_store_parity(snapshot, snapshot["mirrorResults"])
        return snapshot

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        result = dict(self.primary.save_rule_change_candidates(candidates, context or {}) or {})
        result["graphStoreMode"] = "dual"
        result["primaryGraphStore"] = getattr(self.primary, "store_key", "primary")
        result["mirrorResults"] = self.mirror_call("save_rule_change_candidates", candidates, context or {})
        result["graphStoreParity"] = graph_store_parity(result, result["mirrorResults"])
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


def graph_store_parity(primary_result: Dict[str, object], mirror_results: Dict[str, object]) -> Dict[str, object]:
    mirrors = mirror_results or {}
    checks = []
    status = "ok"
    for key, result in mirrors.items():
        if not isinstance(result, dict):
            checks.append({"graphStore": key, "status": "unavailable", "reason": "mirror result is not a mapping"})
            status = "unavailable"
            continue
        mirror_status = str(result.get("status") or "").lower()
        if mirror_status in {"error", "disabled", "driver-missing", "unsupported"}:
            checks.append({"graphStore": key, "status": "unavailable", "mirrorStatus": mirror_status, "reason": result.get("reason") or ""})
            status = "unavailable" if status == "ok" else status
            continue
        mismatches = []
        for count_key in PARITY_COUNT_KEYS:
            if count_key not in primary_result or count_key not in result:
                continue
            if int_or_none(primary_result.get(count_key)) != int_or_none(result.get(count_key)):
                mismatches.append({
                    "key": count_key,
                    "primary": int_or_none(primary_result.get(count_key)),
                    "mirror": int_or_none(result.get(count_key)),
                })
        semantic = graph_store_semantic_parity(primary_result, result)
        checks.append({
            "graphStore": key,
            "status": "mismatch" if (mismatches or semantic.get("status") == "mismatch") else "ok",
            "mismatches": mismatches,
            "semantic": semantic,
            "comparedKeys": [count_key for count_key in PARITY_COUNT_KEYS if count_key in primary_result and count_key in result],
        })
        if mismatches or semantic.get("status") == "mismatch":
            status = "mismatch"
    return {
        "status": status,
        "primaryGraphStore": primary_result.get("primaryGraphStore") or primary_result.get("graphStore") or "primary",
        "mirrorCount": len(mirrors),
        "checks": checks,
    }


def int_or_none(value: object):
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def graph_store_semantic_parity(primary_result: Dict[str, object], mirror_result: Dict[str, object]) -> Dict[str, object]:
    primary = graph_store_semantic_signature(primary_result)
    mirror = graph_store_semantic_signature(mirror_result)
    if not primary.get("comparable") or not mirror.get("comparable"):
        return {
            "status": "not-compared",
            "reason": "semantic signatures are available only for RuleBox or InferenceBox snapshots.",
        }
    primary_items = set(primary.get("items") or [])
    mirror_items = set(mirror.get("items") or [])
    missing = sorted(primary_items - mirror_items)
    extra = sorted(mirror_items - primary_items)
    return {
        "status": "mismatch" if missing or extra else "ok",
        "domain": primary.get("domain") if primary.get("domain") == mirror.get("domain") else "mixed",
        "primaryFingerprint": primary.get("fingerprint"),
        "mirrorFingerprint": mirror.get("fingerprint"),
        "primaryCount": len(primary_items),
        "mirrorCount": len(mirror_items),
        "missingInMirror": missing[:12],
        "extraInMirror": extra[:12],
    }


def graph_store_semantic_signature(result: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(result, dict):
        return {"comparable": False}
    rules = result.get("rules") if isinstance(result.get("rules"), list) else []
    if rules:
        items = sorted(rule_semantic_signature(rule) for rule in rules if isinstance(rule, dict))
        return semantic_signature_payload("rulebox", items)
    relations = result.get("relations") if isinstance(result.get("relations"), list) else []
    traces = result.get("traces") if isinstance(result.get("traces"), list) else []
    if relations or traces:
        items = sorted(
            [inference_relation_semantic_signature(item) for item in relations if isinstance(item, dict)]
            + [inference_trace_semantic_signature(item) for item in traces if isinstance(item, dict)]
        )
        return semantic_signature_payload("inferencebox", items)
    return {"comparable": False}


def semantic_signature_payload(domain: str, items: List[str]) -> Dict[str, object]:
    fingerprint = hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return {
        "comparable": True,
        "domain": domain,
        "fingerprint": fingerprint,
        "items": items,
    }


def rule_semantic_signature(rule: Dict[str, object]) -> str:
    conditions = [
        "|".join([
            str(item.get("condition_id") or item.get("conditionId") or ""),
            str(item.get("kind") or ""),
            str(item.get("field") or ""),
            str(item.get("operator") or ""),
            stable_json(item.get("value")),
            str(item.get("relation_type") or item.get("relationType") or ""),
            stable_json(item.get("target_property_filters") or item.get("targetPropertyFilters") or {}),
            stable_json(item.get("relation_property_filters") or item.get("relationPropertyFilters") or {}),
        ])
        for item in (rule.get("conditions") or [])
        if isinstance(item, dict)
    ]
    derivations = [
        "|".join([
            str(item.get("relation_type") or item.get("relationType") or ""),
            str(item.get("target_kind") or item.get("targetKind") or ""),
            str(item.get("target_key") or item.get("targetKey") or ""),
            str(item.get("decision_stage") or item.get("decisionStage") or ""),
            str(item.get("action_group") or item.get("actionGroup") or ""),
            str(item.get("action_level") or item.get("actionLevel") or ""),
        ])
        for item in (rule.get("derivations") or [])
        if isinstance(item, dict)
    ]
    return "|".join([
        "rule",
        str(rule.get("rule_id") or rule.get("ruleId") or ""),
        str(rule.get("version") or ""),
        str(bool(rule.get("enabled", True))),
        stable_json(sorted(conditions)),
        stable_json(sorted(derivations)),
    ])


def inference_relation_semantic_signature(item: Dict[str, object]) -> str:
    return "|".join([
        "relation",
        str(item.get("type") or ""),
        str(item.get("source") or ""),
        str(item.get("target") or ""),
        str(item.get("ruleId") or ""),
        str(item.get("decisionStage") or ""),
        str(item.get("polarity") or ""),
    ])


def inference_trace_semantic_signature(item: Dict[str, object]) -> str:
    return "|".join([
        "trace",
        str(item.get("symbol") or ""),
        str(item.get("ruleId") or ""),
        stable_json(sorted(str(value) for value in (item.get("matchedConditionIds") or []))),
    ])


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
