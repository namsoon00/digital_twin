"""Selection implementation; facade-independent dependencies."""

from __future__ import annotations
from .selection_ports import SelectionPort
from copy import deepcopy
from digital_twin.domain.ontology_runtime_operations import (
    native_rule_adaptive_target_sharding_policy,
    native_rule_adaptive_target_sharding_profile,
)
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.modules.reasoning.domain.projection_facts import rule_id_from_payload
from typing import Dict, List


def prior_rule_selection_context(
    _store: SelectionPort,
    snapshot: AccountSnapshot,
    inference_symbols: List[str],
    world_id: str = "",
    candidate_scope_plan: List[Dict[str, object]] = None,
    rulebox_rules_hash: str = "",
    tbox_fingerprint: str = "",
    requested_fact_families: List[str] = None,
    requested_fact_families_by_symbol: Dict[str, List[str]] = None,
) -> Dict[str, object]:
    """Prove which unaffected native rules must be re-materialized.

    This is a reuse proof, not a Python rule evaluation. Only the compact
    MySQL full-catalog slot generation is accepted. Reading an old
    InferenceBox can recover matched IDs, but cannot prove all non-matches
    came from one generation, so it must trigger a full bootstrap instead.
    """
    audited = _store.audited_prior_rule_selection_context(
        snapshot,
        inference_symbols,
        candidate_scope_plan=candidate_scope_plan,
        rulebox_rules_hash=rulebox_rules_hash,
        tbox_fingerprint=tbox_fingerprint,
        world_id=world_id,
        requested_fact_families=requested_fact_families,
        requested_fact_families_by_symbol=requested_fact_families_by_symbol,
    )
    return audited or {
        "reusable": False,
        "proofSource": "",
        "matchedRuleIds": [],
        "matchedRuleCount": 0,
        "fallbackReason": "coherent-rule-result-slot-proof-unavailable",
    }


def adaptive_native_rule_target_sharding_profile(
    _store: SelectionPort,
    snapshot: AccountSnapshot,
    world_id: str = "",
    rulebox_rules_hash: str = "",
) -> Dict[str, object]:
    """Build a bounded execution-only profile from compatible audits.

    Historical durations are not projected into the ABox and are never a
    RuleBox condition. They only let the TypeDB adapter avoid replaying a
    recently timed-out multi-symbol read at its known unsafe size.
    """

    policy = native_rule_adaptive_target_sharding_policy(_store.settings)
    if not bool(policy.get("enabled")):
        return native_rule_adaptive_target_sharding_profile([], _store.settings)
    if not _store.projection_run_store or not hasattr(
        _store.projection_run_store, "latest"
    ):
        profile = native_rule_adaptive_target_sharding_profile([], _store.settings)
        profile["source"] = "projection-audit-unavailable"
        return profile
    read_limit = max(
        20,
        min(160, int(policy.get("lookbackRunLimit") or 12) * 4),
    )
    try:
        namespace = _store.execution_namespace()
        rows = _store.projection_run_store.latest(
            account_id=str(snapshot.account_id or ""),
            limit=read_limit,
            world_id=world_id,
            execution_namespace_id=str(namespace.get("executionNamespaceId") or ""),
            engine_deployment_id=str(namespace.get("engineDeploymentId") or ""),
            graph_database=str(namespace.get("graphDatabase") or ""),
            release_fingerprint="",
        )
    except Exception:
        profile = native_rule_adaptive_target_sharding_profile([], _store.settings)
        profile["source"] = "projection-audit-read-failed"
        return profile

    observations = []
    compatible_rows = 0
    expected_rulebox_hash = str(rulebox_rules_hash or "").strip()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("graphStore") or "").strip() != "typedb":
            continue
        if (
            expected_rulebox_hash
            and str(row.get("ruleboxRulesHash") or "") != expected_rulebox_hash
        ):
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        observation = (
            result.get("runtimeObservation") if isinstance(result, dict) else {}
        )
        if not isinstance(observation, dict):
            continue
        compatible_rows += 1
        observations.append(observation)

    profile = native_rule_adaptive_target_sharding_profile(
        observations, _store.settings
    )
    profile.update(
        {
            "source": "projection-audit",
            "compatibleAuditRunCount": compatible_rows,
        }
    )
    return profile


def matched_rule_ids_from_inference_payload(payload: Dict[str, object]) -> List[str]:
    """Read TypeDB-reported rule ids without treating them as a decision."""
    values = dict(payload or {}) if isinstance(payload, dict) else {}
    collected = []
    for key in ["typedbNativeRuleMatchedRuleIds", "matchedRuleIds"]:
        for value in values.get(key) or []:
            rule_id = str(value or "").strip()
            if rule_id and rule_id not in collected:
                collected.append(rule_id)
    for trace in values.get("traces") or []:
        if not isinstance(trace, dict):
            continue
        rule_id = str(trace.get("ruleId") or trace.get("sourceRuleId") or "").strip()
        if rule_id and rule_id not in collected:
            collected.append(rule_id)
    return collected[:160]


def audited_prior_rule_selection_context(
    _store: SelectionPort,
    snapshot: AccountSnapshot,
    inference_symbols: List[str],
    candidate_scope_plan: List[Dict[str, object]] = None,
    rulebox_rules_hash: str = "",
    tbox_fingerprint: str = "",
    world_id: str = "",
    requested_fact_families: List[str] = None,
    requested_fact_families_by_symbol: Dict[str, List[str]] = None,
) -> Dict[str, object]:
    """Recover a target-specific native-rule proof from projection audit.

    MySQL stores only the immutable scope fingerprints and TypeDB's
    completed match set. It never asserts a new match or decides an
    investment action; the next direct TypeQL query remains the
    evaluator.
    """
    if not _store.projection_run_store:
        return {}
    targets = sorted(
        {
            str(symbol or "").upper().strip()
            for symbol in inference_symbols or []
            if str(symbol or "").strip()
        }
    )
    if not targets:
        return {}
    # The slot writer persists one row per executable rule ID.  Auxiliary
    # catalogue records without an ID are useful to the compiler, but are
    # not executable slots.  Counting them here made every 116-slot V2
    # generation look incomplete against an expected count of 118 and
    # forced a full native evaluation on every request.
    catalog_rule_ids = sorted(
        {
            rule_id_from_payload(rule)
            for rule in _store.rulebox_rules_for_impact()
            if isinstance(rule, dict)
            and rule.get("enabled", True) is not False
            and rule_id_from_payload(rule)
        }
    )
    slot_reader = getattr(
        _store.projection_run_store,
        "active_rule_result_slot_context",
        None,
    )
    if callable(slot_reader):
        try:
            namespace = _store.execution_namespace()
            slot_context = slot_reader(
                world_id=world_id,
                account_id=str(snapshot.account_id or ""),
                symbols=targets,
                rulebox_rules_hash=rulebox_rules_hash,
                tbox_fingerprint=tbox_fingerprint,
                expected_rule_count=len(catalog_rule_ids),
                execution_namespace_id=str(namespace.get("executionNamespaceId") or ""),
                engine_deployment_id=str(namespace.get("engineDeploymentId") or ""),
                graph_database=str(namespace.get("graphDatabase") or ""),
                release_fingerprint="",
                catalog_rule_ids=catalog_rule_ids,
            )
        except Exception:
            slot_context = {}
        if bool((slot_context or {}).get("reusable")):
            return dict(slot_context)
    # Old projection rows retained only matched IDs and could not prove
    # the state of every non-matching rule from one generation. Never use
    # that partial history as an incremental execution proof.
    return {}


def shared_inference_selection_context(
    _store: SelectionPort,
    impact_plan: Dict[str, object],
    reasoning_context: Dict[str, object],
    inference_symbols: List[str],
    account_selection_context: Dict[str, object] = None,
) -> Dict[str, object]:
    """Combine exact market reuse proof with account-local rule coverage."""

    proof = (
        reasoning_context.get("sharedInferenceReuseProof")
        if isinstance(reasoning_context, dict)
        and isinstance(reasoning_context.get("sharedInferenceReuseProof"), dict)
        else {}
    )
    targets = sorted(
        {
            str(symbol or "").upper().strip()
            for symbol in inference_symbols or []
            if str(symbol or "").strip()
        }
    )
    proof_targets = sorted(
        {
            str(symbol or "").upper().strip()
            for symbol in proof.get("targetSymbols") or []
            if str(symbol or "").strip()
        }
    )
    if not bool(proof.get("reuseEligible")) or not targets or proof_targets != targets:
        return {}
    enabled_rule_ids = [
        str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
        for rule in _store.rulebox_rules_for_impact()
        if isinstance(rule, dict)
        and rule.get("enabled", True) is not False
        and str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
    ]
    if not enabled_rule_ids:
        return {}
    available = set(enabled_rule_ids)
    market_rule_ids = {
        str(rule_id or "").strip()
        for rule_id in proof.get("marketRuleCatalogIds") or []
        if str(rule_id or "").strip()
    }
    matched_market_rule_ids = {
        str(rule_id or "").strip()
        for rule_id in proof.get("matchedMarketRuleIds") or []
        if str(rule_id or "").strip()
    }
    if (
        not market_rule_ids
        or not market_rule_ids.issubset(available)
        or not matched_market_rule_ids.issubset(market_rule_ids)
    ):
        return {}
    account_context = (
        dict(account_selection_context or {})
        if isinstance(account_selection_context, dict)
        else {}
    )
    if bool(account_context.get("reusable")):
        base_candidates = {
            str(rule_id or "").strip()
            for rule_id in account_context.get("candidateRuleIds") or []
            if str(rule_id or "").strip() in available
        }
        if not base_candidates:
            base_candidates = {
                str(rule_id or "").strip()
                for rule_id in impact_plan.get("candidateRuleIds") or []
                if str(rule_id or "").strip() in available
            }
        prior_matches = {
            str(rule_id or "").strip()
            for rule_id in account_context.get("matchedRuleIds") or []
            if str(rule_id or "").strip() in available
        }
    else:
        # Without an account proof every non-market rule is evaluated.
        # Only the exact-revision market catalogue may be deferred.
        base_candidates = set(available)
        prior_matches = set()
    candidate_ids = base_candidates.difference(market_rule_ids)
    prior_matches.difference_update(market_rule_ids)
    prior_matches.update(matched_market_rule_ids)
    if not candidate_ids and matched_market_rule_ids:
        # The native selector requires at least one candidate. Re-running
        # one already matched market rule still avoids all known market
        # non-matches while keeping TypeDB as the evaluator.
        candidate_ids.add(sorted(matched_market_rule_ids)[0])
    if not candidate_ids:
        return {}
    selected_ids = candidate_ids | prior_matches
    if len(selected_ids) >= len(enabled_rule_ids):
        return {}
    symbols = proof.get("symbols") if isinstance(proof.get("symbols"), dict) else {}
    snapshot_ids = sorted(
        {
            str(dict(value or {}).get("snapshotId") or "").strip()
            for value in symbols.values()
            if isinstance(value, dict) and str(value.get("snapshotId") or "").strip()
        }
    )
    return {
        "reusable": True,
        "proofSource": (
            "typedb-rule-result-slots+shared-market-head"
            if bool(account_context.get("reusable"))
            else "shared-market-head+complete-account-catalog"
        ),
        "targetSymbols": targets,
        "sharedSnapshotIds": snapshot_ids,
        "marketRuleCatalogIds": [
            rule_id for rule_id in enabled_rule_ids if rule_id in market_rule_ids
        ],
        "matchedRuleIds": [
            rule_id for rule_id in enabled_rule_ids if rule_id in prior_matches
        ],
        "matchedRuleCount": len(prior_matches),
        "candidateRuleIds": [
            rule_id for rule_id in enabled_rule_ids if rule_id in candidate_ids
        ],
        "candidateRuleCount": len(candidate_ids),
        "deferredMarketRuleCount": len(market_rule_ids.difference(selected_ids)),
        "fallbackReason": "",
    }


def combine_audited_target_rule_selection_contexts(
    _store: SelectionPort, targets: List[str], target_contexts: List[Dict[str, object]]
) -> Dict[str, object]:
    """Combine independently verified target proofs for one batch.

    A batch still publishes one coherent TypeDB InferenceBox. Each subject
    can, however, have a different last completed projection. Requiring a
    previous *batch* with the identical target list disabled reuse whenever
    the adaptive scheduler grouped two or more pending symbols. A union of
    complete per-target proofs is safe: candidate rules and prior TypeDB
    matches are both re-executed, never asserted from this audit data.
    """
    contexts = [dict(context or {}) for context in target_contexts or []]
    if not targets or len(contexts) != len(targets):
        return {}
    if all(
        context.get("proofSource") == "typedb-rule-result-slots"
        and bool(context.get("reusable"))
        for context in contexts
    ):
        matched_rule_ids = sorted(
            {
                str(rule_id or "").strip()
                for context in contexts
                for rule_id in context.get("matchedRuleIds") or []
                if str(rule_id or "").strip()
            }
        )
        return {
            "reusable": True,
            "proofSource": "typedb-rule-result-slots",
            "matchedRuleIds": matched_rule_ids,
            "matchedRuleCount": len(matched_rule_ids),
            "reusedTargetSymbols": list(targets),
            "proofRunId": ",".join(
                str(context.get("proofRunId") or "") for context in contexts
            )[:640],
            "inferenceGenerationId": ",".join(
                str(context.get("inferenceGenerationId") or "") for context in contexts
            )[:640],
            "sourceAboxSnapshotId": ",".join(
                str(context.get("sourceAboxSnapshotId") or "") for context in contexts
            )[:640],
            "fallbackReason": "",
        }
    plans = [
        context.get("inferenceImpactPlan")
        for context in contexts
        if isinstance(context.get("inferenceImpactPlan"), dict)
    ]
    if len(plans) != len(targets) or not all(
        bool(plan.get("nativeRuleSelectionEligible")) for plan in plans
    ):
        return {}

    def rule_id(rule: object) -> str:
        if isinstance(rule, dict):
            return str(rule.get("ruleId") or rule.get("rule_id") or "").strip()
        return str(getattr(rule, "rule_id", "") or "").strip()

    enabled_rule_ids = [
        value
        for value in (rule_id(rule) for rule in _store.rulebox_rules_for_impact())
        if value
    ]
    if not enabled_rule_ids:
        return {}
    candidate_ids = {
        str(rule_id or "").strip()
        for plan in plans
        for rule_id in plan.get("candidateRuleIds") or []
        if str(rule_id or "").strip()
    }
    candidate_ids.intersection_update(enabled_rule_ids)
    ordered_candidates = [
        rule_id for rule_id in enabled_rule_ids if rule_id in candidate_ids
    ]
    if not ordered_candidates or len(ordered_candidates) >= len(enabled_rule_ids):
        return {}

    matched_rule_ids: List[str] = []
    proof_run_ids: List[str] = []
    inference_generation_ids: List[str] = []
    source_abox_snapshot_ids: List[str] = []
    for context in contexts:
        for rule_id in context.get("matchedRuleIds") or []:
            clean_rule_id = str(rule_id or "").strip()
            if clean_rule_id and clean_rule_id not in matched_rule_ids:
                matched_rule_ids.append(clean_rule_id)
        for key, values in [
            ("proofRunId", proof_run_ids),
            ("inferenceGenerationId", inference_generation_ids),
            ("sourceAboxSnapshotId", source_abox_snapshot_ids),
        ]:
            value = str(context.get(key) or "").strip()
            if value and value not in values:
                values.append(value)

    merged_plan = deepcopy(plans[0])
    deferred_rule_ids = [
        rule_id for rule_id in enabled_rule_ids if rule_id not in candidate_ids
    ]
    merged_plan.update(
        {
            "explicitTargetSymbols": list(targets),
            "inferenceTargetSymbols": list(targets),
            "candidateRuleIds": ordered_candidates,
            "deferredRuleIds": deferred_rule_ids,
            "candidateRuleCount": len(ordered_candidates),
            "enabledRuleCount": len(enabled_rule_ids),
            "nativeRuleSelectionEligible": True,
            "nativeRuleSelectionEligibilityReason": "audited-multi-target-proof-candidate-subset",
            "auditedTargetReuse": {
                "mode": "independent-target-proofs",
                "targetSymbols": list(targets),
                "proofRunIds": list(proof_run_ids),
            },
        }
    )
    diagnostics = dict(merged_plan.get("diagnostics") or {})
    diagnostics.update(
        {
            "targetSymbolCount": len(targets),
            "candidateRuleCount": len(ordered_candidates),
            "enabledRuleCount": len(enabled_rule_ids),
            "candidateRuleRatioPct": round(
                (len(ordered_candidates) / max(1, len(enabled_rule_ids))) * 100, 1
            ),
            "candidateSubsetAvailable": True,
            "selectionEligibilityReason": "audited-multi-target-proof-candidate-subset",
        }
    )
    reason_codes = list(diagnostics.get("reasonCodes") or [])
    if "audited-multi-target-proof-reuse" not in reason_codes:
        reason_codes.append("audited-multi-target-proof-reuse")
    diagnostics["reasonCodes"] = reason_codes
    merged_plan["diagnostics"] = diagnostics
    return {
        "reusable": True,
        "proofSource": "audited-target-scope-proofs",
        "proofRunId": ",".join(proof_run_ids)[:640],
        "proofRunIds": proof_run_ids,
        "matchedRuleIds": matched_rule_ids,
        "matchedRuleCount": len(matched_rule_ids),
        "inferenceGenerationId": ",".join(inference_generation_ids)[:640],
        "sourceAboxSnapshotId": ",".join(source_abox_snapshot_ids)[:640],
        "inferenceImpactPlan": merged_plan,
        "candidateRuleIds": ordered_candidates,
        "deferredRuleIds": deferred_rule_ids,
        "recomputedCandidateRuleCount": len(ordered_candidates),
        "recomputedChangedScopeCount": sum(
            int(context.get("recomputedChangedScopeCount") or 0) for context in contexts
        ),
        "reusedTargetSymbols": list(targets),
        "fallbackReason": "",
    }


def impact_plan_with_audited_candidates(
    impact_plan: Dict[str, object], selection_context: Dict[str, object]
) -> Dict[str, object]:
    """Apply only proof-backed candidate ids to the current impact plan."""
    base = deepcopy(impact_plan or {}) if isinstance(impact_plan, dict) else {}
    context = (
        dict(selection_context or {}) if isinstance(selection_context, dict) else {}
    )
    if not base or not bool(context.get("reusable")):
        return {}
    candidate_ids = [
        str(rule_id or "").strip()
        for rule_id in context.get("candidateRuleIds") or []
        if str(rule_id or "").strip()
    ]
    if context.get("partialCatalogProof"):
        # Missing slots are additional work, not a replacement for rules
        # affected by the current source revision.
        candidate_ids = list(
            dict.fromkeys(
                [
                    *candidate_ids,
                    *(base.get("candidateRuleIds") or []),
                ]
            )
        )
    all_rule_ids = []
    for rule_id in list(base.get("candidateRuleIds") or []) + list(
        base.get("deferredRuleIds") or []
    ):
        clean_rule_id = str(rule_id or "").strip()
        if clean_rule_id and clean_rule_id not in all_rule_ids:
            all_rule_ids.append(clean_rule_id)
    if (
        not candidate_ids
        or not all_rule_ids
        or any(rule_id not in all_rule_ids for rule_id in candidate_ids)
    ):
        return {}
    candidate_ids = [rule_id for rule_id in all_rule_ids if rule_id in candidate_ids]
    deferred_rule_ids = [
        rule_id for rule_id in all_rule_ids if rule_id not in candidate_ids
    ]
    selection_reason = (
        "audited-target-proof-candidate-subset"
        if deferred_rule_ids
        else "complete-target-coverage-required"
    )
    base.update(
        {
            "candidateRuleIds": candidate_ids,
            "deferredRuleIds": deferred_rule_ids,
            "candidateRuleCount": len(candidate_ids),
            "enabledRuleCount": max(
                int(base.get("enabledRuleCount") or 0), len(all_rule_ids)
            ),
            "nativeRuleSelectionEligible": bool(deferred_rule_ids),
            "nativeRuleSelectionEligibilityReason": selection_reason,
        }
    )
    diagnostics = dict(base.get("diagnostics") or {})
    enabled_count = int(base.get("enabledRuleCount") or len(all_rule_ids))
    diagnostics.update(
        {
            "candidateRuleCount": len(candidate_ids),
            "enabledRuleCount": enabled_count,
            "candidateRuleRatioPct": round(
                (len(candidate_ids) / max(1, enabled_count)) * 100, 1
            ),
            "candidateSubsetAvailable": bool(deferred_rule_ids),
            "selectionEligibilityReason": selection_reason,
        }
    )
    reason_codes = list(diagnostics.get("reasonCodes") or [])
    if "audited-target-proof-reuse" not in reason_codes:
        reason_codes.append("audited-target-proof-reuse")
    diagnostics["reasonCodes"] = reason_codes
    base["diagnostics"] = diagnostics
    return base
