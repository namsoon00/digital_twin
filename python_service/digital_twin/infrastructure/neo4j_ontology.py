import base64
import json
import re
import urllib.request
from typing import Dict, Iterable, List

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.ontology_decision_policy import decision_stage_from_action, relation_stage_priority
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from ..domain.ontology_rulebox_governance import (
    normalize_rule_change_candidate,
    rulebox_governance_candidates,
    rulebox_version_payload,
)
from ..domain.ontology_rulebox_projection import add_rulebox_concepts
from ..domain.ontology_schema import default_tbox_metadata, normalize_tbox_metadata, tbox_entities, tbox_relations
from .settings import runtime_settings, utc_now
from .neo4j_ontology_payloads import (
    Neo4jOntologyRowMapperMixin,
    bool_or_none,
    condition_relation_filter_bool,
    condition_relation_filter_number,
    condition_relation_filter_values,
    condition_target_filter_bool,
    condition_target_filter_number,
    condition_target_filter_values,
    condition_target_level_types,
    derivation_decision_stage,
    derivation_stage_priority,
    group_relation_rows,
    list_of_strings,
    number_or_none,
    safe_relation_type,
)
from .neo4j_ontology_lifecycle import (
    active_tbox_metadata_from_rows,
    active_tbox_metadata_statements,
    active_tbox_metadata_unavailable,
    clear_inferencebox_statements,
    clear_rulebox_statements,
    deactivate_current_abox_statements,
    graph_abox_lifecycle,
    graph_box_entity_counts,
    graph_box_relation_counts,
    http_result_rowsets,
    neo4j_record_to_dict,
    ontology_seed_graph,
)
from .neo4j_ontology_rulebox import (
    build_rulebox_rules_from_rows,
    condition_payload_from_row,
    derivation_payload_from_row,
    json_object,
    native_reasoning_statement_for_relation_type,
    native_reasoning_statements_for_relation_types,
    rule_change_candidate_from_row,
    rule_change_candidate_statements,
    rulebox_graph_from_rules,
    rulebox_relation_types_statement,
    rulebox_rules_from_payload,
    rulebox_rules_to_payload,
    rulebox_snapshot_from_rows,
    rulebox_snapshot_statements,
    rulebox_store_snapshot_unavailable,
    rulebox_version_from_row,
    rulebox_version_statements,
)
from .neo4j_ontology_inferencebox import (
    first_row,
    inferencebox_entity_payload,
    inferencebox_relation_payload,
    inferencebox_snapshot_default,
    inferencebox_snapshot_from_rows,
    inferencebox_snapshot_statements,
    inferencebox_trace_payload,
)


class NullOntologyGraphRepository:
    def active_tbox_metadata(self) -> Dict[str, object]:
        metadata = default_tbox_metadata()
        metadata.update({
            "configured": False,
            "status": "code-fallback",
            "source": "code",
            "reason": "Neo4j ontology storage is not configured.",
        })
        return metadata

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        return {
            "saved": False,
            "status": "disabled",
            "reason": "Neo4j ontology storage is not configured.",
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        }

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        graph = ontology_seed_graph()
        result = self.save_graph(graph)
        result.update({
            "configured": False,
            "seeded": False,
            "reason": "Neo4j ontology storage is not configured.",
            "tboxEntityCount": graph_box_entity_counts(graph).get("TBox", 0),
            "ruleBoxEntityCount": graph_box_entity_counts(graph).get("RuleBox", 0),
        })
        return result

    def rulebox_snapshot(self) -> Dict[str, object]:
        rules = rulebox_rules_to_payload(default_graph_inference_rules())
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "source": "defaults",
            "reason": "Neo4j ontology storage is not configured.",
            "engineVersion": GRAPH_REASONER_VERSION,
            "rules": rules,
            "ruleCount": len(rules),
            "conditionCount": sum(len(item.get("conditions") or []) for item in rules),
            "derivationCount": sum(len(item.get("derivations") or []) for item in rules),
            "versions": [],
            "versionCount": 0,
            "changeCandidates": rulebox_governance_candidates(rules, []),
        }

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        snapshot = self.rulebox_snapshot()
        snapshot.update({
            "saved": False,
            "status": "disabled",
            "reason": "Neo4j URI가 없어 RuleBox를 저장하지 않았습니다.",
        })
        return snapshot

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "reason": "Neo4j URI가 없어 RuleBox 추론을 실행하지 않았습니다.",
            "statementCount": 0,
        }

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "reason": "Neo4j URI가 없어 InferenceBox를 조회하지 않았습니다.",
            "symbols": list(symbols or []),
            "entities": [],
            "relations": [],
            "traces": [],
            "entityCount": 0,
            "relationCount": 0,
            "traceCount": 0,
        }

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "reason": "Neo4j URI가 없어 RuleChangeCandidate를 저장하지 않았습니다.",
            "candidateCount": len(list(candidates or [])),
            "savedCount": 0,
        }


class Neo4jOntologyGraphRepository(Neo4jOntologyRowMapperMixin):
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

    def active_tbox_metadata(self) -> Dict[str, object]:
        if not self.uri:
            return NullOntologyGraphRepository().active_tbox_metadata()
        if self.uri.startswith("http://") or self.uri.startswith("https://"):
            return self.active_tbox_metadata_via_http()
        if self.uri.startswith("bolt://") or self.uri.startswith("neo4j://"):
            return self.active_tbox_metadata_via_driver()
        metadata = default_tbox_metadata()
        metadata.update({"configured": True, "status": "unsupported-uri", "source": "code-fallback"})
        return metadata

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

    def schema_statements(self) -> List[Dict[str, object]]:
        statements = [
            "CREATE CONSTRAINT ontology_entity_id IF NOT EXISTS FOR (n:OntologyEntity) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT ontology_evidence_id IF NOT EXISTS FOR (n:OntologyEvidence) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT ontology_belief_id IF NOT EXISTS FOR (n:OntologyBelief) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT ontology_opinion_id IF NOT EXISTS FOR (n:OntologyOpinion) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT ontology_reasoning_card_id IF NOT EXISTS FOR (n:OntologyReasoningCard) REQUIRE n.id IS UNIQUE",
            "CREATE INDEX ontology_entity_box_kind IF NOT EXISTS FOR (n:OntologyEntity) ON (n.ontologyBox, n.kind)",
            "CREATE INDEX ontology_entity_updated IF NOT EXISTS FOR (n:OntologyEntity) ON (n.updatedAt)",
            "CREATE INDEX ontology_entity_rule_id IF NOT EXISTS FOR (n:OntologyEntity) ON (n.ruleId)",
            "CREATE INDEX ontology_entity_symbol IF NOT EXISTS FOR (n:OntologyEntity) ON (n.symbol)",
            "CREATE INDEX ontology_entity_current_account IF NOT EXISTS FOR (n:OntologyEntity) ON (n.ontologyBox, n.accountId, n.isCurrent)",
            "CREATE INDEX ontology_entity_abox_snapshot IF NOT EXISTS FOR (n:OntologyEntity) ON (n.aboxSnapshotId)",
            "CREATE INDEX ontology_entity_tbox_version IF NOT EXISTS FOR (n:OntologyEntity) ON (n.tboxVersion)",
            "CREATE INDEX ontology_entity_tbox_class IF NOT EXISTS FOR (n:OntologyEntity) ON (n.tboxClass)",
            "CREATE INDEX ontology_entity_bounded_context IF NOT EXISTS FOR (n:OntologyEntity) ON (n.boundedContext)",
            "CREATE INDEX ontology_entity_condition_kind IF NOT EXISTS FOR (n:OntologyEntity) ON (n.conditionKind)",
            "CREATE INDEX ontology_entity_derivation_relation_type IF NOT EXISTS FOR (n:OntologyEntity) ON (n.derivationRelationType)",
            "CREATE INDEX ontology_entity_level_type IF NOT EXISTS FOR (n:OntologyEntity) ON (n.levelType)",
            "CREATE INDEX ontology_entity_data_scope IF NOT EXISTS FOR (n:OntologyEntity) ON (n.dataScope)",
            "CREATE INDEX ontology_entity_relation_scope IF NOT EXISTS FOR (n:OntologyEntity) ON (n.relationScope)",
            "CREATE INDEX ontology_tbox_class_name IF NOT EXISTS FOR (n:OntologyTBoxClass) ON (n.className)",
            "CREATE INDEX ontology_tbox_relation_type IF NOT EXISTS FOR (n:OntologyTBoxRelation) ON (n.relationTypeName)",
            "CREATE INDEX ontology_box_name IF NOT EXISTS FOR (n:OntologyBox) ON (n.label)",
            "CREATE INDEX ontology_abox_symbol IF NOT EXISTS FOR (n:ABox) ON (n.symbol)",
            "CREATE INDEX ontology_rulebox_rule_id IF NOT EXISTS FOR (n:RuleBox) ON (n.ruleId)",
            "CREATE INDEX ontology_inferencebox_rule_id IF NOT EXISTS FOR (n:InferenceBox) ON (n.ruleId)",
            "CREATE INDEX ontology_evidence_subject IF NOT EXISTS FOR (n:OntologyEvidence) ON (n.subject)",
            "CREATE INDEX ontology_opinion_symbol IF NOT EXISTS FOR (n:OntologyOpinion) ON (n.symbol)",
            "CREATE INDEX ontology_reasoning_card_symbol IF NOT EXISTS FOR (n:OntologyReasoningCard) ON (n.symbol)",
        ]
        return [{"statement": statement, "parameters": {}} for statement in statements]

    def statements(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        updated_at = utc_now()
        statements = deactivate_current_abox_statements(graph)
        statements.extend([
            {
                "statement": (
                    "UNWIND $rows AS row "
                    "MERGE (n:OntologyEntity {id: row.id}) "
                    "SET n.label = row.label, n.kind = row.kind, "
                    "n.ontologyBox = row.ontologyBox, n.symbol = row.symbol, n.ruleId = row.ruleId, "
                    "n.version = row.version, n.sourceKind = row.sourceKind, "
                    "n.actionGroup = row.actionGroup, n.actionLevel = row.actionLevel, n.promptHint = row.promptHint, "
                    "n.anyConditionMinCount = row.anyConditionMinCount, "
                    "n.tboxClass = row.tboxClass, n.tboxClasses = row.tboxClasses, n.boundedContext = row.boundedContext, "
                    "n.className = row.className, n.parentClass = row.parentClass, n.relationTypeName = row.relationTypeName, "
                    "n.box = row.box, n.scope = row.scope, n.dataScope = row.dataScope, n.domainScope = row.domainScope, "
                    "n.sourceContext = row.sourceContext, n.targetContext = row.targetContext, "
                    "n.accountId = row.accountId, n.aboxSnapshotId = row.aboxSnapshotId, n.snapshotId = row.snapshotId, "
                    "n.asOf = row.asOf, n.isCurrent = row.isCurrent, n.tboxVersion = row.tboxVersion, "
                    "n.activeTboxVersion = row.activeTboxVersion, n.tboxFingerprint = row.tboxFingerprint, n.activeTboxSource = row.activeTboxSource, "
                    "n.sourceValue = row.sourceValue, n.profitLossRate = row.profitLossRate, n.levelType = row.levelType, "
                    "n.field = row.field, n.valueNumber = row.valueNumber, n.polarity = row.polarity, n.transitionType = row.transitionType, n.group = row.group, "
                    "n.relationScope = row.relationScope, n.eventType = row.eventType, n.materialityScore = row.materialityScore, "
                    "n.title = row.title, n.url = row.url, n.publishedAt = row.publishedAt, n.observedAt = row.observedAt, "
                    "n.materialityPassed = row.materialityPassed, n.relevanceScore = row.relevanceScore, "
                    "n.sourceReliability = row.sourceReliability, n.impactScore = row.impactScore, n.confidence = row.confidence, "
                    "n.enabled = row.enabled, n.conditionId = row.conditionId, n.conditionKind = row.conditionKind, "
                    "n.conditionField = row.conditionField, n.conditionOperator = row.conditionOperator, n.conditionRole = row.conditionRole, "
                    "n.conditionValueString = row.conditionValueString, n.conditionValueNumber = row.conditionValueNumber, "
                    "n.conditionRelationType = row.conditionRelationType, n.conditionDirection = row.conditionDirection, "
                    "n.conditionTargetKind = row.conditionTargetKind, n.conditionTargetLevelTypes = row.conditionTargetLevelTypes, "
                    "n.conditionTargetFields = row.conditionTargetFields, n.conditionTargetTboxClasses = row.conditionTargetTboxClasses, "
                    "n.conditionTargetGroups = row.conditionTargetGroups, n.conditionTargetScopes = row.conditionTargetScopes, "
                    "n.conditionTargetDataScopes = row.conditionTargetDataScopes, n.conditionTargetDomainScopes = row.conditionTargetDomainScopes, "
                    "n.conditionTargetRelationScopes = row.conditionTargetRelationScopes, "
                    "n.conditionTargetEventTypes = row.conditionTargetEventTypes, n.conditionTargetPolarities = row.conditionTargetPolarities, "
                    "n.conditionTargetMaterialityPassed = row.conditionTargetMaterialityPassed, "
                    "n.conditionTargetMinMaterialityScore = row.conditionTargetMinMaterialityScore, "
                    "n.conditionTargetMinValue = row.conditionTargetMinValue, n.conditionTargetMaxValue = row.conditionTargetMaxValue, "
                    "n.conditionRelationPolarities = row.conditionRelationPolarities, n.conditionRelationTransitionTypes = row.conditionRelationTransitionTypes, "
                    "n.conditionRelationFields = row.conditionRelationFields, n.conditionRelationSignalGroups = row.conditionRelationSignalGroups, "
                    "n.conditionRelationMaterialityPassed = row.conditionRelationMaterialityPassed, "
                    "n.conditionRelationMinRiskImpact = row.conditionRelationMinRiskImpact, n.conditionRelationMinSupportImpact = row.conditionRelationMinSupportImpact, "
                    "n.conditionMinWeight = row.conditionMinWeight, n.derivationRelationType = row.derivationRelationType, "
                    "n.derivationIndex = row.derivationIndex, "
                    "n.derivationTargetKind = row.derivationTargetKind, n.derivationTargetKey = row.derivationTargetKey, "
                    "n.derivationTargetLabel = row.derivationTargetLabel, n.derivationTboxClass = row.derivationTboxClass, "
                    "n.derivationTboxClasses = row.derivationTboxClasses, n.derivationPolarity = row.derivationPolarity, "
                    "n.derivationRiskImpact = row.derivationRiskImpact, n.derivationSupportImpact = row.derivationSupportImpact, "
                    "n.derivationWeight = row.derivationWeight, n.derivationBeliefLabel = row.derivationBeliefLabel, "
                    "n.derivationAiInfluenceLabel = row.derivationAiInfluenceLabel, n.derivationActionGroup = row.derivationActionGroup, "
                    "n.derivationActionLevel = row.derivationActionLevel, n.derivationDecisionStage = row.derivationDecisionStage, "
                    "n.derivationStagePriority = row.derivationStagePriority, "
                    "n.propertiesJson = row.propertiesJson, n.updatedAt = $updatedAt "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'TBox' THEN [1] ELSE [] END | SET n:TBox) "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'ABox' THEN [1] ELSE [] END | SET n:ABox) "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'RuleBox' THEN [1] ELSE [] END | SET n:RuleBox) "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'InferenceBox' THEN [1] ELSE [] END | SET n:InferenceBox) "
                    "FOREACH (_ IN CASE WHEN row.kind = 'ontology-box' THEN [1] ELSE [] END | SET n:OntologyBox) "
                    "FOREACH (_ IN CASE WHEN row.kind = 'bounded-context' THEN [1] ELSE [] END | SET n:OntologyBoundedContext) "
                    "FOREACH (_ IN CASE WHEN row.kind = 'tbox-class' THEN [1] ELSE [] END | SET n:OntologyTBoxClass) "
                    "FOREACH (_ IN CASE WHEN row.kind = 'tbox-relation' THEN [1] ELSE [] END | SET n:OntologyTBoxRelation)"
                ),
                "parameters": {"rows": self.rows_for_entities(graph), "updatedAt": updated_at},
            },
            {
                "statement": (
                    "UNWIND $rows AS row "
                    "MERGE (n:OntologyEvidence {id: row.id}) "
                    "SET n.subject = row.subject, n.kind = row.kind, n.source = row.source, n.summary = row.summary, n.ontologyBox = row.ontologyBox, "
                    "n.accountId = row.accountId, n.aboxSnapshotId = row.aboxSnapshotId, n.snapshotId = row.snapshotId, "
                    "n.asOf = row.asOf, n.isCurrent = row.isCurrent, n.tboxVersion = row.tboxVersion, "
                    "n.valueJson = row.valueJson, n.confidence = row.confidence, n.updatedAt = $updatedAt "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'ABox' THEN [1] ELSE [] END | SET n:ABox) "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'InferenceBox' THEN [1] ELSE [] END | SET n:InferenceBox) "
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
                    "n.confidence = row.confidence, n.ontologyBox = row.ontologyBox, n.evidenceIds = row.evidenceIds, "
                    "n.accountId = row.accountId, n.aboxSnapshotId = row.aboxSnapshotId, n.snapshotId = row.snapshotId, "
                    "n.asOf = row.asOf, n.isCurrent = row.isCurrent, n.tboxVersion = row.tboxVersion, n.updatedAt = $updatedAt "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'ABox' THEN [1] ELSE [] END | SET n:ABox) "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'InferenceBox' THEN [1] ELSE [] END | SET n:InferenceBox) "
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
                    "n.ontologyBox = row.ontologyBox, n.accountId = row.accountId, n.aboxSnapshotId = row.aboxSnapshotId, "
                    "n.snapshotId = row.snapshotId, n.asOf = row.asOf, n.isCurrent = row.isCurrent, "
                    "n.tboxVersion = row.tboxVersion, n.payloadJson = row.payloadJson, n.updatedAt = $updatedAt "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'ABox' THEN [1] ELSE [] END | SET n:ABox) "
                    "WITH row, n, 'stock:' + row.symbol AS stockId MATCH (s:OntologyEntity {id: stockId}) "
                    "MERGE (s)-[:HAS_OPINION]->(n)"
                ),
                "parameters": {"rows": self.rows_for_opinions(graph), "updatedAt": updated_at},
            },
            {
                "statement": (
                    "UNWIND $rows AS row "
                    "MERGE (n:OntologyReasoningCard {id: row.id}) "
                    "SET n.symbol = row.symbol, n.companyName = row.companyName, n.source = row.source, "
                    "n.portfolioRelation = row.portfolioRelation, n.status = row.status, "
                    "n.ontologyBox = row.ontologyBox, n.accountId = row.accountId, n.aboxSnapshotId = row.aboxSnapshotId, "
                    "n.snapshotId = row.snapshotId, n.asOf = row.asOf, n.isCurrent = row.isCurrent, "
                    "n.tboxVersion = row.tboxVersion, n.payloadJson = row.payloadJson, n.updatedAt = $updatedAt "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'ABox' THEN [1] ELSE [] END | SET n:ABox) "
                    "WITH row, n, 'stock:' + row.symbol AS stockId MATCH (s:OntologyEntity {id: stockId}) "
                    "MERGE (s)-[:HAS_REASONING_CARD]->(n)"
                ),
                "parameters": {"rows": self.rows_for_reasoning_cards(graph), "updatedAt": updated_at},
            },
        ])
        for relation_type, rows in group_relation_rows(self.rows_for_relations(graph)).items():
            statements.append({
                "statement": (
                    "UNWIND $rows AS row "
                    "MATCH (a:OntologyEntity {id: row.source}) "
                    "MATCH (b:OntologyEntity {id: row.target}) "
                    "MERGE (a)-[r:" + relation_type + "]->(b) "
                    "SET r.weight = row.weight, r.evidenceIds = row.evidenceIds, "
                    "r.ontologyBox = row.ontologyBox, r.accountId = row.accountId, r.aboxSnapshotId = row.aboxSnapshotId, "
                    "r.snapshotId = row.snapshotId, r.asOf = row.asOf, r.isCurrent = row.isCurrent, "
                    "r.tboxVersion = row.tboxVersion, r.activeTboxVersion = row.activeTboxVersion, r.tboxFingerprint = row.tboxFingerprint, "
                    "r.ruleId = row.ruleId, r.boundedContext = row.boundedContext, "
                    "r.polarity = row.polarity, r.transitionType = row.transitionType, r.riskImpact = row.riskImpact, "
                    "r.supportImpact = row.supportImpact, r.decisionStage = row.decisionStage, r.stagePriority = row.stagePriority, "
                    "r.aiInfluenceLabel = row.aiInfluenceLabel, "
                    "r.field = row.field, r.signalGroup = row.signalGroup, r.materialityPassed = row.materialityPassed, r.materialityScore = row.materialityScore, "
                    "r.propertiesJson = row.propertiesJson, r.updatedAt = $updatedAt"
                ),
                "parameters": {"rows": rows, "updatedAt": updated_at},
            })
        return statements

    def save_graph_via_http(self, graph: PortfolioOntology) -> Dict[str, object]:
        endpoint = neo4j_http_endpoint(self.uri, self.database)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.user and self.password:
            token = base64.b64encode((self.user + ":" + self.password).encode("utf-8")).decode("ascii")
            headers["Authorization"] = "Basic " + token

        schema_prepared = False
        schema_reason = ""
        try:
            schema_payload = self.post_http_statements(endpoint, headers, self.schema_statements())
            schema_errors = schema_payload.get("errors") or []
            schema_prepared = not bool(schema_errors)
            if schema_errors:
                schema_reason = json.dumps(schema_errors[:2], ensure_ascii=False)[:300]
        except Exception as error:  # noqa: BLE001 - schema prep is best effort.
            schema_reason = str(error)[:180]

        try:
            payload = self.post_http_statements_batched(endpoint, headers, self.statements(graph))
        except Exception as error:  # noqa: BLE001 - persistence must not break monitoring.
            return {
                "saved": False,
                "status": "error",
                "reason": str(error)[:180],
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
                "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
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
        native_reasoning = self.run_native_rulebox_reasoning_via_http(endpoint, headers) if self.should_run_native_reasoning(graph) else {
            "status": "skipped",
            "statementCount": 0,
            "reason": "graph requested persistence-only Neo4j seed",
        }
        box_entity_counts = graph_box_entity_counts(graph)
        box_relation_counts = graph_box_relation_counts(graph)
        return {
            "saved": True,
            "status": "ok",
            "schemaPrepared": schema_prepared,
            "schemaReason": schema_reason,
            "nativeReasoning": native_reasoning,
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

    def post_http_statements(self, endpoint: str, headers: Dict[str, str], statements: List[Dict[str, object]]) -> Dict[str, object]:
        body = json.dumps({"statements": statements}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8") or "{}")

    def post_http_statements_batched(self, endpoint: str, headers: Dict[str, str], statements: List[Dict[str, object]], batch_size: int = 12) -> Dict[str, object]:
        if not statements:
            return {"results": [], "errors": []}
        merged = {"results": [], "errors": []}
        safe_batch_size = max(1, int(batch_size or 12))
        for index in range(0, len(statements), safe_batch_size):
            payload = self.post_http_statements(endpoint, headers, statements[index:index + safe_batch_size])
            merged["results"].extend(payload.get("results") or [])
            errors = payload.get("errors") or []
            if errors:
                merged["errors"].extend(errors)
                break
        return merged

    def http_endpoint_and_headers(self):
        endpoint = neo4j_http_endpoint(self.uri, self.database)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.user and self.password:
            token = base64.b64encode((self.user + ":" + self.password).encode("utf-8")).decode("ascii")
            headers["Authorization"] = "Basic " + token
        return endpoint, headers

    def active_tbox_metadata_via_http(self) -> Dict[str, object]:
        endpoint, headers = self.http_endpoint_and_headers()
        try:
            payload = self.post_http_statements(endpoint, headers, active_tbox_metadata_statements())
        except Exception as error:  # noqa: BLE001 - projection can fall back to code TBox.
            return active_tbox_metadata_unavailable("error", str(error)[:180], "neo4j-http")
        errors = payload.get("errors") or []
        if errors:
            return active_tbox_metadata_unavailable("neo4j-error", json.dumps(errors[:2], ensure_ascii=False)[:300], "neo4j-http")
        return active_tbox_metadata_from_rows(http_result_rowsets(payload, ["entities", "relations"]), "neo4j-http")

    def active_tbox_metadata_via_driver(self) -> Dict[str, object]:
        try:
            from neo4j import GraphDatabase
        except Exception as error:  # noqa: BLE001 - optional dependency.
            return active_tbox_metadata_unavailable("driver-missing", "neo4j Python driver is not installed: " + str(error)[:120], "neo4j-driver")
        try:
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user and self.password else None)
            rowsets: Dict[str, List[Dict[str, object]]] = {}
            with driver.session(database=self.database) as session:
                for key, statement in zip(["entities", "relations"], active_tbox_metadata_statements()):
                    rowsets[key] = [neo4j_record_to_dict(record) for record in session.run(statement["statement"], **statement["parameters"])]
            driver.close()
            return active_tbox_metadata_from_rows(rowsets, "neo4j-driver")
        except Exception as error:  # noqa: BLE001 - projection can fall back to code TBox.
            return active_tbox_metadata_unavailable("error", str(error)[:180], "neo4j-driver")

    def rulebox_snapshot(self) -> Dict[str, object]:
        if not self.uri:
            return NullOntologyGraphRepository().rulebox_snapshot()
        if self.uri.startswith("http://") or self.uri.startswith("https://"):
            return self.rulebox_snapshot_via_http()
        if self.uri.startswith("bolt://") or self.uri.startswith("neo4j://"):
            return self.rulebox_snapshot_via_driver()
        return {
            "configured": True,
            "saved": False,
            "status": "unsupported-uri",
            "source": "neo4j",
            "reason": "Neo4j URI must start with http://, https://, bolt://, or neo4j://.",
            "rules": [],
            "ruleCount": 0,
            "defaultsFallbackUsed": False,
            "versions": [],
            "versionCount": 0,
            "changeCandidates": rulebox_governance_candidates([], []),
        }

    def rulebox_snapshot_via_http(self) -> Dict[str, object]:
        endpoint, headers = self.http_endpoint_and_headers()
        try:
            payload = self.post_http_statements(endpoint, headers, rulebox_snapshot_statements())
        except Exception as error:  # noqa: BLE001 - admin read should degrade to defaults.
            return rulebox_store_snapshot_unavailable("error", str(error)[:180], source="neo4j-http")
        errors = payload.get("errors") or []
        if errors:
            return rulebox_store_snapshot_unavailable("neo4j-error", json.dumps(errors[:2], ensure_ascii=False)[:300], source="neo4j-http")
        rowsets = http_result_rowsets(payload, ["rules", "conditions", "derivations", "relationTypes", "versions", "candidates"])
        return rulebox_snapshot_from_rows(rowsets, source="neo4j-http")

    def rulebox_snapshot_via_driver(self) -> Dict[str, object]:
        try:
            from neo4j import GraphDatabase
        except Exception as error:  # noqa: BLE001 - optional dependency.
            return rulebox_store_snapshot_unavailable("driver-missing", "neo4j Python driver is not installed: " + str(error)[:120], source="neo4j-driver")
        try:
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user and self.password else None)
            with driver.session(database=self.database) as session:
                rowsets = {}
                for key, statement in zip(["rules", "conditions", "derivations", "relationTypes", "versions", "candidates"], rulebox_snapshot_statements()):
                    result = session.run(statement["statement"], **statement["parameters"])
                    rowsets[key] = [neo4j_record_to_dict(record) for record in result]
            driver.close()
            return rulebox_snapshot_from_rows(rowsets, source="neo4j-driver")
        except Exception as error:  # noqa: BLE001 - admin read should degrade to defaults.
            return rulebox_store_snapshot_unavailable("error", str(error)[:180], source="neo4j-driver")

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        if not self.uri:
            return NullOntologyGraphRepository().save_rulebox(payload)
        try:
            rules = rulebox_rules_from_payload(payload or {})
        except ValueError as error:
            return {"configured": True, "saved": False, "status": "invalid-rulebox", "reason": str(error)}
        clear_inference = bool((payload or {}).get("clearInference", True))
        clear_result = self.clear_rulebox(clear_inference=clear_inference)
        if clear_result.get("status") not in {"ok", "skipped"}:
            snapshot = self.rulebox_snapshot()
            snapshot.update({"saved": False, "status": clear_result.get("status"), "reason": clear_result.get("reason"), "clearResult": clear_result})
            return snapshot
        save_result = self.save_graph(rulebox_graph_from_rules(rules))
        version_result = self.record_rulebox_version(rules, payload or {}, save_result, clear_result)
        snapshot = self.rulebox_snapshot()
        snapshot.update({
            "saved": bool(save_result.get("saved")),
            "status": save_result.get("status") or snapshot.get("status"),
            "reason": save_result.get("reason") or snapshot.get("reason") or "",
            "clearResult": clear_result,
            "saveResult": save_result,
            "versionResult": version_result,
        })
        return snapshot

    def record_rulebox_version(
        self,
        rules: Iterable[GraphInferenceRule],
        payload: Dict[str, object],
        save_result: Dict[str, object],
        clear_result: Dict[str, object],
    ) -> Dict[str, object]:
        if not save_result.get("saved"):
            return {"status": "skipped", "reason": "RuleBox graph was not saved."}
        version = rulebox_version_payload(
            list(rules),
            utc_now(),
            change_reason=str((payload or {}).get("changeReason") or "").strip(),
            author=str((payload or {}).get("author") or "local-admin").strip(),
        )
        version["clearInference"] = bool((clear_result or {}).get("clearInference"))
        statements = rulebox_version_statements(version)
        if self.uri.startswith("http://") or self.uri.startswith("https://"):
            endpoint, headers = self.http_endpoint_and_headers()
            try:
                response = self.post_http_statements(endpoint, headers, statements)
            except Exception as error:  # noqa: BLE001 - governance write should report structured errors.
                return {"status": "error", "reason": str(error)[:180], "version": version}
            errors = response.get("errors") or []
            if errors:
                return {"status": "neo4j-error", "reason": json.dumps(errors[:2], ensure_ascii=False)[:300], "version": version}
            return {"status": "ok", "version": version}
        if self.uri.startswith("bolt://") or self.uri.startswith("neo4j://"):
            try:
                from neo4j import GraphDatabase
                driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user and self.password else None)
                with driver.session(database=self.database) as session:
                    for statement in statements:
                        session.run(statement["statement"], **statement["parameters"])
                driver.close()
                return {"status": "ok", "version": version}
            except Exception as error:  # noqa: BLE001 - governance write should report structured errors.
                return {"status": "error", "reason": str(error)[:180], "version": version}
        return {"status": "unsupported-uri", "reason": "Unsupported Neo4j URI.", "version": version}

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        if not self.uri:
            return NullOntologyGraphRepository().save_rule_change_candidates(candidates, context)
        rules = []
        try:
            snapshot = self.rulebox_snapshot()
            rules = snapshot.get("rules") or []
        except Exception:  # noqa: BLE001 - candidate save can still validate without the snapshot.
            rules = []
        existing_rule_ids = [
            str(item.get("rule_id") or item.get("ruleId") or "")
            for item in rules
            if isinstance(item, dict)
        ]
        normalized = [
            normalize_rule_change_candidate(candidate, existing_rule_ids=existing_rule_ids)
            for candidate in (candidates or [])
            if isinstance(candidate, dict)
        ]
        normalized = [item for item in normalized if item]
        if not normalized:
            return {"configured": True, "status": "no-candidates", "candidateCount": 0, "savedCount": 0}
        statements = rule_change_candidate_statements(normalized, context or {})
        if self.uri.startswith("http://") or self.uri.startswith("https://"):
            endpoint, headers = self.http_endpoint_and_headers()
            try:
                response = self.post_http_statements(endpoint, headers, statements)
            except Exception as error:  # noqa: BLE001 - admin command should report structured errors.
                return {"configured": True, "status": "error", "reason": str(error)[:180], "candidateCount": len(normalized), "savedCount": 0}
            errors = response.get("errors") or []
            if errors:
                return {"configured": True, "status": "neo4j-error", "reason": json.dumps(errors[:2], ensure_ascii=False)[:300], "candidateCount": len(normalized), "savedCount": 0}
            return {"configured": True, "status": "ok", "candidateCount": len(normalized), "savedCount": len(normalized)}
        if self.uri.startswith("bolt://") or self.uri.startswith("neo4j://"):
            try:
                from neo4j import GraphDatabase
                driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user and self.password else None)
                with driver.session(database=self.database) as session:
                    for statement in statements:
                        session.run(statement["statement"], **statement["parameters"])
                driver.close()
                return {"configured": True, "status": "ok", "candidateCount": len(normalized), "savedCount": len(normalized)}
            except Exception as error:  # noqa: BLE001 - admin command should report structured errors.
                return {"configured": True, "status": "error", "reason": str(error)[:180], "candidateCount": len(normalized), "savedCount": 0}
        return {"configured": True, "status": "unsupported-uri", "reason": "Unsupported Neo4j URI.", "candidateCount": len(normalized), "savedCount": 0}

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        if not self.uri:
            return NullOntologyGraphRepository().seed_ontology(payload)
        payload = payload or {}
        try:
            rules = rulebox_rules_from_payload(payload) if (payload.get("rules") is not None or payload.get("rulesJson")) else default_graph_inference_rules()
        except ValueError as error:
            return {"configured": True, "saved": False, "seeded": False, "status": "invalid-rulebox", "reason": str(error)}
        rules = list(rules)
        if payload.get("replaceRuleBox"):
            clear_result = self.clear_rulebox(clear_inference=bool(payload.get("clearInference", True)))
            if clear_result.get("status") not in {"ok", "skipped"}:
                return {"configured": True, "saved": False, "seeded": False, "status": clear_result.get("status"), "reason": clear_result.get("reason"), "clearResult": clear_result}
        else:
            clear_result = {"status": "skipped", "reason": "replaceRuleBox disabled"}
        graph = ontology_seed_graph(rules)
        result = self.save_graph(graph)
        result.update({
            "configured": True,
            "seeded": bool(result.get("saved")),
            "engineVersion": GRAPH_REASONER_VERSION,
            "ruleCount": len(rules),
            "clearResult": clear_result,
        })
        return result

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        if not self.uri:
            return NullOntologyGraphRepository().run_rulebox(payload)
        clear_inference = bool((payload or {}).get("clearInference", True))
        clear_result = self.clear_inferencebox() if clear_inference else {"status": "skipped", "reason": "clearInference disabled"}
        if clear_result.get("status") not in {"ok", "skipped"}:
            return {"configured": True, "status": clear_result.get("status"), "reason": clear_result.get("reason"), "clearResult": clear_result}
        if self.uri.startswith("http://") or self.uri.startswith("https://"):
            endpoint, headers = self.http_endpoint_and_headers()
            reasoning = self.run_native_rulebox_reasoning_via_http(endpoint, headers)
        elif self.uri.startswith("bolt://") or self.uri.startswith("neo4j://"):
            reasoning = self.run_native_rulebox_reasoning_via_driver()
        else:
            reasoning = {"status": "unsupported-uri", "reason": "Neo4j URI must start with http://, https://, bolt://, or neo4j://."}
        reasoning.update({"configured": True, "clearResult": clear_result})
        return reasoning

    def inferencebox_snapshot(self, symbols: List[str] = None, limit: int = 80) -> Dict[str, object]:
        if not self.uri:
            return NullOntologyGraphRepository().inferencebox_snapshot(symbols, limit)
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        safe_limit = max(1, min(500, int(limit or 80)))
        if self.uri.startswith("http://") or self.uri.startswith("https://"):
            return self.inferencebox_snapshot_via_http(clean_symbols, safe_limit)
        if self.uri.startswith("bolt://") or self.uri.startswith("neo4j://"):
            return self.inferencebox_snapshot_via_driver(clean_symbols, safe_limit)
        return {
            "configured": True,
            "status": "unsupported-uri",
            "reason": "Neo4j URI must start with http://, https://, bolt://, or neo4j://.",
            "symbols": clean_symbols,
            "entities": [],
            "relations": [],
            "traces": [],
            "entityCount": 0,
            "relationCount": 0,
            "traceCount": 0,
        }

    def inferencebox_snapshot_via_http(self, symbols: List[str], limit: int) -> Dict[str, object]:
        endpoint, headers = self.http_endpoint_and_headers()
        try:
            payload = self.post_http_statements(endpoint, headers, inferencebox_snapshot_statements(symbols, limit))
        except Exception as error:  # noqa: BLE001 - projection read should degrade gracefully.
            return inferencebox_snapshot_default("error", str(error)[:180], True, symbols)
        errors = payload.get("errors") or []
        if errors:
            return inferencebox_snapshot_default("neo4j-error", json.dumps(errors[:2], ensure_ascii=False)[:300], True, symbols)
        rowsets = http_result_rowsets(payload, ["entityCounts", "relationCounts", "traceCounts", "entities", "relations", "traces"])
        return inferencebox_snapshot_from_rows(rowsets, source="neo4j-http", symbols=symbols)

    def inferencebox_snapshot_via_driver(self, symbols: List[str], limit: int) -> Dict[str, object]:
        try:
            from neo4j import GraphDatabase
        except Exception as error:  # noqa: BLE001 - optional dependency.
            return inferencebox_snapshot_default("driver-missing", "neo4j Python driver is not installed: " + str(error)[:120], True, symbols)
        try:
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user and self.password else None)
            rowsets = {}
            with driver.session(database=self.database) as session:
                for key, statement in zip(["entityCounts", "relationCounts", "traceCounts", "entities", "relations", "traces"], inferencebox_snapshot_statements(symbols, limit)):
                    result = session.run(statement["statement"], **statement["parameters"])
                    rowsets[key] = [neo4j_record_to_dict(record) for record in result]
            driver.close()
            return inferencebox_snapshot_from_rows(rowsets, source="neo4j-driver", symbols=symbols)
        except Exception as error:  # noqa: BLE001 - projection read should degrade gracefully.
            return inferencebox_snapshot_default("error", str(error)[:180], True, symbols)

    def clear_rulebox(self, clear_inference: bool = True) -> Dict[str, object]:
        if self.uri.startswith("http://") or self.uri.startswith("https://"):
            endpoint, headers = self.http_endpoint_and_headers()
            try:
                payload = self.post_http_statements(endpoint, headers, clear_rulebox_statements(clear_inference))
            except Exception as error:  # noqa: BLE001 - admin command should report structured errors.
                return {"status": "error", "reason": str(error)[:180]}
            errors = payload.get("errors") or []
            if errors:
                return {"status": "neo4j-error", "reason": json.dumps(errors[:2], ensure_ascii=False)[:300]}
            return {"status": "ok", "clearInference": clear_inference}
        if self.uri.startswith("bolt://") or self.uri.startswith("neo4j://"):
            try:
                from neo4j import GraphDatabase
                driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user and self.password else None)
                with driver.session(database=self.database) as session:
                    for statement in clear_rulebox_statements(clear_inference):
                        session.run(statement["statement"], **statement["parameters"])
                driver.close()
                return {"status": "ok", "clearInference": clear_inference}
            except Exception as error:  # noqa: BLE001 - admin command should report structured errors.
                return {"status": "error", "reason": str(error)[:180]}
        return {"status": "unsupported-uri", "reason": "Unsupported Neo4j URI."}

    def clear_inferencebox(self) -> Dict[str, object]:
        if self.uri.startswith("http://") or self.uri.startswith("https://"):
            endpoint, headers = self.http_endpoint_and_headers()
            try:
                payload = self.post_http_statements(endpoint, headers, clear_inferencebox_statements())
            except Exception as error:  # noqa: BLE001 - admin command should report structured errors.
                return {"status": "error", "reason": str(error)[:180]}
            errors = payload.get("errors") or []
            if errors:
                return {"status": "neo4j-error", "reason": json.dumps(errors[:2], ensure_ascii=False)[:300]}
            return {"status": "ok"}
        if self.uri.startswith("bolt://") or self.uri.startswith("neo4j://"):
            try:
                from neo4j import GraphDatabase
                driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user and self.password else None)
                with driver.session(database=self.database) as session:
                    for statement in clear_inferencebox_statements():
                        session.run(statement["statement"], **statement["parameters"])
                driver.close()
                return {"status": "ok"}
            except Exception as error:  # noqa: BLE001 - admin command should report structured errors.
                return {"status": "error", "reason": str(error)[:180]}
        return {"status": "unsupported-uri", "reason": "Unsupported Neo4j URI."}

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
                "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
            }
        try:
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user and self.password else None)
            with driver.session(database=self.database) as session:
                schema_prepared = True
                schema_reason = ""
                for statement in self.schema_statements():
                    try:
                        session.run(statement["statement"], **statement["parameters"])
                    except Exception as error:  # noqa: BLE001 - schema prep is best effort.
                        schema_prepared = False
                        schema_reason = str(error)[:180]
                for statement in self.statements(graph):
                    session.run(statement["statement"], **statement["parameters"])
                native_reasoning = self.run_native_rulebox_reasoning_via_driver() if self.should_run_native_reasoning(graph) else {
                    "status": "skipped",
                    "statementCount": 0,
                    "reason": "graph requested persistence-only Neo4j seed",
                }
            driver.close()
        except Exception as error:  # noqa: BLE001 - persistence must not break monitoring.
            return {
                "saved": False,
                "status": "error",
                "reason": str(error)[:180],
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
                "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
            }
        return {
            "saved": True,
            "status": "ok",
            "schemaPrepared": schema_prepared,
            "schemaReason": schema_reason,
            "nativeReasoning": native_reasoning,
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "evidenceCount": len(graph.evidence),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        }

    def should_run_native_reasoning(self, graph: PortfolioOntology) -> bool:
        worldview = getattr(graph, "worldview", {}) if isinstance(getattr(graph, "worldview", {}), dict) else {}
        return not bool(worldview.get("skipNativeReasoning"))

    def native_reasoning_statements(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        relation_types = sorted(set(
            safe_relation_type((item.properties or {}).get("relationType") or ((item.properties or {}).get("derivation") or {}).get("relation_type") or "")
            for item in graph.entities
            if item.kind == "relation-template"
        ))
        relation_types = [item for item in relation_types if item]
        return native_reasoning_statements_for_relation_types(relation_types)

    def run_native_reasoning_via_http(self, endpoint: str, headers: Dict[str, str], graph: PortfolioOntology) -> Dict[str, object]:
        statements = self.native_reasoning_statements(graph)
        if not statements:
            return {"status": "skipped", "statementCount": 0, "reason": "no RuleBox relation templates"}
        try:
            payload = self.post_http_statements(endpoint, headers, statements)
        except Exception as error:  # noqa: BLE001 - native reasoning is best effort.
            return {"status": "error", "statementCount": len(statements), "reason": str(error)[:180]}
        errors = payload.get("errors") or []
        if errors:
            return {
                "status": "neo4j-error",
                "statementCount": len(statements),
                "reason": json.dumps(errors[:2], ensure_ascii=False)[:300],
            }
        return {"status": "ok", "statementCount": len(statements)}

    def run_native_reasoning_via_driver(self, session, graph: PortfolioOntology) -> Dict[str, object]:
        statements = self.native_reasoning_statements(graph)
        if not statements:
            return {"status": "skipped", "statementCount": 0, "reason": "no RuleBox relation templates"}
        failures: List[str] = []
        for statement in statements:
            try:
                session.run(statement["statement"], **statement["parameters"])
            except Exception as error:  # noqa: BLE001 - native reasoning is best effort.
                failures.append(str(error)[:180])
        if failures:
            return {"status": "error", "statementCount": len(statements), "reason": "; ".join(failures[:2])}
        return {"status": "ok", "statementCount": len(statements)}

    def run_native_rulebox_reasoning_via_http(self, endpoint: str, headers: Dict[str, str]) -> Dict[str, object]:
        relation_types = self.rulebox_relation_types_via_http(endpoint, headers)
        statements = native_reasoning_statements_for_relation_types(relation_types)
        if not statements:
            return {"status": "skipped", "statementCount": 0, "reason": "no RuleBox relation templates"}
        try:
            payload = self.post_http_statements(endpoint, headers, statements)
        except Exception as error:  # noqa: BLE001 - admin command should report structured errors.
            return {"status": "error", "statementCount": len(statements), "reason": str(error)[:180]}
        errors = payload.get("errors") or []
        if errors:
            return {
                "status": "neo4j-error",
                "statementCount": len(statements),
                "reason": json.dumps(errors[:2], ensure_ascii=False)[:300],
            }
        return {"status": "ok", "statementCount": len(statements), "relationTypes": relation_types}

    def rulebox_relation_types_via_http(self, endpoint: str, headers: Dict[str, str]) -> List[str]:
        payload = self.post_http_statements(endpoint, headers, [rulebox_relation_types_statement()])
        errors = payload.get("errors") or []
        if errors:
            return []
        rowsets = http_result_rowsets(payload, ["relationTypes"])
        return sorted(set(safe_relation_type(row.get("relationType") or "") for row in rowsets.get("relationTypes", []) if row.get("relationType")))

    def run_native_rulebox_reasoning_via_driver(self) -> Dict[str, object]:
        try:
            from neo4j import GraphDatabase
        except Exception as error:  # noqa: BLE001 - optional dependency.
            return {"status": "driver-missing", "reason": "neo4j Python driver is not installed: " + str(error)[:120]}
        try:
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user and self.password else None)
            failures: List[str] = []
            with driver.session(database=self.database) as session:
                relation_types = sorted(set(
                    safe_relation_type(record.get("relationType") or "")
                    for record in session.run(rulebox_relation_types_statement()["statement"], **rulebox_relation_types_statement()["parameters"])
                    if record.get("relationType")
                ))
                statements = native_reasoning_statements_for_relation_types(relation_types)
                for statement in statements:
                    try:
                        session.run(statement["statement"], **statement["parameters"])
                    except Exception as error:  # noqa: BLE001 - keep running other relation types.
                        failures.append(str(error)[:180])
            driver.close()
        except Exception as error:  # noqa: BLE001 - admin command should report structured errors.
            return {"status": "error", "reason": str(error)[:180]}
        if not relation_types:
            return {"status": "skipped", "statementCount": 0, "reason": "no RuleBox relation templates"}
        if failures:
            return {"status": "error", "statementCount": len(statements), "reason": "; ".join(failures[:2]), "relationTypes": relation_types}
        return {"status": "ok", "statementCount": len(statements), "relationTypes": relation_types}


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
