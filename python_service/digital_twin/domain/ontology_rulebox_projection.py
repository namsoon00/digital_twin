from typing import Dict, Iterable

from .ontology_change_impact import rule_condition_dependency_profile
from .ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology, entity_id
from .model_signal_interpretation import (
    MODEL_SIGNAL_BRIDGE_VERSION,
    MODEL_SIGNAL_INTERPRETATION_POLICY_VERSION,
    is_model_signal_interpretation_rule,
    model_signal_bridge_groups,
    model_signal_interpretation_policy,
)
from .ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from .ontology_rule_execution_policy import rule_execution_profile
from .ontology_rule_manifest import rule_domain_manifest
from .ontology_relation_reasoning import ONTOLOGY_RULE_ENGINE_VERSION, RelationRuleDefinition
from .ontology_schema import abox_relation_properties
from .ontology_threshold_policy import rulebox_threshold_policy_payloads


def add_rulebox_concepts(graph: PortfolioOntology, rules: Iterable[GraphInferenceRule]) -> None:
    rules = list(rules or [])
    registry_id = entity_id("rule-registry", GRAPH_REASONER_VERSION)
    graph.entities.append(OntologyEntity(registry_id, "Graph Reasoner RuleBox", "rule-registry", rulebox_properties({
        "tboxClass": "RuleRegistry",
        "tboxClasses": ["RuleRegistry", "GraphReasoner"],
        "version": GRAPH_REASONER_VERSION,
        "engine": "graph-reasoner",
    })))
    graph.relations.append(OntologyRelation(
        entity_id("ontology-box", "RuleBox"),
        registry_id,
        "DEFINES_RULE",
        weight=1.0,
        properties=rulebox_relation_properties("DEFINES_RULE", {"source": GRAPH_REASONER_VERSION}),
    ))
    add_threshold_policy_concepts(graph, registry_id)
    add_model_signal_interpretation_concepts(graph, registry_id, rules)
    for rule in rules:
        execution_profile = rule_execution_profile(rule)
        domain_manifest = rule_domain_manifest(rule, execution=execution_profile)
        knowledge_basis = rule.resolved_knowledge_basis.to_dict()
        claim_contract = rule.resolved_claim_contract
        claim_payload = claim_contract.to_dict()
        interpretation_policy = (
            model_signal_interpretation_policy(rule)
            if is_model_signal_interpretation_rule(rule)
            else None
        )
        rule_id = entity_id("rule", rule.rule_id)
        graph.entities.append(OntologyEntity(rule_id, rule.label, "rule", rulebox_properties({
            "tboxClass": (
                "ModelSignalInterpretationPolicy"
                if interpretation_policy
                else "GraphInferenceRule"
            ),
            "tboxClasses": (
                ["ReasoningRule", "ModelSignalInterpretationPolicy"]
                if interpretation_policy
                else ["ReasoningRule", "GraphInferenceRule"]
            ),
            "ruleId": rule.rule_id,
            "version": rule.version,
            "enabled": rule.enabled,
            "sourceKind": rule.source_kind,
            "actionGroup": rule.action_group,
            "actionLevel": rule.action_level,
            "promptHint": rule.prompt_hint,
            "hypothesisFamilyKey": rule.hypothesis_family_key,
            "hypothesisLifecycle": rule.resolved_hypothesis_lifecycle().to_dict(),
            "modelInputContract": dict(rule.model_input_contract or {}),
            "anyConditionMinCount": rule.any_condition_min_count,
            "executionStage": execution_profile["executionStage"],
            "failurePolicy": execution_profile["failurePolicy"],
            "costHint": execution_profile["costHint"],
            "executionProfile": execution_profile,
            "domainManifest": domain_manifest,
            "knowledgeBasis": knowledge_basis,
            "claimContract": claim_payload,
            "ruleKind": knowledge_basis["ruleKind"],
            "theoryFamily": knowledge_basis["theoryFamily"],
            "thesisFamily": knowledge_basis["thesisFamily"],
            "knowledgeValidationStatus": knowledge_basis["validationStatus"],
            "assessmentScope": domain_manifest["assessmentScope"],
            "triggerFamilies": domain_manifest["triggerFamilies"],
            "requiredFacts": domain_manifest["requiredFacts"],
            "lifecycleClass": domain_manifest["lifecycleClass"],
            "executionStageOverride": rule.execution_stage,
            "failurePolicyOverride": rule.failure_policy,
            "costHintOverride": rule.cost_hint,
            "conditionCount": len(rule.conditions),
            "derivationCount": len(rule.derivations),
            **(interpretation_policy.to_dict() if interpretation_policy else {}),
        })))
        graph.relations.append(OntologyRelation(
            registry_id,
            rule_id,
            "DEFINES_RULE",
            weight=1.0,
            properties=rulebox_relation_properties("DEFINES_RULE", {"ruleId": rule.rule_id}),
        ))
        claim_class = {
            "market-hypothesis": "MarketHypothesisClaim",
            "risk-invariant": "RiskInvariantClaim",
            "execution-feasibility": "ExecutionFeasibilityClaim",
            "data-reliability": "DataReliabilityClaim",
            "causal-context": "CausalContextClaim",
        }.get(claim_contract.claim_type, "RuleClaimContract")
        claim_id = entity_id("rule-claim", claim_contract.claim_contract_id)
        graph.entities.append(OntologyEntity(
            claim_id,
            claim_contract.statement,
            "rule-claim",
            rulebox_properties({
                "tboxClass": claim_class,
                "tboxClasses": ["RuleClaimContract", claim_class],
                **claim_payload,
            }),
        ))
        graph.relations.append(OntologyRelation(
            rule_id,
            claim_id,
            "GOVERNED_BY_CLAIM",
            weight=1.0,
            properties=rulebox_relation_properties("GOVERNED_BY_CLAIM", {
                "ruleId": rule.rule_id,
                "claimContractId": claim_contract.claim_contract_id,
                "claimType": claim_contract.claim_type,
            }),
        ))
        if claim_contract.is_predictive:
            outcome_payload = claim_contract.outcome_contract.to_dict()
            outcome_id = entity_id("hypothesis-outcome-contract", claim_contract.claim_contract_id)
            graph.entities.append(OntologyEntity(
                outcome_id,
                rule.label + " 사후 검증 계약",
                "hypothesis-outcome-contract",
                rulebox_properties({
                    "tboxClass": "HypothesisOutcomeContract",
                    "tboxClasses": ["RuleBoxGovernance", "HypothesisOutcomeContract"],
                    "ruleId": rule.rule_id,
                    "claimContractId": claim_contract.claim_contract_id,
                    **outcome_payload,
                }),
            ))
            graph.relations.append(OntologyRelation(
                claim_id,
                outcome_id,
                "USES_HYPOTHESIS_OUTCOME_CONTRACT",
                weight=1.0,
                properties=rulebox_relation_properties("USES_HYPOTHESIS_OUTCOME_CONTRACT", {
                    "ruleId": rule.rule_id,
                    "claimContractId": claim_contract.claim_contract_id,
                }),
            ))
            for criterion_index, criterion in enumerate(claim_contract.outcome_contract.criteria or []):
                criterion_payload = criterion.to_dict()
                criterion_id = entity_id(
                    "hypothesis-outcome-criterion",
                    claim_contract.claim_contract_id + ":" + criterion.criterion_id,
                )
                graph.entities.append(OntologyEntity(
                    criterion_id,
                    criterion.label,
                    "hypothesis-outcome-criterion",
                    rulebox_properties({
                        "tboxClass": "HypothesisOutcomeCriterion",
                        "tboxClasses": ["RuleBoxGovernance", "HypothesisOutcomeCriterion"],
                        "ruleId": rule.rule_id,
                        "claimContractId": claim_contract.claim_contract_id,
                        "criterionIndex": criterion_index,
                        **criterion_payload,
                    }),
                ))
                graph.relations.append(OntologyRelation(
                    outcome_id,
                    criterion_id,
                    "HAS_OUTCOME_CRITERION",
                    weight=1.0,
                    properties=rulebox_relation_properties("HAS_OUTCOME_CRITERION", {
                        "ruleId": rule.rule_id,
                        "criterionId": criterion.criterion_id,
                    }),
                ))
        if interpretation_policy:
            graph.relations.append(OntologyRelation(
                registry_id,
                rule_id,
                "DEFINES_SIGNAL_INTERPRETATION",
                weight=1.0,
                properties=rulebox_relation_properties("DEFINES_SIGNAL_INTERPRETATION", {
                    "ruleId": rule.rule_id,
                    "policyId": interpretation_policy.policy_id,
                    "bridgeContextKey": interpretation_policy.bridge_context_key,
                    "executionMode": "typedb-shared-model-signal-bridge",
                }),
            ))
        for condition_index, condition in enumerate(rule.conditions):
            condition_id = entity_id("rule-condition", rule.rule_id + ":" + condition.condition_id)
            graph.entities.append(OntologyEntity(condition_id, condition.description, "rule-condition", rulebox_properties({
                "tboxClass": "RuleCondition",
                "tboxClasses": ["RuleCondition", "ValidationRule"],
                "ruleId": rule.rule_id,
                "conditionId": condition.condition_id,
                "conditionIndex": condition_index,
                "condition": condition.to_dict(),
            })))
            graph.relations.append(OntologyRelation(
                rule_id,
                condition_id,
                "HAS_CONDITION",
                weight=1.0,
                properties=rulebox_relation_properties("HAS_CONDITION", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition.condition_id,
                }),
            ))
            dependency = rule_condition_dependency_profile(condition)
            dependency_id = entity_id("rule-dependency", rule.rule_id + ":" + condition.condition_id)
            graph.entities.append(OntologyEntity(dependency_id, condition.description, "rule-dependency", rulebox_properties({
                "tboxClass": "RuleDependency",
                "tboxClasses": ["RuleDependency", "RuleCondition", "ContextRequirement", "InferenceInvalidator"],
                "ruleId": rule.rule_id,
                "conditionId": condition.condition_id,
                "scopeFamilies": list(dependency.get("scopeFamilies") or []),
                "dependencyKeys": list(dependency.get("dependencyKeys") or []),
                "conditionKind": dependency.get("conditionKind"),
                "field": dependency.get("field"),
                "relationType": dependency.get("relationType"),
                "targetKind": dependency.get("targetKind"),
                "role": dependency.get("role"),
                "conservative": bool(dependency.get("conservative")),
                "canTriggerEvaluation": bool(dependency.get("canTriggerEvaluation", True)),
                "canInvalidatePriorResult": bool(dependency.get("canInvalidatePriorResult", True)),
                "contextOnly": bool(dependency.get("contextOnly")),
            })))
            graph.relations.append(OntologyRelation(
                rule_id,
                dependency_id,
                "HAS_RULE_DEPENDENCY",
                weight=1.0,
                properties=rulebox_relation_properties("HAS_RULE_DEPENDENCY", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition.condition_id,
                    "scopeFamilies": list(dependency.get("scopeFamilies") or []),
                    "conservative": bool(dependency.get("conservative")),
                }),
            ))
            graph.relations.append(OntologyRelation(
                rule_id,
                dependency_id,
                "TRIGGERS_EVALUATION",
                weight=1.0,
                properties=rulebox_relation_properties("TRIGGERS_EVALUATION", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition.condition_id,
                    "dependencyKeys": list(dependency.get("dependencyKeys") or []),
                    "enabled": bool(dependency.get("canTriggerEvaluation", True)),
                    "evaluationAuthority": "typedb",
                }),
            ))
            graph.relations.append(OntologyRelation(
                rule_id,
                dependency_id,
                "REQUIRES_CONTEXT",
                weight=1.0,
                properties=rulebox_relation_properties("REQUIRES_CONTEXT", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition.condition_id,
                    "dependencyKeys": list(dependency.get("dependencyKeys") or []),
                    "retainWhenUnchanged": True,
                }),
            ))
            graph.relations.append(OntologyRelation(
                rule_id,
                dependency_id,
                "INVALIDATED_BY",
                weight=1.0,
                properties=rulebox_relation_properties("INVALIDATED_BY", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition.condition_id,
                    "dependencyKeys": list(dependency.get("dependencyKeys") or []),
                    "evaluationAuthority": "typedb",
                    "enabled": bool(dependency.get("canInvalidatePriorResult", True)),
                }),
            ))
        for dependency_index, dependency in enumerate(
            (rule.model_input_contract or {}).get("conditionProfiles") or []
        ):
            if not isinstance(dependency, dict):
                continue
            condition_id_value = str(
                dependency.get("conditionId")
                or "model-input-" + str(dependency_index + 1)
            )
            dependency_id = entity_id(
                "rule-dependency",
                rule.rule_id + ":model-input:" + condition_id_value,
            )
            graph.entities.append(OntologyEntity(
                dependency_id,
                "모델 입력 · " + condition_id_value,
                "rule-dependency",
                rulebox_properties({
                    "tboxClass": "RuleDependency",
                    "tboxClasses": ["RuleDependency", "InferenceInvalidator"],
                    "ruleId": rule.rule_id,
                    "conditionId": condition_id_value,
                    "dependencyRole": "model-input-routing",
                    "scopeFamilies": list(dependency.get("scopeFamilies") or []),
                    "dependencyKeys": list(dependency.get("dependencyKeys") or []),
                    "conditionKind": dependency.get("conditionKind"),
                    "field": dependency.get("field"),
                    "relationType": dependency.get("relationType"),
                    "targetKind": dependency.get("targetKind"),
                    "role": dependency.get("role"),
                    "conservative": bool(dependency.get("conservative")),
                    "canTriggerEvaluation": bool(
                        dependency.get("canTriggerEvaluation", True)
                    ),
                    "canInvalidatePriorResult": bool(
                        dependency.get("canInvalidatePriorResult", True)
                    ),
                    "contextOnly": False,
                }),
            ))
            graph.relations.append(OntologyRelation(
                rule_id,
                dependency_id,
                "HAS_RULE_DEPENDENCY",
                weight=1.0,
                properties=rulebox_relation_properties("HAS_RULE_DEPENDENCY", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition_id_value,
                    "dependencyRole": "model-input-routing",
                    "scopeFamilies": list(dependency.get("scopeFamilies") or []),
                    "conservative": bool(dependency.get("conservative")),
                }),
            ))
            graph.relations.append(OntologyRelation(
                rule_id,
                dependency_id,
                "TRIGGERS_EVALUATION",
                weight=1.0,
                properties=rulebox_relation_properties("TRIGGERS_EVALUATION", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition_id_value,
                    "dependencyRole": "model-input-routing",
                    "dependencyKeys": list(dependency.get("dependencyKeys") or []),
                    "enabled": bool(dependency.get("canTriggerEvaluation", True)),
                    "evaluationAuthority": "statistical-model",
                }),
            ))
            graph.relations.append(OntologyRelation(
                rule_id,
                dependency_id,
                "INVALIDATED_BY",
                weight=1.0,
                properties=rulebox_relation_properties("INVALIDATED_BY", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition_id_value,
                    "dependencyRole": "model-input-routing",
                    "dependencyKeys": list(dependency.get("dependencyKeys") or []),
                    "evaluationAuthority": "statistical-model",
                    "enabled": bool(
                        dependency.get("canInvalidatePriorResult", True)
                    ),
                }),
            ))
        for index, derivation in enumerate(rule.derivations):
            template_id = entity_id("relation-template", rule.rule_id + ":" + str(index))
            derivation_payload = derivation.to_dict()
            derivation_payload["action_group"] = derivation.action_group or rule.action_group
            derivation_payload["action_level"] = derivation.action_level or rule.action_level
            graph.entities.append(OntologyEntity(template_id, derivation.target_label, "relation-template", rulebox_properties({
                "tboxClass": "RelationTemplate",
                "tboxClasses": ["RelationTemplate", "DerivedAssertion", "DerivedFactLineage", "RuleDecisionPolicy", "RulePriorityPolicy"],
                "ruleId": rule.rule_id,
                "relationType": derivation.relation_type,
                "derivationIndex": index,
                "derivation": derivation_payload,
            })))
            graph.relations.append(OntologyRelation(
                rule_id,
                template_id,
                "DERIVES_RELATION",
                weight=1.0,
                properties=rulebox_relation_properties("DERIVES_RELATION", {
                    "ruleId": rule.rule_id,
                    "relationType": derivation.relation_type,
                }),
            ))
            graph.relations.append(OntologyRelation(
                rule_id,
                template_id,
                "DERIVES_FACT",
                weight=1.0,
                properties=rulebox_relation_properties("DERIVES_FACT", {
                    "ruleId": rule.rule_id,
                    "relationType": derivation.relation_type,
                    "targetKind": derivation.target_kind,
                    "evaluationAuthority": "typedb",
                }),
            ))
            if interpretation_policy:
                graph.relations.append(OntologyRelation(
                    rule_id,
                    template_id,
                    "PRESERVES_RULE_LINEAGE",
                    weight=1.0,
                    properties=rulebox_relation_properties("PRESERVES_RULE_LINEAGE", {
                        "ruleId": rule.rule_id,
                        "policyId": interpretation_policy.policy_id,
                        "relationType": derivation.relation_type,
                        "lineageStable": True,
                    }),
                ))


def add_model_signal_interpretation_concepts(
    graph: PortfolioOntology,
    registry_id: str,
    rules: Iterable[GraphInferenceRule],
) -> None:
    """Project shared execution bridges without duplicating policy meaning."""

    for group in model_signal_bridge_groups(rules, enabled_only=False):
        bridge_id = entity_id("model-signal-bridge", group.source_scope)
        graph.entities.append(OntologyEntity(
            bridge_id,
            "모델 신호 범용 브리지 · " + group.source_scope,
            "model-signal-bridge",
            rulebox_properties({
                "tboxClass": "ModelSignalBridge",
                "tboxClasses": ["ReasoningRule", "ModelSignalBridge"],
                "bridgeId": "model-signal-bridge:" + group.source_scope,
                "bridgeContextKey": group.bridge_context_key,
                "sourceScope": group.source_scope,
                "version": MODEL_SIGNAL_BRIDGE_VERSION,
                "interpretationPolicyVersion": MODEL_SIGNAL_INTERPRETATION_POLICY_VERSION,
                "executionMode": "typedb-shared-model-signal-bridge",
                "policyCount": len(group.policy_ids),
                "policyIds": list(group.policy_ids),
                "lineageRuleIds": list(group.legacy_rule_ids),
            }),
        ))
        graph.relations.append(OntologyRelation(
            registry_id,
            bridge_id,
            "DEFINES_MODEL_SIGNAL_BRIDGE",
            weight=1.0,
            properties=rulebox_relation_properties("DEFINES_MODEL_SIGNAL_BRIDGE", {
                "bridgeContextKey": group.bridge_context_key,
                "sourceScope": group.source_scope,
                "version": MODEL_SIGNAL_BRIDGE_VERSION,
            }),
        ))
        for rule_id_value in group.legacy_rule_ids:
            graph.relations.append(OntologyRelation(
                bridge_id,
                entity_id("rule", rule_id_value),
                "APPLIES_SIGNAL_INTERPRETATION",
                weight=1.0,
                properties=rulebox_relation_properties("APPLIES_SIGNAL_INTERPRETATION", {
                    "ruleId": rule_id_value,
                    "policyId": "model-signal-interpretation:" + rule_id_value,
                    "bridgeContextKey": group.bridge_context_key,
                    "sourceScope": group.source_scope,
                }),
            ))


def add_threshold_policy_concepts(graph: PortfolioOntology, registry_id: str) -> None:
    policy_registry_id = entity_id("threshold-policy-registry", GRAPH_REASONER_VERSION)
    graph.entities.append(OntologyEntity(policy_registry_id, "Ontology Threshold Policy Registry", "threshold-policy-registry", rulebox_properties({
        "tboxClass": "RuleRegistry",
        "tboxClasses": ["RuleRegistry", "RuleDecisionPolicy"],
        "version": GRAPH_REASONER_VERSION,
        "engine": "threshold-policy",
    })))
    graph.relations.append(OntologyRelation(
        registry_id,
        policy_registry_id,
        "DEFINES_POLICY",
        weight=1.0,
        properties=rulebox_relation_properties("DEFINES_POLICY", {"source": GRAPH_REASONER_VERSION}),
    ))
    for index, payload in enumerate(rulebox_threshold_policy_payloads()):
        policy_id = str(payload.get("policyId") or "threshold-policy-" + str(index + 1))
        entity = entity_id("threshold-policy", policy_id)
        graph.entities.append(OntologyEntity(entity, str(payload.get("label") or policy_id), "threshold-policy", rulebox_properties({
            "tboxClass": payload.get("tboxClass"),
            "tboxClasses": payload.get("tboxClasses") or [],
            "policyId": policy_id,
            "policyVersion": payload.get("version"),
            "policySource": payload.get("source"),
            "thresholdCount": payload.get("thresholdCount"),
            "thresholds": payload.get("thresholds") or {},
        })))
        graph.relations.append(OntologyRelation(
            policy_registry_id,
            entity,
            "DEFINES_POLICY",
            weight=1.0,
            properties=rulebox_relation_properties("DEFINES_POLICY", {
                "policyId": policy_id,
                "source": GRAPH_REASONER_VERSION,
            }),
        ))


def add_relation_rulebox_concepts(graph: PortfolioOntology, rules: Iterable[RelationRuleDefinition]) -> None:
    registry_id = entity_id("relation-rule-registry", ONTOLOGY_RULE_ENGINE_VERSION)
    graph.entities.append(OntologyEntity(registry_id, "Relation RuleBox", "relation-rule-registry", rulebox_properties({
        "tboxClass": "RuleRegistry",
        "tboxClasses": ["RuleRegistry", "RelationRuleRegistry"],
        "version": ONTOLOGY_RULE_ENGINE_VERSION,
        "engine": "ontology-relation-rules",
    })))
    graph.relations.append(OntologyRelation(
        entity_id("ontology-box", "RuleBox"),
        registry_id,
        "DEFINES_RULE",
        weight=1.0,
        properties=rulebox_relation_properties("DEFINES_RULE", {"source": ONTOLOGY_RULE_ENGINE_VERSION}),
    ))
    for rule in rules or []:
        rule_id = entity_id("relation-rule", rule.rule_id)
        graph.entities.append(OntologyEntity(rule_id, rule.label, "relation-rule", rulebox_properties({
            "tboxClass": "RelationReasoningRule",
            "tboxClasses": ["ReasoningRule", "RelationReasoningRule"],
            "ruleId": rule.rule_id,
            "version": rule.version,
            "engine": ONTOLOGY_RULE_ENGINE_VERSION,
            "relationType": rule.relation_type,
            "signalType": rule.signal_type,
            "conditionSummary": rule.condition_summary,
            "promptHint": rule.prompt_hint,
            "requiredFields": list(rule.required_fields or []),
        })))
        graph.relations.append(OntologyRelation(
            registry_id,
            rule_id,
            "DEFINES_RULE",
            weight=1.0,
            properties=rulebox_relation_properties("DEFINES_RULE", {"ruleId": rule.rule_id, "source": ONTOLOGY_RULE_ENGINE_VERSION}),
        ))
        condition_id = entity_id("relation-rule-condition", rule.rule_id)
        graph.entities.append(OntologyEntity(condition_id, rule.condition_summary or rule.label, "relation-rule-condition", rulebox_properties({
            "tboxClass": "RuleCondition",
            "tboxClasses": ["RuleCondition", "RelationRuleCondition"],
            "ruleId": rule.rule_id,
            "conditionSummary": rule.condition_summary,
            "requiredFields": list(rule.required_fields or []),
        })))
        graph.relations.append(OntologyRelation(
            rule_id,
            condition_id,
            "HAS_CONDITION",
            weight=1.0,
            properties=rulebox_relation_properties("HAS_CONDITION", {"ruleId": rule.rule_id, "source": ONTOLOGY_RULE_ENGINE_VERSION}),
        ))
        dependency = rule_condition_dependency_profile({
            "conditionId": rule.rule_id,
            "kind": "relation",
            "relationType": rule.relation_type,
            "relationPropertyFilters": {"field": list(rule.required_fields or [])},
        })
        dependency_id = entity_id("relation-rule-dependency", rule.rule_id)
        graph.entities.append(OntologyEntity(dependency_id, rule.condition_summary or rule.label, "rule-dependency", rulebox_properties({
            "tboxClass": "RuleDependency",
            "tboxClasses": ["RuleDependency", "RelationRuleCondition"],
            "ruleId": rule.rule_id,
            "conditionId": rule.rule_id,
            "scopeFamilies": list(dependency.get("scopeFamilies") or []),
            "conditionKind": dependency.get("conditionKind"),
            "field": dependency.get("field"),
            "relationType": dependency.get("relationType"),
            "targetKind": dependency.get("targetKind"),
            "role": dependency.get("role"),
            "conservative": bool(dependency.get("conservative")),
        })))
        graph.relations.append(OntologyRelation(
            rule_id,
            dependency_id,
            "HAS_RULE_DEPENDENCY",
            weight=1.0,
            properties=rulebox_relation_properties("HAS_RULE_DEPENDENCY", {
                "ruleId": rule.rule_id,
                "conditionId": rule.rule_id,
                "scopeFamilies": list(dependency.get("scopeFamilies") or []),
                "conservative": bool(dependency.get("conservative")),
                "source": ONTOLOGY_RULE_ENGINE_VERSION,
            }),
        ))
        template_id = entity_id("relation-rule-template", rule.rule_id)
        graph.entities.append(OntologyEntity(template_id, rule.relation_type, "relation-rule-template", rulebox_properties({
            "tboxClass": "RelationTemplate",
            "tboxClasses": ["RelationTemplate", "RelationRuleTemplate"],
            "ruleId": rule.rule_id,
            "relationType": rule.relation_type,
            "signalType": rule.signal_type,
            "promptHint": rule.prompt_hint,
        })))
        graph.relations.append(OntologyRelation(
            rule_id,
            template_id,
            "DERIVES_RELATION",
            weight=1.0,
            properties=rulebox_relation_properties("DERIVES_RELATION", {
                "ruleId": rule.rule_id,
                "relationType": rule.relation_type,
                "source": ONTOLOGY_RULE_ENGINE_VERSION,
            }),
        ))


def rulebox_properties(properties: Dict[str, object]) -> Dict[str, object]:
    payload = dict(properties or {})
    payload.setdefault("ontologyBox", "RuleBox")
    payload.setdefault("box", "RuleBox")
    payload.setdefault("boundedContext", "reasoning-insight")
    payload.setdefault("engineVersion", GRAPH_REASONER_VERSION)
    return payload


def rulebox_relation_properties(relation_type: str, properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = abox_relation_properties(relation_type, properties or {})
    payload.update({"ontologyBox": "RuleBox", "box": "RuleBox", "engineVersion": GRAPH_REASONER_VERSION})
    return payload
