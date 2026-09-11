"""Planning for TypeQL, without database execution."""

import math
from typing import Dict, Iterable, List, Set

from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_execution_units import rule_evaluation_grain
from digital_twin.modules.model_registry.contracts import RULE_EXECUTION_POLICY_VERSION, rule_execution_profile
from digital_twin.modules.model_registry.contracts import GraphInferenceRule
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.preflight import (
    typedb_native_rule_any_relation_requirement,
    typedb_native_rule_manifest_evidence_preflight,
    typedb_native_rule_required_conditions_preflight,
    typedb_native_rule_required_relation_types,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
    normalized_condition_role,
    typedb_rule_condition_payloads,
    typedb_rule_is_enabled,
    typedb_source_kind_uses_symbol_scope,
)


def typedb_native_rule_query_complexity(rule: object) -> int:
    """Estimate query cost from persisted rule shape, never from market data."""
    conditions = typedb_rule_condition_payloads(rule)
    any_count = sum(
        1
        for condition in conditions
        if normalized_condition_role(condition) in {"any", "optional"}
    )
    raw_minimum = getattr(rule, "any_condition_min_count", None)
    if raw_minimum is None and isinstance(rule, dict):
        raw_minimum = rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")
    any_minimum = max(1, int(number_or_none(raw_minimum) or 1))
    combinations = 0
    if any_count:
        try:
            combinations = math.comb(any_count, min(any_count, any_minimum))
        except ValueError:
            combinations = any_count
    return len(conditions) + min(64, combinations) * 3


def typedb_reasoning_subject_source_kinds(subject_kinds: Iterable[object]) -> Set[str]:
    """Map scheduling subjects to the RuleBox source kinds they can own."""

    mapping = {
        "INSTRUMENT": {"stock", "crypto-asset"},
        "STOCK": {"stock"},
        "PORTFOLIO": {"portfolio"},
        "ACCOUNT": {"account"},
    }
    result: Set[str] = set()
    for value in subject_kinds or []:
        result.update(mapping.get(str(value or "").upper().strip(), set()))
    return result


def typedb_rule_execution_profile_fields(subject: object) -> Dict[str, object]:
    """Return compact RuleBox execution metadata for plans and traces."""

    if isinstance(subject, dict) and isinstance(subject.get("executionProfile"), dict):
        profile = dict(subject.get("executionProfile") or {})
    else:
        rule = subject.get("rule") if isinstance(subject, dict) and subject.get("rule") else subject
        profile = rule_execution_profile(rule)
    return {
        "executionStage": str(profile.get("executionStage") or "core"),
        "failurePolicy": str(profile.get("failurePolicy") or "invalidate-generation"),
        "costHint": str(profile.get("costHint") or "medium"),
        "costScore": int(number_or_none(profile.get("costScore")) or 0),
        "supportOnly": bool(profile.get("supportOnly")),
        "executionProfileVersion": str(profile.get("version") or RULE_EXECUTION_POLICY_VERSION),
    }


def typedb_rule_execution_failure_partition(
    failures: Iterable[Dict[str, object]],
) -> Dict[str, List[Dict[str, object]]]:
    """Separate fail-closed rules from support-only coverage gaps."""

    blocking = []
    supporting = []
    for item in failures or []:
        if not isinstance(item, dict):
            continue
        failure = dict(item)
        if str(failure.get("failurePolicy") or "") == "preserve-core-with-gap":
            supporting.append(failure)
        else:
            blocking.append(failure)
    return {"blocking": blocking, "supporting": supporting}


def typedb_native_rule_execution_plan(
    rules: Iterable[GraphInferenceRule],
    target_symbols: Iterable[str],
    relation_types_by_symbol: Dict[str, Iterable[str]] = None,
    query_limit: int = 0,
    preflight_graph: PortfolioOntology = None,
    preflight_incoming_relations_complete: bool = True,
    subject_properties_by_symbol: Dict[str, Dict[str, object]] = None,
    relation_evidence_by_symbol: Dict[str, List[Dict[str, object]]] = None,
    relation_evidence_complete_by_symbol: Dict[str, bool] = None,
) -> Dict[str, object]:
    """Build a complete TypeDB-function plan for selected ABox subjects.

    The planner removes only rule/symbol pairs that cannot satisfy required
    ABox conditions.  When a bounded preflight graph is available it may also
    prove that a required source value or relation filter is impossible; it
    never accepts a rule match. TypeDB functions still evaluate every
    surviving condition, including numeric thresholds, negation, and any
    branches. A query limit is intentionally ignored: an InferenceBox
    generation must be complete for every selected subject, otherwise the
    caller returns a blocked partial result instead of using a biased subset.
    """
    clean_symbols = clean_symbols_from_payload(list(target_symbols or []))
    type_index = {
        str(symbol or "").upper().strip(): {
            str(relation_type or "").upper().strip()
            for relation_type in relation_types or []
            if str(relation_type or "").strip()
        }
        for symbol, relation_types in dict(relation_types_by_symbol or {}).items()
        if str(symbol or "").strip()
    }
    entries: List[Dict[str, object]] = []
    for rule in rules or []:
        if not typedb_rule_is_enabled(rule):
            continue
        rule_id = str(getattr(rule, "rule_id", "") or (rule.get("rule_id") if isinstance(rule, dict) else "") or "")
        required_relation_types = typedb_native_rule_required_relation_types(rule)
        any_relation_types, any_relation_minimum = typedb_native_rule_any_relation_requirement(rule)
        source_kind = str(
            getattr(rule, "source_kind", "")
            or (rule.get("source_kind") or rule.get("sourceKind") if isinstance(rule, dict) else "")
            or "stock"
        )
        symbol_scoped_source = typedb_source_kind_uses_symbol_scope(source_kind)
        candidate_symbols = list(clean_symbols) if symbol_scoped_source else []
        if symbol_scoped_source and clean_symbols and required_relation_types:
            candidate_symbols = [
                symbol
                for symbol in clean_symbols
                if required_relation_types.issubset(type_index.get(symbol, set()))
            ]
        if symbol_scoped_source and candidate_symbols and any_relation_types and any_relation_minimum:
            candidate_symbols = [
                symbol
                for symbol in candidate_symbols
                if sum(
                    1
                    for relation_type in any_relation_types
                    if relation_type in type_index.get(symbol, set())
                ) >= any_relation_minimum
            ]
        preflight_pruned_symbols: Dict[str, Dict[str, object]] = {}
        subject_property_pruned_symbols: Dict[str, Dict[str, object]] = {}
        manifest_evidence_pruned_symbols: Dict[str, Dict[str, object]] = {}
        if symbol_scoped_source and candidate_symbols and (
            subject_properties_by_symbol or relation_evidence_by_symbol
        ):
            retained_symbols = []
            for symbol in candidate_symbols:
                preflight = typedb_native_rule_manifest_evidence_preflight(
                    rule,
                    symbol,
                    subject_properties_by_symbol,
                    relation_evidence_by_symbol,
                    relation_evidence_complete_by_symbol,
                )
                if str(preflight.get("status") or "") == "impossible":
                    preflight = {**preflight, "source": "manifest-evidence-index"}
                    preflight_pruned_symbols[symbol] = preflight
                    manifest_evidence_pruned_symbols[symbol] = preflight
                    if subject_properties_by_symbol and not relation_evidence_by_symbol:
                        subject_property_pruned_symbols[symbol] = preflight
                    continue
                retained_symbols.append(symbol)
            candidate_symbols = retained_symbols
        if symbol_scoped_source and candidate_symbols and preflight_graph is not None:
            retained_symbols = []
            for symbol in candidate_symbols:
                preflight = typedb_native_rule_required_conditions_preflight(
                    preflight_graph,
                    rule,
                    symbol,
                    incoming_relations_complete=preflight_incoming_relations_complete,
                )
                if str(preflight.get("status") or "") == "impossible":
                    preflight_pruned_symbols[symbol] = preflight
                    continue
                retained_symbols.append(symbol)
            candidate_symbols = retained_symbols
        execution_profile = rule_execution_profile(rule)
        evaluation_grain = rule_evaluation_grain(rule)
        entry = {
            "rule": rule,
            "ruleId": rule_id,
            "requiredRelationTypes": sorted(required_relation_types),
            "anyRelationTypes": list(any_relation_types),
            "anyRelationMinimum": any_relation_minimum,
            "candidateSymbols": candidate_symbols,
            "queryComplexity": typedb_native_rule_query_complexity(rule),
            "preflightPrunedSymbols": preflight_pruned_symbols,
            "subjectPropertyPreflightPrunedSymbols": subject_property_pruned_symbols,
            "manifestEvidencePreflightPrunedSymbols": manifest_evidence_pruned_symbols,
            "executionProfile": execution_profile,
            "evaluationGrain": evaluation_grain,
            **typedb_rule_execution_profile_fields({"executionProfile": execution_profile}),
        }
        if symbol_scoped_source and clean_symbols and not candidate_symbols:
            preflight_reasons = [
                str(item.get("reason") or "")
                for item in preflight_pruned_symbols.values()
                if str(item.get("reason") or "")
            ]
            entry.update({
                "selected": False,
                "status": "not-applicable-preflight" if preflight_pruned_symbols else "not-applicable",
                "reason": (
                    preflight_reasons[0]
                    if preflight_reasons
                    else "Active ABox has no candidate symbol with every required relation type."
                ),
            })
        else:
            entry.update({"selected": True, "status": "planned", "reason": ""})
        entries.append(entry)
    selected_entries = [item for item in entries if item.get("selected")]

    def execution_priority(item: Dict[str, object]):
        rule = item.get("rule")
        conditions = typedb_rule_condition_payloads(rule)
        has_any_group = any(
            normalized_condition_role(condition) in {"any", "optional"}
            for condition in conditions
        )
        # A direct base match for an N-of-M rule is followed by a
        # second TypeDB cardinality query. Run those bounded checks first so a
        # long series of ordinary matches cannot leave the only cardinality
        # proof until the shared read budget is nearly exhausted.
        return (
            {"critical": 0, "core": 1, "supporting": 2}.get(
                str(item.get("executionStage") or "core"),
                1,
            ),
            0 if has_any_group else 1,
            int(item.get("queryComplexity") or 0),
            str(item.get("ruleId") or ""),
        )

    selected_entries.sort(
        key=execution_priority,
    )
    skipped_entries = [item for item in entries if not item.get("selected")]
    return {
        "status": "ok",
        "targetSymbols": clean_symbols,
        "queryLimit": 0,
        "candidateRuleCount": len(entries),
        "selectedRuleCount": len(selected_entries),
        "skippedRuleCount": len(skipped_entries),
        "preflightEnabled": preflight_graph is not None,
        "subjectPropertyPreflightEnabled": bool(subject_properties_by_symbol),
        "manifestEvidencePreflightEnabled": bool(
            subject_properties_by_symbol or relation_evidence_by_symbol
        ),
        "relationEvidencePreflightEnabled": bool(relation_evidence_by_symbol),
        "preflightIncomingRelationsComplete": bool(preflight_incoming_relations_complete),
        "preflightPrunedRuleCount": len([
            item for item in skipped_entries
            if str(item.get("status") or "") == "not-applicable-preflight"
        ]),
        "preflightPrunedSymbolCount": sum(
            len(dict(item.get("preflightPrunedSymbols") or {}))
            for item in entries
        ),
        "subjectPropertyPreflightPrunedSymbolCount": sum(
            len(dict(item.get("subjectPropertyPreflightPrunedSymbols") or {}))
            for item in entries
        ),
        "manifestEvidencePreflightPrunedSymbolCount": sum(
            len(dict(item.get("manifestEvidencePreflightPrunedSymbols") or {}))
            for item in entries
        ),
        "selectedEntries": selected_entries,
        "skippedEntries": skipped_entries,
        "relationTypesBySymbol": {
            symbol: sorted(values)
            for symbol, values in type_index.items()
        },
    }


def typedb_native_rule_adaptive_target_parallelism_by_rule_id(
    profile: Dict[str, object] = None,
) -> Dict[str, int]:
    """Read only explicit, bounded proactive-sharding recommendations.

    The profile is operational history assembled outside TypeDB. It never
    grants a rule match, omits a rule, or changes a candidate symbol. The
    caller still requires a stable ABox lease and a complete result.
    """

    values = dict(profile or {}) if isinstance(profile, dict) else {}
    if str(values.get("status") or "").strip().lower() != "active":
        return {}
    if values.get("enabled") is False:
        return {}
    recommendations: Dict[str, int] = {}
    for item in values.get("rules") or []:
        if not isinstance(item, dict) or not bool(item.get("preemptiveTargetSharding")):
            continue
        rule_id = str(item.get("ruleId") or "").strip()
        parallelism = int(number_or_none(item.get("targetParallelism")) or 1)
        if rule_id and parallelism > 1:
            recommendations[rule_id] = max(2, min(4, parallelism))
    return recommendations


def typedb_native_rule_target_work_plan(
    entries: Iterable[Dict[str, object]],
    target_parallelism: int = 1,
    adaptive_target_parallelism_by_rule_id: Dict[str, int] = None,
) -> Dict[str, object]:
    """Split one verified target set into bounded, read-only TypeDB work.

    A RuleBox generation is committed as one InferenceBox generation, so this
    helper must never change the target set or accept a partial result. It only
    turns an already selected ``rule x candidate-symbols`` entry into stable
    shards. The caller keeps one global worker cap and treats any failed shard
    as a failure of the complete generation.
    """
    selected_entries = [dict(item) for item in (entries or []) if isinstance(item, dict)]
    requested_parallelism = max(
        1,
        min(8, int(number_or_none(target_parallelism) or 1)),
    )
    adaptive_parallelism_by_rule_id = {
        str(rule_id or "").strip(): max(2, min(4, int(number_or_none(parallelism) or 2)))
        for rule_id, parallelism in dict(adaptive_target_parallelism_by_rule_id or {}).items()
        if str(rule_id or "").strip() and int(number_or_none(parallelism) or 0) > 1
    }
    all_candidate_symbols = clean_symbols_from_payload([
        symbol
        for entry in selected_entries
        for symbol in clean_symbols_from_payload(entry.get("candidateSymbols") or [])
    ])
    effective_parallelism = (
        min(requested_parallelism, len(all_candidate_symbols))
        if len(all_candidate_symbols) > 1
        else 1
    )
    work_items: List[Dict[str, object]] = []
    max_shard_count = 0
    sharded_rule_ids = set()
    adaptive_sharded_rule_ids = set()

    for entry in selected_entries:
        candidate_symbols = clean_symbols_from_payload(entry.get("candidateSymbols") or [])
        rule_id = str(entry.get("ruleId") or "").strip()
        adaptive_parallelism = int(adaptive_parallelism_by_rule_id.get(rule_id) or 1)
        adaptive_sharding_requested = adaptive_parallelism > 1
        entry_parallelism = adaptive_parallelism if adaptive_sharding_requested else effective_parallelism
        shard_count = min(entry_parallelism, len(candidate_symbols)) if candidate_symbols else 1
        max_shard_count = max(max_shard_count, shard_count)
        if shard_count > 1:
            sharded_rule_ids.add(rule_id)
            if adaptive_sharding_requested:
                adaptive_sharded_rule_ids.add(rule_id)
        shards = (
            [candidate_symbols[index::shard_count] for index in range(shard_count)]
            if candidate_symbols
            else [[]]
        )
        for shard_index, shard_symbols in enumerate(shards):
            work_item = dict(entry)
            work_item["candidateSymbols"] = list(shard_symbols)
            work_item["targetWorkShardIndex"] = shard_index
            work_item["targetWorkShardCount"] = shard_count
            work_item["targetWorkShardingUsed"] = shard_count > 1
            work_item["targetWorkAdaptiveShardingUsed"] = bool(
                adaptive_sharding_requested and shard_count > 1
            )
            work_items.append(work_item)

    return {
        "requestedTargetParallelism": requested_parallelism,
        "effectiveTargetParallelism": effective_parallelism,
        "targetSymbols": all_candidate_symbols,
        "targetSymbolCount": len(all_candidate_symbols),
        "targetWorkShardingUsed": bool(sharded_rule_ids),
        "targetWorkShardCount": max_shard_count,
        "targetWorkItemCount": len(work_items),
        "targetWorkOriginalEntryCount": len(selected_entries),
        "targetWorkShardedRuleCount": len([rule_id for rule_id in sharded_rule_ids if rule_id]),
        "targetWorkAdaptiveShardingUsed": bool(adaptive_sharded_rule_ids),
        "targetWorkAdaptiveShardedRuleCount": len([
            rule_id for rule_id in adaptive_sharded_rule_ids if rule_id
        ]),
        "targetWorkAdaptiveShardedRuleIds": sorted(
            rule_id for rule_id in adaptive_sharded_rule_ids if rule_id
        ),
        "workItems": work_items,
    }


def typedb_native_rule_execution_selection(
    rules: Iterable[GraphInferenceRule],
    candidate_rule_ids: Iterable[object] = None,
    prior_matched_rule_ids: Iterable[object] = None,
    eligible: bool = False,
    prior_inference_reusable: bool = False,
    global_impact: bool = False,
    bounded_global_context: bool = False,
) -> Dict[str, object]:
    """Select the native RuleBox slice affected by the current revision.

    A RuleBox rule is never evaluated in Python.  For a local immutable scope
    change, an unchanged rule can only remain matched when it was matched in
    the previous aligned InferenceBox; known non-matches remain non-matches.
    We execute changed candidates plus verified previous matches when that
    compact proof is available. Without it, deferred rules have unknown
    outcomes, not known non-matches. Establish complete coverage for the
    requested subjects before producing a replacement InferenceBox.
    """
    all_rules = [rule for rule in rules or [] if typedb_rule_is_enabled(rule)]
    all_ids = [str(getattr(rule, "rule_id", "") or "").strip() for rule in all_rules]
    all_ids = [rule_id for rule_id in all_ids if rule_id]
    available = set(all_ids)
    candidates = {
        str(value or "").strip()
        for value in candidate_rule_ids or []
        if str(value or "").strip()
    }
    prior_matches = {
        str(value or "").strip()
        for value in prior_matched_rule_ids or []
        if str(value or "").strip()
    }
    fallback_reason = ""
    if not eligible:
        fallback_reason = "impact-plan-not-eligible"
    elif not candidates:
        fallback_reason = "candidate-rules-unavailable"
    elif candidates - available:
        fallback_reason = "rulebox-version-or-candidate-mismatch"
    elif prior_inference_reusable and (prior_matches - available):
        fallback_reason = "rulebox-version-or-prior-match-mismatch"
    elif not prior_inference_reusable:
        fallback_reason = "prior-rule-coverage-unavailable"
    if fallback_reason:
        return {
            "selectedRules": all_rules,
            "selectedRuleIds": all_ids,
            "deferredRuleIds": [],
            "candidateRuleIds": sorted(candidates),
            "priorMatchedRuleIds": sorted(prior_matches),
            "selectionApplied": False,
            "fallbackReason": fallback_reason,
            "coverageMode": "complete-target-evaluation",
            "fullRuleCount": len(all_rules),
        }
    selected_ids = candidates | (prior_matches if prior_inference_reusable else set())
    selected_rules = [rule for rule in all_rules if str(getattr(rule, "rule_id", "") or "") in selected_ids]
    deferred = [rule_id for rule_id in all_ids if rule_id not in selected_ids]
    return {
        "selectedRules": selected_rules,
        "selectedRuleIds": [str(getattr(rule, "rule_id", "") or "") for rule in selected_rules],
        "deferredRuleIds": deferred,
        "candidateRuleIds": sorted(candidates),
        "priorMatchedRuleIds": sorted(prior_matches),
        "selectionApplied": len(selected_rules) < len(all_rules),
        "fallbackReason": "",
        "coverageMode": "candidate-plus-prior-matches",
        "fullRuleCount": len(all_rules),
    }


def typedb_native_rule_execution_plan_summary(plan: Dict[str, object]) -> Dict[str, object]:
    """Return bounded operational diagnostics without serialising rule bodies."""
    payload = dict(plan or {})
    selected = [item for item in payload.get("selectedEntries") or [] if isinstance(item, dict)]
    skipped = [item for item in payload.get("skippedEntries") or [] if isinstance(item, dict)]
    status_counts: Dict[str, int] = {}
    for item in skipped:
        status = str(item.get("status") or "skipped")
        status_counts[status] = int(status_counts.get(status, 0) or 0) + 1
    return {
        "status": str(payload.get("status") or ""),
        "targetSymbols": list(payload.get("targetSymbols") or []),
        "queryLimit": int(number_or_none(payload.get("queryLimit")) or 0),
        "candidateRuleCount": int(number_or_none(payload.get("candidateRuleCount")) or 0),
        "selectedRuleCount": len(selected),
        "skippedRuleCount": len(skipped),
        "preflightEnabled": bool(payload.get("preflightEnabled")),
        "subjectPropertyPreflightEnabled": bool(payload.get("subjectPropertyPreflightEnabled")),
        "manifestEvidencePreflightEnabled": bool(payload.get("manifestEvidencePreflightEnabled")),
        "relationEvidencePreflightEnabled": bool(payload.get("relationEvidencePreflightEnabled")),
        "preflightIncomingRelationsComplete": bool(payload.get("preflightIncomingRelationsComplete", True)),
        "preflightPrunedRuleCount": int(number_or_none(payload.get("preflightPrunedRuleCount")) or 0),
        "preflightPrunedSymbolCount": int(number_or_none(payload.get("preflightPrunedSymbolCount")) or 0),
        "subjectPropertyPreflightPrunedSymbolCount": int(
            number_or_none(payload.get("subjectPropertyPreflightPrunedSymbolCount")) or 0
        ),
        "manifestEvidencePreflightPrunedSymbolCount": int(
            number_or_none(payload.get("manifestEvidencePreflightPrunedSymbolCount")) or 0
        ),
        "skippedByStatus": status_counts,
        "selectedRules": [
            {
                "ruleId": str(item.get("ruleId") or ""),
                "candidateSymbols": list(item.get("candidateSymbols") or []),
                "queryComplexity": int(number_or_none(item.get("queryComplexity")) or 0),
                "evaluationGrain": str(item.get("evaluationGrain") or ""),
                **typedb_rule_execution_profile_fields(item),
            }
            for item in selected[:200]
        ],
        "relationTypesBySymbol": dict(payload.get("relationTypesBySymbol") or {}),
    }
