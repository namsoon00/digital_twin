import copy
import hashlib
import json
import math
import time
import uuid
from typing import Dict, Iterable, List, Tuple

from ..domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from ..domain.ontology_graph_reasoner import run_graph_reasoner
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from ..domain.ontology_rulebox_governance import (
    normalize_rule_change_candidate,
    rulebox_governance_candidates,
    rulebox_rules_hash,
)
from ..domain.ontology_schema import default_tbox_metadata
from .graph_store_inferencebox import (
    inferencebox_entity_payload,
    inferencebox_relation_payload,
    inferencebox_snapshot_from_rows,
    inferencebox_trace_payload,
)
from .graph_store_lifecycle import (
    active_tbox_metadata_from_rows,
    active_tbox_metadata_unavailable,
    graph_box_entity_counts,
    graph_box_relation_counts,
    ontology_seed_graph,
)
from .graph_store_payloads import GraphStoreOntologyRowMapperMixin, list_of_strings, number_or_none
from .graph_store_rulebox import (
    rulebox_graph_from_rules,
    rulebox_rules_from_payload,
    rulebox_snapshot_from_rows,
    rulebox_rules_to_payload,
)
from .settings import runtime_settings, utc_now


def typedb_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def typedb_number(value: object):
    parsed = number_or_none(value)
    if parsed is None:
        return None
    numeric = float(parsed)
    return numeric if math.isfinite(numeric) else None


def typedb_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def typedb_error_code(error: object) -> str:
    text = str(error or "").lower()
    if any(term in text for term in ["unable to connect", "connection refused", "connect failed", "unavailable"]):
        return "typedbConnectionError"
    if any(term in text for term in ["timeout", "timed out", "deadline"]):
        return "typedbTimeout"
    if any(term in text for term in ["schema"]):
        return "typedbSchemaError"
    return "typedbReadError"


def clean_symbols_from_payload(value: object) -> List[str]:
    if isinstance(value, list):
        raw_values = value
    elif value in (None, ""):
        raw_values = []
    else:
        raw_values = [value]
    return sorted(set(str(item or "").upper().strip() for item in raw_values if str(item or "").strip()))


def inference_generation_id() -> str:
    return "inference-generation:" + uuid.uuid4().hex[:16]


def generated_inference_id(original_id: str, generation_id: str) -> str:
    original = str(original_id or "unknown")
    digest = hashlib.sha256((str(generation_id or "") + "|" + original).encode("utf-8")).hexdigest()[:12]
    return original + ":gen:" + digest


def rulebox_runtime_metadata(rules_payload: List[Dict[str, object]]) -> Dict[str, object]:
    rules_payload = [item for item in (rules_payload or []) if isinstance(item, dict)]
    rules_hash = rulebox_rules_hash(rules_payload)
    return {
        "ruleboxRulesHash": rules_hash,
        "ruleboxShortHash": rules_hash[:12],
        "ruleboxRuleCount": len(rules_payload),
        "ruleboxConditionCount": sum(len(item.get("conditions") or []) for item in rules_payload),
        "ruleboxDerivationCount": sum(len(item.get("derivations") or []) for item in rules_payload),
        "ruleboxEngineVersion": GRAPH_REASONER_VERSION,
    }


def rulebox_structural_fingerprint(rules_payload: List[Dict[str, object]]) -> Dict[str, Tuple[int, int]]:
    fingerprint: Dict[str, Tuple[int, int]] = {}
    for rule in rules_payload or []:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
        if not rule_id:
            continue
        fingerprint[rule_id] = (
            len(rule.get("conditions") or []),
            len(rule.get("derivations") or []),
        )
    return fingerprint


def node_boxes(graph: PortfolioOntology) -> List[str]:
    boxes = {
        str((item.properties or {}).get("ontologyBox") or "ABox")
        for item in graph.entities
        if str((item.properties or {}).get("ontologyBox") or "ABox")
    }
    boxes.update(
        str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "ABox"))
        for item in graph.evidence
        if str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "ABox"))
    )
    boxes.update(
        "InferenceBox" if str(item.belief_id or "").startswith("belief:inference:") else "ABox"
        for item in graph.beliefs
    )
    boxes.update(
        str((item.properties or {}).get("ontologyBox") or "ABox")
        for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "ABox")
    )
    if graph.opinions or getattr(graph, "reasoning_cards", None):
        boxes.add("ABox")
    return sorted(boxes)


def typeql_has(attribute: str, value: object, numeric: bool = False) -> str:
    if value in (None, "", [], {}):
        return ""
    if numeric:
        parsed = typedb_number(value)
        if parsed is None:
            return ""
        return ", has " + attribute + " " + str(parsed)
    return ", has " + attribute + " " + typedb_string(value)


def typeql_has_bool_string(attribute: str, value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        normalized = "true" if value else "false"
    else:
        normalized = str(value).strip().lower()
        if normalized not in {"true", "false", "1", "0", "yes", "no", "y", "n", "on", "off"}:
            return ""
        normalized = "true" if normalized in {"true", "1", "yes", "y", "on"} else "false"
    return ", has " + attribute + " " + typedb_string(normalized)


def json_object(value: object) -> Dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def list_of_strings(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if value in (None, ""):
        return []
    return [str(value)]


def typedb_concept_value(concept: object):
    if concept is None:
        return None
    get_value = getattr(concept, "get_value", None)
    if callable(get_value):
        return get_value()
    value = getattr(concept, "value", None)
    if callable(value):
        return value()
    if value is not None:
        return value
    get_label = getattr(concept, "get_label", None)
    if callable(get_label):
        return str(get_label())
    return concept


def typedb_row_value(row: object, name: str):
    if row is None:
        return None
    getter = getattr(row, "get", None)
    if callable(getter):
        try:
            return typedb_concept_value(getter(name))
        except Exception:
            return None
    if isinstance(row, dict):
        return typedb_concept_value(row.get(name))
    return None


def merge_flat_properties(row: Dict[str, object], props: Dict[str, object]) -> Dict[str, object]:
    merged = dict(props or {})
    nested = merged.get("properties") if isinstance(merged.get("properties"), dict) else {}
    if nested:
        merged.update(nested)
    for key, value in row.items():
        if value not in (None, "", [], {}):
            merged.setdefault(key, value)
    return merged


TYPEDB_NATIVE_REASONING_PROFILE_VERSION = "typedb-function-readiness-v1"
TYPEDB_FUNCTION_SUBJECT_FIELDS = {
    "source",
    "symbol",
    "kind",
    "ontologyBox",
    "tboxClass",
    "profitLossRate",
    "value",
    "valueNumber",
}
TYPEDB_FUNCTION_TARGET_FILTERS = {
    "field",
    "levelType",
    "dataScope",
    "domainScope",
    "relationScope",
    "group",
    "polarity",
    "eventType",
    "materialityPassed",
    "minMaterialityScore",
    "minValue",
    "maxValue",
    "tboxClass",
    "tboxClasses",
}
TYPEDB_FUNCTION_RELATION_FILTERS = {
    "field",
    "signalGroup",
    "polarity",
    "transitionType",
    "materialityPassed",
    "minMaterialityScore",
    "minRiskImpact",
    "minSupportImpact",
}
TYPEDB_FUNCTION_OPERATORS = {"==", "eq", "!=", "ne", "<=", "lte", ">=", "gte", "<", "lt", ">", "gt", "exists", "present"}


class NullTypeDBOntologyGraphRepository:
    store_key = "typedb"
    store_label = "TypeDB"

    def active_tbox_metadata(self) -> Dict[str, object]:
        metadata = default_tbox_metadata()
        metadata.update({
            "configured": False,
            "status": "code-fallback",
            "source": "code",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        })
        return metadata

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        }

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        graph = ontology_seed_graph()
        result = self.save_graph(graph)
        result.update({
            "seeded": False,
            "engineVersion": GRAPH_REASONER_VERSION,
            "ruleCount": len(default_graph_inference_rules()),
        })
        return result

    def rulebox_snapshot(self) -> Dict[str, object]:
        rules = rulebox_rules_to_payload(default_graph_inference_rules())
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "source": "typedb-defaults",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "engineVersion": GRAPH_REASONER_VERSION,
            "rules": rules,
            "ruleCount": len(rules),
            "conditionCount": sum(len(item.get("conditions") or []) for item in rules),
            "derivationCount": sum(len(item.get("derivations") or []) for item in rules),
            "relationTypes": sorted({
                str(derivation.get("relation_type") or derivation.get("relationType") or "")
                for rule in rules
                for derivation in (rule.get("derivations") or [])
                if isinstance(derivation, dict)
            }),
            "defaultsFallbackUsed": True,
            "versions": [],
            "versionCount": 0,
            "changeCandidates": rulebox_governance_candidates(rules, []),
            "nativeReasoningProfile": typedb_native_reasoning_profile(rules),
        }

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        snapshot = self.rulebox_snapshot()
        snapshot.update({"saved": False, "status": "disabled"})
        return snapshot

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "statementCount": 0,
        }

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "reasoningMode": "disabled",
            "reason": "TypeDB ontology storage is not configured.",
            "symbols": list(symbols or []),
            "entities": [],
            "relations": [],
            "traces": [],
            "entityCount": 0,
            "relationCount": 0,
            "traceCount": 0,
            "nativeEntityCount": 0,
            "nativeRelationCount": 0,
            "nativeTraceCount": 0,
            "nativeTypeDbReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
        }

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "candidateCount": len(list(candidates or [])),
            "savedCount": 0,
        }


class TypeDBOntologyGraphRepository(GraphStoreOntologyRowMapperMixin):
    store_key = "typedb"
    store_label = "TypeDB"

    def __init__(
        self,
        address: str,
        user: str = "admin",
        password: str = "password",
        database: str = "orbit_alpha_ontology",
        tls_enabled: bool = False,
        timeout_seconds: int = 20,
        retry_count: int = 2,
        inference_generation_keep_count: int = 2,
    ):
        self.address = str(address or "").strip()
        self.user = str(user or "admin").strip() or "admin"
        self.password = str(password or "password")
        self.database = str(database or "orbit_alpha_ontology").strip() or "orbit_alpha_ontology"
        self.tls_enabled = bool(tls_enabled)
        self.timeout_seconds = max(2, int(timeout_seconds or 20))
        self.retry_count = max(0, int(retry_count or 0))
        self.inference_generation_keep_count = max(1, int(inference_generation_keep_count or 2))
        self._last_graph = None
        self._last_rules: List[GraphInferenceRule] = []

    def with_typedb_retries(self, operation):
        attempts = max(1, self.retry_count + 1)
        last_error = None
        for index in range(attempts):
            try:
                return operation()
            except Exception as error:  # noqa: BLE001 - TypeDB connectivity can be transient.
                last_error = error
                if index >= attempts - 1:
                    break
                time.sleep(min(2.0, 0.25 * (index + 1)))
        raise last_error

    def active_tbox_metadata(self) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().active_tbox_metadata()
        try:
            entity_rows = self.read_entity_rows(["TBox"])
            relation_rows = self.read_relation_rows(["TBox"])
        except Exception as error:  # noqa: BLE001 - metadata must be safe for UI/bootstrap.
            metadata = active_tbox_metadata_unavailable("error", str(error)[:180], "typedb")
            metadata.update({"graphStore": "typedb", "storeSource": "typedb-typeql"})
            return metadata
        version = ""
        fingerprint = ""
        updated_at = ""
        for row in entity_rows:
            props = json_object(row.get("propertiesJson"))
            version = version or str(row.get("version") or props.get("version") or props.get("tboxVersion") or "")
            fingerprint = fingerprint or str(row.get("fingerprint") or props.get("fingerprint") or props.get("tboxFingerprint") or "")
            updated_at = max(updated_at, str(row.get("updatedAt") or props.get("updatedAt") or ""))
        metadata = active_tbox_metadata_from_rows(
            {
                "entities": [{
                    "entityCount": len(entity_rows),
                    "version": version,
                    "fingerprint": fingerprint,
                    "updatedAt": updated_at,
                }],
                "relations": [{"relationCount": len(relation_rows)}],
            },
            "typedb-typeql",
        )
        metadata.update({"graphStore": "typedb", "source": "typedb-typeql", "storeSource": "typedb-typeql"})
        return metadata

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().save_graph(graph)
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], graph)
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    self.write_graph(driver, imported, graph)
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
        except Exception as error:  # noqa: BLE001 - graph-store persistence must not block monitoring.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reason": str(error)[:240],
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
                "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
            }
        self._last_graph = copy.deepcopy(graph)
        box_entity_counts = graph_box_entity_counts(graph)
        box_relation_counts = graph_box_relation_counts(graph)
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "schemaPrepared": True,
            "address": self.address,
            "database": self.database,
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "tboxEntityCount": box_entity_counts.get("TBox", 0),
            "aboxEntityCount": box_entity_counts.get("ABox", 0),
            "ruleBoxEntityCount": box_entity_counts.get("RuleBox", 0),
            "inferenceBoxEntityCount": box_entity_counts.get("InferenceBox", 0),
            "tboxRelationCount": box_relation_counts.get("TBox", 0),
            "aboxRelationCount": box_relation_counts.get("ABox", 0),
            "ruleBoxRelationCount": box_relation_counts.get("RuleBox", 0),
            "inferenceBoxRelationCount": box_relation_counts.get("InferenceBox", 0),
            "evidenceCount": len(graph.evidence),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        }

    def driver_missing_result(self, error: Exception, graph: PortfolioOntology) -> Dict[str, object]:
        return {
            "configured": True,
            "saved": False,
            "status": "driver-missing",
            "graphStore": "typedb",
            "reason": "typedb-driver Python package is not installed: " + str(error)[:160],
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        }

    def driver_imports(self) -> Tuple[object, object]:
        try:
            from typedb.driver import Credentials, DriverOptions, DriverTlsConfig, TransactionType, TypeDB

            return (TypeDB, Credentials, DriverOptions, DriverTlsConfig, TransactionType), None
        except Exception as error:  # noqa: BLE001 - optional dependency.
            return None, error

    def open_driver(self, imported):
        TypeDB, Credentials, DriverOptions, DriverTlsConfig, _TransactionType = imported[0]
        tls_config = DriverTlsConfig.enabled() if self.tls_enabled else DriverTlsConfig.disabled()
        return TypeDB.driver(
            self.address,
            Credentials(self.user, self.password),
            DriverOptions(tls_config),
        )

    def close_driver(self, driver) -> None:
        close = getattr(driver, "close", None)
        if callable(close):
            close()

    def ensure_database(self, driver) -> None:
        databases = getattr(driver, "databases", None)
        if databases is None:
            return
        try:
            contains = getattr(databases, "contains", None)
            if callable(contains) and contains(self.database):
                return
        except Exception:
            pass
        try:
            databases.create(self.database)
        except Exception as error:
            if "already" not in str(error).lower() and "exist" not in str(error).lower():
                raise

    def ensure_schema(self, driver, imported) -> None:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        with driver.transaction(self.database, TransactionType.SCHEMA) as tx:
            tx.query(self.schema_query()).resolve()
            tx.commit()

    def read_rows(self, query: str, columns: Iterable[str]) -> List[Dict[str, object]]:
        if not self.address:
            return []
        imported = self.driver_imports()
        if imported[0] is None:
            raise RuntimeError("typedb-driver Python package is not installed: " + str(imported[1])[:160])
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        def operation():
            driver = self.open_driver(imported)
            try:
                self.ensure_database(driver)
                with driver.transaction(self.database, TransactionType.READ) as tx:
                    resolved = tx.query(query).resolve()
                    rows = []
                    for item in resolved:
                        rows.append({name: typedb_row_value(item, name) for name in columns})
                    return rows
            finally:
                self.close_driver(driver)
        return self.with_typedb_retries(operation)

    def read_entity_rows(self, boxes: Iterable[str] = None, limit: int = 0) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for box in normalized_boxes(boxes):
            query = (
                "match $n isa ontology-node, "
                "has ontology-id $id, "
                "has ontology-label $label, "
                "has ontology-kind $kind, "
                "has ontology-box " + typedb_string(box) + ", "
                "has ontology-updated-at $updatedAt, "
                "has ontology-json $json; "
            )
            rows.extend(self.entity_rows_from_typeql(self.read_rows(
                query,
                ["id", "label", "kind", "updatedAt", "json"],
            ), box))
        rows = sorted(rows, key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("id") or "")), reverse=True)
        safe_limit = int(limit or 0)
        return rows[:safe_limit] if safe_limit > 0 else rows

    def read_relation_rows(self, boxes: Iterable[str] = None, limit: int = 0) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for box in normalized_boxes(boxes):
            query = (
                "match "
                "$source isa ontology-node, has ontology-id $sourceId, has ontology-label $sourceLabel; "
                "$target isa ontology-node, has ontology-id $targetId, has ontology-label $targetLabel; "
                "$r isa ontology-assertion, links (source: $source, target: $target), "
                "has ontology-id $id, "
                "has ontology-relation-type $type, "
                "has ontology-box " + typedb_string(box) + ", "
                "has ontology-updated-at $updatedAt, "
                "has ontology-json $json, "
                "has ontology-weight $weight; "
            )
            rows.extend(self.relation_rows_from_typeql(self.read_rows(
                query,
                ["id", "sourceId", "sourceLabel", "targetId", "targetLabel", "type", "updatedAt", "json", "weight"],
            ), box))
        rows = sorted(rows, key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("source") or ""), str(item.get("target") or "")), reverse=True)
        safe_limit = int(limit or 0)
        return rows[:safe_limit] if safe_limit > 0 else rows

    def entity_rows_from_typeql(self, rows: Iterable[Dict[str, object]], box: str) -> List[Dict[str, object]]:
        return [self.entity_row_from_typeql(row, box) for row in rows or [] if str(row.get("id") or "")]

    def entity_row_from_typeql(self, row: Dict[str, object], box: str) -> Dict[str, object]:
        props = json_object(row.get("json"))
        node_kind = str(row.get("kind") or props.get("kind") or "")
        merged = merge_flat_properties({
            "id": row.get("id"),
            "label": row.get("label"),
            "kind": node_kind,
            "ontologyBox": box,
            "symbol": row.get("symbol"),
            "ruleId": row.get("ruleId"),
            "tboxClass": row.get("tboxClass"),
            "updatedAt": row.get("updatedAt"),
        }, props)
        condition = merged.get("condition") if isinstance(merged.get("condition"), dict) else {}
        derivation = merged.get("derivation") if isinstance(merged.get("derivation"), dict) else {}
        proposed = merged.get("proposedRule") if isinstance(merged.get("proposedRule"), dict) else None
        payload = {
            **merged,
            "id": str(row.get("id") or merged.get("id") or ""),
            "label": str(row.get("label") or merged.get("label") or row.get("id") or ""),
            "nodeKind": node_kind,
            "kind": str(condition.get("kind") or merged.get("conditionKind") or node_kind) if node_kind == "rule-condition" else node_kind,
            "ontologyBox": str(box or merged.get("ontologyBox") or "ABox"),
            "symbol": str(row.get("symbol") or merged.get("symbol") or ""),
            "ruleId": str(row.get("ruleId") or merged.get("ruleId") or ""),
            "tboxClass": str(row.get("tboxClass") or merged.get("tboxClass") or ""),
            "updatedAt": str(row.get("updatedAt") or merged.get("updatedAt") or ""),
            "propertiesJson": json.dumps(props, ensure_ascii=False, sort_keys=True),
            "version": str(merged.get("version") or ""),
            "sourceKind": str(merged.get("sourceKind") or ""),
            "actionGroup": str(merged.get("actionGroup") or merged.get("action_group") or ""),
            "actionLevel": str(merged.get("actionLevel") or merged.get("action_level") or ""),
            "promptHint": str(merged.get("promptHint") or ""),
            "anyConditionMinCount": int(number_or_none(merged.get("anyConditionMinCount")) or 1),
            "enabled": bool(merged.get("enabled", True)),
            "conditionId": str(merged.get("conditionId") or condition.get("condition_id") or ""),
            "conditionIndex": int(number_or_none(merged.get("conditionIndex")) or 0),
            "conditionKind": str(condition.get("kind") or merged.get("conditionKind") or ""),
            "conditionField": str(condition.get("field") or merged.get("conditionField") or ""),
            "conditionOperator": str(condition.get("operator") or merged.get("conditionOperator") or ""),
            "conditionRole": str(condition.get("role") or merged.get("conditionRole") or "required"),
            "conditionValueString": str(condition.get("value") or merged.get("conditionValueString") or ""),
            "conditionValueNumber": number_or_none(condition.get("value") if "value" in condition else merged.get("conditionValueNumber")),
            "conditionRelationType": str(condition.get("relation_type") or merged.get("conditionRelationType") or "").upper(),
            "conditionDirection": str(condition.get("direction") or merged.get("conditionDirection") or "out"),
            "conditionTargetKind": str(condition.get("target_kind") or merged.get("conditionTargetKind") or ""),
            "conditionMinWeight": float(number_or_none(condition.get("min_weight") if "min_weight" in condition else merged.get("conditionMinWeight")) or 0),
            "derivationIndex": int(number_or_none(merged.get("derivationIndex")) or 0),
            "derivationRelationType": str(derivation.get("relation_type") or merged.get("derivationRelationType") or "").upper(),
            "derivationTargetKind": str(derivation.get("target_kind") or merged.get("derivationTargetKind") or ""),
            "derivationTargetKey": str(derivation.get("target_key") or merged.get("derivationTargetKey") or ""),
            "derivationTargetLabel": str(derivation.get("target_label") or merged.get("derivationTargetLabel") or ""),
            "derivationTboxClass": str(derivation.get("tbox_class") or merged.get("derivationTboxClass") or ""),
            "derivationTboxClasses": list_of_strings(derivation.get("tbox_classes") or merged.get("derivationTboxClasses")),
            "derivationPolarity": str(derivation.get("polarity") or merged.get("derivationPolarity") or ""),
            "derivationRiskImpact": number_or_none(derivation.get("risk_impact") or merged.get("derivationRiskImpact")),
            "derivationSupportImpact": number_or_none(derivation.get("support_impact") or merged.get("derivationSupportImpact")),
            "derivationWeight": number_or_none(derivation.get("weight") or merged.get("derivationWeight")),
            "derivationBeliefLabel": str(derivation.get("belief_label") or merged.get("derivationBeliefLabel") or ""),
            "derivationAiInfluenceLabel": str(derivation.get("ai_influence_label") or merged.get("derivationAiInfluenceLabel") or ""),
            "derivationActionGroup": str(derivation.get("action_group") or merged.get("derivationActionGroup") or ""),
            "derivationActionLevel": str(derivation.get("action_level") or merged.get("derivationActionLevel") or ""),
            "derivationDecisionStage": str(derivation.get("decision_stage") or derivation.get("decisionStage") or merged.get("derivationDecisionStage") or ""),
            "derivationStagePriority": number_or_none(derivation.get("stage_priority") or derivation.get("stagePriority") or merged.get("derivationStagePriority")),
            "derivationTargetRole": str(derivation.get("target_role") or derivation.get("targetRole") or merged.get("derivationTargetRole") or ""),
            "derivationActionPolicy": str(derivation.get("action_policy") or derivation.get("actionPolicy") or merged.get("derivationActionPolicy") or ""),
            "derivationAllowedActions": list_of_strings(derivation.get("allowed_actions") or derivation.get("allowedActions") or merged.get("derivationAllowedActions")),
            "derivationBlockedActions": list_of_strings(derivation.get("blocked_actions") or derivation.get("blockedActions") or merged.get("derivationBlockedActions")),
            "polarity": str(merged.get("polarity") or ""),
            "confidence": number_or_none(merged.get("confidence")),
            "decisionStage": str(merged.get("decisionStage") or ""),
            "stagePriority": number_or_none(merged.get("stagePriority")),
            "nativeTypeDbReasoned": bool(merged.get("nativeTypeDbReasoned")),
            "title": str(merged.get("title") or row.get("label") or ""),
            "status": str(merged.get("status") or ""),
            "priority": number_or_none(merged.get("priority")) or 0,
            "source": str(merged.get("source") or ""),
            "rationale": str(merged.get("rationale") or ""),
            "expectedEffect": str(merged.get("expectedEffect") or ""),
            "risk": str(merged.get("risk") or ""),
            "action": str(merged.get("action") or ""),
            "requiresData": list_of_strings(merged.get("requiresData")),
            "proposedRuleJson": json.dumps(proposed, ensure_ascii=False, sort_keys=True) if proposed else str(merged.get("proposedRuleJson") or ""),
            "validationWarnings": list_of_strings(merged.get("validationWarnings")),
            "promptVersion": str(merged.get("promptVersion") or ""),
            "createdAt": str(merged.get("createdAt") or ""),
            "symbols": list_of_strings(merged.get("symbols")),
        }
        return payload

    def relation_rows_from_typeql(self, rows: Iterable[Dict[str, object]], box: str) -> List[Dict[str, object]]:
        return [self.relation_row_from_typeql(row, box) for row in rows or [] if str(row.get("sourceId") or "") and str(row.get("targetId") or "")]

    def relation_row_from_typeql(self, row: Dict[str, object], box: str) -> Dict[str, object]:
        props = json_object(row.get("json"))
        merged = merge_flat_properties({
            "source": row.get("sourceId"),
            "sourceLabel": row.get("sourceLabel"),
            "target": row.get("targetId"),
            "targetLabel": row.get("targetLabel"),
            "type": row.get("type"),
            "ruleId": row.get("ruleId"),
            "ontologyBox": box,
            "updatedAt": row.get("updatedAt"),
            "weight": row.get("weight"),
        }, props)
        return {
            **merged,
            "id": str(row.get("id") or merged.get("id") or ""),
            "source": str(row.get("sourceId") or merged.get("source") or ""),
            "sourceLabel": str(row.get("sourceLabel") or merged.get("sourceLabel") or ""),
            "target": str(row.get("targetId") or merged.get("target") or ""),
            "targetLabel": str(row.get("targetLabel") or merged.get("targetLabel") or ""),
            "type": str(row.get("type") or merged.get("type") or ""),
            "relationType": str(row.get("type") or merged.get("relationType") or merged.get("type") or ""),
            "ontologyBox": str(box or merged.get("ontologyBox") or "ABox"),
            "symbol": str(merged.get("symbol") or ""),
            "ruleId": str(row.get("ruleId") or merged.get("ruleId") or ""),
            "weight": number_or_none(row.get("weight") if row.get("weight") is not None else merged.get("weight")),
            "updatedAt": str(row.get("updatedAt") or merged.get("updatedAt") or ""),
            "propertiesJson": json.dumps(props, ensure_ascii=False, sort_keys=True),
            "polarity": str(merged.get("polarity") or ""),
            "riskImpact": number_or_none(merged.get("riskImpact")),
            "supportImpact": number_or_none(merged.get("supportImpact")),
            "decisionStage": str(merged.get("decisionStage") or ""),
            "stagePriority": number_or_none(merged.get("stagePriority")),
            "targetRole": str(merged.get("targetRole") or ""),
            "actionPolicy": str(merged.get("actionPolicy") or ""),
            "allowedActions": list_of_strings(merged.get("allowedActions")),
            "blockedActions": list_of_strings(merged.get("blockedActions")),
            "aiInfluenceLabel": str(merged.get("aiInfluenceLabel") or ""),
            "inferenceTraceId": str(merged.get("inferenceTraceId") or ""),
            "nativeTypeDbReasoned": bool(merged.get("nativeTypeDbReasoned")),
        }

    def write_graph(self, driver, imported, graph: PortfolioOntology) -> None:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        boxes = node_boxes(graph)
        queries = self.delete_queries(boxes) + self.insert_queries(graph)
        if not queries:
            return
        with driver.transaction(self.database, TransactionType.WRITE) as tx:
            for query in queries:
                tx.query(query).resolve()
            tx.commit()

    def clear_inferencebox(self) -> Dict[str, object]:
        if not self.address:
            return {
                "configured": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": True,
                "status": "driver-missing",
                "graphStore": "typedb",
                "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160],
            }
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    with driver.transaction(self.database, TransactionType.WRITE) as tx:
                        for query in self.delete_queries(["InferenceBox"]):
                            tx.query(query).resolve()
                        tx.commit()
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
            return {
                "configured": True,
                "status": "ok",
                "graphStore": "typedb",
                "clearedBox": "InferenceBox",
            }
        except Exception as error:  # noqa: BLE001 - caller reports clear failure as inference boundary status.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def schema_query(self) -> str:
        return """
define
attribute ontology-id, value string;
attribute ontology-label, value string;
attribute ontology-kind, value string;
attribute ontology-box, value string;
attribute ontology-symbol, value string;
attribute ontology-rule-id, value string;
attribute ontology-account-id, value string;
attribute ontology-snapshot-id, value string;
attribute ontology-tbox-class, value string;
attribute ontology-relation-type, value string;
attribute ontology-updated-at, value string;
attribute ontology-json, value string;
attribute ontology-weight, value double;
attribute ontology-confidence, value double;
attribute ontology-source-value, value string;
attribute ontology-field, value string;
attribute ontology-level-type, value string;
attribute ontology-data-scope, value string;
attribute ontology-domain-scope, value string;
attribute ontology-relation-scope, value string;
attribute ontology-group, value string;
attribute ontology-polarity, value string;
attribute ontology-transition-type, value string;
attribute ontology-signal-group, value string;
attribute ontology-event-type, value string;
attribute ontology-materiality-passed, value string;
attribute ontology-value-number, value double;
attribute ontology-profit-loss-rate, value double;
attribute ontology-materiality-score, value double;
attribute ontology-risk-impact, value double;
attribute ontology-support-impact, value double;
attribute ontology-stage-priority, value double;

entity ontology-node @abstract,
    owns ontology-id @key,
    owns ontology-label,
    owns ontology-kind,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-snapshot-id,
    owns ontology-tbox-class,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-confidence,
    owns ontology-source-value,
    owns ontology-field,
    owns ontology-level-type,
    owns ontology-data-scope,
    owns ontology-domain-scope,
    owns ontology-relation-scope,
    owns ontology-group,
    owns ontology-polarity,
    owns ontology-event-type,
    owns ontology-materiality-passed,
    owns ontology-value-number,
    owns ontology-profit-loss-rate,
    owns ontology-materiality-score,
    plays ontology-assertion:source,
    plays ontology-assertion:target;

entity ontology-entity, sub ontology-node;
entity ontology-evidence, sub ontology-node;
entity ontology-belief, sub ontology-node;
entity ontology-opinion, sub ontology-node;
entity ontology-reasoning-card, sub ontology-node;

relation ontology-assertion,
    relates source,
    relates target,
    owns ontology-id @key,
    owns ontology-relation-type,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-snapshot-id,
    owns ontology-tbox-class,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-weight,
    owns ontology-field,
    owns ontology-polarity,
    owns ontology-transition-type,
    owns ontology-signal-group,
    owns ontology-materiality-passed,
    owns ontology-materiality-score,
    owns ontology-risk-impact,
    owns ontology-support-impact,
    owns ontology-stage-priority;
""".strip()

    def delete_queries(self, boxes: Iterable[str]) -> List[str]:
        queries = []
        for box in sorted(set(str(item or "").strip() for item in boxes if str(item or "").strip())):
            queries.append(
                "match $r isa ontology-assertion, has ontology-box " + typedb_string(box) + "; delete $r;"
            )
            queries.append(
                "match $n isa ontology-node, has ontology-box " + typedb_string(box) + "; delete $n;"
            )
        return queries

    def insert_queries(self, graph: PortfolioOntology) -> List[str]:
        queries: List[str] = []
        updated_at = utc_now()
        node_rows = self.node_rows(graph)
        for row in node_rows:
            queries.append(self.node_insert_query(row, updated_at))
        node_ids = {str(row.get("id") or "") for row in node_rows}
        for row in self.rows_for_relations(graph):
            if str(row.get("source") or "") in node_ids and str(row.get("target") or "") in node_ids:
                queries.append(self.relation_insert_query(row, updated_at))
        for row in self.support_relation_rows(graph):
            if str(row.get("source") or "") in node_ids and str(row.get("target") or "") in node_ids:
                queries.append(self.relation_insert_query(row, updated_at))
        return [item for item in queries if item]

    def node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        rows = []
        rows.extend({**row, "nodeType": "ontology-entity"} for row in self.rows_for_entities(graph))
        rows.extend(self.evidence_node_rows(graph))
        rows.extend(self.belief_node_rows(graph))
        rows.extend(self.opinion_node_rows(graph))
        rows.extend(self.reasoning_card_node_rows(graph))
        return [row for row in rows if str(row.get("id") or "")]

    def evidence_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-evidence",
                "label": row.get("summary") or row.get("id"),
                "kind": "evidence:" + str(row.get("kind") or "evidence"),
                "symbol": "",
                "ruleId": "",
                "tboxClass": "Evidence",
                "propertiesJson": row.get("valueJson") or "{}",
            }
            for row in self.rows_for_evidence(graph)
        ]

    def belief_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-belief",
                "label": row.get("label") or row.get("id"),
                "kind": "belief",
                "symbol": symbol_from_subject(row.get("subject")),
                "ruleId": rule_id_from_value(row.get("id")),
                "tboxClass": "Belief",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
            for row in self.rows_for_beliefs(graph)
        ]

    def opinion_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-opinion",
                "label": str(row.get("symbol") or row.get("id")),
                "kind": "opinion",
                "ruleId": "",
                "tboxClass": "InvestmentOpinion",
                "propertiesJson": row.get("payloadJson") or "{}",
            }
            for row in self.rows_for_opinions(graph)
        ]

    def reasoning_card_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-reasoning-card",
                "label": row.get("companyName") or row.get("symbol") or row.get("id"),
                "kind": "reasoning-card",
                "ruleId": "",
                "tboxClass": "ReasoningCard",
                "propertiesJson": row.get("payloadJson") or "{}",
            }
            for row in self.rows_for_reasoning_cards(graph)
        ]

    def support_relation_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for row in self.rows_for_evidence(graph):
            rows.append({
                "source": row.get("subject"),
                "target": row.get("id"),
                "type": "HAS_EVIDENCE",
                "weight": row.get("confidence") or 0.7,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_beliefs(graph):
            rows.append({
                "source": row.get("subject"),
                "target": row.get("id"),
                "type": "HAS_BELIEF",
                "weight": row.get("confidence") or 0.7,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": row.get("ruleId") or rule_id_from_value(row.get("id")),
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_opinions(graph):
            rows.append({
                "source": "stock:" + str(row.get("symbol") or "").upper(),
                "target": row.get("id"),
                "type": "HAS_OPINION",
                "weight": row.get("conviction") or 0.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_reasoning_cards(graph):
            rows.append({
                "source": "stock:" + str(row.get("symbol") or "").upper(),
                "target": row.get("id"),
                "type": "HAS_REASONING_CARD",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        return [row for row in rows if row.get("source") and row.get("target")]

    def node_insert_query(self, row: Dict[str, object], updated_at: str) -> str:
        node_type = str(row.get("nodeType") or "ontology-entity")
        return (
            "insert $n isa " + node_type
            + ", has ontology-id " + typedb_string(row.get("id"))
            + typeql_has("ontology-label", row.get("label"))
            + typeql_has("ontology-kind", row.get("kind"))
            + typeql_has("ontology-box", row.get("ontologyBox") or "ABox")
            + typeql_has("ontology-symbol", row.get("symbol"))
            + typeql_has("ontology-rule-id", row.get("ruleId"))
            + typeql_has("ontology-account-id", row.get("accountId"))
            + typeql_has("ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId"))
            + typeql_has("ontology-tbox-class", row.get("tboxClass"))
            + typeql_has("ontology-updated-at", updated_at)
            + typeql_has("ontology-json", row.get("propertiesJson"))
            + typeql_has("ontology-confidence", row.get("confidence"), numeric=True)
            + typeql_has("ontology-source-value", row.get("sourceValue"))
            + typeql_has("ontology-field", row.get("field"))
            + typeql_has("ontology-level-type", row.get("levelType"))
            + typeql_has("ontology-data-scope", row.get("dataScope"))
            + typeql_has("ontology-domain-scope", row.get("domainScope"))
            + typeql_has("ontology-relation-scope", row.get("relationScope"))
            + typeql_has("ontology-group", row.get("group"))
            + typeql_has("ontology-polarity", row.get("polarity"))
            + typeql_has("ontology-event-type", row.get("eventType"))
            + typeql_has_bool_string("ontology-materiality-passed", row.get("materialityPassed"))
            + typeql_has("ontology-value-number", row.get("valueNumber"), numeric=True)
            + typeql_has("ontology-profit-loss-rate", row.get("profitLossRate"), numeric=True)
            + typeql_has("ontology-materiality-score", row.get("materialityScore"), numeric=True)
            + ";"
        )

    def relation_insert_query(self, row: Dict[str, object], updated_at: str) -> str:
        relation_id = relation_row_id(row)
        return (
            "match "
            "$source isa ontology-node, has ontology-id " + typedb_string(row.get("source")) + "; "
            "$target isa ontology-node, has ontology-id " + typedb_string(row.get("target")) + "; "
            "insert "
            "$r isa ontology-assertion, links (source: $source, target: $target)"
            + ", has ontology-id " + typedb_string(relation_id)
            + typeql_has("ontology-relation-type", row.get("type"))
            + typeql_has("ontology-box", row.get("ontologyBox") or "ABox")
            + typeql_has("ontology-symbol", row.get("symbol"))
            + typeql_has("ontology-rule-id", row.get("ruleId"))
            + typeql_has("ontology-account-id", row.get("accountId"))
            + typeql_has("ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId"))
            + typeql_has("ontology-tbox-class", row.get("tboxClass"))
            + typeql_has("ontology-updated-at", updated_at)
            + typeql_has("ontology-json", row.get("propertiesJson"))
            + typeql_has("ontology-weight", row.get("weight"), numeric=True)
            + typeql_has("ontology-field", row.get("field"))
            + typeql_has("ontology-polarity", row.get("polarity"))
            + typeql_has("ontology-transition-type", row.get("transitionType"))
            + typeql_has("ontology-signal-group", row.get("signalGroup"))
            + typeql_has_bool_string("ontology-materiality-passed", row.get("materialityPassed"))
            + typeql_has("ontology-materiality-score", row.get("materialityScore"), numeric=True)
            + typeql_has("ontology-risk-impact", row.get("riskImpact"), numeric=True)
            + typeql_has("ontology-support-impact", row.get("supportImpact"), numeric=True)
            + typeql_has("ontology-stage-priority", row.get("stagePriority"), numeric=True)
            + ";"
        )

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload or {}
        try:
            rules = rulebox_rules_from_payload(payload) if (payload.get("rules") is not None or payload.get("rulesJson")) else default_graph_inference_rules()
        except ValueError as error:
            return {"configured": True, "saved": False, "seeded": False, "status": "invalid-rulebox", "graphStore": "typedb", "reason": str(error)}
        rules = list(rules)
        rules_payload = rulebox_rules_to_payload(rules)
        self._last_rules = rules
        result = self.save_graph(ontology_seed_graph(rules))
        result.update({
            "configured": True,
            "seeded": bool(result.get("saved")),
            "engineVersion": GRAPH_REASONER_VERSION,
            "ruleCount": len(rules),
            "graphStore": "typedb",
        })
        if typedb_bool(payload.get("replaceRuleBox")) and result.get("saved"):
            expected_rulebox = rulebox_runtime_metadata(rules_payload)
            rulebox_result = self.save_rulebox({"rules": rules_payload})
            expected_structure = rulebox_structural_fingerprint(rules_payload)
            active_rules_payload = rulebox_result.get("rules") if isinstance(rulebox_result.get("rules"), list) else []
            active_structure = rulebox_structural_fingerprint(active_rules_payload)
            active_rule_count = int(number_or_none(rulebox_result.get("ruleCount") or rulebox_result.get("ruleboxRuleCount")) or 0)
            active_rule_hash = str(rulebox_result.get("ruleboxRulesHash") or "")
            hash_matched = active_rule_hash == expected_rulebox["ruleboxRulesHash"]
            replace_verified = (
                bool(rulebox_result.get("saved"))
                and str(rulebox_result.get("status") or "") == "ok"
                and active_rule_count == len(rules_payload)
                and active_structure == expected_structure
            )
            result.update({
                "ruleBoxReplaceRequested": True,
                "ruleBoxReplaced": replace_verified,
                "ruleBoxHashMatched": hash_matched,
                "activeRuleBoxRuleCount": active_rule_count,
                "expectedRuleBoxRuleCount": len(rules_payload),
                "activeRuleBoxShortHash": str(rulebox_result.get("ruleboxShortHash") or active_rule_hash[:12]),
                "expectedRuleBoxShortHash": expected_rulebox["ruleboxShortHash"],
                "ruleBoxReplaceResult": {
                    "saved": bool(rulebox_result.get("saved")),
                    "status": rulebox_result.get("status") or "",
                    "reason": rulebox_result.get("reason") or "",
                    "ruleCount": active_rule_count,
                    "conditionCount": int(number_or_none(rulebox_result.get("conditionCount") or rulebox_result.get("ruleboxConditionCount")) or 0),
                    "derivationCount": int(number_or_none(rulebox_result.get("derivationCount") or rulebox_result.get("ruleboxDerivationCount")) or 0),
                    "ruleboxShortHash": str(rulebox_result.get("ruleboxShortHash") or active_rule_hash[:12]),
                },
            })
            if replace_verified and typedb_bool(payload.get("clearInference")):
                result["clearInferenceResult"] = self.clear_inferencebox()
            if not replace_verified:
                result.update({
                    "saved": False,
                    "seeded": False,
                    "status": rulebox_result.get("status") or "rulebox-replace-failed",
                    "reason": (
                        "RuleBox replace requested but active RuleBox did not match the seeded rules. "
                        + str(rulebox_result.get("reason") or "")
                    ).strip(),
                })
        return result

    def rulebox_snapshot(self) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().rulebox_snapshot()
        try:
            entities = self.read_entity_rows(["RuleBox", "RuleBoxGovernance"])
            relations = self.read_relation_rows(["RuleBox", "RuleBoxGovernance"])
        except Exception as error:  # noqa: BLE001 - admin read model must fail closed.
            rules = rulebox_rules_to_payload(self._last_rules or default_graph_inference_rules())
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "source": "typedb-typeql",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "engineVersion": GRAPH_REASONER_VERSION,
                "rules": [],
                "ruleCount": 0,
                "conditionCount": 0,
                "derivationCount": 0,
                "relationTypes": [],
                "defaultsFallbackUsed": False,
                "bootstrapAvailable": True,
                "bootstrapRuleCount": len(rules),
                "bootstrapRules": rules,
                "versions": [],
                "versionCount": 0,
                "changeCandidates": rulebox_governance_candidates([], []),
            }
        rowsets = {
            "rules": [row for row in entities if entity_node_kind(row) == "rule" and row.get("ontologyBox") == "RuleBox"],
            "conditions": [row for row in entities if entity_node_kind(row) == "rule-condition" and row.get("ontologyBox") == "RuleBox"],
            "derivations": [row for row in entities if entity_node_kind(row) == "relation-template" and row.get("ontologyBox") == "RuleBox"],
            "relationTypes": relation_type_rows_from_derivations(entities, relations),
            "versions": [row for row in entities if entity_node_kind(row) == "rulebox-version" and row.get("ontologyBox") == "RuleBoxGovernance"],
            "candidates": [row for row in entities if entity_node_kind(row) == "rule-change-candidate" and row.get("ontologyBox") == "RuleBoxGovernance"],
        }
        snapshot = rulebox_snapshot_from_rows(rowsets, "typedb-typeql")
        snapshot.update({"graphStore": "typedb", "source": "typedb-typeql"})
        snapshot.update(rulebox_runtime_metadata(snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []))
        if snapshot.get("status") == "ok":
            try:
                self._last_rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
            except ValueError:
                pass
        snapshot["nativeReasoningProfile"] = typedb_native_reasoning_profile(snapshot.get("rules") or [])
        return snapshot

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        try:
            rules = rulebox_rules_from_payload(payload or {})
        except ValueError as error:
            return {"configured": True, "saved": False, "status": "invalid-rulebox", "graphStore": "typedb", "reason": str(error)}
        self._last_rules = list(rules)
        save_result = self.save_graph(rulebox_graph_from_rules(self._last_rules, include_tbox=False))
        snapshot = self.rulebox_snapshot()
        snapshot.update({
            "saved": bool(save_result.get("saved")),
            "status": save_result.get("status") or snapshot.get("status"),
            "reason": save_result.get("reason") or snapshot.get("reason") or "",
            "saveResult": save_result,
        })
        return snapshot

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().run_rulebox(payload)
        payload = payload if isinstance(payload, dict) else {}
        target_symbols = clean_symbols_from_payload(
            payload.get("symbols")
            or payload.get("targetSymbols")
            or payload.get("changedSymbols")
        )
        force_clear_requested = typedb_bool(payload.get("forceClearInference"))
        if "forceClearInference" not in payload:
            force_clear_requested = typedb_bool(payload.get("clearInference"))
        destructive_clear_allowed = typedb_bool(payload.get("allowDestructiveInferenceClear"))
        prune_requested = typedb_bool(payload.get("pruneOldGenerations")) if "pruneOldGenerations" in payload else True
        keep_generation_count = max(1, int(number_or_none(payload.get("keepGenerationCount")) or self.inference_generation_keep_count))
        generation_id = str(payload.get("generationId") or inference_generation_id())
        generation_at = utc_now()
        clear_requested = force_clear_requested and destructive_clear_allowed
        clear_result = {}
        if force_clear_requested and not clear_requested:
            clear_result = {
                "configured": True,
                "status": "skipped",
                "graphStore": "typedb",
                "reason": "InferenceBox is generation-scoped; destructive clear is skipped unless allowDestructiveInferenceClear is true.",
                "preservedPreviousInference": True,
            }
        try:
            abox_rows = self.read_entity_rows(["ABox"], limit=1)
        except Exception as error:  # noqa: BLE001 - report TypeDB read failures through diagnostics.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "source": "typedbRuleBox",
                "reasoningMode": "typedb-rulebox-materialization-blocked",
                "reasonCode": typedb_error_code(error),
                "reason": "TypeDB ABox 조회 실패: " + str(error)[:180],
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "clearResult": clear_result,
            }
        if not abox_rows:
            return {
                "configured": True,
                "status": "missing-abox",
                "graphStore": "typedb",
                "source": "typedbRuleBox",
                "reasoningMode": "typedb-rulebox-materialization-blocked",
                "reason": "TypeDB에 실행 가능한 ABox 그래프가 없습니다.",
                "statementCount": 0,
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "clearResult": clear_result,
            }
        snapshot = self.rulebox_snapshot()
        rules = snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
        rulebox_metadata = rulebox_runtime_metadata(rules)
        native_profile = typedb_native_reasoning_profile(rules)
        if str(snapshot.get("status") or "") != "ok" or not rules:
            return {
                "configured": True,
                "status": "rulebox-not-ready",
                "graphStore": "typedb",
                "source": "typedbRuleBox",
                "reasoningMode": "typedb-rulebox-materialization-blocked",
                "reason": str(snapshot.get("reason") or "TypeDB RuleBox rules are not available."),
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "clearResult": clear_result,
                "nativeReasoningProfile": native_profile,
                "ruleboxMetadata": rulebox_metadata,
                **rulebox_metadata,
            }
        try:
            graph = self.load_graph_from_typedb(["ABox"])
            before_entities = len(graph.entities)
            before_relations = len(graph.relations)
            run_graph_reasoner(graph, rulebox_rules_from_payload({"rules": rules}), target_symbols=target_symbols)
            inference_graph = typedb_inferencebox_graph(
                graph,
                generation_id=generation_id,
                generation_at=generation_at,
                rulebox_metadata=rulebox_metadata,
            )
            if clear_requested:
                clear_result = self.clear_inferencebox()
                if str(clear_result.get("status") or "") != "ok":
                    return {
                        "configured": True,
                        "status": "error",
                        "graphStore": "typedb",
                        "source": "typedbRuleBox",
                        "reasoningMode": "typedb-rulebox-materialization-blocked",
                        "reasonCode": str(clear_result.get("reasonCode") or "typedbClearError"),
                        "reason": "TypeDB InferenceBox 초기화 실패: " + str(clear_result.get("reason") or clear_result.get("status") or ""),
                        "statementCount": 0,
                        "relationTypes": [],
                        "nativeTypeDbReasoningUsed": False,
                        "typedbNativeFunctionReasoningUsed": False,
                        "typedbBootstrapReasoningUsed": False,
                        "pythonBootstrapDisabled": True,
                        "clearResult": clear_result,
                        "nativeReasoningProfile": native_profile,
                        "ruleboxMetadata": rulebox_metadata,
                        **rulebox_metadata,
                    }
            save_result = self.write_inferencebox_graph(inference_graph)
        except Exception as error:  # noqa: BLE001 - expose materialization failures to monitoring diagnostics.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "source": "typedbRuleBox",
                "reasoningMode": "typedb-rulebox-materialized",
                "reasonCode": typedb_error_code(error),
                "reason": "TypeDB RuleBox materialization failed: " + str(error)[:180],
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "clearResult": clear_result,
                "nativeReasoningProfile": native_profile,
                "ruleboxMetadata": rulebox_metadata,
                **rulebox_metadata,
            }
        relation_types = sorted({
            str(item.relation_type or "")
            for item in inference_graph.relations
            if str(item.relation_type or "").strip()
        })
        materialized_entity_count = len(inference_graph.entities) + len(inference_graph.evidence) + len(inference_graph.beliefs)
        materialized_relation_count = len(inference_graph.relations)
        has_materialized_relations = materialized_relation_count > 0
        saved_ok = bool(save_result.get("saved"))
        prune_result = self.prune_inferencebox_generations(generation_id, keep_count=keep_generation_count) if saved_ok and prune_requested else {}
        return {
            "configured": True,
            "status": ("ok" if has_materialized_relations else "empty") if saved_ok else str(save_result.get("status") or "error"),
            "graphStore": "typedb",
            "source": "typedbRuleBox",
            "reasoningMode": "typedb-rulebox-materialized",
            "reason": ("" if has_materialized_relations else "TypeDB RuleBox matched no ABox facts.") if saved_ok else str(save_result.get("reason") or ""),
            "statementCount": materialized_entity_count + materialized_relation_count,
            "entityCount": materialized_entity_count,
            "relationCount": materialized_relation_count,
            "traceCount": len([item for item in inference_graph.entities if item.kind == "inference-trace"]),
            "relationTypes": relation_types,
            "nativeTypeDbReasoningUsed": saved_ok and has_materialized_relations,
            "typedbNativeFunctionReasoningUsed": False,
            "typedbNativeReasoningReady": native_profile.get("status") in {"ready", "partial"},
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "materializationSource": "typedb-abox-rulebox",
            "inferenceGenerationId": generation_id,
            "inferenceGenerationAt": generation_at,
            "targetSymbols": target_symbols,
            "incrementalScope": "symbols" if target_symbols else "all-symbols",
            "readAboxEntityCount": before_entities,
            "readAboxRelationCount": before_relations,
            "clearResult": clear_result,
            "pruneResult": prune_result,
            "saveResult": save_result,
            "nativeReasoningProfile": native_profile,
            "ruleboxMetadata": rulebox_metadata,
            **rulebox_metadata,
        }

    def write_inferencebox_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        if not self.address:
            return {
                "configured": False,
                "saved": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": True,
                "saved": False,
                "status": "driver-missing",
                "graphStore": "typedb",
                "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160],
            }
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        node_rows = [
            row for row in self.node_rows(graph)
            if str(row.get("ontologyBox") or "") == "InferenceBox"
        ]
        relation_rows = [
            row for row in self.rows_for_relations(graph) + self.support_relation_rows(graph)
            if str(row.get("ontologyBox") or "") == "InferenceBox"
        ]
        queries = [self.node_insert_query(row, utc_now()) for row in node_rows]
        queries.extend(self.relation_insert_query(row, utc_now()) for row in relation_rows if row.get("source") and row.get("target"))
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    if queries:
                        with driver.transaction(self.database, TransactionType.WRITE) as tx:
                            for query in queries:
                                tx.query(query).resolve()
                            tx.commit()
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
            return {
                "configured": True,
                "saved": True,
                "status": "ok",
                "graphStore": "typedb",
                "entityCount": len(node_rows),
                "relationCount": len(relation_rows),
                "statementCount": len(queries),
                "inferenceGenerationId": str((graph.worldview or {}).get("inferenceGenerationId") or ""),
                "inferenceGenerationAt": str((graph.worldview or {}).get("inferenceGenerationAt") or ""),
            }
        except Exception as error:  # noqa: BLE001 - materialization failure must be visible to diagnostics.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "entityCount": len(node_rows),
                "relationCount": len(relation_rows),
                "statementCount": len(queries),
                "inferenceGenerationId": str((graph.worldview or {}).get("inferenceGenerationId") or ""),
            }

    def prune_inferencebox_generations(self, active_generation_id: str, keep_count: int = 2) -> Dict[str, object]:
        active_generation_id = str(active_generation_id or "").strip()
        if not active_generation_id:
            return {"configured": bool(self.address), "status": "skipped", "reason": "active generation id is empty"}
        try:
            entity_rows = self.read_entity_rows(["InferenceBox"])
            relation_rows = self.read_relation_rows(["InferenceBox"])
        except Exception as error:  # noqa: BLE001 - pruning must not fail materialization.
            return {"configured": True, "status": "error", "reason": str(error)[:180], "activeGenerationId": active_generation_id}
        records = inference_generation_records(entity_rows, relation_rows)
        if not records:
            return {"configured": True, "status": "skipped", "reason": "no generation-scoped InferenceBox rows", "activeGenerationId": active_generation_id}
        keep = {active_generation_id}
        for item in sorted(records, key=lambda row: str(row.get("latestAt") or ""), reverse=True)[: max(1, int(keep_count or 2))]:
            keep.add(str(item.get("generationId") or ""))
        prune_ids = [
            str(item.get("generationId") or "")
            for item in records
            if str(item.get("generationId") or "") and str(item.get("generationId") or "") not in keep
        ]
        if not prune_ids:
            return {
                "configured": True,
                "status": "ok",
                "activeGenerationId": active_generation_id,
                "keptGenerationCount": len(keep),
                "deletedGenerationCount": 0,
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {"configured": True, "status": "driver-missing", "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160]}
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        queries = []
        for generation_id in prune_ids:
            queries.append(
                "match $r isa ontology-assertion, has ontology-box \"InferenceBox\", has ontology-snapshot-id "
                + typedb_string(generation_id)
                + "; delete $r;"
            )
            queries.append(
                "match $n isa ontology-node, has ontology-box \"InferenceBox\", has ontology-snapshot-id "
                + typedb_string(generation_id)
                + "; delete $n;"
            )
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    with driver.transaction(self.database, TransactionType.WRITE) as tx:
                        for query in queries:
                            tx.query(query).resolve()
                        tx.commit()
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
            return {
                "configured": True,
                "status": "ok",
                "activeGenerationId": active_generation_id,
                "keptGenerationCount": len(keep),
                "deletedGenerationCount": len(prune_ids),
                "deletedGenerationIds": prune_ids[:20],
            }
        except Exception as error:  # noqa: BLE001 - pruning is non-critical but must be visible.
            return {
                "configured": True,
                "status": "error",
                "reason": str(error)[:220],
                "activeGenerationId": active_generation_id,
                "deletedGenerationCount": 0,
            }

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        safe_limit = max(1, min(500, int(limit or 80)))
        if not self.address:
            return NullTypeDBOntologyGraphRepository().inferencebox_snapshot(clean_symbols, safe_limit)
        try:
            return self.inferencebox_snapshot_from_typedb(clean_symbols, safe_limit)
        except Exception as error:  # noqa: BLE001 - expose TypeDB read failures to monitoring diagnostics.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "reasoningMode": "typedb-typeql-read",
                "querySource": "typedb-typeql",
                "typedbReadStatus": "error",
                "reasonCode": typedb_error_code(error),
                "typedbReadReason": str(error)[:180],
                "reason": "TypeDB InferenceBox 조회 실패: " + str(error)[:180],
                "symbols": clean_symbols,
                "entities": [],
                "relations": [],
                "traces": [],
                "entityCount": 0,
                "relationCount": 0,
                "traceCount": 0,
                "nativeEntityCount": 0,
                "nativeRelationCount": 0,
                "nativeTraceCount": 0,
                "nativeTypeDbReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
            }
    def inferencebox_snapshot_from_typedb(self, clean_symbols: List[str], safe_limit: int) -> Dict[str, object]:
        all_entity_rows = self.read_entity_rows(["InferenceBox"])
        all_relation_rows = self.read_relation_rows(["InferenceBox"])
        active_generation = active_inference_generation(all_entity_rows, all_relation_rows)
        generation_id = str((active_generation or {}).get("generationId") or "")
        generation_scoped = bool(generation_id)
        scoped_entity_rows = [
            row for row in all_entity_rows
            if not generation_scoped or row_inference_generation_id(row) == generation_id
        ]
        scoped_relation_rows = [
            row for row in all_relation_rows
            if not generation_scoped or row_inference_generation_id(row) == generation_id
        ]
        entity_rows = [
            row for row in scoped_entity_rows
            if not clean_symbols or str(row.get("symbol") or "").upper() in clean_symbols
        ]
        relation_rows = [
            row for row in scoped_relation_rows
            if not clean_symbols
            or any(symbol in str(row.get(key) or "").upper() for symbol in clean_symbols for key in ["source", "target", "symbol"])
        ]
        native_entity_rows = [row for row in entity_rows if bool(row.get("nativeTypeDbReasoned"))]
        native_relation_rows = [row for row in relation_rows if bool(row.get("nativeTypeDbReasoned"))]
        native_trace_rows = [row for row in native_entity_rows if str(row.get("kind") or "") == "inference-trace"]
        ignored_relation_count = len(relation_rows) - len(native_relation_rows)
        ignored_trace_count = len([row for row in entity_rows if str(row.get("kind") or "") == "inference-trace"]) - len(native_trace_rows)
        generation_rulebox_metadata = inference_rulebox_metadata(scoped_entity_rows, scoped_relation_rows)
        rowsets = {
            "entityCounts": [{"entityCount": len(native_entity_rows), "nativeEntityCount": len(native_entity_rows)}],
            "relationCounts": [{"relationCount": len(native_relation_rows), "nativeRelationCount": len(native_relation_rows)}],
            "traceCounts": [{"traceCount": len(native_trace_rows), "nativeTraceCount": len(native_trace_rows)}],
            "entities": native_entity_rows[:safe_limit],
            "relations": native_relation_rows[:safe_limit],
            "traces": [{**row, "matchedConditionIds": matched_condition_ids(row)} for row in native_trace_rows[:safe_limit]],
        }
        snapshot = inferencebox_snapshot_from_rows(rowsets, "typedb-typeql", clean_symbols)
        has_native_output = bool(native_relation_rows or native_trace_rows)
        snapshot.update({
            "graphStore": "typedb",
            "source": "typedbInferenceBox",
            "status": "ok" if has_native_output else "empty",
            "reasoningMode": "typedb-rulebox-materialized" if has_native_output else "typedb-rulebox-materialization-required",
            "querySource": "typedb-typeql",
            "typedbReadStatus": "ok",
            "reason": "" if has_native_output else "TypeDB InferenceBox 관계가 아직 없습니다. RuleBox materialization 결과를 확인해야 합니다.",
            "nativeTypeDbReasoningUsed": has_native_output,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "inferenceGenerationId": generation_id,
            "inferenceGenerationAt": str((active_generation or {}).get("latestAt") or ""),
            "generationScoped": generation_scoped,
            "generationCount": len(inference_generation_records(all_entity_rows, all_relation_rows)),
            "inactiveGenerationEntityCount": max(0, len(all_entity_rows) - len(scoped_entity_rows)) if generation_scoped else 0,
            "inactiveGenerationRelationCount": max(0, len(all_relation_rows) - len(scoped_relation_rows)) if generation_scoped else 0,
            "ignoredNonNativeRelationCount": ignored_relation_count,
            "ignoredNonNativeTraceCount": ignored_trace_count,
            **generation_rulebox_metadata,
        })
        return snapshot

    def load_graph_from_typedb(self, boxes: Iterable[str] = None) -> PortfolioOntology:
        graph = PortfolioOntology("typedb-read-model")
        for row in self.read_entity_rows(boxes or ["ABox"]):
            properties = json_object(row.get("propertiesJson"))
            properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
            if row.get("symbol"):
                properties.setdefault("symbol", row.get("symbol"))
            if row.get("tboxClass"):
                properties.setdefault("tboxClass", row.get("tboxClass"))
            graph.entities.append(OntologyEntity(
                str(row.get("id") or ""),
                str(row.get("label") or row.get("id") or ""),
                str(row.get("kind") or ""),
                properties,
            ))
        for row in self.read_relation_rows(boxes or ["ABox"]):
            properties = json_object(row.get("propertiesJson"))
            properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
            if row.get("ruleId"):
                properties.setdefault("ruleId", row.get("ruleId"))
            graph.relations.append(OntologyRelation(
                str(row.get("source") or ""),
                str(row.get("target") or ""),
                str(row.get("type") or ""),
                float(number_or_none(row.get("weight")) or 1.0),
                [],
                properties,
            ))
        return graph

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        if not self._last_rules:
            try:
                snapshot = self.rulebox_snapshot()
                self._last_rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
            except ValueError:
                self._last_rules = []
        normalized = [
            normalize_rule_change_candidate(candidate, existing_rule_ids=[rule.rule_id for rule in self._last_rules])
            for candidate in (candidates or [])
            if isinstance(candidate, dict)
        ]
        normalized = [item for item in normalized if item]
        if not normalized:
            return {"configured": bool(self.address), "status": "no-candidates", "graphStore": "typedb", "candidateCount": 0, "savedCount": 0}
        graph = PortfolioOntology("typedb-rule-change-candidates")
        for item in normalized:
            from ..domain.ontology_contracts import OntologyEntity

            graph.entities.append(OntologyEntity(
                "rule-change-candidate:" + str(item.get("id") or item.get("title") or len(graph.entities)),
                str(item.get("title") or "Rule change candidate"),
                "rule-change-candidate",
                {
                    "ontologyBox": "RuleBoxGovernance",
                    "boundedContext": "reasoning-insight",
                    "tboxClass": "RuleChangeCandidate",
                    "properties": item,
                },
            ))
        save_result = self.save_graph(graph)
        return {
            "configured": bool(self.address),
            "status": save_result.get("status"),
            "graphStore": "typedb",
            "candidateCount": len(normalized),
            "savedCount": len(normalized) if save_result.get("saved") else 0,
            "saveResult": save_result,
        }


def normalized_boxes(boxes: Iterable[str] = None) -> List[str]:
    values = [str(item or "").strip() for item in (boxes or []) if str(item or "").strip()]
    return sorted(set(values or ["ABox"]))


def relation_type_rows_from_derivations(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    values = set()
    for row in entity_rows or []:
        if entity_node_kind(row) == "relation-template":
            values.add(str(row.get("derivationRelationType") or row.get("relationType") or "").upper())
    for row in relation_rows or []:
        if row.get("ontologyBox") == "RuleBox":
            values.add(str(row.get("type") or row.get("relationType") or "").upper())
    return [{"relationType": item} for item in sorted(value for value in values if value)]


def typedb_inferencebox_graph(
    graph: PortfolioOntology,
    generation_id: str = None,
    generation_at: str = None,
    rulebox_metadata: Dict[str, object] = None,
) -> PortfolioOntology:
    generation_id = str(generation_id or inference_generation_id())
    generation_at = str(generation_at or utc_now())
    rulebox_metadata = dict(rulebox_metadata or {})
    inference_graph = PortfolioOntology(str(graph.portfolio_id or "typedb-inferencebox"))
    id_map: Dict[str, str] = {}
    for item in graph.entities:
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox":
            id_map[item.entity_id] = generated_inference_id(item.entity_id, generation_id)
    for item in graph.evidence:
        if str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "")) == "InferenceBox":
            id_map[item.evidence_id] = generated_inference_id(item.evidence_id, generation_id)
    inference_graph.entities = [
        OntologyEntity(
            id_map.get(item.entity_id, item.entity_id),
            item.label,
            item.kind,
            typedb_reasoned_properties(item.properties, generation_id, generation_at, item.entity_id, rulebox_metadata),
        )
        for item in graph.entities
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
    ]
    inference_graph.relations = [
        OntologyRelation(
            id_map.get(item.source, item.source),
            id_map.get(item.target, item.target),
            item.relation_type,
            item.weight,
            [id_map.get(value, value) for value in list(item.evidence_ids or [])],
            typedb_reasoned_properties(item.properties, generation_id, generation_at, rulebox_metadata=rulebox_metadata),
        )
        for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
    ]
    inference_graph.evidence = [
        OntologyEvidence(
            id_map.get(item.evidence_id, item.evidence_id),
            id_map.get(item.subject, item.subject),
            item.kind,
            item.source,
            item.summary,
            typedb_reasoned_properties(item.value, generation_id, generation_at, item.evidence_id, rulebox_metadata),
            item.confidence,
        )
        for item in graph.evidence
        if str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "")) == "InferenceBox"
    ]
    inference_graph.beliefs = []
    inference_graph.worldview = {
        "reasoningMode": "typedb-rulebox-materialized",
        "materializationSource": "typedb-abox-rulebox",
        "inferenceGenerationId": generation_id,
        "inferenceGenerationAt": generation_at,
        **rulebox_metadata,
    }
    return inference_graph


def typedb_reasoned_properties(
    properties: Dict[str, object],
    generation_id: str = "",
    generation_at: str = "",
    original_id: str = "",
    rulebox_metadata: Dict[str, object] = None,
) -> Dict[str, object]:
    payload = dict(properties or {})
    for key, value in dict(rulebox_metadata or {}).items():
        if value not in (None, "", [], {}):
            payload.setdefault(key, value)
    payload.setdefault("ontologyBox", "InferenceBox")
    payload.setdefault("box", "InferenceBox")
    payload["nativeTypeDbReasoned"] = True
    payload["typeDbMaterialized"] = True
    payload["graphInferenceUsed"] = True
    payload["typedbMaterialized"] = True
    payload["reasoningMode"] = "typedb-rulebox-materialized"
    payload["materializationSource"] = "typedb-abox-rulebox"
    if generation_id:
        payload["inferenceGenerationId"] = generation_id
        payload["snapshotId"] = generation_id
        payload["aboxSnapshotId"] = generation_id
    if generation_at:
        payload["inferenceGenerationAt"] = generation_at
        payload["asOf"] = generation_at
    if original_id:
        payload["originalId"] = original_id
    return payload


def inference_rulebox_metadata(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    keys = [
        "ruleboxRulesHash",
        "ruleboxShortHash",
        "ruleboxRuleCount",
        "ruleboxConditionCount",
        "ruleboxDerivationCount",
        "ruleboxEngineVersion",
    ]
    metadata: Dict[str, object] = {}
    for row in list(entity_rows or []) + list(relation_rows or []):
        if not isinstance(row, dict):
            continue
        props = json_object(row.get("propertiesJson"))
        props.update(json_object(row.get("valueJson")))
        source = {**props, **row}
        for key in keys:
            value = source.get(key)
            if value not in (None, "", [], {}) and key not in metadata:
                metadata[key] = value
        if all(key in metadata for key in keys):
            break
    for key in ["ruleboxRuleCount", "ruleboxConditionCount", "ruleboxDerivationCount"]:
        if key in metadata:
            metadata[key] = int(number_or_none(metadata.get(key)) or 0)
    return metadata


def row_inference_generation_id(row: Dict[str, object]) -> str:
    if not isinstance(row, dict):
        return ""
    direct = str(row.get("inferenceGenerationId") or row.get("snapshotId") or row.get("aboxSnapshotId") or "").strip()
    if direct:
        return direct
    try:
        props = json.loads(str(row.get("propertiesJson") or "{}"))
    except json.JSONDecodeError:
        props = {}
    return str((props or {}).get("inferenceGenerationId") or (props or {}).get("snapshotId") or "").strip()


def row_inference_generation_at(row: Dict[str, object]) -> str:
    if not isinstance(row, dict):
        return ""
    direct = str(row.get("inferenceGenerationAt") or row.get("asOf") or row.get("updatedAt") or "").strip()
    if direct:
        return direct
    try:
        props = json.loads(str(row.get("propertiesJson") or "{}"))
    except json.JSONDecodeError:
        props = {}
    return str((props or {}).get("inferenceGenerationAt") or (props or {}).get("asOf") or "").strip()


def inference_generation_records(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    records: Dict[str, Dict[str, object]] = {}
    for row in list(entity_rows or []) + list(relation_rows or []):
        generation_id = row_inference_generation_id(row)
        if not generation_id:
            continue
        record = records.setdefault(generation_id, {
            "generationId": generation_id,
            "latestAt": "",
            "entityCount": 0,
            "relationCount": 0,
        })
        latest_at = row_inference_generation_at(row)
        if latest_at > str(record.get("latestAt") or ""):
            record["latestAt"] = latest_at
        if "relationType" in row or "type" in row and row.get("source"):
            record["relationCount"] = int(record.get("relationCount") or 0) + 1
        else:
            record["entityCount"] = int(record.get("entityCount") or 0) + 1
    return sorted(records.values(), key=lambda item: str(item.get("latestAt") or ""), reverse=True)


def active_inference_generation(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    records = inference_generation_records(entity_rows, relation_rows)
    return records[0] if records else {}


def entity_node_kind(row: Dict[str, object]) -> str:
    return str(row.get("nodeKind") or row.get("kind") or "")


def typedb_native_reasoning_profile(rules: Iterable[object]) -> Dict[str, object]:
    rule_payloads = [
        item.to_dict() if hasattr(item, "to_dict") else dict(item)
        for item in (rules or [])
        if isinstance(item, dict) or hasattr(item, "to_dict")
    ]
    rule_profiles = [typedb_native_rule_profile(rule) for rule in rule_payloads]
    ready = [item for item in rule_profiles if item.get("status") == "ready"]
    partial = [item for item in rule_profiles if item.get("status") == "partial"]
    blocked = [item for item in rule_profiles if item.get("status") == "blocked"]
    unsupported = sum(int(item.get("unsupportedConditionCount") or 0) for item in rule_profiles)
    supported = sum(int(item.get("supportedConditionCount") or 0) for item in rule_profiles)
    status = "ready" if rule_profiles and len(ready) == len(rule_profiles) else ("partial" if ready or partial else "blocked")
    blockers = [
        blocker
        for item in rule_profiles
        for blocker in (item.get("blockers") or [])
        if isinstance(blocker, dict)
    ]
    return {
        "version": TYPEDB_NATIVE_REASONING_PROFILE_VERSION,
        "graphStore": "typedb",
        "reasoningModel": "typedb-rulebox-materialization",
        "status": status,
        "ruleCount": len(rule_profiles),
        "readyRuleCount": len(ready),
        "partialRuleCount": len(partial),
        "blockedRuleCount": len(blocked),
        "supportedConditionCount": supported,
        "unsupportedConditionCount": unsupported,
        "materializationRequired": True,
        "materializationTarget": "InferenceBox",
        "materializationStrategy": "typedb-abox-rulebox-to-inferencebox",
        "reason": "TypeDB ABox facts and stored RuleBox rules are materialized into TypeDB InferenceBox.",
        "readyRules": [item.get("ruleId") for item in ready][:24],
        "partialRules": [item.get("ruleId") for item in partial][:24],
        "blockedRules": [item.get("ruleId") for item in blocked][:24],
        "blockers": blockers[:24],
        "rules": rule_profiles[:80],
    }


def typedb_native_rule_profile(rule: Dict[str, object]) -> Dict[str, object]:
    conditions = [item for item in (rule.get("conditions") or []) if isinstance(item, dict)]
    derivations = [item for item in (rule.get("derivations") or []) if isinstance(item, dict)]
    condition_profiles = [typedb_native_condition_profile(item) for item in conditions]
    blockers = [
        blocker
        for item in condition_profiles
        for blocker in (item.get("blockers") or [])
        if isinstance(blocker, dict)
    ]
    supported = [item for item in condition_profiles if item.get("status") == "ready"]
    partial = [item for item in condition_profiles if item.get("status") == "partial"]
    if blockers and not supported and not partial:
        status = "blocked"
    elif blockers:
        status = "partial"
    else:
        status = "ready"
    return {
        "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
        "label": str(rule.get("label") or ""),
        "status": status,
        "conditionCount": len(conditions),
        "derivationCount": len(derivations),
        "supportedConditionCount": len(supported),
        "unsupportedConditionCount": len(blockers),
        "blockers": blockers,
        "conditions": condition_profiles,
        "functionBlueprint": typedb_function_blueprint(rule) if status in {"ready", "partial"} else "",
    }


def typedb_native_condition_profile(condition: Dict[str, object]) -> Dict[str, object]:
    condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "")
    kind = str(condition.get("kind") or "")
    blockers: List[Dict[str, object]] = []
    operator = str(condition.get("operator") or "==")
    if operator not in TYPEDB_FUNCTION_OPERATORS:
        blockers.append(condition_blocker(condition_id, "unsupported-operator", "TypeDB function profile does not support operator " + operator))
    if kind == "subject_property":
        field = str(condition.get("field") or "")
        if field not in TYPEDB_FUNCTION_SUBJECT_FIELDS:
            blockers.append(condition_blocker(condition_id, "json-bound-subject-field", field + " is still JSON-bound or not promoted to a TypeDB attribute."))
    elif kind == "relation":
        if not str(condition.get("relation_type") or condition.get("relationType") or ""):
            blockers.append(condition_blocker(condition_id, "missing-relation-type", "Relation condition needs an explicit relation_type."))
        blockers.extend(filter_blockers(condition_id, condition.get("target_property_filters") or condition.get("targetPropertyFilters") or {}, TYPEDB_FUNCTION_TARGET_FILTERS, "target"))
        blockers.extend(filter_blockers(condition_id, condition.get("relation_property_filters") or condition.get("relationPropertyFilters") or {}, TYPEDB_FUNCTION_RELATION_FILTERS, "relation"))
    else:
        blockers.append(condition_blocker(condition_id, "unsupported-condition-kind", kind + " is not mapped to a TypeDB function pattern."))
    return {
        "conditionId": condition_id,
        "kind": kind,
        "status": "ready" if not blockers else "partial",
        "blockers": blockers,
    }


def filter_blockers(condition_id: str, filters: Dict[str, object], supported: set, scope: str) -> List[Dict[str, object]]:
    blockers = []
    for key in sorted((filters or {}).keys()):
        if key not in supported:
            blockers.append(condition_blocker(condition_id, "json-bound-" + scope + "-filter", key + " is not promoted to a TypeDB attribute."))
    return blockers


def condition_blocker(condition_id: str, code: str, reason: str) -> Dict[str, object]:
    return {
        "conditionId": condition_id,
        "code": code,
        "reason": reason,
    }


def typedb_function_blueprint(rule: Dict[str, object]) -> str:
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "rule").replace(".", "_").replace("-", "_")
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    return (
        "fun orbit_inference_" + rule_id + "() -> { ontology-node, ontology-node }:\n"
        "  match\n"
        "    $source isa ontology-node, has ontology-kind " + typedb_string(source_kind) + ";\n"
        "    # RuleBox conditions are represented by promoted ontology-* attributes where available.\n"
        "  return { $source, $source };"
    )


def relation_row_id(row: Dict[str, object]) -> str:
    seed = "|".join([
        str(row.get("source") or ""),
        str(row.get("type") or ""),
        str(row.get("target") or ""),
        str(row.get("ontologyBox") or ""),
        str(row.get("snapshotId") or row.get("aboxSnapshotId") or ""),
        str(row.get("ruleId") or ""),
    ])
    return "ontology-assertion:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def symbol_from_subject(value: object) -> str:
    raw = str(value or "")
    if raw.startswith("stock:"):
        return raw.split(":", 1)[1].upper()
    return ""


def rule_id_from_value(value: object) -> str:
    raw = str(value or "")
    if ":graph." in raw:
        return raw.split(":", 2)[-1]
    return ""


def matched_condition_ids(row: Dict[str, object]) -> List[str]:
    try:
        properties = json.loads(str(row.get("propertiesJson") or "{}"))
    except json.JSONDecodeError:
        properties = {}
    matches = properties.get("matchedConditions") if isinstance(properties, dict) else []
    return [
        str(item.get("conditionId") or "")
        for item in (matches or [])
        if isinstance(item, dict) and str(item.get("conditionId") or "")
    ]


def typedb_repository_from_settings(settings: Dict[str, str] = None):
    settings = settings or runtime_settings()
    enabled = str(settings.get("ontologyTypeDbEnabled") or "0").strip().lower() not in {"0", "false", "no", "off"}
    address = str(settings.get("typedbAddress") or "").strip()
    if not enabled or not address:
        return NullTypeDBOntologyGraphRepository()
    return TypeDBOntologyGraphRepository(
        address=address,
        user=str(settings.get("typedbUser") or "admin"),
        password=str(settings.get("typedbPassword") or "password"),
        database=str(settings.get("typedbDatabase") or "orbit_alpha_ontology"),
        tls_enabled=typedb_bool(settings.get("typedbTlsEnabled")),
        timeout_seconds=int(settings.get("typedbTimeoutSeconds") or 20),
        retry_count=int(number_or_none(settings.get("typedbRetryCount")) or 2),
        inference_generation_keep_count=int(number_or_none(settings.get("typedbInferenceGenerationKeepCount")) or 1),
    )
