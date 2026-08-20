"""Compile RuleBox policy into shared premises and private account overlays.

The compiler changes where facts are evaluated, never whether a condition is
true. Public market and durable knowledge predicates are evaluated by TypeDB
in SharedPremiseWorld and become compact premise references. Account predicates
and those references are then evaluated by TypeDB in PortfolioWorld.
"""

from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import json
import re
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .hypothesis_scoping import ACCOUNT_FIELDS, condition_scope_profile
from .ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology, entity_id
from .ontology_rulebox_contracts import (
    GraphInferenceRule,
    GraphRuleCondition,
    GraphRuleDerivation,
    HypothesisLifecyclePolicy,
)
from .market_world_projection import (
    SHARED_WORLD_PROJECTION_CONTRACT_VERSION,
    is_account_entity,
    shared_world_property_allowed,
)
from .ontology_worlds import world_metadata


WORLD_PARTITIONED_REASONING_VERSION = "world-partitioned-reasoning-v2"
ACCOUNT_OVERLAY_PROJECTION_CONTRACT_VERSION = "account-overlay-projection-v3"
SHARED_PREMISE_RULE_PREFIX = "shared.premise."
SHARED_PREMISE_RELATION = "HAS_SHARED_MARKET_PREMISE"
SHARED_PREMISE_KIND = "shared-market-premise"

ACCOUNT_TOPOLOGY_RELATIONS = {
    "MANAGES_PORTFOLIO",
    "HAS_POSITION",
    "HAS_WATCHLIST",
    "HOLDS",
    "WATCHES",
    "REPRESENTS_STOCK",
    "HAS_POSITION_ROLE",
    "HAS_RISK_BUDGET",
    "HAS_INVESTMENT_STRATEGY",
    "HAS_DELIVERY_PROFILE",
    "HAS_PROFIT_POLICY",
    "HAS_CASH",
    "HOLDS_CASH",
    SHARED_PREMISE_RELATION,
}

ACCOUNT_STOCK_IDENTITY_PROPERTIES = {
    "ontologybox", "box", "tboxclass", "tboxclasses", "symbol", "ticker",
    "code", "name", "market", "currency", "accountid", "portfolioid",
    "aboxscopeid", "aboxscopetype", "aboxscopefamily", "scopegenerationid",
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _property_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


def _condition_payload(condition: object) -> Dict[str, object]:
    if isinstance(condition, Mapping):
        return dict(condition)
    serializer = getattr(condition, "to_dict", None)
    return dict(serializer() or {}) if callable(serializer) else {}


def _rule_payload(rule: object) -> Dict[str, object]:
    if isinstance(rule, Mapping):
        return dict(rule)
    serializer = getattr(rule, "to_dict", None)
    return dict(serializer() or {}) if callable(serializer) else {}


def _scoped_condition(condition: GraphRuleCondition, scope: str) -> GraphRuleCondition:
    return replace(condition, hypothesis_scope=scope)


def premise_rule_id(rule_id: object) -> str:
    return SHARED_PREMISE_RULE_PREFIX + _text(rule_id)


def semantic_rule_id(rule_id: object) -> str:
    value = _text(rule_id)
    return value[len(SHARED_PREMISE_RULE_PREFIX):] if value.startswith(SHARED_PREMISE_RULE_PREFIX) else value


def rule_condition_ownership(rule: object) -> Dict[str, object]:
    payload = _rule_payload(rule)
    profiles = [
        condition_scope_profile(_condition_payload(condition), index)
        for index, condition in enumerate(payload.get("conditions") or [])
    ]
    scopes = {str(item.get("scope") or "unverified") for item in profiles}
    if not profiles or "unverified" in scopes or "mixed" in scopes:
        scope = "unverified"
    elif scopes == {"market"}:
        scope = "market"
    elif scopes == {"account"}:
        scope = "account"
    elif scopes == {"market", "account"}:
        scope = "mixed"
    else:
        scope = "unverified"
    return {
        "ruleId": _text(payload.get("rule_id") or payload.get("ruleId")),
        "scope": scope,
        "profiles": profiles,
        "marketConditionIds": [item["conditionId"] for item in profiles if item.get("scope") == "market"],
        "accountConditionIds": [item["conditionId"] for item in profiles if item.get("scope") == "account"],
        "unverifiedConditionIds": [
            item["conditionId"] for item in profiles if item.get("scope") in {"mixed", "unverified"}
        ],
    }


def _lifecycle_for(rule: GraphInferenceRule, conditions: Sequence[GraphRuleCondition]) -> HypothesisLifecyclePolicy:
    configured = rule.resolved_hypothesis_lifecycle()
    present = {condition.condition_id for condition in conditions}
    formation = [value for value in configured.formation_condition_ids if value in present]
    if not formation:
        formation = [
            condition.condition_id
            for condition in conditions
            if str(condition.role or "required").lower() not in {"optional", "negative", "exclude", "not"}
        ]
    return HypothesisLifecyclePolicy(
        formation_condition_ids=formation,
        invalidation_condition_ids=[
            value for value in configured.invalidation_condition_ids if value in present
        ],
        validity_minutes=configured.validity_minutes,
        required_freshness_domains=list(configured.required_freshness_domains),
        next_data_requirements=list(configured.next_data_requirements),
        invalidation_mode=configured.invalidation_mode,
        outcome_contract=configured.outcome_contract,
    )


def _premise_condition(rule_id: str, role: str = "required") -> GraphRuleCondition:
    return GraphRuleCondition(
        condition_id="shared-market-premise:" + rule_id,
        kind="relation",
        description="SharedPremiseWorld에서 TypeDB가 확정한 공유 시장 전제가 존재합니다.",
        relation_type=SHARED_PREMISE_RELATION,
        direction="out",
        target_kind=SHARED_PREMISE_KIND,
        target_property_filters={"group": rule_id},
        role=role,
        hypothesis_scope="account",
        evidence_group_key="shared-premise:" + rule_id,
        change_trigger=True,
        invalidation_trigger=True,
    )


def _premise_derivation(rule: GraphInferenceRule, group_key: str = "") -> GraphRuleDerivation:
    semantic_group = group_key or rule.rule_id
    return GraphRuleDerivation(
        relation_type="ESTABLISHES_SHARED_MARKET_PREMISE",
        target_kind=SHARED_PREMISE_KIND,
        target_key="{symbol}:" + semantic_group,
        target_label="{name} shared market premise",
        tbox_class="SharedMarketPremise",
        tbox_classes=["SharedMarketPremise", "DerivedAssertion"],
        polarity="context",
        evidence_role="context",
        decision_effect="reference",
        belief_label="",
        ai_influence_label="공유 시장 전제",
        action_group="shared-premise",
        action_level="observe",
        decision_stage="reference",
        decision_label="공유 시장 전제",
    )


def _partition_mixed_rule(
    rule: GraphInferenceRule,
    ownership: Mapping[str, object],
) -> Tuple[List[GraphInferenceRule], GraphInferenceRule]:
    market_ids = set(ownership.get("marketConditionIds") or [])
    account_ids = set(ownership.get("accountConditionIds") or [])
    market_conditions = [
        _scoped_condition(condition, "market")
        for condition in rule.conditions
        if condition.condition_id in market_ids
    ]
    account_conditions = [
        _scoped_condition(condition, "account")
        for condition in rule.conditions
        if condition.condition_id in account_ids
    ]
    market_any = [condition for condition in market_conditions if str(condition.role).lower() in {"any", "optional"}]
    account_any = [condition for condition in account_conditions if str(condition.role).lower() in {"any", "optional"}]
    if market_any and account_any and int(rule.any_condition_min_count or 1) != 1:
        raise ValueError(
            "Cross-world N-of-M rule cannot preserve cardinality: " + rule.rule_id
        )
    if market_any and account_any:
        market_required = [condition for condition in market_conditions if condition not in market_any]
        shared_rules = []
        resolver_conditions = list(account_conditions)
        if market_required:
            required_premise = replace(
                rule,
                rule_id=premise_rule_id(rule.rule_id),
                label=rule.label + " · required shared premise",
                conditions=market_required,
                derivations=[_premise_derivation(rule)],
                action_group="shared-premise",
                action_level="observe",
                prompt_hint="Shared TypeDB premise for " + rule.rule_id,
                hypothesis_family_key="shared-premise:" + (rule.hypothesis_family_key or rule.rule_id),
                hypothesis_lifecycle=_lifecycle_for(rule, market_required),
                any_condition_min_count=1,
                execution_stage="shared-premise",
                failure_policy="block",
                cost_hint="bounded-shared",
            )
            shared_rules.append(required_premise)
            resolver_conditions.append(_premise_condition(rule.rule_id, "required"))
        any_group = rule.rule_id + "#any"
        any_premise = replace(
            rule,
            rule_id="shared.premise.any." + rule.rule_id,
            label=rule.label + " · optional shared premise",
            conditions=market_any,
            derivations=[_premise_derivation(rule, any_group)],
            action_group="shared-premise",
            action_level="observe",
            prompt_hint="Shared TypeDB optional premise for " + rule.rule_id,
            hypothesis_family_key="shared-premise-any:" + (rule.hypothesis_family_key or rule.rule_id),
            hypothesis_lifecycle=_lifecycle_for(rule, market_any),
            any_condition_min_count=1,
            execution_stage="shared-premise",
            failure_policy="block",
            cost_hint="bounded-shared",
        )
        shared_rules.append(any_premise)
        resolver_conditions.append(_premise_condition(any_group, "any"))
        resolver = replace(
            rule,
            conditions=resolver_conditions,
            hypothesis_lifecycle=_lifecycle_for(rule, resolver_conditions),
            any_condition_min_count=1,
            execution_stage="account-overlay",
        )
        return shared_rules, resolver
    premise = replace(
        rule,
        rule_id=premise_rule_id(rule.rule_id),
        label=rule.label + " · shared premise",
        conditions=market_conditions,
        derivations=[_premise_derivation(rule)],
        action_group="shared-premise",
        action_level="observe",
        prompt_hint="Shared TypeDB premise for " + rule.rule_id,
        hypothesis_family_key="shared-premise:" + (rule.hypothesis_family_key or rule.rule_id),
        hypothesis_lifecycle=_lifecycle_for(rule, market_conditions),
        any_condition_min_count=(
            int(rule.any_condition_min_count or 1) if market_any else 1
        ),
        execution_stage="shared-premise",
        failure_policy="block",
        cost_hint="bounded-shared",
    )
    premise_role = "any" if market_any and account_any else "required"
    resolver_conditions = [*account_conditions, _premise_condition(rule.rule_id, premise_role)]
    resolver = replace(
        rule,
        conditions=resolver_conditions,
        hypothesis_lifecycle=_lifecycle_for(rule, resolver_conditions),
        any_condition_min_count=(
            1 if premise_role == "any" else int(rule.any_condition_min_count or 1) if account_any else 1
        ),
        execution_stage="account-overlay",
    )
    return [premise], resolver


def _partition_market_rule(rule: GraphInferenceRule) -> Tuple[GraphInferenceRule, GraphInferenceRule]:
    """Compile a shared-only match into a compact account-visible relation.

    SharedPremiseWorld still owns every raw market predicate. PortfolioWorld
    receives only the verified premise reference and evaluates the original
    semantic derivation so account notifications never depend on copied market
    facts or Python threshold logic.
    """

    market_conditions = [
        _scoped_condition(condition, "market")
        for condition in rule.conditions
    ]
    premise = replace(
        rule,
        rule_id=premise_rule_id(rule.rule_id),
        label=rule.label + " · shared premise",
        conditions=market_conditions,
        derivations=[_premise_derivation(rule)],
        action_group="shared-premise",
        action_level="observe",
        prompt_hint="Shared TypeDB premise for " + rule.rule_id,
        hypothesis_family_key="shared-premise:" + (rule.hypothesis_family_key or rule.rule_id),
        hypothesis_lifecycle=_lifecycle_for(rule, market_conditions),
        execution_stage="shared-premise",
        failure_policy="block",
        cost_hint="bounded-shared",
    )
    resolver_conditions = [_premise_condition(rule.rule_id)]
    resolver = replace(
        rule,
        conditions=resolver_conditions,
        hypothesis_lifecycle=_lifecycle_for(rule, resolver_conditions),
        any_condition_min_count=1,
        execution_stage="account-overlay",
    )
    return premise, resolver


def compile_world_partitioned_rules(rules: Iterable[GraphInferenceRule]) -> Dict[str, object]:
    shared_rules: List[GraphInferenceRule] = []
    overlay_rules: List[GraphInferenceRule] = []
    manifest_rows = []
    failures = []
    for rule in rules or []:
        if not rule or not rule.enabled:
            continue
        ownership = rule_condition_ownership(rule)
        scope = str(ownership.get("scope") or "unverified")
        try:
            if scope == "market":
                premise, resolver = _partition_market_rule(rule)
                shared_rules.append(premise)
                overlay_rules.append(resolver)
            elif scope == "account":
                overlay_rules.append(replace(
                    rule,
                    conditions=[_scoped_condition(item, "account") for item in rule.conditions],
                    execution_stage="account-overlay",
                ))
            elif scope == "mixed":
                premises, resolver = _partition_mixed_rule(rule, ownership)
                shared_rules.extend(premises)
                overlay_rules.append(resolver)
            else:
                failures.append({
                    "ruleId": rule.rule_id,
                    "conditionIds": list(ownership.get("unverifiedConditionIds") or []),
                    "reason": "unverified-condition-ownership",
                })
        except ValueError as error:
            failures.append({"ruleId": rule.rule_id, "reason": str(error)})
        manifest_rows.append({
            "ruleId": rule.rule_id,
            "scope": scope,
            "sharedRuleId": premise_rule_id(rule.rule_id) if scope in {"market", "mixed"} else "",
            "sharedRuleIds": [
                item.rule_id
                for item in shared_rules
                if semantic_rule_id(item.rule_id) == rule.rule_id
                or item.rule_id == "shared.premise.any." + rule.rule_id
            ],
            "overlayRuleId": rule.rule_id if scope in {"account", "market", "mixed"} else "",
            "marketConditionCount": len(ownership.get("marketConditionIds") or []),
            "accountConditionCount": len(ownership.get("accountConditionIds") or []),
        })
    return {
        "version": WORLD_PARTITIONED_REASONING_VERSION,
        "status": "ready" if not failures else "invalid",
        "sharedRules": shared_rules,
        "overlayRules": overlay_rules,
        "sharedRuleIds": [rule.rule_id for rule in shared_rules],
        "overlayRuleIds": [rule.rule_id for rule in overlay_rules],
        "sourceRuleCount": len(manifest_rows),
        "sharedRuleCount": len(shared_rules),
        "overlayRuleCount": len(overlay_rules),
        "mixedRuleCount": len([row for row in manifest_rows if row["scope"] == "mixed"]),
        "failures": failures,
        "rules": manifest_rows,
    }


def shared_premise_matches(inference: Mapping[str, object]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for trace in inference.get("traces") or []:
        if not isinstance(trace, Mapping):
            continue
        generated_id = _text(trace.get("ruleId"))
        if not generated_id.startswith(SHARED_PREMISE_RULE_PREFIX):
            continue
        symbol = _text(trace.get("symbol")).upper()
        original = (
            generated_id[len("shared.premise.any."):] + "#any"
            if generated_id.startswith("shared.premise.any.")
            else semantic_rule_id(generated_id)
        )
        if symbol and original and original not in result.setdefault(symbol, []):
            result[symbol].append(original)
    return {symbol: sorted(values) for symbol, values in result.items()}


def _deduplicate_inference_rows(rows: Iterable[Mapping[str, object]]) -> List[Dict[str, object]]:
    selected = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        value = dict(row)
        identity = _text(
            value.get("id")
            or value.get("traceId")
            or value.get("inferenceTraceId")
            or value.get("relationId")
        )
        if not identity:
            identity = json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(value)
    return selected[:480]


def attach_shared_premise_evidence(
    projection: Mapping[str, object],
    proof: Mapping[str, object],
) -> Dict[str, object]:
    """Compose exact shared TypeDB evidence without the legacy head service."""

    result = dict(projection or {})
    if not bool((proof or {}).get("ready")):
        return result
    inference = dict(result.get("inferenceBox") or {})
    if not inference:
        return result
    symbol_proofs = dict((proof or {}).get("symbols") or {})
    shared_relations = [
        dict(row)
        for symbol_proof in symbol_proofs.values()
        if isinstance(symbol_proof, Mapping)
        for row in symbol_proof.get("relations") or []
        if isinstance(row, Mapping)
    ]
    shared_traces = [
        dict(row)
        for symbol_proof in symbol_proofs.values()
        if isinstance(symbol_proof, Mapping)
        for row in symbol_proof.get("traces") or []
        if isinstance(row, Mapping)
    ]
    relations = _deduplicate_inference_rows(
        list(inference.get("relations") or []) + shared_relations
    )
    traces = _deduplicate_inference_rows(
        list(inference.get("traces") or []) + shared_traces
    )
    inference.update({
        "relations": relations,
        "traces": traces,
        "relationCount": len(relations),
        "traceCount": len(traces),
        "sharedPremiseEvidenceAttached": True,
        "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
        "sharedPremiseWorldId": _text((proof or {}).get("worldId")),
        "sharedPremiseInferenceGenerationId": _text(
            (proof or {}).get("inferenceGenerationId")
        ),
        "sharedPremiseSourceAboxSnapshotId": _text(
            (proof or {}).get("sourceAboxSnapshotId")
        ),
        "sharedPremiseRelationCount": len(shared_relations),
        "sharedPremiseTraceCount": len(shared_traces),
    })
    result["inferenceBox"] = inference
    return result


def shared_premise_world_graph(
    source_graph: PortfolioOntology,
    shared_rules: Iterable[GraphInferenceRule],
    world,
) -> PortfolioOntology:
    """Build the reusable TypeDB input view for all public rule predicates.

    MarketWorld and KnowledgeWorld retain their distinct source ownership and
    retention policies. This compact reasoning view contains only entities and
    relation types consumed by shared RuleBox conditions, allowing one native
    TypeDB pass to combine current market facts with durable company facts.
    """

    rules = list(shared_rules or [])
    source_kinds = {
        _text(rule.source_kind).lower()
        for rule in rules
        if _text(rule.source_kind)
    }
    required_relation_types = {
        _text(condition.relation_type).upper()
        for rule in rules
        for condition in rule.conditions or []
        if _text(condition.kind) == "relation" and _text(condition.relation_type)
    }
    def shared_entity_allowed(entity: OntologyEntity) -> bool:
        if _text((entity.properties or {}).get("ontologyBox") or "ABox") != "ABox":
            return False
        if is_account_entity(entity):
            return False
        properties = dict(entity.properties or {})
        fact_field = _property_key(properties.get("field"))
        if fact_field and fact_field in ACCOUNT_FIELDS:
            return False
        return True

    entity_by_id = {
        entity.entity_id: entity
        for entity in source_graph.entities or []
        if shared_entity_allowed(entity)
    }
    relations = []
    required_entity_ids = {
        entity.entity_id
        for entity in entity_by_id.values()
        if _text(entity.kind).lower() in source_kinds
    }
    seen_relations = set()
    for relation in source_graph.relations or []:
        relation_type = _text(relation.relation_type).upper()
        if relation_type not in required_relation_types:
            continue
        if relation.source not in entity_by_id or relation.target not in entity_by_id:
            continue
        identity = (relation.source, relation.target, relation_type)
        if identity in seen_relations:
            continue
        seen_relations.add(identity)
        required_entity_ids.update({relation.source, relation.target})
        relations.append(OntologyRelation(
            relation.source,
            relation.target,
            relation.relation_type,
            properties={
                key: deepcopy(value)
                for key, value in dict(relation.properties or {}).items()
                if shared_world_property_allowed(key, relation=True)
            },
        ))
    entities = [
        OntologyEntity(
            entity.entity_id,
            entity.label,
            entity.kind,
            {
                key: deepcopy(value)
                for key, value in dict(entity.properties or {}).items()
                if shared_world_property_allowed(key)
            },
        )
        for entity in entity_by_id.values()
        if entity.entity_id in required_entity_ids
    ]
    worldview = {
        **world_metadata(world),
        "sharedPremiseWorldProjection": True,
        "sharedWorldProjection": "premise",
        "sharedWorldProjectionContractVersion": SHARED_WORLD_PROJECTION_CONTRACT_VERSION,
        "marketContextMode": "shared-market-and-knowledge-direct-premises",
        "asOf": _text((source_graph.worldview or {}).get("asOf")),
        "requiredRelationTypes": sorted(required_relation_types),
        "sourceRuleCount": len(rules),
    }
    return PortfolioOntology(
        world.world_id,
        entities=entities,
        relations=relations,
        evidence=[],
        worldview=worldview,
    )


def add_shared_premise_references(
    graph: PortfolioOntology,
    premises_by_symbol: Mapping[str, Iterable[str]],
    shared_generation_id: str = "",
    source_abox_snapshot_id: str = "",
) -> PortfolioOntology:
    known_entities = {item.entity_id for item in graph.entities}
    for symbol, rule_ids in dict(premises_by_symbol or {}).items():
        clean_symbol = _text(symbol).upper()
        subject_id = next((
            candidate_id
            for candidate_id in (
                entity_id("stock", clean_symbol),
                entity_id("crypto-asset", clean_symbol),
            )
            if candidate_id in known_entities
        ), "")
        if not subject_id:
            continue
        for rule_id in sorted({_text(value) for value in rule_ids or [] if _text(value)}):
            premise_id = entity_id(SHARED_PREMISE_KIND, clean_symbol + ":" + rule_id)
            if premise_id not in known_entities:
                graph.entities.append(OntologyEntity(
                    premise_id,
                    clean_symbol + " shared premise",
                    SHARED_PREMISE_KIND,
                    {
                        "ontologyBox": "ABox",
                        "tboxClass": "SharedMarketPremise",
                        "tboxClasses": ["SharedMarketPremise", "DerivedAssertion"],
                        "symbol": clean_symbol,
                        "group": rule_id,
                        "sharedInferenceGenerationId": _text(shared_generation_id),
                        "sharedSourceAboxSnapshotId": _text(source_abox_snapshot_id),
                        "readScope": "shared-premise-reference",
                    },
                ))
                known_entities.add(premise_id)
            graph.relations.append(OntologyRelation(
                subject_id,
                premise_id,
                SHARED_PREMISE_RELATION,
                properties={
                    "ontologyBox": "ABox",
                    "symbol": clean_symbol,
                    "signalGroup": rule_id,
                    "evidenceRole": "context",
                    "dataState": "sufficient",
                    "source": "typedb-market-world-premise",
                },
            ))
    graph.worldview.update({
        "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
        "marketContextMode": "shared-premise-account-overlay",
        "sharedPremiseReferenceCount": sum(len(set(values or [])) for values in premises_by_symbol.values()),
        "sharedInferenceGenerationId": _text(shared_generation_id),
        "sharedSourceAboxSnapshotId": _text(source_abox_snapshot_id),
    })
    return graph


def account_overlay_graph(
    source_graph: PortfolioOntology,
    overlay_rules: Iterable[GraphInferenceRule],
    premises_by_symbol: Mapping[str, Iterable[str]],
    shared_generation_id: str = "",
    source_abox_snapshot_id: str = "",
) -> PortfolioOntology:
    """Keep private decision inputs and compact shared premise references.

    Raw quote, flow, news, macro, and company observations stay in their
    shared worlds. Account-derived P/L, exposure, execution capacity, policy,
    and position facts remain private because they cannot be reused across
    accounts.
    """

    rules = list(overlay_rules or [])
    relation_patterns = [
        {
            "relationType": _text(condition.relation_type).upper(),
            "sourceKind": _text(rule.source_kind).lower(),
            "targetKind": _text(condition.target_kind).lower(),
            "direction": _text(condition.direction or "out").lower(),
        }
        for rule in rules
        for condition in rule.conditions or []
        if _text(condition.kind) == "relation" and _text(condition.relation_type)
    ]
    required_stock_properties = {
        _property_key(condition.field)
        for rule in rules
        for condition in rule.conditions or []
        if _text(condition.kind) in {"property", "subject_property"}
        and _text(condition.field)
    } | {
        _property_key(key)
        for rule in rules
        for condition in rule.conditions or []
        if _text(condition.target_kind).lower() == "stock"
        for key in dict(condition.target_property_filters or {})
    }
    entity_by_id = {
        entity.entity_id: entity
        for entity in source_graph.entities or []
    }

    def relation_matches_overlay_condition(relation: OntologyRelation) -> bool:
        relation_type = _text(relation.relation_type).upper()
        if relation_type in ACCOUNT_TOPOLOGY_RELATIONS:
            return True
        source_kind = _text(getattr(entity_by_id.get(relation.source), "kind", "")).lower()
        target_kind = _text(getattr(entity_by_id.get(relation.target), "kind", "")).lower()
        for pattern in relation_patterns:
            if relation_type != pattern["relationType"]:
                continue
            forward = bool(
                (not pattern["sourceKind"] or source_kind == pattern["sourceKind"])
                and (not pattern["targetKind"] or target_kind == pattern["targetKind"])
            )
            reverse = bool(
                (not pattern["sourceKind"] or target_kind == pattern["sourceKind"])
                and (not pattern["targetKind"] or source_kind == pattern["targetKind"])
            )
            if pattern["direction"] in {"in", "incoming", "target-to-source"}:
                matched = reverse
            elif pattern["direction"] in {"both", "either", "any", "undirected"}:
                matched = forward or reverse
            else:
                matched = forward
            if matched:
                return True
        return False

    relations = [
        relation
        for relation in source_graph.relations or []
        if relation_matches_overlay_condition(relation)
        and _text((relation.properties or {}).get("ontologyBox") or "ABox") == "ABox"
    ]
    endpoint_ids = {
        endpoint
        for relation in relations
        for endpoint in (relation.source, relation.target)
        if _text(endpoint)
    }
    # A market-only rule has no private relation endpoint before its compact
    # shared-premise edge is added. Retain the requested instrument identity so
    # pure shared rules can bind both listed stocks and BTC/ETH crypto assets in
    # the account overlay without copying their raw market observations.
    for symbol in premises_by_symbol:
        clean_symbol = _text(symbol).upper()
        for subject_kind in ("stock", "crypto-asset"):
            candidate_id = entity_id(subject_kind, clean_symbol)
            if candidate_id in entity_by_id:
                endpoint_ids.add(candidate_id)
    entities = []
    for entity in source_graph.entities or []:
        if entity.entity_id not in endpoint_ids:
            continue
        properties = dict(entity.properties or {})
        if _text(entity.kind).lower() in {"stock", "crypto-asset"}:
            properties = {
                key: value
                for key, value in properties.items()
                if _property_key(key) in ACCOUNT_STOCK_IDENTITY_PROPERTIES
                or _property_key(key) in required_stock_properties
            }
            properties["marketReadMirrorRemoved"] = True
            properties["marketContextMode"] = "shared-premise-reference"
        entities.append(OntologyEntity(entity.entity_id, entity.label, entity.kind, properties))
    graph = PortfolioOntology(
        source_graph.portfolio_id,
        entities=entities,
        relations=relations,
        evidence=[],
        beliefs=[],
        opinions=[],
        reasoning_cards=[],
        worldview={
            **dict(source_graph.worldview or {}),
            "runtimeProjectionMode": "account-overlay-facts-and-shared-premises",
            "marketReadMirrorRemoved": True,
            "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
            "accountOverlayProjectionContractVersion": ACCOUNT_OVERLAY_PROJECTION_CONTRACT_VERSION,
        },
        prompt=source_graph.prompt,
    )
    return add_shared_premise_references(
        graph,
        premises_by_symbol,
        shared_generation_id=shared_generation_id,
        source_abox_snapshot_id=source_abox_snapshot_id,
    )
