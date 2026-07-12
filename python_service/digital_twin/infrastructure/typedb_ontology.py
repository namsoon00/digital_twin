import copy
import hashlib
import json
from typing import Dict, Iterable, List, Tuple

from ..domain.ontology_contracts import PortfolioOntology
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
    inferencebox_trace_payload,
)
from .neo4j_ontology_lifecycle import (
    graph_box_entity_counts,
    graph_box_relation_counts,
    ontology_seed_graph,
)
from .neo4j_ontology_payloads import Neo4jOntologyRowMapperMixin, number_or_none
from .neo4j_ontology_rulebox import (
    rulebox_graph_from_rules,
    rulebox_rules_from_payload,
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
        self._last_rules: List[GraphInferenceRule] = list(default_graph_inference_rules())

    def active_tbox_metadata(self) -> Dict[str, object]:
        metadata = default_tbox_metadata()
        metadata.update({
            "configured": bool(self.address),
            "status": "code-fallback",
            "source": "code-fallback",
            "graphStore": "typedb",
            "storeSource": "typedb",
            "reason": "TypeDB TBox read model is bootstrapped from the code TBox until TypeQL read queries are enabled.",
        })
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
ontology-id sub attribute, value string;
ontology-label sub attribute, value string;
ontology-kind sub attribute, value string;
ontology-box sub attribute, value string;
ontology-symbol sub attribute, value string;
ontology-rule-id sub attribute, value string;
ontology-account-id sub attribute, value string;
ontology-snapshot-id sub attribute, value string;
ontology-tbox-class sub attribute, value string;
ontology-relation-type sub attribute, value string;
ontology-updated-at sub attribute, value string;
ontology-json sub attribute, value string;
ontology-weight sub attribute, value double;
ontology-confidence sub attribute, value double;

ontology-node sub entity, abstract,
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

ontology-entity sub ontology-node;
ontology-evidence sub ontology-node;
ontology-belief sub ontology-node;
ontology-opinion sub ontology-node;
ontology-reasoning-card sub ontology-node;

ontology-assertion sub relation,
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
            "$r (source: $source, target: $target) isa ontology-assertion"
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
        rules = rulebox_rules_to_payload(self._last_rules or default_graph_inference_rules())
        return {
            "configured": bool(self.address),
            "saved": True,
            "status": "ok" if self.address else "disabled",
            "source": "typedb-code-bootstrap",
            "graphStore": "typedb",
            "reason": "TypeDB RuleBox read model is using the active in-process rule catalog until TypeQL reads are enabled.",
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
        if not self._last_graph:
            return {
                "configured": True,
                "status": "missing-abox",
                "graphStore": "typedb",
                "reason": "TypeDB RuleBox 실행 전에 저장된 ABox 그래프가 없습니다.",
                "statementCount": 0,
                "nativeTypeDbReasoningUsed": False,
            }
        graph = copy.deepcopy(self._last_graph)
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
        graph = self._last_inference_graph
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        safe_limit = max(1, min(500, int(limit or 80)))
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

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
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
