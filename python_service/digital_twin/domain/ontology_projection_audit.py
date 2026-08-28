import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Dict, Iterable, List, Mapping

from .ontology_change_impact import compact_inference_impact_plan, scope_symbol
from .ontology_contracts import PortfolioOntology
from .ontology_runtime_operations import (
    compact_abox_relation_persistence,
    native_replay_validation,
    native_rule_timing_profile,
)
from .portfolio import AccountSnapshot, utc_now_iso


def _json_payload(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(value: object) -> str:
    return hashlib.sha256(_json_payload(value).encode("utf-8")).hexdigest()


def _clean_symbols(symbols: Iterable[object]) -> List[str]:
    return sorted({
        str(symbol or "").upper().strip()
        for symbol in symbols or []
        if str(symbol or "").strip()
    })


INFERENCE_REUSE_PROOF_VERSION = "target-inference-reuse-proof-v1"
REASONING_REQUEST_CONTEXT_VERSION = "reasoning-request-context-v3"


def compact_reasoning_request_context(
    context: Mapping[str, object] = None,
    target_symbols: Iterable[object] = None,
) -> Dict[str, object]:
    """Keep bounded trigger provenance and immutable facts for exact replay."""
    values = dict(context or {}) if isinstance(context, Mapping) else {}
    targets = set(_clean_symbols(target_symbols or values.get("targetSymbols") or []))

    def clean_list(value: object, limit: int = 80) -> List[str]:
        items = value if isinstance(value, (list, tuple, set)) else [value]
        return sorted({
            str(item or "").strip()
            for item in items
            if str(item or "").strip()
        })[:max(1, int(limit or 80))]

    def symbol_map(value: object, list_values: bool) -> Dict[str, object]:
        source = value if isinstance(value, Mapping) else {}
        result: Dict[str, object] = {}
        for raw_symbol, raw_value in source.items():
            symbol = str(raw_symbol or "").upper().strip()
            if not symbol or (targets and symbol not in targets):
                continue
            if list_values:
                cleaned = clean_list(raw_value, limit=80)
                if cleaned:
                    result[symbol] = cleaned
            else:
                cleaned = str(raw_value or "").strip()
                if cleaned:
                    result[symbol] = cleaned[:160]
        return dict(sorted(result.items()))

    def symbol_family_map(value: object) -> Dict[str, List[str]]:
        """Keep empty per-symbol families as a conservative routing marker."""
        source = value if isinstance(value, Mapping) else {}
        result: Dict[str, List[str]] = {}
        for raw_symbol, raw_value in source.items():
            symbol = str(raw_symbol or "").upper().strip()
            if not symbol or (targets and symbol not in targets):
                continue
            result[symbol] = clean_list(raw_value, limit=30)
        return dict(sorted(result.items()))

    raw_queue_pressure = values.get("queuePressure")
    queue_pressure = raw_queue_pressure if isinstance(raw_queue_pressure, Mapping) else {}
    raw_queue_dispatch = values.get("queueDispatch")
    queue_dispatch = raw_queue_dispatch if isinstance(raw_queue_dispatch, Mapping) else {}
    raw_batch_plan = values.get("batchPlan")
    batch_plan = raw_batch_plan if isinstance(raw_batch_plan, Mapping) else {}
    raw_crypto_transitions = values.get("cryptoTransitions")
    crypto_transitions = raw_crypto_transitions if isinstance(raw_crypto_transitions, (list, tuple)) else []
    observation_followups = {
        str(symbol or "").upper().strip()
        for symbol in (values.get("observationFollowupSymbols") or [])
        if str(symbol or "").strip()
    }
    if targets:
        observation_followups.intersection_update(targets)

    def non_negative_integer(value: object) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0

    compact_crypto_transitions = []
    seen_crypto_signatures = set()
    for item in crypto_transitions:
        if not isinstance(item, Mapping):
            continue
        signature = str(item.get("signature") or "").strip()
        if not signature or signature in seen_crypto_signatures:
            continue
        seen_crypto_signatures.add(signature)
        compact_crypto_transitions.append({
            key: item.get(key)
            for key in [
                "symbol", "coinId", "name", "horizon", "field", "direction", "severity",
                "changePct", "thresholdPct", "previousBand", "currentBand", "transition",
                "observedAt", "signature",
            ]
            if item.get(key) not in (None, "", [], {})
        })
        if len(compact_crypto_transitions) >= 12:
            break
    verified_source_snapshot = values.get("verifiedSourceSnapshot")
    verified_source_snapshot = verified_source_snapshot if isinstance(verified_source_snapshot, Mapping) else {}
    compact_verified_source_snapshot = {
        key: verified_source_snapshot.get(key)
        for key in [
            "version", "snapshotId", "generatedAt", "accountId", "positionChangedCount", "portfolioContextChanged",
            "externalSignalGroups", "cryptoTransitionTargetSymbols",
        ]
        if verified_source_snapshot.get(key) not in (None, "", [], {})
    }
    shared_reuse = values.get("sharedInferenceReuseProof")
    shared_reuse = shared_reuse if isinstance(shared_reuse, Mapping) else {}
    compact_shared_reuse = {
        "contractVersion": str(shared_reuse.get("contractVersion") or "")[:80],
        "status": str(shared_reuse.get("status") or "")[:80],
        "reuseEligible": bool(shared_reuse.get("reuseEligible")),
        "deploymentId": str(shared_reuse.get("deploymentId") or "")[:191],
        "releaseFingerprint": str(shared_reuse.get("releaseFingerprint") or "")[:191],
        "targetSymbols": _clean_symbols(shared_reuse.get("targetSymbols") or [])[:80],
        "marketRuleCatalogIds": clean_list(
            shared_reuse.get("marketRuleCatalogIds"), limit=160
        ),
        "matchedMarketRuleIds": clean_list(
            shared_reuse.get("matchedMarketRuleIds"), limit=160
        ),
        "symbols": {
            str(symbol or "").upper().strip(): {
                "snapshotId": str(dict(proof or {}).get("snapshotId") or "")[:191],
                "semanticFingerprint": str(
                    dict(proof or {}).get("semanticFingerprint") or ""
                )[:64],
                "sourceRevisionFingerprint": str(
                    dict(proof or {}).get("sourceRevisionFingerprint") or ""
                )[:64],
                "marketInputFingerprint": str(
                    dict(proof or {}).get("marketInputFingerprint") or ""
                )[:64],
                "sourceAsOf": str(dict(proof or {}).get("sourceAsOf") or "")[:80],
            }
            for symbol, proof in dict(shared_reuse.get("symbols") or {}).items()
            if str(symbol or "").strip() and isinstance(proof, Mapping)
        },
    }
    semantic_change_set = (
        dict(values.get("semanticChangeSet") or {})
        if isinstance(values.get("semanticChangeSet"), Mapping)
        else {}
    )
    compact_semantic_change_set = {
        "version": str(semantic_change_set.get("version") or "")[:64],
        "changeSetId": str(semantic_change_set.get("changeSetId") or "")[:191],
        "fingerprint": str(semantic_change_set.get("fingerprint") or "")[:64],
        "observedAt": str(semantic_change_set.get("observedAt") or "")[:80],
        "requestedAt": str(semantic_change_set.get("requestedAt") or "")[:80],
        "authoritativeFactBoundary": bool(
            semantic_change_set.get("authoritativeFactBoundary")
        ),
        "authoritativeDependencyBoundary": bool(
            semantic_change_set.get("authoritativeDependencyBoundary")
        ),
        "factSlices": [
            {
                "version": str(item.get("version") or "")[:64],
                "subjectId": str(item.get("subjectId") or "").upper()[:40],
                "scopeFamilies": clean_list(item.get("scopeFamilies"), limit=30),
                "dependencyKeys": clean_list(item.get("dependencyKeys"), limit=120),
                "changedFields": clean_list(item.get("changedFields"), limit=80),
                "factTypes": clean_list(item.get("factTypes"), limit=30),
                "observedAt": str(item.get("observedAt") or "")[:80],
                "fingerprint": str(item.get("fingerprint") or "")[:64],
                "revisionVector": {
                    "fingerprint": str(
                        dict(item.get("revisionVector") or {}).get("fingerprint") or ""
                    )[:64],
                    "revisions": {
                        str(key or "")[:40]: str(value or "")[:191]
                        for key, value in dict(
                            dict(item.get("revisionVector") or {}).get("revisions") or {}
                        ).items()
                        if str(key or "").strip() and str(value or "").strip()
                    },
                },
            }
            for item in semantic_change_set.get("factSlices") or []
            if isinstance(item, Mapping)
            and (
                not targets
                or str(item.get("subjectId") or "").upper().strip() in targets
            )
        ][:80],
    }
    ontology_store_route = (
        dict(values.get("ontologyStoreRoute") or {})
        if isinstance(values.get("ontologyStoreRoute"), Mapping)
        else {}
    )
    source_facts = [
        dict(item)
        for item in values.get("sourceFacts") or []
        if isinstance(item, Mapping)
        and (
            not targets
            or targets.intersection(_clean_symbols(item.get("subjectIds") or []))
        )
    ][:20]
    return {
        "version": str(values.get("version") or REASONING_REQUEST_CONTEXT_VERSION),
        "requestEventIds": clean_list(values.get("requestEventIds"), limit=80),
        "sourceEventIds": clean_list(values.get("sourceEventIds"), limit=80),
        "triggers": clean_list(values.get("triggers"), limit=20),
        "factTypes": clean_list(values.get("factTypes"), limit=30),
        "workClasses": clean_list(values.get("workClasses"), limit=8),
        "impactScopes": clean_list(values.get("impactScopes"), limit=8),
        "reasoningLanes": clean_list(values.get("reasoningLanes"), limit=8),
        "subjectKinds": clean_list(values.get("subjectKinds"), limit=8),
        "subjectIds": clean_list(values.get("subjectIds"), limit=40),
        "affectedSymbols": _clean_symbols(values.get("affectedSymbols") or [])[:200],
        "subjectRevisions": {
            str(subject_id or "")[:191]: str(revision or "")[:191]
            for subject_id, revision in dict(values.get("subjectRevisions") or {}).items()
            if str(subject_id or "").strip() and str(revision or "").strip()
        },
        "subjectChangedFields": clean_list(values.get("subjectChangedFields"), limit=80),
        "requestedScopeFamilies": clean_list(values.get("requestedScopeFamilies"), limit=30),
        "requestedScopeFamiliesBySymbol": symbol_family_map(
            values.get("requestedScopeFamiliesBySymbol")
        ),
        "requestedDependencyKeys": clean_list(
            values.get("requestedDependencyKeys"), limit=120
        ),
        "requestedDependencyKeysBySymbol": symbol_family_map(
            values.get("requestedDependencyKeysBySymbol")
        ),
        "eventFactBoundaryAuthoritative": bool(
            values.get("eventFactBoundaryAuthoritative")
        ),
        "eventDependencyBoundaryAuthoritative": bool(
            values.get("eventDependencyBoundaryAuthoritative")
        ),
        "targetSymbols": sorted(targets)[:80],
        "sourceObservedAt": str(values.get("sourceObservedAt") or "").strip()[:80],
        "changedFieldsBySymbol": symbol_map(values.get("changedFieldsBySymbol"), list_values=True),
        "factRevisionsBySymbol": symbol_map(values.get("factRevisionsBySymbol"), list_values=False),
        "revisionVectorsBySymbol": {
            str(symbol or "").upper().strip(): {
                str(key or "")[:40]: str(value or "")[:191]
                for key, value in dict(vector or {}).items()
                if str(key or "").strip() and str(value or "").strip()
            }
            for symbol, vector in dict(values.get("revisionVectorsBySymbol") or {}).items()
            if str(symbol or "").strip()
            and (not targets or str(symbol or "").upper().strip() in targets)
            and isinstance(vector, Mapping)
        },
        "semanticChangeSet": compact_semantic_change_set,
        "sourceFacts": source_facts,
        "ontologyStoreRoute": {
            "version": str(ontology_store_route.get("version") or "")[:64],
            "shardId": str(ontology_store_route.get("shard_id") or ontology_store_route.get("shardId") or "")[:64],
            "shardIndex": non_negative_integer(
                ontology_store_route.get("shard_index")
                if ontology_store_route.get("shard_index") is not None
                else ontology_store_route.get("shardIndex")
            ),
            "shardCount": max(1, non_negative_integer(
                ontology_store_route.get("shard_count")
                if ontology_store_route.get("shard_count") is not None
                else ontology_store_route.get("shardCount")
            )),
            "routingKey": str(ontology_store_route.get("routing_key") or ontology_store_route.get("routingKey") or "")[:80],
            "symbols": _clean_symbols(ontology_store_route.get("symbols") or [])[:80],
        },
        "scopeRepairRequestsBySymbol": {
            str(symbol or "").upper().strip(): {
                "requestId": str(dict(repair or {}).get("requestId") or "")[:191],
                "scopeIds": clean_list(dict(repair or {}).get("scopeIds"), limit=40),
            }
            for symbol, repair in dict(values.get("scopeRepairRequestsBySymbol") or {}).items()
            if str(symbol or "").strip()
            and (not targets or str(symbol or "").upper().strip() in targets)
            and isinstance(repair, Mapping)
            and str(repair.get("requestId") or "").strip()
        },
        "factChangeContracts": [
            {
                "requestEventId": str(item.get("requestEventId") or "")[:191],
                "version": str(item.get("version") or "")[:64],
                "status": str(item.get("status") or "")[:64],
                "dependencyKeys": clean_list(
                    item.get("dependencyKeys"), limit=160
                ),
                "dependencyKeysBySymbol": symbol_family_map(
                    item.get("dependencyKeysBySymbol")
                ),
                "dependencyKeysComplete": bool(
                    item.get("dependencyKeysComplete")
                ),
                "dependencyKeysCompleteBySymbol": {
                    str(symbol or "").upper().strip(): bool(value)
                    for symbol, value in dict(
                        item.get("dependencyKeysCompleteBySymbol") or {}
                    ).items()
                    if str(symbol or "").strip()
                },
                "unclassifiedFactTypes": clean_list(
                    item.get("unclassifiedFactTypes"), limit=20
                ),
                "unclassifiedFactTypesBySymbol": symbol_family_map(
                    item.get("unclassifiedFactTypesBySymbol")
                ),
            }
            for item in values.get("factChangeContracts") or []
            if isinstance(item, Mapping)
        ][:80],
        # Delivery provenance only. It enables a bounded current-state ABox
        # patch after an already-outboxed raw quote, but is never a TypeDB
        # rule condition or an investment conclusion.
        "observationFollowupSymbols": sorted(observation_followups)[:80],
        # Scheduler pressure remains operational provenance. It controls only
        # bounded queue dispatch and never expands a subject request into a
        # whole-world projection or becomes an investment fact.
        "queuePressure": {
            "effectivePendingCount": non_negative_integer(queue_pressure.get("effectivePendingCount")),
            "selectedRequestCount": non_negative_integer(queue_pressure.get("selectedRequestCount")),
            "omittedSymbolCount": non_negative_integer(queue_pressure.get("omittedSymbolCount")),
            "hasDeferredWork": bool(queue_pressure.get("hasDeferredWork")),
        },
        "queueDispatch": {
            "mode": str(queue_dispatch.get("mode") or "")[:80],
            "selectedLanes": clean_list(queue_dispatch.get("selectedLanes"), limit=5),
            "selectedWorkClasses": clean_list(
                queue_dispatch.get("selectedWorkClasses"),
                limit=12,
            ),
            "selectedByLane": {
                str(key or "")[:40]: non_negative_integer(value)
                for key, value in dict(queue_dispatch.get("selectedByLane") or {}).items()
                if str(key or "").strip()
            },
            "selectedByPriority": {
                str(key or "")[:40]: non_negative_integer(value)
                for key, value in dict(queue_dispatch.get("selectedByPriority") or {}).items()
                if str(key or "").strip()
            },
            "oldestRequestAt": str(queue_dispatch.get("oldestRequestAt") or "")[:80],
            "oldestSourceObservedAt": str(
                queue_dispatch.get("oldestSourceObservedAt") or ""
            )[:80],
        },
        # This is scheduler provenance only. It explains why several symbols
        # shared one coherent TypeDB generation without becoming a RuleBox
        # condition or a user-facing investment signal.
        "batchPlan": {
            "version": str(batch_plan.get("version") or "")[:80],
            "enabled": bool(batch_plan.get("enabled")),
            "mode": str(batch_plan.get("mode") or "")[:80],
            "targetSymbolLimit": non_negative_integer(batch_plan.get("targetSymbolLimit")),
            "singleSubjectInference": bool(batch_plan.get("singleSubjectInference")),
            "multiSubjectInferenceDisabled": bool(batch_plan.get("multiSubjectInferenceDisabled")),
            "proposedMultiSubjectLimit": non_negative_integer(
                batch_plan.get("proposedMultiSubjectLimit")
            ),
            "proposedMultiSubjectMode": str(
                batch_plan.get("proposedMultiSubjectMode") or ""
            )[:80],
            "proposedReasonCodes": clean_list(
                batch_plan.get("proposedReasonCodes"),
                limit=12,
            ),
            "bulkWriteBatchingRetained": bool(batch_plan.get("bulkWriteBatchingRetained")),
            "hardTargetSymbolLimit": non_negative_integer(batch_plan.get("hardTargetSymbolLimit")),
            "steadyTargetSymbolLimit": non_negative_integer(batch_plan.get("steadyTargetSymbolLimit")),
            "burstTargetSymbolLimit": non_negative_integer(batch_plan.get("burstTargetSymbolLimit")),
            "pendingRequestCount": non_negative_integer(batch_plan.get("pendingRequestCount")),
            "pendingSymbolCount": non_negative_integer(batch_plan.get("pendingSymbolCount")),
            "oldestWaitSeconds": non_negative_integer(batch_plan.get("oldestWaitSeconds")),
            "executionBudgetSeconds": non_negative_integer(batch_plan.get("executionBudgetSeconds")),
            "backlogBurstEnabled": bool(batch_plan.get("backlogBurstEnabled")),
            "backlogBurstAgeSeconds": non_negative_integer(batch_plan.get("backlogBurstAgeSeconds")),
            "backlogEscape": bool(batch_plan.get("backlogEscape")),
            "baselineTargetSymbolCount": non_negative_integer(batch_plan.get("baselineTargetSymbolCount")),
            "estimatedPerTargetRuntimeMs": non_negative_integer(batch_plan.get("estimatedPerTargetRuntimeMs")),
            "runtimeEstimateBasis": str(batch_plan.get("runtimeEstimateBasis") or "")[:80],
            "runtimeEstimateBasisMs": non_negative_integer(batch_plan.get("runtimeEstimateBasisMs")),
            "targetParallelism": non_negative_integer(batch_plan.get("targetParallelism")),
            "estimatedFixedRuntimeMs": non_negative_integer(batch_plan.get("estimatedFixedRuntimeMs")),
            "estimatedIncrementalTargetRuntimeMs": non_negative_integer(
                batch_plan.get("estimatedIncrementalTargetRuntimeMs")
            ),
            "rampTargetSymbolLimit": non_negative_integer(batch_plan.get("rampTargetSymbolLimit")),
            "estimatedBurstRuntimeMs": non_negative_integer(batch_plan.get("estimatedBurstRuntimeMs")),
            "budgetTargetSymbolLimit": non_negative_integer(batch_plan.get("budgetTargetSymbolLimit")),
            "recentExecutionSource": str(batch_plan.get("recentExecutionSource") or "")[:40],
            "runtimeGuard": bool(batch_plan.get("runtimeGuard")),
            "reasonCodes": clean_list(batch_plan.get("reasonCodes"), limit=12),
        },
        # Bounded scheduling provenance. It controls whether a completed
        # current-state generation may be delivered, never a RuleBox rule.
        "cryptoTransitions": compact_crypto_transitions,
        "verifiedSourceSnapshot": compact_verified_source_snapshot,
        "sharedInferenceReuseProof": compact_shared_reuse,
    }


def inference_reuse_scope_plan(scope_plan: Iterable[object], limit: int = 260) -> List[Dict[str, object]]:
    """Keep the immutable facts needed to prove a later target-rule reuse.

    This is operational provenance, not a second rule engine.  A later
    projection compares these TypeDB ABox scope identities with its candidate
    Manifest and still asks TypeDB to evaluate every selected RuleBox rule.
    """
    rows: List[Dict[str, object]] = []
    for item in scope_plan or []:
        if not isinstance(item, dict):
            continue
        scope_id = str(item.get("scopeId") or "").strip()
        if not scope_id:
            continue
        semantic_fingerprints = item.get("semanticFingerprints")
        semantic_dependency_fingerprints = item.get("semanticDependencyFingerprints")
        dependencies = item.get("dependencyScopeIds")
        rows.append({
            "scopeId": scope_id,
            "scopeType": str(item.get("scopeType") or "").strip(),
            "scopeFamily": str(item.get("scopeFamily") or "").strip(),
            "impactScopeFamilies": sorted({
                str(value or "").strip()
                for value in (item.get("impactScopeFamilies") or [])
                if str(value or "").strip()
            }),
            "semanticFingerprints": {
                str(key or "").strip(): str(value or "").strip()
                for key, value in dict(semantic_fingerprints or {}).items()
                if str(key or "").strip() and str(value or "").strip()
            },
            "semanticDependencyFingerprintVersion": str(
                item.get("semanticDependencyFingerprintVersion") or ""
            ).strip(),
            "semanticDependencyFingerprints": {
                str(key or "").strip(): str(value or "").strip()
                for key, value in dict(semantic_dependency_fingerprints or {}).items()
                if str(key or "").strip() and str(value or "").strip()
            },
            "generationId": str(item.get("generationId") or "").strip(),
            "fingerprint": str(item.get("fingerprint") or "").strip(),
            "baseFingerprint": str(item.get("baseFingerprint") or "").strip(),
            "dependencyScopeIds": sorted({
                str(value or "").strip()
                for value in (dependencies or [])
                if str(value or "").strip()
            }),
        })
    return sorted(rows, key=lambda item: item["scopeId"])[:max(1, int(limit or 260))]


def inference_reuse_scope_plan_for_targets(
    scope_plan: Iterable[object],
    target_symbols: Iterable[object],
) -> List[Dict[str, object]]:
    """Return one target's scopes plus the facts it directly depends on.

    The full plan is still fingerprinted and validated for audit integrity.
    This smaller view only prevents an unrelated symbol's changed generation
    from broadening the current target's native RuleBox candidate list.
    """
    rows = inference_reuse_scope_plan(scope_plan)
    targets = set(_clean_symbols(target_symbols))
    if not rows or not targets:
        return rows
    by_scope = {str(row.get("scopeId") or "").strip(): row for row in rows}
    selected = {
        scope_id
        for scope_id in by_scope
        if scope_symbol(scope_id) in targets
    }
    if not selected:
        return rows
    pending = list(selected)
    while pending:
        scope_id = pending.pop()
        row = by_scope.get(scope_id) or {}
        for dependency in row.get("dependencyScopeIds") or []:
            dependency_id = str(dependency or "").strip()
            if not dependency_id or dependency_id not in by_scope or dependency_id in selected:
                continue
            dependency_symbol = scope_symbol(dependency_id)
            if dependency_symbol and dependency_symbol not in targets:
                # A relation-only scope records every endpoint generation so
                # TypeDB can safely rebind immutable storage rows. That is a
                # persistence dependency, not evidence that another symbol's
                # market fact is part of this target's inference input. The
                # relation scope's own semantic fingerprint still captures a
                # real cross-symbol relation change; its endpoint generation
                # rebinding must not turn a PLTR run into a whole-portfolio
                # candidate-rule evaluation.
                continue
            selected.add(dependency_id)
            pending.append(dependency_id)
    return [row for row in rows if str(row.get("scopeId") or "") in selected]


def inference_reuse_scope_plan_fingerprint(scope_plan: Iterable[object]) -> str:
    """Fingerprint only the persisted proof surface, not the full ABox."""
    return _hash_payload(inference_reuse_scope_plan(scope_plan))


def inference_reuse_proof_summary(proof: Dict[str, object]) -> Dict[str, object]:
    """Persist bounded native-reuse evidence alongside a projection audit."""
    values = dict(proof or {})
    return {
        "version": str(values.get("version") or INFERENCE_REUSE_PROOF_VERSION),
        "status": str(values.get("status") or ""),
        "reason": str(values.get("reason") or "")[:300],
        "coverageComplete": bool(values.get("coverageComplete")),
        "executionNamespaceId": str(values.get("executionNamespaceId") or ""),
        "engineDeploymentId": str(values.get("engineDeploymentId") or ""),
        "graphDatabase": str(values.get("graphDatabase") or ""),
        "releaseFingerprint": str(values.get("releaseFingerprint") or ""),
        "sourceAboxSnapshotId": str(values.get("sourceAboxSnapshotId") or ""),
        "inferenceGenerationId": str(values.get("inferenceGenerationId") or ""),
        "targetSymbols": _clean_symbols(values.get("targetSymbols") or []),
        "matchedRuleIds": sorted({
            str(value or "").strip()
            for value in (values.get("matchedRuleIds") or [])
            if str(value or "").strip()
        })[:160],
        "matchedRuleCount": int(values.get("matchedRuleCount") or 0),
        "ruleboxRulesHash": str(values.get("ruleboxRulesHash") or ""),
        "tboxFingerprint": str(values.get("tboxFingerprint") or ""),
        "scopePlanFingerprint": str(values.get("scopePlanFingerprint") or ""),
        "scopePlanCount": int(values.get("scopePlanCount") or 0),
        "selectionApplied": bool(values.get("selectionApplied")),
        "inheritedCoverage": bool(values.get("inheritedCoverage")),
    }


def projection_source_snapshot(snapshot: AccountSnapshot) -> Dict[str, object]:
    """Return the source payload needed to reproduce a material ABox generation.

    Projection-only state can contain a previous monitor snapshot, an existing
    graph result, or rendered AI data. Those values are derived after source
    collection and must not become a recursive source-of-truth payload.
    """
    payload = snapshot.to_monitor_state()
    # Decisions are generated from the preceding InferenceBox/AI pass. They
    # are useful to the delivery read model but are not causal ABox input.
    # Keeping them here made a completed inference look like a fresh market
    # change and reopened the same RuleBox slice on the next worker cycle.
    payload.pop("decisions", None)
    metadata = dict(payload.get("metadata") or {})
    for key in ["previousMonitorState", "monitorStateHistory", "ontology", "reasoningSnapshotReplay"]:
        metadata.pop(key, None)
    payload["metadata"] = metadata
    return payload


def projection_source_snapshot_fingerprint(snapshot: AccountSnapshot) -> str:
    return _hash_payload(projection_source_snapshot(snapshot))


def projection_analysis_telemetry(result: Mapping[str, object]) -> Dict[str, object]:
    """Describe whether a projection left enough evidence for diagnosis.

    Missing telemetry is explicit. This prevents an interrupted recovery from
    looking like a zero-cost, zero-rule successful execution in operational
    history and keeps performance conclusions tied to complete samples.
    """
    values = dict(result or {})
    execution = (
        dict(values.get("ruleboxExecution") or {})
        if isinstance(values.get("ruleboxExecution"), Mapping)
        else {}
    )
    replay = (
        dict(values.get("nativeReplayValidation") or {})
        if isinstance(values.get("nativeReplayValidation"), Mapping)
        else {}
    )
    runtime_stages = (
        dict(values.get("runtimeStages") or {})
        if isinstance(values.get("runtimeStages"), Mapping)
        else {}
    )
    native_stages = (
        dict(execution.get("typedbNativeStageTimings") or {})
        if isinstance(execution.get("typedbNativeStageTimings"), Mapping)
        else {}
    )
    timing = native_rule_timing_profile(execution)
    selection_applied = bool(execution.get("nativeRuleSelectionApplied"))
    candidate_count = int(execution.get("nativeRuleSelectionCandidateCount") or 0)
    executed_count = int(execution.get("nativeRuleSelectionExecutedCount") or 0)
    deferred_count = int(execution.get("nativeRuleSelectionDeferredCount") or 0)
    full_count = int(execution.get("nativeRuleSelectionFullRuleCount") or 0)
    if selection_applied:
        ledger_complete = bool(
            full_count > 0
            and executed_count >= candidate_count
            and executed_count + deferred_count == full_count
        )
    else:
        ledger_complete = bool(execution.get("nativeInferenceEvaluationComplete"))
    missing = []
    if not execution:
        missing.append("ruleboxExecution")
    if selection_applied and not full_count:
        missing.append("nativeRuleSelectionFullRuleCount")
    if selection_applied and not ledger_complete:
        missing.append("completeSelectedDeferredLedger")
    if not runtime_stages:
        missing.append("runtimeStages")
    if not native_stages:
        missing.append("typedbNativeStageTimings")
    if int(timing.get("executedRuleCount") or 0) <= 0:
        missing.append("perRuleTiming")
    return {
        "version": "ontology-projection-analysis-v1",
        "complete": not missing and ledger_complete,
        "executionLedgerStatus": "complete" if ledger_complete else "incomplete",
        "stageTimingStatus": "complete" if runtime_stages and native_stages else "partial",
        "ruleTimingStatus": (
            "complete" if int(timing.get("executedRuleCount") or 0) > 0 else "unavailable"
        ),
        "recoveredAfterRuntimeInterruption": bool(
            values.get("recoveredAfterRuntimeInterruption")
        ),
        "recoveryMode": str(values.get("recoveryMode") or ""),
        "candidateRuleCount": candidate_count,
        "executedRuleCount": executed_count,
        "deferredRuleCount": deferred_count,
        "fullRuleCount": full_count,
        "replayValidationStatus": str(replay.get("status") or ""),
        "replayValidationVerified": bool(replay.get("verified")),
        "missingFields": sorted(set(missing)),
    }


def projection_result_summary(result: Dict[str, object]) -> Dict[str, object]:
    """Persist a bounded audit payload instead of another full graph copy."""
    values = dict(result or {})
    inference = dict(values.get("inferenceBox") or {})
    execution = dict(values.get("ruleboxExecution") or {})
    verification = dict(values.get("aboxPersistenceVerification") or {})
    active_pointer = dict(verification.get("activePointer") or {})
    activation = dict(verification.get("activation") or {})
    cleanup = dict(verification.get("candidateCleanup") or {})
    retired_cleanup = dict(verification.get("retiredActiveCleanup") or {})
    impact_plan = compact_inference_impact_plan(values.get("inferenceImpactPlan") or {})
    projection_scope = dict(values.get("projectionScope") or {})
    target_patch = dict(projection_scope.get("targetScopedManifestPatch") or {})
    scope_selection_trace = dict(target_patch.get("scopeSelectionTrace") or {})
    runtime_identity = dict(values.get("runtimeIdentity") or {})
    ontology_world = dict(values.get("ontologyWorld") or {})
    market_world = dict(values.get("marketWorld") or {})
    inference_reuse_proof = inference_reuse_proof_summary(
        values.get("inferenceReuseProof") if isinstance(values.get("inferenceReuseProof"), dict) else {}
    )
    prior_inference_reuse = dict(values.get("priorInferenceReuse") or {})
    inference_detail_outbox = dict(values.get("inferenceDetailOutbox") or {})
    native_stage_timings = dict(execution.get("typedbNativeStageTimings") or {})
    model_signal_bridge_execution = dict(
        execution.get("modelSignalBridgeExecution") or {}
    )
    replay_validation = dict(values.get("nativeReplayValidation") or {})
    equivalence_audit = dict(values.get("incrementalEquivalenceAudit") or {})
    native_rule_failure = dict(values.get("nativeRuleFailure") or {})
    reasoning_context = compact_reasoning_request_context(values.get("reasoningContext"))
    analysis_telemetry = projection_analysis_telemetry(values)
    return {
        "saved": bool(values.get("saved")),
        "status": str(values.get("status") or ""),
        "reason": str(values.get("reason") or "")[:500],
        "graphStore": str(values.get("graphStore") or ""),
        "runtimeIdentity": {
            "contract": str(runtime_identity.get("contract") or ""),
            "version": str(runtime_identity.get("version") or ""),
            "revision": str(runtime_identity.get("revision") or ""),
            "source": str(runtime_identity.get("source") or ""),
            "python": str(runtime_identity.get("python") or ""),
        },
        "projectionMode": str(values.get("projectionMode") or ""),
        "materialChangeDetected": bool(values.get("materialChangeDetected")),
        "materialFingerprint": str(values.get("materialFingerprint") or ""),
        "aboxSnapshotId": str(values.get("aboxSnapshotId") or ""),
        "world": {
            "tenantId": str(ontology_world.get("tenantId") or ""),
            "worldId": str(ontology_world.get("worldId") or projection_scope.get("worldId") or ""),
            "worldType": str(ontology_world.get("worldType") or ""),
            "marketWorldId": str(market_world.get("worldId") or projection_scope.get("marketWorldId") or ""),
        },
        "scopeTopologyVersion": str(projection_scope.get("scopeTopologyVersion") or ""),
        "scopeFamilyCounts": dict(projection_scope.get("scopeFamilyCounts") or {}),
        "relationPersistence": compact_abox_relation_persistence(values.get("relationPersistence")),
        "targetScopedManifestPatch": {
            "status": str(target_patch.get("status") or ""),
            "mode": str(target_patch.get("mode") or ""),
            "fallbackReason": str(target_patch.get("fallbackReason") or "")[:220],
            "factSlotStatus": str(target_patch.get("factSlotStatus") or ""),
            "factSlotFallbackReason": str(
                target_patch.get("factSlotFallbackReason") or ""
            )[:220],
            "factSlotSelectedScopeCount": int(
                target_patch.get("factSlotSelectedScopeCount") or 0
            ),
            "factSlotDeferredScopeCount": int(
                target_patch.get("factSlotDeferredScopeCount") or 0
            ),
            "targetSymbols": _clean_symbols(target_patch.get("targetSymbols") or []),
            "selectedIncomingScopeCount": int(target_patch.get("selectedIncomingScopeCount") or 0),
            "reusedActiveScopeCount": int(target_patch.get("reusedActiveScopeCount") or 0),
            "deferredScopeCount": int(target_patch.get("deferredScopeCount") or 0),
            "scopeIntegrityAuditIntervalMinutes": float(
                target_patch.get("scopeIntegrityAuditIntervalMinutes") or 0
            ),
            "scopeIntegrityAuditDue": bool(target_patch.get("scopeIntegrityAuditDue")),
            "automaticFullProjectionBlocked": bool(
                target_patch.get("automaticFullProjectionBlocked")
            ),
            "factSlotFamiliesBySymbol": {
                str(symbol or "").upper().strip(): sorted({
                    str(value or "").strip()
                    for value in values or []
                    if str(value or "").strip()
                })[:20]
                for symbol, values in dict(
                    target_patch.get("factSlotFamiliesBySymbol") or {}
                ).items()
                if str(symbol or "").strip()
            },
            "factSlotChangedFieldsBySymbol": {
                str(symbol or "").upper().strip(): sorted({
                    str(value or "").strip()
                    for value in values or []
                    if str(value or "").strip()
                })[:80]
                for symbol, values in dict(
                    target_patch.get("factSlotChangedFieldsBySymbol") or {}
                ).items()
                if str(symbol or "").strip()
            },
            "factSlotPreciseFieldRoutingSymbols": _clean_symbols(
                target_patch.get("factSlotPreciseFieldRoutingSymbols") or []
            ),
            "factSlotUnclassifiedChangedFieldsBySymbol": {
                str(symbol or "").upper().strip(): sorted({
                    str(value or "").strip()
                    for value in values or []
                    if str(value or "").strip()
                })[:80]
                for symbol, values in dict(
                    target_patch.get("factSlotUnclassifiedChangedFieldsBySymbol") or {}
                ).items()
                if str(symbol or "").strip()
            },
            "scopeSelectionTrace": {
                "version": str(scope_selection_trace.get("version") or ""),
                "selected": [
                    dict(item)
                    for item in (scope_selection_trace.get("selected") or [])[:40]
                    if isinstance(item, Mapping)
                ],
                "deferred": [
                    dict(item)
                    for item in (scope_selection_trace.get("deferred") or [])[:40]
                    if isinstance(item, Mapping)
                ],
            },
        },
        "inferenceImpactPlan": impact_plan,
        "priorInferenceReuse": {
            "reusable": bool(prior_inference_reuse.get("reusable")),
            "proofSource": str(prior_inference_reuse.get("proofSource") or ""),
            "proofRunId": str(prior_inference_reuse.get("proofRunId") or ""),
            "matchedRuleCount": int(prior_inference_reuse.get("matchedRuleCount") or 0),
            "fallbackReason": str(prior_inference_reuse.get("fallbackReason") or "")[:220],
            "recomputedCandidateRuleCount": int(prior_inference_reuse.get("recomputedCandidateRuleCount") or 0),
            "recomputedChangedScopeCount": int(prior_inference_reuse.get("recomputedChangedScopeCount") or 0),
        },
        "inferenceReuseProof": inference_reuse_proof,
        "nativeReplayValidation": {
            "version": str(replay_validation.get("version") or ""),
            "status": str(replay_validation.get("status") or ""),
            "reason": str(replay_validation.get("reason") or "")[:300],
            "selectionApplied": bool(replay_validation.get("selectionApplied")),
            "coverageComplete": bool(replay_validation.get("coverageComplete")),
            "nativeEvaluationComplete": bool(replay_validation.get("nativeEvaluationComplete")),
            "generationAligned": bool(replay_validation.get("generationAligned")),
            "verified": bool(replay_validation.get("verified")),
            "candidateRuleCount": int(replay_validation.get("candidateRuleCount") or 0),
            "executedRuleCount": int(replay_validation.get("executedRuleCount") or 0),
            "deferredRuleCount": int(replay_validation.get("deferredRuleCount") or 0),
            "fullRuleCount": int(replay_validation.get("fullRuleCount") or 0),
            "selectedRuleLedgerComplete": bool(
                replay_validation.get("selectedRuleLedgerComplete")
            ),
        },
        "incrementalEquivalenceAudit": {
            "version": str(equivalence_audit.get("version") or ""),
            "status": str(equivalence_audit.get("status") or ""),
            "verified": bool(equivalence_audit.get("verified")),
            "reconciledByFullEvaluation": bool(
                equivalence_audit.get("reconciledByFullEvaluation")
            ),
            "comparedRuleCount": int(equivalence_audit.get("comparedRuleCount") or 0),
            "mismatchCount": int(equivalence_audit.get("mismatchCount") or 0),
            "reason": str(equivalence_audit.get("reason") or "")[:300],
            "mismatches": [
                dict(item)
                for item in equivalence_audit.get("mismatches") or []
                if isinstance(item, Mapping)
            ][:20],
        },
        "analysisTelemetry": analysis_telemetry,
        "recoveredAfterRuntimeInterruption": bool(
            values.get("recoveredAfterRuntimeInterruption")
        ),
        "recoveryMode": str(values.get("recoveryMode") or ""),
        "preservedActiveGeneration": bool(values.get("preservedActiveGeneration")),
        "nativeRuleFailure": {
            "version": str(native_rule_failure.get("version") or ""),
            "stage": str(native_rule_failure.get("stage") or ""),
            "status": str(native_rule_failure.get("status") or ""),
            "executionStatus": str(native_rule_failure.get("executionStatus") or ""),
            "reasonCode": str(native_rule_failure.get("reasonCode") or ""),
            "ruleId": str(native_rule_failure.get("ruleId") or ""),
            "blockingRuleStatus": str(native_rule_failure.get("blockingRuleStatus") or ""),
            "targetSymbols": _clean_symbols(native_rule_failure.get("targetSymbols") or []),
            "queryMode": str(native_rule_failure.get("queryMode") or ""),
            "retryable": bool(native_rule_failure.get("retryable")),
            "recommendedRetryAfterSeconds": int(
                native_rule_failure.get("recommendedRetryAfterSeconds") or 0
            ),
            "reason": str(native_rule_failure.get("reason") or "")[:500],
        },
        "reasoningContext": reasoning_context,
        "entityCount": int(values.get("entityCount") or 0),
        "relationCount": int(values.get("relationCount") or 0),
        "activeAbox": {
            "snapshotId": str(active_pointer.get("aboxSnapshotId") or ""),
            "status": str(active_pointer.get("status") or ""),
            "projectionRunId": str(active_pointer.get("projectionRunId") or ""),
        },
        "activation": {
            "status": str(activation.get("status") or ""),
            "snapshotId": str(activation.get("snapshotId") or ""),
            "atomic": bool(activation.get("atomic")),
        },
        "candidateCleanup": {
            "status": str(cleanup.get("status") or ""),
            "removedCount": len(cleanup.get("removedCandidateSnapshotIds") or []),
            "remainingInactiveCount": int(cleanup.get("remainingInactiveCandidateCount") or 0),
        },
        "retiredActiveCleanup": {
            "status": str(retired_cleanup.get("status") or ""),
            "snapshotId": str(retired_cleanup.get("aboxSnapshotId") or ""),
            "deletedBatchCount": int(retired_cleanup.get("deletedBatchCount") or 0),
        },
        "inferenceBox": {
            "status": str(inference.get("status") or ""),
            "generationId": str(inference.get("inferenceGenerationId") or ""),
            "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
            "targetSymbols": _clean_symbols(inference.get("targetSymbols") or []),
            "relationCount": int(inference.get("relationCount") or len(inference.get("relations") or [])),
            "traceCount": len(inference.get("traces") or []),
            "generationAligned": bool(inference.get("generationAligned")),
            "nativeTypeDbReasoningUsed": bool(inference.get("nativeTypeDbReasoningUsed")),
            "nativeTypeDbReasoningCompleted": bool(
                inference.get("nativeTypeDbReasoningCompleted")
                or inference.get("typedbNativeRuleEvaluationCompleted")
            ),
            "nativeInferenceOutcome": str(inference.get("nativeInferenceOutcome") or ""),
            "reasoningMode": str(inference.get("reasoningMode") or ""),
        },
        "inferenceDetailOutbox": {
            "status": str(inference_detail_outbox.get("status") or ""),
            "saved": bool(inference_detail_outbox.get("saved")),
            "eventuallyConsistent": bool(inference_detail_outbox.get("eventuallyConsistent")),
            "jobId": str(inference_detail_outbox.get("jobId") or ""),
            "inferenceGenerationId": str(inference_detail_outbox.get("inferenceGenerationId") or ""),
            "sourceAboxSnapshotId": str(inference_detail_outbox.get("sourceAboxSnapshotId") or ""),
            "reason": str(inference_detail_outbox.get("reason") or "")[:220],
        },
        "ruleboxExecution": {
            "status": str(execution.get("status") or ""),
            "reason": str(execution.get("reason") or "")[:500],
            "rulesHash": str(execution.get("rulesHash") or ""),
            "sourceRulesHash": str(execution.get("sourceRulesHash") or ""),
            "ruleboxRulesHash": str(execution.get("ruleboxRulesHash") or ""),
            "selectedRuleCount": int(execution.get("selectedRuleCount") or 0),
            "matchedRuleCount": int(execution.get("matchedRuleCount") or 0),
            "typedbNativeRuleExecutedCount": int(execution.get("typedbNativeRuleExecutedCount") or 0),
            "typedbNativeRuleMatchedCount": int(execution.get("typedbNativeRuleMatchedCount") or 0),
            "modelSignalBridgeExecution": {
                key: model_signal_bridge_execution.get(key)
                for key in [
                    "status",
                    "logicalModelSignalPolicyCount",
                    "batchedSimplePolicyCount",
                    "constrainedPolicyCount",
                    "modelSignalBridgeReadCount",
                    "eliminatedModelSignalPolicyQueryCount",
                    "ignoredContractIds",
                    "subjectCount",
                ]
                if key in model_signal_bridge_execution
            },
            "typedbNativeRuleMatchedRuleIds": sorted({
                str(value or "").strip()
                for value in execution.get("typedbNativeRuleMatchedRuleIds") or []
                if str(value or "").strip()
            })[:160],
            "typedbNativeManifestEvidencePreflightEnabled": bool(
                execution.get("typedbNativeManifestEvidencePreflightEnabled")
            ),
            "typedbNativeRelationEvidencePreflightEnabled": bool(
                execution.get("typedbNativeRelationEvidencePreflightEnabled")
            ),
            "typedbNativeManifestEvidencePreflightPrunedSymbolCount": int(
                execution.get("typedbNativeManifestEvidencePreflightPrunedSymbolCount") or 0
            ),
            "typedbNativeRuleParallelism": int(execution.get("typedbNativeRuleParallelism") or 1),
            "typedbNativeRuleParallelUsed": bool(execution.get("typedbNativeRuleParallelUsed")),
            "typedbNativeRuleSubjectRuleParallelism": int(
                execution.get("typedbNativeRuleSubjectRuleParallelism") or 1
            ),
            "typedbNativeRuleTotalReadParallelismCap": int(
                execution.get("typedbNativeRuleTotalReadParallelismCap") or 1
            ),
            "typedbNativeRuleEffectiveTotalReadParallelism": int(
                execution.get("typedbNativeRuleEffectiveTotalReadParallelism") or 1
            ),
            "typedbNativeRuleTargetParallelism": int(execution.get("typedbNativeRuleTargetParallelism") or 1),
            "typedbNativeRuleSubjectFanoutUsed": bool(execution.get("typedbNativeRuleSubjectFanoutUsed")),
            "typedbNativeRuleSubjectFanoutParallelism": int(
                execution.get("typedbNativeRuleSubjectFanoutParallelism") or 1
            ),
            "typedbNativeRuleSubjectFanoutDurationMs": int(
                execution.get("typedbNativeRuleSubjectFanoutDurationMs") or 0
            ),
            "typedbNativeRuleSubjectFanoutFailureCount": int(
                execution.get("typedbNativeRuleSubjectFanoutFailureCount") or 0
            ),
            "typedbNativeRuleSubjectFanoutSubjects": [
                dict(item)
                for item in execution.get("typedbNativeRuleSubjectFanoutSubjects") or []
                if isinstance(item, dict)
            ][:8],
            "typedbNativeRuleTargetWorkShardingUsed": bool(execution.get("typedbNativeRuleTargetWorkShardingUsed")),
            "typedbNativeRuleTargetWorkShardCount": int(execution.get("typedbNativeRuleTargetWorkShardCount") or 0),
            "typedbNativeRuleWorkItemCount": int(execution.get("typedbNativeRuleWorkItemCount") or 0),
            "typedbNativeRuleAdaptiveTargetShardingEnabled": bool(
                execution.get("typedbNativeRuleAdaptiveTargetShardingEnabled")
            ),
            "typedbNativeRuleAdaptiveTargetShardingProfileStatus": str(
                execution.get("typedbNativeRuleAdaptiveTargetShardingProfileStatus") or ""
            ),
            "typedbNativeRuleAdaptiveTargetShardingUsed": bool(
                execution.get("typedbNativeRuleAdaptiveTargetShardingUsed")
            ),
            "typedbNativeRuleAdaptiveTargetShardedRuleCount": int(
                execution.get("typedbNativeRuleAdaptiveTargetShardedRuleCount") or 0
            ),
            "typedbNativeRuleCommitMode": str(execution.get("typedbNativeRuleCommitMode") or ""),
            "nativeInferenceEvaluationComplete": bool(execution.get("nativeInferenceEvaluationComplete")),
            "nativeInferenceOutcome": str(execution.get("nativeInferenceOutcome") or ""),
            "nativeRuleSelectionApplied": bool(execution.get("nativeRuleSelectionApplied")),
            "nativeRuleSelectionFallbackReason": str(execution.get("nativeRuleSelectionFallbackReason") or ""),
            "nativeRuleSelectionCandidateCount": int(execution.get("nativeRuleSelectionCandidateCount") or 0),
            "nativeRuleSelectionPriorMatchedCount": int(execution.get("nativeRuleSelectionPriorMatchedCount") or 0),
            "nativeRuleSelectionExecutedCount": int(execution.get("nativeRuleSelectionExecutedCount") or 0),
            "nativeRuleSelectionDeferredCount": int(execution.get("nativeRuleSelectionDeferredCount") or 0),
            "nativeRuleSelectionFullRuleCount": int(
                execution.get("nativeRuleSelectionFullRuleCount") or 0
            ),
            "nativeRuleSelectionExecutedRuleIds": sorted({
                str(value or "").strip()
                for value in execution.get("nativeRuleSelectionExecutedRuleIds") or []
                if str(value or "").strip()
            })[:160],
            "nativeRuleSelectionDeferredRuleIds": sorted({
                str(value or "").strip()
                for value in execution.get("nativeRuleSelectionDeferredRuleIds") or []
                if str(value or "").strip()
            })[:160],
            "nativeRulePreflightStatus": str(execution.get("nativeRulePreflightStatus") or ""),
            "nativeRulePreflightMode": str(execution.get("nativeRulePreflightMode") or ""),
            "nativeRulePreflightReason": str(execution.get("nativeRulePreflightReason") or "")[:220],
            "nativeRulePreflightSourceCount": int(execution.get("nativeRulePreflightSourceCount") or 0),
            "nativeRulePreflightLoadedSourceCount": int(
                execution.get("nativeRulePreflightLoadedSourceCount") or 0
            ),
            "nativeRulePreflightEntityCount": int(execution.get("nativeRulePreflightEntityCount") or 0),
            "nativeRulePreflightRelationCount": int(execution.get("nativeRulePreflightRelationCount") or 0),
            "sourceAboxGenerationMode": str(execution.get("sourceAboxGenerationMode") or ""),
            "sourceAboxGenerationValid": bool(execution.get("sourceAboxGenerationValid")),
            "sourceAboxMembershipValidation": str(execution.get("sourceAboxMembershipValidation") or ""),
            "nativeRuleTiming": native_rule_timing_profile(execution),
            "nativeStageTimings": {
                str(key): int(value or 0)
                for key, value in native_stage_timings.items()
                if str(key or "") and isinstance(value, (int, float))
            },
        },
    }


@dataclass(frozen=True)
class OntologyProjectionRun:
    run_id: str
    portfolio_id: str
    account_id: str
    tenant_id: str
    world_id: str
    world_type: str
    market_world_id: str
    source_snapshot_at: str
    source_snapshot_fingerprint: str
    first_observed_at: str
    last_observed_at: str
    started_at: str
    completed_at: str
    activated_at: str
    status: str
    graph_store: str
    execution_namespace_id: str
    engine_deployment_id: str
    graph_database: str
    release_fingerprint: str
    validation_cohort_id: str
    projection_mode: str
    material_fingerprint: str
    abox_snapshot_id: str
    active_abox_snapshot_id: str
    tbox_version: str
    tbox_fingerprint: str
    rulebox_rules_hash: str
    entity_count: int
    relation_count: int
    inference_generation_id: str
    inference_status: str
    source_symbols: List[str]
    context_payload: Dict[str, object]
    result_payload: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def projection_run_from_payload(payload: Dict[str, object]) -> OntologyProjectionRun:
    """Rehydrate one durable audit row without making MySQL a domain dependency."""
    values = dict(payload or {})

    def value(snake_case: str, camel_case: str = "", fallback: object = ""):
        return values.get(camel_case or snake_case, values.get(snake_case, fallback))

    def integer(snake_case: str, camel_case: str = "") -> int:
        try:
            return int(value(snake_case, camel_case, 0) or 0)
        except (TypeError, ValueError):
            return 0

    source_symbols = value("source_symbols", "sourceSymbols", [])
    context_payload = value("context_payload", "context", {})
    result_payload = value("result_payload", "result", {})
    return OntologyProjectionRun(
        run_id=str(value("run_id", "runId") or ""),
        portfolio_id=str(value("portfolio_id", "portfolioId") or ""),
        account_id=str(value("account_id", "accountId") or ""),
        tenant_id=str(value("tenant_id", "tenantId") or ""),
        world_id=str(value("world_id", "worldId") or ""),
        world_type=str(value("world_type", "worldType") or ""),
        market_world_id=str(value("market_world_id", "marketWorldId") or ""),
        source_snapshot_at=str(value("source_snapshot_at", "sourceSnapshotAt") or ""),
        source_snapshot_fingerprint=str(value("source_snapshot_fingerprint", "sourceSnapshotFingerprint") or ""),
        first_observed_at=str(value("first_observed_at", "firstObservedAt") or ""),
        last_observed_at=str(value("last_observed_at", "lastObservedAt") or ""),
        started_at=str(value("started_at", "startedAt") or ""),
        completed_at=str(value("completed_at", "completedAt") or ""),
        activated_at=str(value("activated_at", "activatedAt") or ""),
        status=str(value("status") or ""),
        graph_store=str(value("graph_store", "graphStore") or ""),
        execution_namespace_id=str(value("execution_namespace_id", "executionNamespaceId") or ""),
        engine_deployment_id=str(value("engine_deployment_id", "engineDeploymentId") or ""),
        graph_database=str(value("graph_database", "graphDatabase") or ""),
        release_fingerprint=str(value("release_fingerprint", "releaseFingerprint") or ""),
        validation_cohort_id=str(value("validation_cohort_id", "validationCohortId") or ""),
        projection_mode=str(value("projection_mode", "projectionMode") or ""),
        material_fingerprint=str(value("material_fingerprint", "materialFingerprint") or ""),
        abox_snapshot_id=str(value("abox_snapshot_id", "aboxSnapshotId") or ""),
        active_abox_snapshot_id=str(value("active_abox_snapshot_id", "activeAboxSnapshotId") or ""),
        tbox_version=str(value("tbox_version", "tboxVersion") or ""),
        tbox_fingerprint=str(value("tbox_fingerprint", "tboxFingerprint") or ""),
        rulebox_rules_hash=str(value("rulebox_rules_hash", "ruleboxRulesHash") or ""),
        entity_count=integer("entity_count", "entityCount"),
        relation_count=integer("relation_count", "relationCount"),
        inference_generation_id=str(value("inference_generation_id", "inferenceGenerationId") or ""),
        inference_status=str(value("inference_status", "inferenceStatus") or ""),
        source_symbols=list(source_symbols) if isinstance(source_symbols, list) else [],
        context_payload=dict(context_payload) if isinstance(context_payload, dict) else {},
        result_payload=dict(result_payload) if isinstance(result_payload, dict) else {},
    )


def build_ontology_projection_run(
    snapshot: AccountSnapshot,
    graph: PortfolioOntology,
    material_fingerprint: str,
    abox_snapshot_id: str,
    graph_store: str,
    target_symbols: Iterable[object] = None,
    rulebox_metadata: Dict[str, object] = None,
    reasoning_context: Mapping[str, object] = None,
    execution_namespace: Mapping[str, object] = None,
    started_at: str = "",
) -> OntologyProjectionRun:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    active_tbox = dict(worldview.get("activeTBox") or {})
    source_snapshot = projection_source_snapshot(snapshot)
    source_fingerprint = _hash_payload(source_snapshot)
    symbols = _clean_symbols(target_symbols or [
        getattr(item, "symbol", "")
        for item in list(snapshot.positions or []) + list(snapshot.watchlist or [])
        if not item.is_cash()
    ])
    stamp = str(started_at or utc_now_iso())
    namespace = dict(execution_namespace or {})
    engine_deployment_id = str(namespace.get("engineDeploymentId") or "").strip()
    graph_database = str(namespace.get("graphDatabase") or "").strip()
    release_fingerprint = str(namespace.get("releaseFingerprint") or "").strip()
    validation_cohort_id = str(namespace.get("validationCohortId") or "").strip()
    execution_namespace_id = str(namespace.get("executionNamespaceId") or "").strip()
    if not execution_namespace_id:
        execution_namespace_id = "projection-namespace:" + _hash_payload({
            "engineDeploymentId": engine_deployment_id,
            "graphDatabase": graph_database,
            "releaseFingerprint": release_fingerprint,
            "validationCohortId": validation_cohort_id,
        })[:32]
    # Material fingerprints can recur after an intervening market move. Keep
    # every activation occurrence for audit, rather than overwriting the old
    # record merely because its facts happen to match again.
    run_seed = "|".join([
        execution_namespace_id,
        str(worldview.get("worldId") or ""),
        str(snapshot.account_id or "account"),
        str(material_fingerprint or ""),
        str(abox_snapshot_id or ""),
        stamp,
    ])
    run_id = "ontology-projection:" + hashlib.sha256(run_seed.encode("utf-8")).hexdigest()[:24]
    entity_count = len([
        item for item in graph.entities
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox"
    ])
    relation_count = len([
        item for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox"
    ])
    rulebox = dict(rulebox_metadata or {})
    compact_reasoning_context = compact_reasoning_request_context(
        reasoning_context,
        target_symbols=symbols,
    )
    return OntologyProjectionRun(
        run_id=run_id,
        portfolio_id=str(graph.portfolio_id or snapshot.account_id or ""),
        account_id=str(snapshot.account_id or ""),
        tenant_id=str(worldview.get("tenantId") or ""),
        world_id=str(worldview.get("worldId") or ""),
        world_type=str(worldview.get("worldType") or ""),
        market_world_id=str(worldview.get("marketWorldId") or ""),
        source_snapshot_at=str(snapshot.generated_at or ""),
        source_snapshot_fingerprint=source_fingerprint,
        first_observed_at=stamp,
        last_observed_at=stamp,
        started_at=stamp,
        completed_at="",
        activated_at="",
        status="projecting",
        graph_store=str(graph_store or "typedb"),
        execution_namespace_id=execution_namespace_id,
        engine_deployment_id=engine_deployment_id,
        graph_database=graph_database,
        release_fingerprint=release_fingerprint,
        validation_cohort_id=validation_cohort_id,
        projection_mode=str(worldview.get("runtimeProjectionMode") or "abox-facts-only"),
        material_fingerprint=str(material_fingerprint or ""),
        abox_snapshot_id=str(abox_snapshot_id or ""),
        active_abox_snapshot_id="",
        tbox_version=str(active_tbox.get("version") or active_tbox.get("tboxVersion") or ""),
        tbox_fingerprint=str(active_tbox.get("fingerprint") or active_tbox.get("tboxFingerprint") or ""),
        rulebox_rules_hash=str(rulebox.get("ruleboxRulesHash") or rulebox.get("rulesHash") or ""),
        entity_count=entity_count,
        relation_count=relation_count,
        inference_generation_id="",
        inference_status="",
        source_symbols=symbols,
        context_payload={
            "sourceSnapshotFingerprint": source_fingerprint,
            "sourceSnapshotReference": {
                "accountId": str(snapshot.account_id or ""),
                "generatedAt": str(snapshot.generated_at or ""),
                "store": "monitor_snapshot_history",
            },
            "world": {
                "tenantId": str(worldview.get("tenantId") or ""),
                "worldId": str(worldview.get("worldId") or ""),
                "worldType": str(worldview.get("worldType") or ""),
                "marketWorldId": str(worldview.get("marketWorldId") or ""),
            },
            "executionNamespace": {
                "executionNamespaceId": execution_namespace_id,
                "engineDeploymentId": engine_deployment_id,
                "graphDatabase": graph_database,
                "releaseFingerprint": release_fingerprint,
                "validationCohortId": validation_cohort_id,
            },
            "sourceSnapshotSummary": {
                "mode": str(snapshot.mode or ""),
                "status": str(snapshot.status or ""),
                "positionCount": len(snapshot.positions or []),
                "watchlistCount": len(snapshot.watchlist or []),
                "externalSignalKeys": sorted(list((snapshot.external_signals or {}).keys()))[:80],
            },
            "targetSymbols": symbols,
            "reasoningRequest": compact_reasoning_context,
            "scopeTopology": {
                "version": str(worldview.get("scopeTopologyVersion") or ""),
                "scopeCount": len(worldview.get("scopePlan") or []),
                "scopeFamilyCounts": dict(worldview.get("scopeFamilyCounts") or {}),
                "scopeDelta": dict(worldview.get("scopeDelta") or {}),
                "inferenceImpactPlan": compact_inference_impact_plan(worldview.get("inferenceImpactPlan") or {}),
                # Scope identifiers and semantic fingerprints are enough to
                # prove a later target-scoped native-rule selection. Raw ABox
                # facts remain in TypeDB and are never copied into MySQL.
                "inferenceReuseScopePlan": inference_reuse_scope_plan(worldview.get("scopePlan") or []),
                "inferenceReuseScopePlanFingerprint": inference_reuse_scope_plan_fingerprint(
                    worldview.get("scopePlan") or []
                ),
            },
            "tbox": {
                "version": str(active_tbox.get("version") or active_tbox.get("tboxVersion") or ""),
                "fingerprint": str(active_tbox.get("fingerprint") or active_tbox.get("tboxFingerprint") or ""),
            },
        },
        result_payload={},
    )


def complete_ontology_projection_run(
    run: OntologyProjectionRun,
    result: Dict[str, object],
    completed_at: str = "",
) -> OntologyProjectionRun:
    values = dict(result or {})
    summary = projection_result_summary(values)
    inference = dict(values.get("inferenceBox") or {})
    verification = dict(values.get("aboxPersistenceVerification") or {})
    active_pointer = dict(verification.get("activePointer") or {})
    activation = dict(verification.get("activation") or {})
    stamp = str(completed_at or utc_now_iso())
    resolved_status = str(values.get("status") or ("ok" if values.get("saved") else run.status))
    activated = bool(values.get("saved")) and resolved_status == "ok"
    inference_source_abox = str(inference.get("sourceAboxSnapshotId") or "").strip()
    inference_is_aligned = bool(inference.get("generationAligned")) and bool(inference.get("nativeTypeDbReasoningUsed"))
    verified_active_abox_snapshot_id = str(active_pointer.get("aboxSnapshotId") or "").strip()
    # ``save_graph`` reports the predecessor pointer while a candidate ABox
    # waits for native inference. Once TypeDB returns an aligned InferenceBox
    # for *this* audit run, that proof is stronger than the stale save-time
    # pointer. Keeping the predecessor here made every later target reuse
    # proof fail and forced a complete RuleBox pass.
    if (
        inference_is_aligned
        and inference_source_abox
        and inference_source_abox == str(run.abox_snapshot_id or "").strip()
    ):
        verified_active_abox_snapshot_id = inference_source_abox
    elif not verified_active_abox_snapshot_id:
        activation_status = str(activation.get("status") or "").strip().lower()
        if activation_status in {"activated", "recovered-after-runtime-interruption"}:
            verified_active_abox_snapshot_id = str(activation.get("snapshotId") or "").strip()
    if not verified_active_abox_snapshot_id and inference_is_aligned:
        verified_active_abox_snapshot_id = inference_source_abox
    return replace(
        run,
        last_observed_at=stamp,
        completed_at=stamp,
        activated_at=stamp if activated else run.activated_at,
        status=resolved_status,
        graph_store=str(values.get("graphStore") or run.graph_store),
        projection_mode=str(values.get("projectionMode") or run.projection_mode),
        active_abox_snapshot_id=str(
            verified_active_abox_snapshot_id
            or values.get("aboxSnapshotId")
            or run.active_abox_snapshot_id
            or ""
        ),
        inference_generation_id=str(inference.get("inferenceGenerationId") or run.inference_generation_id),
        inference_status=str(inference.get("status") or run.inference_status),
        result_payload=summary,
    )


def apply_projection_run_identity(graph: PortfolioOntology, run_id: str) -> PortfolioOntology:
    clean_run_id = str(run_id or "").strip()
    if not clean_run_id:
        return graph
    for item in graph.entities:
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox":
            item.properties["projectionRunId"] = clean_run_id
    for item in graph.relations:
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox":
            item.properties["projectionRunId"] = clean_run_id
    for item in graph.evidence:
        if str((item.value or {}).get("ontologyBox") or "ABox") == "ABox":
            item.value["projectionRunId"] = clean_run_id
    graph.worldview["projectionRunId"] = clean_run_id
    return graph
