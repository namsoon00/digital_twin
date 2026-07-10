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
    rulebox_governance_candidates,
    rulebox_version_payload,
)
from ..domain.ontology_rulebox_projection import add_rulebox_concepts
from ..domain.ontology_schema import tbox_entities, tbox_relations
from .settings import runtime_settings, utc_now


class NullOntologyGraphRepository:
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
            "CREATE INDEX ontology_entity_tbox_class IF NOT EXISTS FOR (n:OntologyEntity) ON (n.tboxClass)",
            "CREATE INDEX ontology_entity_bounded_context IF NOT EXISTS FOR (n:OntologyEntity) ON (n.boundedContext)",
            "CREATE INDEX ontology_entity_condition_kind IF NOT EXISTS FOR (n:OntologyEntity) ON (n.conditionKind)",
            "CREATE INDEX ontology_entity_derivation_relation_type IF NOT EXISTS FOR (n:OntologyEntity) ON (n.derivationRelationType)",
            "CREATE INDEX ontology_entity_level_type IF NOT EXISTS FOR (n:OntologyEntity) ON (n.levelType)",
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

    def rows_for_entities(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for item in graph.entities:
            properties = item.properties or {}
            condition = properties.get("condition") if isinstance(properties.get("condition"), dict) else {}
            derivation = properties.get("derivation") if isinstance(properties.get("derivation"), dict) else {}
            rows.append({
                "id": item.entity_id,
                "label": item.label,
                "kind": item.kind,
                "ontologyBox": str(properties.get("ontologyBox") or "ABox"),
                "symbol": str(properties.get("symbol") or ""),
                "ruleId": str(properties.get("ruleId") or ""),
                "version": str(properties.get("version") or ""),
                "sourceKind": str(properties.get("sourceKind") or ""),
                "actionGroup": str(properties.get("actionGroup") or ""),
                "actionLevel": str(properties.get("actionLevel") or ""),
                "promptHint": str(properties.get("promptHint") or ""),
                "tboxClass": str(properties.get("tboxClass") or ""),
                "tboxClasses": list_of_strings(properties.get("tboxClasses")),
                "className": str(properties.get("className") or ""),
                "parentClass": str(properties.get("parentClass") or ""),
                "relationTypeName": str(properties.get("relationType") or ""),
                "boundedContext": str(properties.get("boundedContext") or ""),
                "box": str(properties.get("box") or properties.get("ontologyBox") or ""),
                "sourceContext": str(properties.get("sourceContext") or ""),
                "targetContext": str(properties.get("targetContext") or ""),
                "sourceValue": str(properties.get("source") or ""),
                "profitLossRate": number_or_none(properties.get("profitLossRate")),
                "levelType": str(properties.get("levelType") or ""),
                "field": str(properties.get("field") or ""),
                "valueNumber": number_or_none(properties.get("value")),
                "polarity": str(properties.get("polarity") or ""),
                "group": str(properties.get("group") or ""),
                "relationScope": str(properties.get("relationScope") or ""),
                "eventType": str(properties.get("eventType") or ""),
                "materialityScore": number_or_none(properties.get("materialityScore")),
                "materialityPassed": bool(properties.get("materialityPassed")) if "materialityPassed" in properties else None,
                "relevanceScore": number_or_none(properties.get("relevanceScore")),
                "sourceReliability": number_or_none(properties.get("sourceReliability")),
                "impactScore": number_or_none(properties.get("impactScore")),
                "confidence": number_or_none(properties.get("confidence")),
                "enabled": bool(properties.get("enabled")) if "enabled" in properties else False,
                "conditionId": str(properties.get("conditionId") or condition.get("condition_id") or ""),
                "conditionKind": str(condition.get("kind") or ""),
                "conditionField": str(condition.get("field") or ""),
                "conditionOperator": str(condition.get("operator") or ""),
                "conditionValueString": str(condition.get("value") or ""),
                "conditionValueNumber": number_or_none(condition.get("value")),
                "conditionRelationType": str(condition.get("relation_type") or "").upper(),
                "conditionDirection": str(condition.get("direction") or "out"),
                "conditionTargetKind": str(condition.get("target_kind") or ""),
                "conditionTargetLevelTypes": condition_target_level_types(condition),
                "conditionTargetFields": condition_target_filter_values(condition, "field"),
                "conditionTargetTboxClasses": condition_target_filter_values(condition, "tboxClass") + condition_target_filter_values(condition, "tboxClasses"),
                "conditionTargetGroups": condition_target_filter_values(condition, "group"),
                "conditionTargetRelationScopes": condition_target_filter_values(condition, "relationScope"),
                "conditionTargetEventTypes": condition_target_filter_values(condition, "eventType"),
                "conditionTargetPolarities": condition_target_filter_values(condition, "polarity"),
                "conditionTargetMaterialityPassed": condition_target_filter_bool(condition, "materialityPassed"),
                "conditionTargetMinMaterialityScore": condition_target_filter_number(condition, "minMaterialityScore"),
                "conditionTargetMinValue": condition_target_filter_number(condition, "minValue"),
                "conditionTargetMaxValue": condition_target_filter_number(condition, "maxValue"),
                "conditionRelationPolarities": condition_relation_filter_values(condition, "polarity"),
                "conditionRelationTransitionTypes": condition_relation_filter_values(condition, "transitionType"),
                "conditionRelationFields": condition_relation_filter_values(condition, "field"),
                "conditionRelationSignalGroups": condition_relation_filter_values(condition, "signalGroup"),
                "conditionRelationMaterialityPassed": condition_relation_filter_bool(condition, "materialityPassed"),
                "conditionRelationMinRiskImpact": condition_relation_filter_number(condition, "minRiskImpact"),
                "conditionRelationMinSupportImpact": condition_relation_filter_number(condition, "minSupportImpact"),
                "conditionMinWeight": float(condition.get("min_weight") or 0),
                "derivationRelationType": str(derivation.get("relation_type") or "").upper(),
                "derivationIndex": int(properties.get("derivationIndex") or 0),
                "derivationTargetKind": str(derivation.get("target_kind") or ""),
                "derivationTargetKey": str(derivation.get("target_key") or ""),
                "derivationTargetLabel": str(derivation.get("target_label") or ""),
                "derivationTboxClass": str(derivation.get("tbox_class") or ""),
                "derivationTboxClasses": list_of_strings(derivation.get("tbox_classes")),
                "derivationPolarity": str(derivation.get("polarity") or ""),
                "derivationRiskImpact": float(derivation.get("risk_impact") or 0),
                "derivationSupportImpact": float(derivation.get("support_impact") or 0),
                "derivationWeight": float(derivation.get("weight") or 0),
                "derivationBeliefLabel": str(derivation.get("belief_label") or ""),
                "derivationAiInfluenceLabel": str(derivation.get("ai_influence_label") or ""),
                "derivationActionGroup": str(derivation.get("action_group") or ""),
                "derivationActionLevel": str(derivation.get("action_level") or ""),
                "derivationDecisionStage": derivation_decision_stage(derivation),
                "derivationStagePriority": derivation_stage_priority(derivation),
                "propertiesJson": json.dumps(properties, ensure_ascii=False, sort_keys=True),
            })
        return rows

    def rows_for_relations(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for item in graph.relations:
            properties = item.properties or {}
            rows.append({
                "source": item.source,
                "target": item.target,
                "type": safe_relation_type(item.relation_type),
                "weight": float(item.weight or 0),
                "ontologyBox": str(properties.get("ontologyBox") or "ABox"),
                "boundedContext": str(properties.get("boundedContext") or ""),
                "ruleId": str(properties.get("ruleId") or ""),
                "polarity": str(properties.get("polarity") or ""),
                "transitionType": str(properties.get("transitionType") or ""),
                "field": str(properties.get("field") or properties.get("observationField") or ""),
                "signalGroup": str(properties.get("signalGroup") or ""),
                "materialityPassed": bool(properties.get("materialityPassed")) if "materialityPassed" in properties else None,
                "materialityScore": number_or_none(properties.get("materialityScore")),
                "riskImpact": number_or_none(properties.get("riskImpact") or properties.get("opinionImpact")),
                "supportImpact": number_or_none(properties.get("supportImpact")),
                "decisionStage": str(properties.get("decisionStage") or ""),
                "stagePriority": number_or_none(properties.get("stagePriority")),
                "aiInfluenceLabel": str(properties.get("aiInfluenceLabel") or ""),
                "evidenceIds": [str(value) for value in item.evidence_ids],
                "propertiesJson": json.dumps(properties, ensure_ascii=False, sort_keys=True),
            })
        return rows

    def rows_for_evidence(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": item.evidence_id,
                "subject": item.subject,
                "kind": item.kind,
                "source": item.source,
                "summary": item.summary,
                "ontologyBox": str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "ABox")),
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
                "ontologyBox": "InferenceBox" if str(item.belief_id or "").startswith("belief:inference:") else "ABox",
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

    def rows_for_reasoning_cards(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": str(item.get("id") or ""),
                "symbol": str(item.get("symbol") or "").upper(),
                "companyName": str(item.get("companyName") or item.get("displayName") or item.get("symbol") or ""),
                "source": str(item.get("source") or ""),
                "portfolioRelation": str(item.get("portfolioRelation") or ""),
                "status": str(item.get("status") or ""),
                "ontologyBox": "ABox",
                "payloadJson": json.dumps(item, ensure_ascii=False, sort_keys=True),
            }
            for item in (getattr(graph, "reasoning_cards", []) or [])
            if isinstance(item, dict) and item.get("id") and item.get("symbol")
        ]

    def statements(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        updated_at = utc_now()
        statements = [
            {
                "statement": (
                    "UNWIND $rows AS row "
                    "MERGE (n:OntologyEntity {id: row.id}) "
                    "SET n.label = row.label, n.kind = row.kind, "
                    "n.ontologyBox = row.ontologyBox, n.symbol = row.symbol, n.ruleId = row.ruleId, "
                    "n.version = row.version, n.sourceKind = row.sourceKind, "
                    "n.actionGroup = row.actionGroup, n.actionLevel = row.actionLevel, n.promptHint = row.promptHint, "
                    "n.tboxClass = row.tboxClass, n.tboxClasses = row.tboxClasses, n.boundedContext = row.boundedContext, "
                    "n.className = row.className, n.parentClass = row.parentClass, n.relationTypeName = row.relationTypeName, "
                    "n.box = row.box, n.sourceContext = row.sourceContext, n.targetContext = row.targetContext, "
                    "n.sourceValue = row.sourceValue, n.profitLossRate = row.profitLossRate, n.levelType = row.levelType, "
                    "n.field = row.field, n.valueNumber = row.valueNumber, n.polarity = row.polarity, n.group = row.group, "
                    "n.relationScope = row.relationScope, n.eventType = row.eventType, n.materialityScore = row.materialityScore, "
                    "n.materialityPassed = row.materialityPassed, n.relevanceScore = row.relevanceScore, "
                    "n.sourceReliability = row.sourceReliability, n.impactScore = row.impactScore, n.confidence = row.confidence, "
                    "n.enabled = row.enabled, n.conditionId = row.conditionId, n.conditionKind = row.conditionKind, "
                    "n.conditionField = row.conditionField, n.conditionOperator = row.conditionOperator, "
                    "n.conditionValueString = row.conditionValueString, n.conditionValueNumber = row.conditionValueNumber, "
                    "n.conditionRelationType = row.conditionRelationType, n.conditionDirection = row.conditionDirection, "
                    "n.conditionTargetKind = row.conditionTargetKind, n.conditionTargetLevelTypes = row.conditionTargetLevelTypes, "
                    "n.conditionTargetFields = row.conditionTargetFields, n.conditionTargetTboxClasses = row.conditionTargetTboxClasses, "
                    "n.conditionTargetGroups = row.conditionTargetGroups, n.conditionTargetRelationScopes = row.conditionTargetRelationScopes, "
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
                    "n.confidence = row.confidence, n.ontologyBox = row.ontologyBox, n.evidenceIds = row.evidenceIds, n.updatedAt = $updatedAt "
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
                    "n.ontologyBox = row.ontologyBox, n.payloadJson = row.payloadJson, n.updatedAt = $updatedAt "
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
                    "n.ontologyBox = row.ontologyBox, n.payloadJson = row.payloadJson, n.updatedAt = $updatedAt "
                    "FOREACH (_ IN CASE WHEN row.ontologyBox = 'ABox' THEN [1] ELSE [] END | SET n:ABox) "
                    "WITH row, n, 'stock:' + row.symbol AS stockId MATCH (s:OntologyEntity {id: stockId}) "
                    "MERGE (s)-[:HAS_REASONING_CARD]->(n)"
                ),
                "parameters": {"rows": self.rows_for_reasoning_cards(graph), "updatedAt": updated_at},
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
                    "r.ontologyBox = row.ontologyBox, r.ruleId = row.ruleId, r.boundedContext = row.boundedContext, "
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
        if self.user or self.password:
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
        native_reasoning = self.run_native_reasoning_via_http(endpoint, headers, graph) if self.should_run_native_reasoning(graph) else {
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
        if self.user or self.password:
            token = base64.b64encode((self.user + ":" + self.password).encode("utf-8")).decode("ascii")
            headers["Authorization"] = "Basic " + token
        return endpoint, headers

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
        rowsets = http_result_rowsets(payload, ["rules", "conditions", "derivations", "relationTypes", "versions"])
        return rulebox_snapshot_from_rows(rowsets, source="neo4j-http")

    def rulebox_snapshot_via_driver(self) -> Dict[str, object]:
        try:
            from neo4j import GraphDatabase
        except Exception as error:  # noqa: BLE001 - optional dependency.
            return rulebox_store_snapshot_unavailable("driver-missing", "neo4j Python driver is not installed: " + str(error)[:120], source="neo4j-driver")
        try:
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user or self.password else None)
            with driver.session(database=self.database) as session:
                rowsets = {}
                for key, statement in zip(["rules", "conditions", "derivations", "relationTypes", "versions"], rulebox_snapshot_statements()):
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
                driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user or self.password else None)
                with driver.session(database=self.database) as session:
                    for statement in statements:
                        session.run(statement["statement"], **statement["parameters"])
                driver.close()
                return {"status": "ok", "version": version}
            except Exception as error:  # noqa: BLE001 - governance write should report structured errors.
                return {"status": "error", "reason": str(error)[:180], "version": version}
        return {"status": "unsupported-uri", "reason": "Unsupported Neo4j URI.", "version": version}

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
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user or self.password else None)
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
                driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user or self.password else None)
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
                driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user or self.password else None)
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
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user or self.password else None)
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
                native_reasoning = self.run_native_reasoning_via_driver(session, graph) if self.should_run_native_reasoning(graph) else {
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
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password) if self.user or self.password else None)
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


def ontology_seed_graph(rules: Iterable[GraphInferenceRule] = None) -> PortfolioOntology:
    graph = rulebox_graph_from_rules(rules or default_graph_inference_rules())
    graph.portfolio_id = "ontology-seed"
    graph.worldview.update({
        "model": "investment-ontology-seed",
        "description": "TBox schema and default RuleBox concepts persisted to Neo4j before runtime ABox projections arrive.",
        "skipNativeReasoning": True,
    })
    return graph


def graph_box_entity_counts(graph: PortfolioOntology) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in graph.entities:
        box = str((item.properties or {}).get("ontologyBox") or "ABox")
        counts[box] = counts.get(box, 0) + 1
    return counts


def graph_box_relation_counts(graph: PortfolioOntology) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in graph.relations:
        box = str((item.properties or {}).get("ontologyBox") or "ABox")
        counts[box] = counts.get(box, 0) + 1
    return counts


def rulebox_rules_to_payload(rules: Iterable[GraphInferenceRule]) -> List[Dict[str, object]]:
    return [rule.to_dict() for rule in rules]


def rulebox_rules_from_payload(payload: Dict[str, object]) -> List[GraphInferenceRule]:
    payload = payload or {}
    raw_rules = payload.get("rules")
    if payload.get("rulesJson"):
        raw_rules = json.loads(str(payload.get("rulesJson") or "[]"))
    if raw_rules is None:
        if payload.get("useBootstrapDefaults") or payload.get("resetToDefaults"):
            raw_rules = rulebox_rules_to_payload(default_graph_inference_rules())
        else:
            raise ValueError("RuleBox rules are required. Use seed_ontology or useBootstrapDefaults to write bootstrap defaults.")
    if not isinstance(raw_rules, list):
        raise ValueError("RuleBox rules must be a list.")
    rules = [GraphInferenceRule.from_dict(item) for item in raw_rules if isinstance(item, dict)]
    if not rules:
        raise ValueError("RuleBox rules are empty.")
    return rules


def rulebox_graph_from_rules(rules: Iterable[GraphInferenceRule]) -> PortfolioOntology:
    graph = PortfolioOntology("neo4j-rulebox-admin")
    graph.entities.extend(tbox_entities())
    graph.relations.extend(tbox_relations())
    add_rulebox_concepts(graph, rules)
    graph.worldview = {
        "model": "neo4j-rulebox-source-of-truth",
        "engineVersion": GRAPH_REASONER_VERSION,
        "adminEditable": True,
    }
    return graph


def rulebox_default_snapshot(status: str = "defaults", reason: str = "", configured: bool = False) -> Dict[str, object]:
    rules = rulebox_rules_to_payload(default_graph_inference_rules())
    return {
        "configured": configured,
        "saved": False,
        "status": status,
        "source": "defaults",
        "reason": reason,
        "engineVersion": GRAPH_REASONER_VERSION,
        "rules": rules,
        "ruleCount": len(rules),
        "conditionCount": sum(len(item.get("conditions") or []) for item in rules),
        "derivationCount": sum(len(item.get("derivations") or []) for item in rules),
        "defaultsFallbackUsed": True,
        "relationTypes": sorted(set(
            safe_relation_type(derivation.get("relation_type") or "")
            for rule in rules
            for derivation in (rule.get("derivations") or [])
            if derivation.get("relation_type")
        )),
        "versions": [],
        "versionCount": 0,
        "changeCandidates": rulebox_governance_candidates(rules, []),
    }


def rulebox_store_snapshot_unavailable(status: str, reason: str = "", source: str = "neo4j") -> Dict[str, object]:
    bootstrap_rules = rulebox_rules_to_payload(default_graph_inference_rules())
    return {
        "configured": True,
        "saved": False,
        "status": status,
        "source": source,
        "reason": reason,
        "engineVersion": GRAPH_REASONER_VERSION,
        "rules": [],
        "ruleCount": 0,
        "conditionCount": 0,
        "derivationCount": 0,
        "relationTypes": [],
        "defaultsFallbackUsed": False,
        "bootstrapAvailable": True,
        "bootstrapRuleCount": len(bootstrap_rules),
        "bootstrapRules": bootstrap_rules,
        "versions": [],
        "versionCount": 0,
        "changeCandidates": rulebox_governance_candidates([], []),
    }


def rulebox_snapshot_statements() -> List[Dict[str, object]]:
    return [
        {
            "statement": (
                "MATCH (rule:OntologyEntity {kind: 'rule', ontologyBox: 'RuleBox'}) "
                "RETURN rule.id AS id, rule.ruleId AS ruleId, rule.label AS label, rule.version AS version, "
                "rule.sourceKind AS sourceKind, rule.enabled AS enabled, rule.actionGroup AS actionGroup, "
                "rule.actionLevel AS actionLevel, rule.promptHint AS promptHint, rule.propertiesJson AS propertiesJson, "
                "rule.updatedAt AS updatedAt ORDER BY rule.ruleId"
            ),
            "parameters": {},
        },
        {
            "statement": (
                "MATCH (rule:OntologyEntity {kind: 'rule', ontologyBox: 'RuleBox'})-[:HAS_CONDITION]->"
                "(condition:OntologyEntity {kind: 'rule-condition', ontologyBox: 'RuleBox'}) "
                "RETURN rule.ruleId AS ruleId, condition.id AS id, condition.conditionId AS conditionId, "
                "condition.label AS description, condition.conditionKind AS kind, condition.conditionField AS field, "
                "condition.conditionOperator AS operator, condition.conditionValueString AS valueString, "
                "condition.conditionValueNumber AS valueNumber, condition.conditionRelationType AS relationType, "
                "condition.conditionDirection AS direction, condition.conditionTargetKind AS targetKind, "
                "condition.conditionTargetLevelTypes AS targetLevelTypes, condition.conditionRelationPolarities AS relationPolarities, "
                "condition.conditionRelationTransitionTypes AS relationTransitionTypes, condition.conditionMinWeight AS minWeight, "
                "condition.conditionTargetFields AS targetFields, condition.conditionTargetTboxClasses AS targetTboxClasses, "
                "condition.conditionTargetGroups AS targetGroups, condition.conditionTargetRelationScopes AS targetRelationScopes, "
                "condition.conditionTargetEventTypes AS targetEventTypes, condition.conditionTargetPolarities AS targetPolarities, "
                "condition.conditionTargetMaterialityPassed AS targetMaterialityPassed, "
                "condition.conditionTargetMinMaterialityScore AS targetMinMaterialityScore, "
                "condition.conditionTargetMinValue AS targetMinValue, condition.conditionTargetMaxValue AS targetMaxValue, "
                "condition.conditionRelationFields AS relationFields, condition.conditionRelationSignalGroups AS relationSignalGroups, "
                "condition.conditionRelationMaterialityPassed AS relationMaterialityPassed, "
                "condition.conditionRelationMinRiskImpact AS relationMinRiskImpact, "
                "condition.conditionRelationMinSupportImpact AS relationMinSupportImpact, "
                "condition.propertiesJson AS propertiesJson ORDER BY rule.ruleId, condition.conditionId"
            ),
            "parameters": {},
        },
        {
            "statement": (
                "MATCH (rule:OntologyEntity {kind: 'rule', ontologyBox: 'RuleBox'})-[:DERIVES_RELATION]->"
                "(template:OntologyEntity {kind: 'relation-template', ontologyBox: 'RuleBox'}) "
                "RETURN rule.ruleId AS ruleId, template.id AS id, template.label AS label, "
                "template.derivationIndex AS derivationIndex, template.derivationRelationType AS relationType, "
                "template.derivationTargetKind AS targetKind, template.derivationTargetKey AS targetKey, "
                "template.derivationTargetLabel AS targetLabel, template.derivationTboxClass AS tboxClass, "
                "template.derivationTboxClasses AS tboxClasses, template.derivationPolarity AS polarity, "
                "template.derivationRiskImpact AS riskImpact, template.derivationSupportImpact AS supportImpact, "
                "template.derivationWeight AS weight, template.derivationBeliefLabel AS beliefLabel, "
                "template.derivationAiInfluenceLabel AS aiInfluenceLabel, template.derivationActionGroup AS actionGroup, "
                "template.derivationActionLevel AS actionLevel, template.derivationDecisionStage AS decisionStage, "
                "template.derivationStagePriority AS stagePriority, template.propertiesJson AS propertiesJson "
                "ORDER BY rule.ruleId, template.derivationIndex"
            ),
            "parameters": {},
        },
        rulebox_relation_types_statement(),
        {
            "statement": (
                "MATCH (version:OntologyEntity {kind: 'rulebox-version', ontologyBox: 'RuleBoxGovernance'}) "
                "RETURN version.id AS id, version.label AS label, version.versionLabel AS versionLabel, "
                "version.rulesHash AS rulesHash, version.shortHash AS shortHash, version.ruleCount AS ruleCount, "
                "version.conditionCount AS conditionCount, version.derivationCount AS derivationCount, "
                "version.status AS status, version.changeReason AS changeReason, version.author AS author, "
                "version.engineVersion AS engineVersion, version.createdAt AS createdAt "
                "ORDER BY version.createdAt DESC LIMIT 12"
            ),
            "parameters": {},
        },
    ]


def rulebox_version_statements(version: Dict[str, object]) -> List[Dict[str, object]]:
    return [
        {
            "statement": (
                "MERGE (registry:OntologyEntity {id: 'rulebox-governance:graph-reasoner'}) "
                "SET registry.label = 'RuleBox Governance', registry.kind = 'rulebox-governance', "
                "registry.ontologyBox = 'RuleBoxGovernance', registry.boundedContext = 'reasoning-insight', "
                "registry.tboxClass = 'RuleBoxGovernance', registry.engineVersion = $engineVersion, registry.updatedAt = $createdAt "
                "MERGE (version:OntologyEntity {id: $id}) "
                "SET version.label = $label, version.kind = 'rulebox-version', version.ontologyBox = 'RuleBoxGovernance', "
                "version.boundedContext = 'reasoning-insight', version.tboxClass = 'RuleBoxVersion', version.versionLabel = $versionLabel, "
                "version.rulesHash = $rulesHash, version.shortHash = $shortHash, version.ruleCount = $ruleCount, "
                "version.conditionCount = $conditionCount, version.derivationCount = $derivationCount, "
                "version.status = $status, version.changeReason = $changeReason, version.author = $author, "
                "version.engineVersion = $engineVersion, version.rulesJson = $rulesJson, "
                "version.clearInference = $clearInference, version.createdAt = $createdAt, version.updatedAt = $createdAt "
                "MERGE (registry)-[rel:HAS_RULEBOX_VERSION]->(version) "
                "SET rel.weight = 1.0, rel.ontologyBox = 'RuleBoxGovernance', rel.updatedAt = $createdAt"
            ),
            "parameters": {
                "id": str(version.get("id") or ""),
                "label": str(version.get("label") or ""),
                "versionLabel": str(version.get("versionLabel") or ""),
                "rulesHash": str(version.get("rulesHash") or ""),
                "shortHash": str(version.get("shortHash") or ""),
                "ruleCount": int(version.get("ruleCount") or 0),
                "conditionCount": int(version.get("conditionCount") or 0),
                "derivationCount": int(version.get("derivationCount") or 0),
                "status": str(version.get("status") or "saved"),
                "changeReason": str(version.get("changeReason") or ""),
                "author": str(version.get("author") or "local-admin"),
                "engineVersion": str(version.get("engineVersion") or GRAPH_REASONER_VERSION),
                "rulesJson": str(version.get("rulesJson") or "[]"),
                "clearInference": bool(version.get("clearInference")),
                "createdAt": str(version.get("createdAt") or utc_now()),
            },
        }
    ]


def rulebox_relation_types_statement() -> Dict[str, object]:
    return {
        "statement": (
            "MATCH (template:OntologyEntity {kind: 'relation-template', ontologyBox: 'RuleBox'}) "
            "RETURN DISTINCT template.derivationRelationType AS relationType ORDER BY relationType"
        ),
        "parameters": {},
    }


def inferencebox_snapshot_statements(symbols: List[str] = None, limit: int = 80) -> List[Dict[str, object]]:
    clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
    safe_limit = max(1, min(500, int(limit or 80)))
    entity_scope = "n.ontologyBox = 'InferenceBox' AND (size($symbols) = 0 OR n.symbol IN $symbols)"
    relation_scope = "r.ontologyBox = 'InferenceBox' AND (size($symbols) = 0 OR a.symbol IN $symbols OR b.symbol IN $symbols OR r.symbol IN $symbols)"
    trace_scope = "trace.kind = 'inference-trace' AND trace.ontologyBox = 'InferenceBox' AND (size($symbols) = 0 OR trace.symbol IN $symbols)"
    return [
        {
            "statement": (
                "MATCH (n:OntologyEntity) WHERE " + entity_scope + " "
                "RETURN count(n) AS entityCount, "
                "sum(CASE WHEN coalesce(n.nativeNeo4jReasoned, false) THEN 1 ELSE 0 END) AS nativeEntityCount"
            ),
            "parameters": {"symbols": clean_symbols},
        },
        {
            "statement": (
                "MATCH (a)-[r]->(b) WHERE " + relation_scope + " "
                "RETURN count(r) AS relationCount, "
                "sum(CASE WHEN coalesce(r.nativeNeo4jReasoned, false) THEN 1 ELSE 0 END) AS nativeRelationCount"
            ),
            "parameters": {"symbols": clean_symbols},
        },
        {
            "statement": (
                "MATCH (trace:OntologyEntity) WHERE " + trace_scope + " "
                "RETURN count(trace) AS traceCount, "
                "sum(CASE WHEN coalesce(trace.nativeNeo4jReasoned, false) THEN 1 ELSE 0 END) AS nativeTraceCount"
            ),
            "parameters": {"symbols": clean_symbols},
        },
        {
            "statement": (
                "MATCH (n:OntologyEntity) WHERE " + entity_scope + " "
                "RETURN n.id AS id, n.label AS label, n.kind AS kind, n.symbol AS symbol, "
                "n.ruleId AS ruleId, n.tboxClass AS tboxClass, n.polarity AS polarity, "
                "n.actionGroup AS actionGroup, n.actionLevel AS actionLevel, n.confidence AS confidence, "
                "n.decisionStage AS decisionStage, n.stagePriority AS stagePriority, "
                "n.nativeNeo4jReasoned AS nativeNeo4jReasoned, n.updatedAt AS updatedAt "
                "ORDER BY coalesce(n.updatedAt, '') DESC, n.id LIMIT $limit"
            ),
            "parameters": {"symbols": clean_symbols, "limit": safe_limit},
        },
        {
            "statement": (
                "MATCH (a)-[r]->(b) WHERE " + relation_scope + " "
                "RETURN type(r) AS type, a.id AS source, a.label AS sourceLabel, b.id AS target, b.label AS targetLabel, "
                "r.ruleId AS ruleId, r.polarity AS polarity, r.riskImpact AS riskImpact, r.supportImpact AS supportImpact, "
                "r.weight AS weight, r.decisionStage AS decisionStage, r.stagePriority AS stagePriority, "
                "r.aiInfluenceLabel AS aiInfluenceLabel, r.inferenceTraceId AS inferenceTraceId, "
                "r.nativeNeo4jReasoned AS nativeNeo4jReasoned, r.updatedAt AS updatedAt "
                "ORDER BY coalesce(r.updatedAt, '') DESC, type(r), a.id, b.id LIMIT $limit"
            ),
            "parameters": {"symbols": clean_symbols, "limit": safe_limit},
        },
        {
            "statement": (
                "MATCH (trace:OntologyEntity) WHERE " + trace_scope + " "
                "RETURN trace.id AS id, trace.label AS label, trace.symbol AS symbol, trace.ruleId AS ruleId, "
                "trace.confidence AS confidence, trace.matchedConditionIds AS matchedConditionIds, "
                "trace.nativeNeo4jReasoned AS nativeNeo4jReasoned, trace.updatedAt AS updatedAt "
                "ORDER BY coalesce(trace.updatedAt, '') DESC, trace.id LIMIT $limit"
            ),
            "parameters": {"symbols": clean_symbols, "limit": safe_limit},
        },
    ]


def clear_rulebox_statements(clear_inference: bool = True) -> List[Dict[str, object]]:
    statements = []
    if clear_inference:
        statements.extend(clear_inferencebox_statements())
    statements.append({
        "statement": "MATCH (n:OntologyEntity) WHERE n.ontologyBox = 'RuleBox' DETACH DELETE n",
        "parameters": {},
    })
    return statements


def clear_inferencebox_statements() -> List[Dict[str, object]]:
    return [
        {
            "statement": "MATCH (n:OntologyEntity) WHERE n.ontologyBox = 'InferenceBox' DETACH DELETE n",
            "parameters": {},
        },
        {
            "statement": "MATCH (n:OntologyEvidence) WHERE n.ontologyBox = 'InferenceBox' DETACH DELETE n",
            "parameters": {},
        },
        {
            "statement": "MATCH (n:OntologyBelief) WHERE n.ontologyBox = 'InferenceBox' DETACH DELETE n",
            "parameters": {},
        },
    ]


def http_result_rowsets(payload: Dict[str, object], keys: List[str]) -> Dict[str, List[Dict[str, object]]]:
    rowsets: Dict[str, List[Dict[str, object]]] = {}
    for key, result in zip(keys, payload.get("results") or []):
        columns = result.get("columns") or []
        rows = []
        for item in result.get("data") or []:
            values = item.get("row") if isinstance(item, dict) else []
            rows.append(dict(zip(columns, values or [])))
        rowsets[key] = rows
    for key in keys:
        rowsets.setdefault(key, [])
    return rowsets


def neo4j_record_to_dict(record) -> Dict[str, object]:
    if hasattr(record, "data"):
        return record.data()
    return dict(record)


def rulebox_snapshot_from_rows(rowsets: Dict[str, List[Dict[str, object]]], source: str) -> Dict[str, object]:
    rules = build_rulebox_rules_from_rows(
        rowsets.get("rules") or [],
        rowsets.get("conditions") or [],
        rowsets.get("derivations") or [],
    )
    if not rules:
        return rulebox_store_snapshot_unavailable(
            "empty",
            "Neo4j RuleBox nodes are empty. Seed or save RuleBox rules before running graph reasoning.",
            source=source,
        )
    relation_types = sorted(set(
        safe_relation_type(row.get("relationType") or "")
        for row in (rowsets.get("relationTypes") or [])
        if row.get("relationType")
    ))
    payload = rulebox_rules_to_payload(rules)
    versions = [rulebox_version_from_row(row) for row in (rowsets.get("versions") or [])]
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "source": source,
        "engineVersion": GRAPH_REASONER_VERSION,
        "rules": payload,
        "ruleCount": len(payload),
        "conditionCount": sum(len(item.get("conditions") or []) for item in payload),
        "derivationCount": sum(len(item.get("derivations") or []) for item in payload),
        "relationTypes": relation_types,
        "defaultsFallbackUsed": False,
        "versions": versions,
        "versionCount": len(versions),
        "changeCandidates": rulebox_governance_candidates(payload, versions),
    }


def rulebox_version_from_row(row: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "versionLabel": str(row.get("versionLabel") or row.get("shortHash") or ""),
        "rulesHash": str(row.get("rulesHash") or ""),
        "shortHash": str(row.get("shortHash") or ""),
        "ruleCount": int(number_or_none(row.get("ruleCount")) or 0),
        "conditionCount": int(number_or_none(row.get("conditionCount")) or 0),
        "derivationCount": int(number_or_none(row.get("derivationCount")) or 0),
        "status": str(row.get("status") or ""),
        "changeReason": str(row.get("changeReason") or ""),
        "author": str(row.get("author") or ""),
        "engineVersion": str(row.get("engineVersion") or ""),
        "createdAt": str(row.get("createdAt") or ""),
    }


def inferencebox_snapshot_default(status: str, reason: str, configured: bool, symbols: List[str] = None) -> Dict[str, object]:
    return {
        "configured": bool(configured),
        "saved": False,
        "status": status,
        "source": "neo4j",
        "reason": reason,
        "engineVersion": GRAPH_REASONER_VERSION,
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


def inferencebox_snapshot_from_rows(rowsets: Dict[str, List[Dict[str, object]]], source: str, symbols: List[str] = None) -> Dict[str, object]:
    entity_count_row = first_row(rowsets.get("entityCounts"))
    relation_count_row = first_row(rowsets.get("relationCounts"))
    trace_count_row = first_row(rowsets.get("traceCounts"))
    entities = [inferencebox_entity_payload(row) for row in rowsets.get("entities") or []]
    relations = [inferencebox_relation_payload(row) for row in rowsets.get("relations") or []]
    traces = [inferencebox_trace_payload(row) for row in rowsets.get("traces") or []]
    native_relation_count = int(number_or_none(relation_count_row.get("nativeRelationCount")) or 0)
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "source": source,
        "engineVersion": GRAPH_REASONER_VERSION,
        "symbols": list(symbols or []),
        "entityCount": int(number_or_none(entity_count_row.get("entityCount")) or len(entities)),
        "relationCount": int(number_or_none(relation_count_row.get("relationCount")) or len(relations)),
        "traceCount": int(number_or_none(trace_count_row.get("traceCount")) or len(traces)),
        "nativeEntityCount": int(number_or_none(entity_count_row.get("nativeEntityCount")) or 0),
        "nativeRelationCount": native_relation_count,
        "nativeTraceCount": int(number_or_none(trace_count_row.get("nativeTraceCount")) or 0),
        "neo4jNativeReasoningUsed": native_relation_count > 0,
        "entities": entities,
        "relations": relations,
        "traces": traces,
    }


def first_row(rows: List[Dict[str, object]]) -> Dict[str, object]:
    return rows[0] if rows else {}


def inferencebox_entity_payload(row: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "kind": str(row.get("kind") or ""),
        "symbol": str(row.get("symbol") or ""),
        "ruleId": str(row.get("ruleId") or ""),
        "tboxClass": str(row.get("tboxClass") or ""),
        "polarity": str(row.get("polarity") or ""),
        "actionGroup": str(row.get("actionGroup") or ""),
        "actionLevel": str(row.get("actionLevel") or ""),
        "decisionStage": str(row.get("decisionStage") or ""),
        "stagePriority": number_or_none(row.get("stagePriority")),
        "confidence": number_or_none(row.get("confidence")),
        "nativeNeo4jReasoned": bool(row.get("nativeNeo4jReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def inferencebox_relation_payload(row: Dict[str, object]) -> Dict[str, object]:
    return {
        "type": str(row.get("type") or ""),
        "source": str(row.get("source") or ""),
        "sourceLabel": str(row.get("sourceLabel") or ""),
        "target": str(row.get("target") or ""),
        "targetLabel": str(row.get("targetLabel") or ""),
        "ruleId": str(row.get("ruleId") or ""),
        "polarity": str(row.get("polarity") or ""),
        "riskImpact": number_or_none(row.get("riskImpact")),
        "supportImpact": number_or_none(row.get("supportImpact")),
        "weight": number_or_none(row.get("weight")),
        "decisionStage": str(row.get("decisionStage") or ""),
        "stagePriority": number_or_none(row.get("stagePriority")),
        "label": str(row.get("aiInfluenceLabel") or ""),
        "aiInfluenceLabel": str(row.get("aiInfluenceLabel") or ""),
        "inferenceTraceId": str(row.get("inferenceTraceId") or ""),
        "nativeNeo4jReasoned": bool(row.get("nativeNeo4jReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def inferencebox_trace_payload(row: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "symbol": str(row.get("symbol") or ""),
        "ruleId": str(row.get("ruleId") or ""),
        "confidence": number_or_none(row.get("confidence")),
        "matchedConditionIds": list_of_strings(row.get("matchedConditionIds")),
        "nativeNeo4jReasoned": bool(row.get("nativeNeo4jReasoned")),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def build_rulebox_rules_from_rows(
    rule_rows: List[Dict[str, object]],
    condition_rows: List[Dict[str, object]],
    derivation_rows: List[Dict[str, object]],
) -> List[GraphInferenceRule]:
    conditions_by_rule: Dict[str, List[Dict[str, object]]] = {}
    derivations_by_rule: Dict[str, List[Dict[str, object]]] = {}
    for row in condition_rows:
        rule_id = str(row.get("ruleId") or "")
        if rule_id:
            conditions_by_rule.setdefault(rule_id, []).append(condition_payload_from_row(row))
    for row in derivation_rows:
        rule_id = str(row.get("ruleId") or "")
        if rule_id:
            derivations_by_rule.setdefault(rule_id, []).append(derivation_payload_from_row(row))
    rules = []
    for row in rule_rows:
        props = json_object(row.get("propertiesJson"))
        rule_id = str(row.get("ruleId") or props.get("ruleId") or "")
        if not rule_id:
            continue
        payload = {
            "rule_id": rule_id,
            "label": str(row.get("label") or props.get("label") or rule_id),
            "version": str(row.get("version") or props.get("version") or GRAPH_REASONER_VERSION),
            "source_kind": str(row.get("sourceKind") or props.get("sourceKind") or "neo4j"),
            "conditions": conditions_by_rule.get(rule_id) or [],
            "derivations": derivations_by_rule.get(rule_id) or [],
            "action_group": str(row.get("actionGroup") or props.get("actionGroup") or ""),
            "action_level": str(row.get("actionLevel") or props.get("actionLevel") or ""),
            "prompt_hint": str(row.get("promptHint") or props.get("promptHint") or ""),
            "enabled": bool(row.get("enabled")) if row.get("enabled") is not None else bool(props.get("enabled", True)),
        }
        try:
            rules.append(GraphInferenceRule.from_dict(payload))
        except ValueError:
            continue
    return rules


def condition_payload_from_row(row: Dict[str, object]) -> Dict[str, object]:
    props = json_object(row.get("propertiesJson"))
    condition = props.get("condition") if isinstance(props.get("condition"), dict) else {}
    target_level_types = row.get("targetLevelTypes")
    if not isinstance(target_level_types, list):
        target_level_types = []
    target_filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    if not target_filters:
        target_filters = {}
        if target_level_types:
            target_filters["levelType"] = target_level_types
        for row_key, filter_key in [
            ("targetFields", "field"),
            ("targetTboxClasses", "tboxClasses"),
            ("targetGroups", "group"),
            ("targetRelationScopes", "relationScope"),
            ("targetEventTypes", "eventType"),
            ("targetPolarities", "polarity"),
        ]:
            values = row.get(row_key) if isinstance(row.get(row_key), list) else []
            if values:
                target_filters[filter_key] = values
        if row.get("targetMaterialityPassed") is not None:
            target_filters["materialityPassed"] = bool(row.get("targetMaterialityPassed"))
        for row_key, filter_key in [
            ("targetMinMaterialityScore", "minMaterialityScore"),
            ("targetMinValue", "minValue"),
            ("targetMaxValue", "maxValue"),
        ]:
            if row.get(row_key) is not None:
                target_filters[filter_key] = row.get(row_key)
    relation_filters = condition.get("relation_property_filters") if isinstance(condition.get("relation_property_filters"), dict) else {}
    if not relation_filters:
        relation_filters = {}
        polarities = row.get("relationPolarities") if isinstance(row.get("relationPolarities"), list) else []
        transition_types = row.get("relationTransitionTypes") if isinstance(row.get("relationTransitionTypes"), list) else []
        fields = row.get("relationFields") if isinstance(row.get("relationFields"), list) else []
        signal_groups = row.get("relationSignalGroups") if isinstance(row.get("relationSignalGroups"), list) else []
        if polarities:
            relation_filters["polarity"] = polarities
        if transition_types:
            relation_filters["transitionType"] = transition_types
        if fields:
            relation_filters["field"] = fields
        if signal_groups:
            relation_filters["signalGroup"] = signal_groups
        if row.get("relationMaterialityPassed") is not None:
            relation_filters["materialityPassed"] = bool(row.get("relationMaterialityPassed"))
        for row_key, filter_key in [
            ("relationMinRiskImpact", "minRiskImpact"),
            ("relationMinSupportImpact", "minSupportImpact"),
        ]:
            if row.get(row_key) is not None:
                relation_filters[filter_key] = row.get(row_key)
    return {
        "condition_id": str(row.get("conditionId") or condition.get("condition_id") or ""),
        "kind": str(row.get("kind") or condition.get("kind") or ""),
        "description": str(row.get("description") or condition.get("description") or ""),
        "field": str(row.get("field") or condition.get("field") or ""),
        "operator": str(row.get("operator") or condition.get("operator") or "=="),
        "value": condition.get("value") if "value" in condition else (row.get("valueNumber") if row.get("valueNumber") is not None else row.get("valueString")),
        "relation_type": str(row.get("relationType") or condition.get("relation_type") or ""),
        "direction": str(row.get("direction") or condition.get("direction") or "out"),
        "target_kind": str(row.get("targetKind") or condition.get("target_kind") or ""),
        "target_property_filters": target_filters,
        "relation_property_filters": relation_filters,
        "min_weight": float(row.get("minWeight") or condition.get("min_weight") or 0),
    }


def derivation_payload_from_row(row: Dict[str, object]) -> Dict[str, object]:
    props = json_object(row.get("propertiesJson"))
    derivation = props.get("derivation") if isinstance(props.get("derivation"), dict) else {}
    payload = {
        "relation_type": str(row.get("relationType") or derivation.get("relation_type") or ""),
        "target_kind": str(row.get("targetKind") or derivation.get("target_kind") or ""),
        "target_key": str(row.get("targetKey") or derivation.get("target_key") or ""),
        "target_label": str(row.get("targetLabel") or derivation.get("target_label") or row.get("label") or ""),
        "tbox_class": str(row.get("tboxClass") or derivation.get("tbox_class") or ""),
        "tbox_classes": list_of_strings(row.get("tboxClasses") or derivation.get("tbox_classes") or []),
        "polarity": str(row.get("polarity") or derivation.get("polarity") or "context"),
        "risk_impact": float(row.get("riskImpact") or derivation.get("risk_impact") or 0),
        "support_impact": float(row.get("supportImpact") or derivation.get("support_impact") or 0),
        "weight": float(row.get("weight") or derivation.get("weight") or 0.72),
        "belief_label": str(row.get("beliefLabel") or derivation.get("belief_label") or ""),
        "ai_influence_label": str(row.get("aiInfluenceLabel") or derivation.get("ai_influence_label") or ""),
        "action_group": str(row.get("actionGroup") or derivation.get("action_group") or ""),
        "action_level": str(row.get("actionLevel") or derivation.get("action_level") or ""),
        "decision_stage": str(row.get("decisionStage") or row.get("derivationDecisionStage") or derivation.get("decision_stage") or derivation.get("decisionStage") or ""),
        "stage_priority": float(row.get("stagePriority") or row.get("derivationStagePriority") or derivation.get("stage_priority") or derivation.get("stagePriority") or 0),
    }
    if not payload["decision_stage"]:
        payload["decision_stage"] = decision_stage_from_action(payload["action_group"], payload["action_level"])
    if not payload["stage_priority"]:
        payload["stage_priority"] = float(relation_stage_priority({
            "decisionStage": payload["decision_stage"],
            "actionGroup": payload["action_group"],
            "actionLevel": payload["action_level"],
            "riskImpact": payload["risk_impact"],
            "supportImpact": payload["support_impact"],
        }))
    return payload


def json_object(value: object) -> Dict[str, object]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def native_reasoning_statements_for_relation_types(relation_types: Iterable[str]) -> List[Dict[str, object]]:
    cleaned = sorted(set(safe_relation_type(relation_type) for relation_type in relation_types if safe_relation_type(relation_type)))
    return [native_reasoning_statement_for_relation_type(relation_type) for relation_type in cleaned]


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


def native_reasoning_statement_for_relation_type(relation_type: str) -> Dict[str, object]:
    safe_type = safe_relation_type(relation_type)
    statement = (
        "MATCH (rule:OntologyEntity {kind: 'rule', ontologyBox: 'RuleBox'}) "
        "WHERE coalesce(rule.enabled, false) = true "
        "MATCH (rule)-[:HAS_CONDITION]->(condition:OntologyEntity {kind: 'rule-condition', ontologyBox: 'RuleBox'}) "
        "WITH rule, collect(condition) AS conditions "
        "MATCH (rule)-[:DERIVES_RELATION]->(template:OntologyEntity {kind: 'relation-template', ontologyBox: 'RuleBox'}) "
        "WHERE template.derivationRelationType = $relationType "
        "MATCH (stock:OntologyEntity {kind: 'stock'}) "
        "WHERE stock.ontologyBox <> 'TBox' AND all(condition IN conditions WHERE "
        "CASE condition.conditionKind "
        "WHEN 'subject_property' THEN "
        "CASE condition.conditionOperator "
        "WHEN '==' THEN toLower(toString(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END)) = toLower(condition.conditionValueString) "
        "WHEN 'eq' THEN toLower(toString(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END)) = toLower(condition.conditionValueString) "
        "WHEN '!=' THEN toLower(toString(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END)) <> toLower(condition.conditionValueString) "
        "WHEN 'ne' THEN toLower(toString(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END)) <> toLower(condition.conditionValueString) "
        "WHEN '<=' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), 999999999.0) <= condition.conditionValueNumber "
        "WHEN 'lte' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), 999999999.0) <= condition.conditionValueNumber "
        "WHEN '>=' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), -999999999.0) >= condition.conditionValueNumber "
        "WHEN 'gte' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), -999999999.0) >= condition.conditionValueNumber "
        "WHEN '<' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), 999999999.0) < condition.conditionValueNumber "
        "WHEN 'lt' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), 999999999.0) < condition.conditionValueNumber "
        "WHEN '>' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), -999999999.0) > condition.conditionValueNumber "
        "WHEN 'gt' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), -999999999.0) > condition.conditionValueNumber "
        "ELSE false END "
        "WHEN 'relation' THEN EXISTS { "
        "MATCH (stock)-[rel]->(target:OntologyEntity) "
        "WHERE type(rel) = condition.conditionRelationType "
        "AND coalesce(rel.weight, 0.0) >= coalesce(condition.conditionMinWeight, 0.0) "
        "AND (condition.conditionTargetKind = '' OR target.kind = condition.conditionTargetKind) "
        "AND (size(coalesce(condition.conditionTargetLevelTypes, [])) = 0 OR target.levelType IN condition.conditionTargetLevelTypes) "
        "AND (size(coalesce(condition.conditionTargetFields, [])) = 0 OR target.field IN condition.conditionTargetFields) "
        "AND (size(coalesce(condition.conditionTargetTboxClasses, [])) = 0 OR target.tboxClass IN condition.conditionTargetTboxClasses OR any(cls IN coalesce(target.tboxClasses, []) WHERE cls IN condition.conditionTargetTboxClasses)) "
        "AND (size(coalesce(condition.conditionTargetGroups, [])) = 0 OR target.group IN condition.conditionTargetGroups) "
        "AND (size(coalesce(condition.conditionTargetRelationScopes, [])) = 0 OR target.relationScope IN condition.conditionTargetRelationScopes) "
        "AND (size(coalesce(condition.conditionTargetEventTypes, [])) = 0 OR target.eventType IN condition.conditionTargetEventTypes) "
        "AND (size(coalesce(condition.conditionTargetPolarities, [])) = 0 OR target.polarity IN condition.conditionTargetPolarities) "
        "AND (condition.conditionTargetMaterialityPassed IS NULL OR target.materialityPassed = condition.conditionTargetMaterialityPassed) "
        "AND (condition.conditionTargetMinMaterialityScore IS NULL OR coalesce(target.materialityScore, 0.0) >= condition.conditionTargetMinMaterialityScore) "
        "AND (condition.conditionTargetMinValue IS NULL OR coalesce(target.valueNumber, 0.0) >= condition.conditionTargetMinValue) "
        "AND (condition.conditionTargetMaxValue IS NULL OR coalesce(target.valueNumber, 0.0) <= condition.conditionTargetMaxValue) "
        "AND (size(coalesce(condition.conditionRelationPolarities, [])) = 0 OR rel.polarity IN condition.conditionRelationPolarities) "
        "AND (size(coalesce(condition.conditionRelationTransitionTypes, [])) = 0 OR rel.transitionType IN condition.conditionRelationTransitionTypes) "
        "AND (size(coalesce(condition.conditionRelationFields, [])) = 0 OR rel.field IN condition.conditionRelationFields) "
        "AND (size(coalesce(condition.conditionRelationSignalGroups, [])) = 0 OR rel.signalGroup IN condition.conditionRelationSignalGroups) "
        "AND (condition.conditionRelationMaterialityPassed IS NULL OR rel.materialityPassed = condition.conditionRelationMaterialityPassed) "
        "AND (condition.conditionRelationMinRiskImpact IS NULL OR coalesce(rel.riskImpact, 0.0) >= condition.conditionRelationMinRiskImpact) "
        "AND (condition.conditionRelationMinSupportImpact IS NULL OR coalesce(rel.supportImpact, 0.0) >= condition.conditionRelationMinSupportImpact) "
        "} "
        "ELSE false END) "
        "WITH rule, template, stock, conditions, "
        "CASE WHEN 0.62 + size(conditions) * 0.08 > 0.94 THEN 0.94 ELSE 0.62 + size(conditions) * 0.08 END AS confidence "
        "WITH rule, template, stock, conditions, confidence, "
        "replace(replace(template.derivationTargetKey, '{symbol}', stock.symbol), '{displayName}', stock.label) AS targetValue, "
        "replace(replace(template.derivationTargetLabel, '{symbol}', stock.symbol), '{displayName}', stock.label) AS targetLabel "
        "WITH rule, template, stock, conditions, confidence, targetLabel, "
        "template.derivationTargetKind + ':' + targetValue AS targetId, "
        "'inference-trace:' + stock.symbol + ':' + rule.ruleId AS traceId, "
        "'evidence:inference:' + stock.symbol + ':' + rule.ruleId AS evidenceId, "
        "'belief:inference:' + stock.symbol + ':' + rule.ruleId + ':' + toString(coalesce(template.derivationIndex, 0)) AS beliefId "
        "MERGE (target:OntologyEntity {id: targetId}) "
        "SET target.label = targetLabel, target.kind = template.derivationTargetKind, target.ontologyBox = 'InferenceBox', "
        "target.symbol = stock.symbol, target.ruleId = rule.ruleId, target.tboxClass = template.derivationTboxClass, "
        "target.actionGroup = template.derivationActionGroup, target.actionLevel = template.derivationActionLevel, "
        "target.decisionStage = template.derivationDecisionStage, target.stagePriority = template.derivationStagePriority, "
        "target.boundedContext = 'reasoning-insight', target.nativeNeo4jReasoned = true, target.updatedAt = $updatedAt "
        "MERGE (trace:OntologyEntity {id: traceId}) "
        "SET trace.label = stock.label + ' · ' + rule.label, trace.kind = 'inference-trace', trace.ontologyBox = 'InferenceBox', "
        "trace.symbol = stock.symbol, trace.ruleId = rule.ruleId, trace.tboxClass = 'InferenceTrace', "
        "trace.boundedContext = 'reasoning-insight', trace.confidence = confidence, trace.nativeNeo4jReasoned = true, "
        "trace.matchedConditionIds = [c IN conditions | c.conditionId], trace.updatedAt = $updatedAt "
        "MERGE (evidence:OntologyEvidence {id: evidenceId}) "
        "SET evidence.subject = stock.id, evidence.kind = 'inference-trace', evidence.source = 'neo4j-native-rulebox', "
        "evidence.summary = stock.label + ' · ' + rule.label, evidence.ontologyBox = 'InferenceBox', "
        "evidence.confidence = confidence, evidence.nativeNeo4jReasoned = true, evidence.updatedAt = $updatedAt "
        "MERGE (stock)-[:HAS_EVIDENCE]->(evidence) "
        "MERGE (rule)-[triggered:TRIGGERED_INFERENCE]->(trace) "
        "SET triggered.weight = confidence, triggered.ontologyBox = 'InferenceBox', triggered.ruleId = rule.ruleId, triggered.nativeNeo4jReasoned = true, triggered.updatedAt = $updatedAt "
        "MERGE (stock)-[hasTrace:HAS_INFERENCE_TRACE]->(trace) "
        "SET hasTrace.weight = confidence, hasTrace.ontologyBox = 'InferenceBox', hasTrace.ruleId = rule.ruleId, hasTrace.nativeNeo4jReasoned = true, hasTrace.updatedAt = $updatedAt "
        "MERGE (stock)-[inferred:" + safe_type + "]->(target) "
        "SET inferred.weight = coalesce(template.derivationWeight, 0.72), inferred.ontologyBox = 'InferenceBox', inferred.ruleId = rule.ruleId, "
        "inferred.polarity = template.derivationPolarity, inferred.riskImpact = template.derivationRiskImpact, "
        "inferred.supportImpact = template.derivationSupportImpact, inferred.actionGroup = template.derivationActionGroup, "
        "inferred.actionLevel = template.derivationActionLevel, inferred.decisionStage = template.derivationDecisionStage, "
        "inferred.stagePriority = template.derivationStagePriority, inferred.aiInfluenceLabel = template.derivationAiInfluenceLabel, "
        "inferred.inferenceTraceId = traceId, inferred.evidenceIds = [evidenceId], inferred.nativeNeo4jReasoned = true, inferred.updatedAt = $updatedAt "
        "MERGE (target)-[explained:EXPLAINED_BY_TRACE]->(trace) "
        "SET explained.weight = confidence, explained.ontologyBox = 'InferenceBox', explained.ruleId = rule.ruleId, explained.nativeNeo4jReasoned = true, explained.updatedAt = $updatedAt "
        "FOREACH (_ IN CASE WHEN template.derivationBeliefLabel <> '' THEN [1] ELSE [] END | "
        "MERGE (belief:OntologyBelief {id: beliefId}) "
        "SET belief.label = template.derivationBeliefLabel, belief.polarity = CASE WHEN template.derivationPolarity IN ['risk', 'support'] THEN template.derivationPolarity ELSE 'context' END, "
        "belief.confidence = confidence, belief.ontologyBox = 'InferenceBox', belief.evidenceIds = [evidenceId], belief.nativeNeo4jReasoned = true, belief.updatedAt = $updatedAt "
        "MERGE (stock)-[:HAS_BELIEF]->(belief) "
        ")"
    )
    return {"statement": statement, "parameters": {"relationType": safe_type, "updatedAt": utc_now()}}


def number_or_none(value: object):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def derivation_decision_stage(derivation: Dict[str, object]) -> str:
    explicit = str(derivation.get("decision_stage") or derivation.get("decisionStage") or "").strip()
    if explicit:
        return explicit
    return decision_stage_from_action(
        str(derivation.get("action_group") or derivation.get("actionGroup") or ""),
        str(derivation.get("action_level") or derivation.get("actionLevel") or ""),
    )


def derivation_stage_priority(derivation: Dict[str, object]):
    explicit = number_or_none(derivation.get("stage_priority") or derivation.get("stagePriority"))
    if explicit:
        return explicit
    stage = derivation_decision_stage(derivation)
    return float(relation_stage_priority({
        "decisionStage": stage,
        "actionGroup": derivation.get("action_group") or derivation.get("actionGroup"),
        "actionLevel": derivation.get("action_level") or derivation.get("actionLevel"),
        "riskImpact": derivation.get("risk_impact") or derivation.get("riskImpact"),
        "supportImpact": derivation.get("support_impact") or derivation.get("supportImpact"),
    }))


def list_of_strings(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def condition_target_level_types(condition: Dict[str, object]) -> List[str]:
    filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    return list_of_strings(filters.get("levelType"))


def condition_target_filter_values(condition: Dict[str, object], key: str) -> List[str]:
    filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    return list_of_strings(filters.get(key))


def condition_target_filter_bool(condition: Dict[str, object], key: str):
    filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    return bool_or_none(filters.get(key))


def condition_target_filter_number(condition: Dict[str, object], key: str):
    filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    return number_or_none(filters.get(key))


def condition_relation_filter_values(condition: Dict[str, object], key: str) -> List[str]:
    filters = condition.get("relation_property_filters") if isinstance(condition.get("relation_property_filters"), dict) else {}
    return list_of_strings(filters.get(key))


def condition_relation_filter_bool(condition: Dict[str, object], key: str):
    filters = condition.get("relation_property_filters") if isinstance(condition.get("relation_property_filters"), dict) else {}
    return bool_or_none(filters.get(key))


def condition_relation_filter_number(condition: Dict[str, object], key: str):
    filters = condition.get("relation_property_filters") if isinstance(condition.get("relation_property_filters"), dict) else {}
    return number_or_none(filters.get(key))


def bool_or_none(value: object):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


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
