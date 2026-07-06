import base64
import json
import re
import urllib.request
from typing import Dict, Iterable, List

from ..domain.ontology import PortfolioOntology
from .settings import runtime_settings, utc_now


class NullOntologyGraphRepository:
    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        return {
            "saved": False,
            "status": "disabled",
            "reason": "Neo4j ontology storage is not configured.",
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
        }


class Neo4jOntologyGraphRepository:
    def __init__(
        self,
        uri: str,
        user: str = "",
        password: str = "",
        database: str = "neo4j",
        timeout_seconds: int = 8,
    ):
        self.uri = str(uri or "").strip()
        self.user = str(user or "").strip()
        self.password = str(password or "")
        self.database = str(database or "neo4j").strip() or "neo4j"
        self.timeout_seconds = max(2, int(timeout_seconds or 8))

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        if not self.uri:
            return NullOntologyGraphRepository().save_graph(graph)
        if self.uri.startswith("http://") or self.uri.startswith("https://"):
            return self.save_graph_via_http(graph)
        if self.uri.startswith("bolt://") or self.uri.startswith("neo4j://"):
            return self.save_graph_via_driver(graph)
        return {
            "saved": False,
            "status": "unsupported-uri",
            "reason": "Neo4j URI must start with http://, https://, bolt://, or neo4j://.",
        }

    def rows_for_entities(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": item.entity_id,
                "label": item.label,
                "kind": item.kind,
                "ontologyBox": str((item.properties or {}).get("ontologyBox") or "ABox"),
                "propertiesJson": json.dumps(item.properties or {}, ensure_ascii=False, sort_keys=True),
            }
            for item in graph.entities
        ]

    def rows_for_relations(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "source": item.source,
                "target": item.target,
                "type": safe_relation_type(item.relation_type),
                "weight": float(item.weight or 0),
                "ontologyBox": str((item.properties or {}).get("ontologyBox") or "ABox"),
                "evidenceIds": [str(value) for value in item.evidence_ids],
                "propertiesJson": json.dumps(item.properties or {}, ensure_ascii=False, sort_keys=True),
            }
            for item in graph.relations
        ]

    def rows_for_evidence(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": item.evidence_id,
                "subject": item.subject,
                "kind": item.kind,
                "source": item.source,
                "summary": item.summary,
                "ontologyBox": "ABox",
                "valueJson": json.dumps(item.value or {}, ensure_ascii=False, sort_keys=True),
                "confidence": float(item.confidence or 0),
            }
            for item in graph.evidence
        ]

    def rows_for_beliefs(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": item.belief_id,
                "subject": item.subject,
                "label": item.label,
                "polarity": item.polarity,
                "confidence": float(item.confidence or 0),
                "ontologyBox": "ABox",
                "evidenceIds": [str(value) for value in item.evidence_ids],
            }
            for item in graph.beliefs
        ]

    def rows_for_opinions(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": "opinion:" + item.symbol,
                "symbol": item.symbol,
                "action": item.action,
                "tone": item.tone,
                "conviction": float(item.conviction or 0),
                "ontologyPressure": float(item.ontology_pressure or 0),
                "ontologyBox": "ABox",
                "payloadJson": json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True),
            }
            for item in graph.opinions
        ]

    def statements(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        updated_at = utc_now()
        statements = [
            {
                "statement": (
                    "UNWIND $rows AS row "
                    "MERGE (n:OntologyEntity {id: row.id}) "
                    "SET n.label = row.label, n.kind = row.kind, "
                    "n.ontologyBox = row.ontologyBox, n.propertiesJson = row.propertiesJson, n.updatedAt = $updatedAt"
                ),
                "parameters": {"rows": self.rows_for_entities(graph), "updatedAt": updated_at},
            },
            {
                "statement": (
                    "UNWIND $rows AS row "
                    "MERGE (n:OntologyEvidence {id: row.id}) "
                    "SET n.kind = row.kind, n.source = row.source, n.summary = row.summary, n.ontologyBox = row.ontologyBox, "
                    "n.valueJson = row.valueJson, n.confidence = row.confidence, n.updatedAt = $updatedAt "
                    "WITH row, n MATCH (s:OntologyEntity {id: row.subject}) "
                    "MERGE (s)-[:HAS_EVIDENCE]->(n)"
                ),
                "parameters": {"rows": self.rows_for_evidence(graph), "updatedAt": updated_at},
            },
            {
                "statement": (
                    "UNWIND $rows AS row "
                    "MERGE (n:OntologyBelief {id: row.id}) "
                    "SET n.label = row.label, n.polarity = row.polarity, "
                    "n.confidence = row.confidence, n.ontologyBox = row.ontologyBox, n.evidenceIds = row.evidenceIds, n.updatedAt = $updatedAt "
                    "WITH row, n MATCH (s:OntologyEntity {id: row.subject}) "
                    "MERGE (s)-[:HAS_BELIEF]->(n)"
                ),
                "parameters": {"rows": self.rows_for_beliefs(graph), "updatedAt": updated_at},
            },
            {
                "statement": (
                    "UNWIND $rows AS row "
                    "MERGE (n:OntologyOpinion {id: row.id}) "
                    "SET n.symbol = row.symbol, n.action = row.action, n.tone = row.tone, "
                    "n.conviction = row.conviction, n.ontologyPressure = row.ontologyPressure, "
                    "n.ontologyBox = row.ontologyBox, n.payloadJson = row.payloadJson, n.updatedAt = $updatedAt "
                    "WITH row, n, 'stock:' + row.symbol AS stockId MATCH (s:OntologyEntity {id: stockId}) "
                    "MERGE (s)-[:HAS_OPINION]->(n)"
                ),
                "parameters": {"rows": self.rows_for_opinions(graph), "updatedAt": updated_at},
            },
        ]
        for relation_type, rows in group_relation_rows(self.rows_for_relations(graph)).items():
            statements.append({
                "statement": (
                    "UNWIND $rows AS row "
                    "MATCH (a:OntologyEntity {id: row.source}) "
                    "MATCH (b:OntologyEntity {id: row.target}) "
                    "MERGE (a)-[r:" + relation_type + "]->(b) "
                    "SET r.weight = row.weight, r.evidenceIds = row.evidenceIds, "
                    "r.ontologyBox = row.ontologyBox, r.propertiesJson = row.propertiesJson, r.updatedAt = $updatedAt"
                ),
                "parameters": {"rows": rows, "updatedAt": updated_at},
            })
        return statements

    def save_graph_via_http(self, graph: PortfolioOntology) -> Dict[str, object]:
        endpoint = neo4j_http_endpoint(self.uri, self.database)
        body = json.dumps({"statements": self.statements(graph)}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.user or self.password:
            token = base64.b64encode((self.user + ":" + self.password).encode("utf-8")).decode("ascii")
            headers["Authorization"] = "Basic " + token
        request = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except Exception as error:  # noqa: BLE001 - persistence must not break monitoring.
            return {
                "saved": False,
                "status": "error",
                "reason": str(error)[:180],
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
            }
        errors = payload.get("errors") or []
        if errors:
            return {
                "saved": False,
                "status": "neo4j-error",
                "reason": json.dumps(errors[:2], ensure_ascii=False)[:300],
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
            }
        return {
            "saved": True,
            "status": "ok",
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "evidenceCount": len(graph.evidence),
        }

    def save_graph_via_driver(self, graph: PortfolioOntology) -> Dict[str, object]:
        try:
            from neo4j import GraphDatabase
        except Exception as error:  # noqa: BLE001 - optional dependency.
            return {
                "saved": False,
                "status": "driver-missing",
                "reason": "neo4j Python driver is not installed: " + str(error)[:120],
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
            }
        try:
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user or self.password else None)
            with driver.session(database=self.database) as session:
                for statement in self.statements(graph):
                    session.run(statement["statement"], **statement["parameters"])
            driver.close()
        except Exception as error:  # noqa: BLE001 - persistence must not break monitoring.
            return {
                "saved": False,
                "status": "error",
                "reason": str(error)[:180],
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
            }
        return {
            "saved": True,
            "status": "ok",
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "evidenceCount": len(graph.evidence),
        }


def safe_relation_type(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9_]+", "_", str(value or "RELATED_TO").upper()).strip("_")
    if not normalized:
        return "RELATED_TO"
    if not re.match(r"^[A-Z_]", normalized):
        normalized = "R_" + normalized
    return normalized[:60]


def group_relation_rows(rows: Iterable[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("type") or "RELATED_TO"), []).append(row)
    return grouped


def neo4j_http_endpoint(uri: str, database: str) -> str:
    base = str(uri or "").rstrip("/")
    if base.endswith("/tx/commit"):
        return base
    if base.endswith("/tx"):
        return base + "/commit"
    if "/db/" in base:
        return base + "/tx/commit"
    return base + "/db/" + urllib_quote(database or "neo4j") + "/tx/commit"


def urllib_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value or ""), safe="")


def ontology_repository_from_settings(settings: Dict[str, str] = None):
    settings = settings or runtime_settings()
    enabled = str(settings.get("ontologyNeo4jEnabled") or "1").strip().lower() not in {"0", "false", "no", "off"}
    uri = str(settings.get("neo4jUri") or "").strip()
    if not enabled or not uri:
        return NullOntologyGraphRepository()
    return Neo4jOntologyGraphRepository(
        uri=uri,
        user=str(settings.get("neo4jUser") or ""),
        password=str(settings.get("neo4jPassword") or ""),
        database=str(settings.get("neo4jDatabase") or "neo4j"),
        timeout_seconds=int(settings.get("neo4jTimeoutSeconds") or 8),
    )
