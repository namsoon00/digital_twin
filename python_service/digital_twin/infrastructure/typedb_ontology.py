import copy
import hashlib
import json
from typing import Dict, Iterable, List, Tuple

from ..domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from ..domain.ontology_graph_reasoner import run_graph_reasoner
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from ..domain.ontology_rulebox_governance import (
    normalize_rule_change_candidate,
    rulebox_governance_candidates,
)
from ..domain.ontology_schema import default_tbox_metadata
from .neo4j_ontology_inferencebox import (
    inferencebox_entity_payload,
    inferencebox_relation_payload,
    inferencebox_snapshot_from_rows,
    inferencebox_trace_payload,
)
from .neo4j_ontology_lifecycle import (
    active_tbox_metadata_from_rows,
    active_tbox_metadata_unavailable,
    graph_box_entity_counts,
    graph_box_relation_counts,
    ontology_seed_graph,
)
from .neo4j_ontology_payloads import Neo4jOntologyRowMapperMixin, number_or_none
from .neo4j_ontology_rulebox import (
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
    return None if parsed is None else float(parsed)


def typedb_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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
            "source": "typedb",
            "graphStore": "typedb",
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


class TypeDBOntologyGraphRepository(Neo4jOntologyRowMapperMixin):
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
    ):
        self.address = str(address or "").strip()
        self.user = str(user or "admin").strip() or "admin"
        self.password = str(password or "password")
        self.database = str(database or "orbit_alpha_ontology").strip() or "orbit_alpha_ontology"
        self.tls_enabled = bool(tls_enabled)
        self.timeout_seconds = max(2, int(timeout_seconds or 20))
        self._last_graph = None
        self._last_inference_graph = None
        self._last_rules: List[GraphInferenceRule] = []

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
            driver = self.open_driver(imported)
            try:
                self.ensure_database(driver)
                self.ensure_schema(driver, imported)
                self.write_graph(driver, imported, graph)
            finally:
                self.close_driver(driver)
        except Exception as error:  # noqa: BLE001 - graph mirror must not block monitoring.
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
        merged = merge_flat_properties({
            "id": row.get("id"),
            "label": row.get("label"),
            "kind": row.get("kind"),
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
            "kind": str(row.get("kind") or merged.get("kind") or ""),
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
            "polarity": str(merged.get("polarity") or ""),
            "confidence": number_or_none(merged.get("confidence")),
            "decisionStage": str(merged.get("decisionStage") or ""),
            "stagePriority": number_or_none(merged.get("stagePriority")),
            "nativeNeo4jReasoned": bool(merged.get("nativeNeo4jReasoned")),
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
            "aiInfluenceLabel": str(merged.get("aiInfluenceLabel") or ""),
            "inferenceTraceId": str(merged.get("inferenceTraceId") or ""),
            "nativeNeo4jReasoned": bool(merged.get("nativeNeo4jReasoned")),
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
    owns ontology-weight;
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
            + ";"
        )

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload or {}
        try:
            rules = rulebox_rules_from_payload(payload) if (payload.get("rules") is not None or payload.get("rulesJson")) else default_graph_inference_rules()
        except ValueError as error:
            return {"configured": True, "saved": False, "seeded": False, "status": "invalid-rulebox", "graphStore": "typedb", "reason": str(error)}
        rules = list(rules)
        self._last_rules = rules
        result = self.save_graph(ontology_seed_graph(rules))
        result.update({
            "configured": True,
            "seeded": bool(result.get("saved")),
            "engineVersion": GRAPH_REASONER_VERSION,
            "ruleCount": len(rules),
            "graphStore": "typedb",
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
            "rules": [row for row in entities if row.get("kind") == "rule" and row.get("ontologyBox") == "RuleBox"],
            "conditions": [row for row in entities if row.get("kind") == "rule-condition" and row.get("ontologyBox") == "RuleBox"],
            "derivations": [row for row in entities if row.get("kind") == "relation-template" and row.get("ontologyBox") == "RuleBox"],
            "relationTypes": relation_type_rows_from_derivations(entities, relations),
            "versions": [row for row in entities if row.get("kind") == "rulebox-version" and row.get("ontologyBox") == "RuleBoxGovernance"],
            "candidates": [row for row in entities if row.get("kind") == "rule-change-candidate" and row.get("ontologyBox") == "RuleBoxGovernance"],
        }
        snapshot = rulebox_snapshot_from_rows(rowsets, "typedb-typeql")
        snapshot.update({"graphStore": "typedb", "source": "typedb-typeql"})
        if snapshot.get("status") == "ok":
            try:
                self._last_rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
            except ValueError:
                pass
        return snapshot

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        try:
            rules = rulebox_rules_from_payload(payload or {})
        except ValueError as error:
            return {"configured": True, "saved": False, "status": "invalid-rulebox", "graphStore": "typedb", "reason": str(error)}
        self._last_rules = list(rules)
        save_result = self.save_graph(rulebox_graph_from_rules(self._last_rules))
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
        graph = copy.deepcopy(self._last_graph) if self._last_graph else self.load_graph_from_typedb(["ABox"])
        if not graph or not graph.entities:
            return {
                "configured": True,
                "status": "missing-abox",
                "graphStore": "typedb",
                "reason": "TypeDB에 실행 가능한 ABox 그래프가 없습니다.",
                "statementCount": 0,
                "nativeTypeDbReasoningUsed": False,
            }
        if not self._last_rules:
            snapshot = self.rulebox_snapshot()
            try:
                self._last_rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
            except ValueError:
                self._last_rules = list(default_graph_inference_rules())
        run_graph_reasoner(graph, self._last_rules or default_graph_inference_rules())
        inference_entities = [
            item for item in graph.entities
            if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
        ]
        inference_relations = [
            item for item in graph.relations
            if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
        ]
        if inference_entities or inference_relations:
            self.save_graph(graph)
        self._last_inference_graph = graph
        return {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "reason": "TypeDB bootstrap mode uses the domain graph reasoner to materialize InferenceBox while TypeQL native rules are prepared.",
            "statementCount": len(inference_relations),
            "relationTypes": sorted({str(item.relation_type or "") for item in inference_relations if str(item.relation_type or "")}),
            "nativeTypeDbReasoningUsed": False,
            "typedbBootstrapReasoningUsed": True,
        }

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        safe_limit = max(1, min(500, int(limit or 80)))
        if self.address:
            try:
                snapshot = self.inferencebox_snapshot_from_typedb(clean_symbols, safe_limit)
                if snapshot.get("entityCount") or snapshot.get("relationCount") or snapshot.get("traceCount"):
                    return snapshot
            except Exception:
                pass
        graph = self._last_inference_graph
        if not graph:
            return {
                "configured": bool(self.address),
                "saved": False,
                "status": "empty",
                "source": "typedb-bootstrap",
                "graphStore": "typedb",
                "reason": "아직 현재 프로세스에서 TypeDB InferenceBox를 만들지 않았습니다.",
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
        entity_rows = [
            row for row in self.rows_for_entities(graph)
            if row.get("ontologyBox") == "InferenceBox" and (not clean_symbols or str(row.get("symbol") or "").upper() in clean_symbols)
        ][:safe_limit]
        relation_rows = [
            row for row in self.rows_for_relations(graph)
            if row.get("ontologyBox") == "InferenceBox" and (
                not clean_symbols
                or any(symbol in str(row.get(key) or "").upper() for symbol in clean_symbols for key in ["source", "target", "symbol"])
            )
        ][:safe_limit]
        trace_rows = [row for row in entity_rows if str(row.get("kind") or "") == "inference-trace"][:safe_limit]
        return {
            "configured": bool(self.address),
            "saved": True,
            "status": "ok",
            "source": "typedb-bootstrap",
            "graphStore": "typedb",
            "engineVersion": GRAPH_REASONER_VERSION,
            "symbols": clean_symbols,
            "entityCount": len(entity_rows),
            "relationCount": len(relation_rows),
            "traceCount": len(trace_rows),
            "nativeEntityCount": 0,
            "nativeRelationCount": 0,
            "nativeTraceCount": 0,
            "neo4jNativeReasoningUsed": False,
            "nativeTypeDbReasoningUsed": False,
            "typedbBootstrapReasoningUsed": True,
            "entities": [inferencebox_entity_payload(row) for row in entity_rows],
            "relations": [inferencebox_relation_payload(row) for row in relation_rows],
            "traces": [inferencebox_trace_payload({**row, "matchedConditionIds": matched_condition_ids(row)}) for row in trace_rows],
        }

    def inferencebox_snapshot_from_typedb(self, clean_symbols: List[str], safe_limit: int) -> Dict[str, object]:
        entity_rows = [
            row for row in self.read_entity_rows(["InferenceBox"])
            if not clean_symbols or str(row.get("symbol") or "").upper() in clean_symbols
        ]
        relation_rows = [
            row for row in self.read_relation_rows(["InferenceBox"])
            if not clean_symbols
            or any(symbol in str(row.get(key) or "").upper() for symbol in clean_symbols for key in ["source", "target", "symbol"])
        ]
        trace_rows = [row for row in entity_rows if str(row.get("kind") or "") == "inference-trace"]
        rowsets = {
            "entityCounts": [{"entityCount": len(entity_rows), "nativeEntityCount": 0}],
            "relationCounts": [{"relationCount": len(relation_rows), "nativeRelationCount": 0}],
            "traceCounts": [{"traceCount": len(trace_rows), "nativeTraceCount": 0}],
            "entities": entity_rows[:safe_limit],
            "relations": relation_rows[:safe_limit],
            "traces": [{**row, "matchedConditionIds": matched_condition_ids(row)} for row in trace_rows[:safe_limit]],
        }
        snapshot = inferencebox_snapshot_from_rows(rowsets, "typedb-typeql", clean_symbols)
        snapshot.update({
            "graphStore": "typedb",
            "source": "typedbInferenceBox",
            "neo4jNativeReasoningUsed": False,
            "nativeTypeDbReasoningUsed": False,
            "typedbBootstrapReasoningUsed": True,
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
        if row.get("kind") == "relation-template":
            values.add(str(row.get("derivationRelationType") or row.get("relationType") or "").upper())
    for row in relation_rows or []:
        if row.get("ontologyBox") == "RuleBox":
            values.add(str(row.get("type") or row.get("relationType") or "").upper())
    return [{"relationType": item} for item in sorted(value for value in values if value)]


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
    )
