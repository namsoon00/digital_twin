"""Deterministic contracts for comparing versioned ontology engines.

The comparison boundary consumes graph-backed alert candidates and immutable
projection receipts.  It never evaluates an investment rule in Python; both
baseline and candidate decisions must already have been produced by their own
TypeDB InferenceBox generation.
"""

from __future__ import annotations

import hashlib
import json
import base64
import zlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Sequence, Tuple

from .portfolio_ontology_catalog import OPERATIONAL_PIPELINES, SETTING_CONCEPT_TYPES


REASONING_SHADOW_CONTRACT_VERSION = "reasoning-shadow-comparison-v2"
PROJECTION_RUNTIME_CONTEXT_PACKET_VERSION = "projection-runtime-context-zlib-v1"
MAX_PROJECTION_RUNTIME_CONTEXT_BYTES = 16 * 1024 * 1024


ONTOLOGY_RUNTIME_POLICY_SETTING_KEYS = frozenset(
    set(SETTING_CONCEPT_TYPES)
    | {
        str(item.get(key) or "")
        for item in OPERATIONAL_PIPELINES
        for key in ("scheduleKey", "fallbackSettingKey")
        if str(item.get(key) or "")
    }
    | {
        "fxRates",
        "fxExposureReviewPct",
        "hypothesisOutcomeReviewMinimumSamples",
        "investmentBrainMaximumHypothesisCount",
        "investmentBrainMinimumHypothesisCount",
        "investmentBrainOutcomeReviewMinimumSamples",
        "investmentStrategyProfile",
        "marketMaterialityInvestorFlowRatioPct",
        "marketMaterialityPriceChangePct",
        "marketMaterialityTrendDistanceChangePct",
        "marketMaterialityTrendDistancePct",
        "marketMaterialityVolumeRatio",
        "materialityGateEnabled",
        "ontologyThresholdPolicy",
        "temporalWindowPeriods",
        "ontologyTemporalObservationAnchorProjectionEnabled",
        "statisticalPriceSignalReleaseId",
        "valuationAssumptions",
    }
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def frozen_projection_runtime_context(value: Mapping[str, object]) -> Dict[str, object]:
    """Freeze only causal projection context and ontology-owned policy.

    Runtime settings also contain database addresses and provider secrets.
    Those values are neither investment facts nor valid shadow inputs, so the
    durable replay packet keeps only keys consumed by ontology construction.
    """

    context = deepcopy(dict(value or {}))
    settings = context.get("settings")
    settings = dict(settings or {}) if isinstance(settings, Mapping) else {}
    context["settings"] = {
        key: deepcopy(settings[key])
        for key in sorted(ONTOLOGY_RUNTIME_POLICY_SETTING_KEYS)
        if key in settings
    }
    return context


def pack_projection_runtime_contexts(
    values: Mapping[str, Mapping[str, object]],
) -> Dict[str, object]:
    contexts = {
        str(account_id or ""): frozen_projection_runtime_context(context)
        for account_id, context in dict(values or {}).items()
        if str(account_id or "") and isinstance(context, Mapping)
    }
    raw = canonical_json(contexts).encode("utf-8")
    if len(raw) > MAX_PROJECTION_RUNTIME_CONTEXT_BYTES:
        raise ValueError("Projection runtime context exceeds the immutable shadow input limit")
    compressed = zlib.compress(raw, level=9)
    return {
        "version": PROJECTION_RUNTIME_CONTEXT_PACKET_VERSION,
        "encoding": "zlib+base64",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "uncompressedBytes": len(raw),
        "compressedBytes": len(compressed),
        "accountIds": sorted(contexts),
        "data": base64.b64encode(compressed).decode("ascii"),
    }


def unpack_projection_runtime_contexts(packet: Mapping[str, object]) -> Dict[str, Dict[str, object]]:
    values = dict(packet or {})
    if str(values.get("version") or "") != PROJECTION_RUNTIME_CONTEXT_PACKET_VERSION:
        raise ValueError("Unsupported projection runtime context packet")
    if str(values.get("encoding") or "") != "zlib+base64":
        raise ValueError("Unsupported projection runtime context encoding")
    try:
        compressed = base64.b64decode(str(values.get("data") or ""), validate=True)
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(
            compressed,
            MAX_PROJECTION_RUNTIME_CONTEXT_BYTES + 1,
        )
        raw += decompressor.flush()
    except (ValueError, zlib.error) as error:
        raise ValueError("Invalid projection runtime context payload") from error
    if len(raw) > MAX_PROJECTION_RUNTIME_CONTEXT_BYTES or not decompressor.eof:
        raise ValueError("Projection runtime context exceeds the immutable shadow input limit")
    if int(values.get("uncompressedBytes") or -1) != len(raw):
        raise ValueError("Projection runtime context size verification failed")
    if hashlib.sha256(raw).hexdigest() != str(values.get("sha256") or ""):
        raise ValueError("Projection runtime context hash verification failed")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Projection runtime context JSON is invalid") from error
    if not isinstance(decoded, dict):
        raise ValueError("Projection runtime context must contain an account map")
    contexts = {
        str(account_id or ""): frozen_projection_runtime_context(context)
        for account_id, context in decoded.items()
        if str(account_id or "") and isinstance(context, Mapping)
    }
    if sorted(contexts) != sorted(str(value or "") for value in values.get("accountIds") or []):
        raise ValueError("Projection runtime context account verification failed")
    return contexts


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _strings(values: Iterable[object]) -> Tuple[str, ...]:
    return tuple(sorted({str(value or "").strip() for value in values or [] if str(value or "").strip()}))


def _rounded(value: object, fallback: float = 0.0) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return fallback


def _first_value(values: Mapping[str, object], *names: str, fallback: object = "") -> object:
    source = _mapping(values)
    for name in names:
        if name in source and source.get(name) not in (None, ""):
            return source.get(name)
    return fallback


def _event_field(event: object, name: str, fallback: object = "") -> object:
    if isinstance(event, Mapping):
        aliases = {
            "account_id": "accountId",
            "account_label": "accountLabel",
            "generated_at": "generatedAt",
        }
        if name in event:
            return event.get(name, fallback)
        return event.get(aliases.get(name, ""), fallback)
    return getattr(event, name, fallback)


def graph_candidate_packet(event: object) -> Dict[str, object]:
    """Return only TypeDB-owned decision semantics from one alert candidate."""

    metadata = _mapping(_event_field(event, "metadata", {}))
    context = _mapping(metadata.get("ontologyRelationContext"))
    synthesis = _mapping(metadata.get("v2DecisionSynthesis"))
    decision = _mapping(context.get("decision"))
    envelope = _mapping(context.get("actionEnvelope"))
    state = _mapping(context.get("decisionState"))
    inference = _mapping(context.get("graphStoreInference"))
    active_rules = [
        _mapping(item)
        for item in context.get("activeRules") or []
        if isinstance(item, Mapping)
    ]
    relations = [
        _mapping(item)
        for item in inference.get("relations") or []
        if isinstance(item, Mapping)
    ]
    traces = [
        _mapping(item)
        for item in inference.get("traces") or []
        if isinstance(item, Mapping)
    ]
    rule_ids = _strings(
        [item.get("ruleId") for item in active_rules]
        + [item.get("ruleId") for item in relations]
        + [item.get("ruleId") for item in traces]
    )
    relation_slots = sorted({
        "|".join([
            str(item.get("type") or ""),
            str(item.get("ruleId") or ""),
            str(item.get("decisionStage") or ""),
            str(item.get("decisionEffect") or ""),
        ])
        for item in relations
        if str(item.get("type") or item.get("ruleId") or "").strip()
    })
    evidence_ids = _strings(
        list(context.get("evidenceIds") or [])
        + list(context.get("counterEvidenceIds") or [])
        + [item.get("evidenceId") for item in traces]
        + [
            evidence_id
            for alternative in synthesis.get("alternatives") or []
            if isinstance(alternative, Mapping)
            for evidence_id in (
                list(_first_value(alternative, "supporting_evidence_ids", "supportingEvidenceIds", fallback=[]) or [])
                + list(_first_value(alternative, "counter_evidence_ids", "counterEvidenceIds", fallback=[]) or [])
                + list(_first_value(alternative, "evidence_conflict_ids", "evidenceConflictIds", fallback=[]) or [])
            )
        ]
    )
    selected_rule_id = str(
        decision.get("selectedRuleId")
        or envelope.get("selectedRuleId")
        or _first_value(synthesis, "selected_rule_id", "selectedRuleId")
        or metadata.get("selectedRuleId")
        or ""
    ).strip()
    candidate_action = str(
        decision.get("action")
        or decision.get("candidateAction")
        or envelope.get("candidateAction")
        or envelope.get("selectedDecisionEffect")
        or _first_value(
            synthesis,
            "graph_candidate_action",
            "graphCandidateAction",
            "investment_view_action",
            "investmentViewAction",
            "execution_action",
            "executionAction",
        )
        or ""
    ).strip()
    synthesis_rule_ids = _strings(
        [selected_rule_id]
        + list(metadata.get("matchedRuleIds") or [])
        + list(_first_value(synthesis, "portfolio_constraint_rule_ids", "portfolioConstraintRuleIds", fallback=[]) or [])
        + list(_first_value(synthesis, "execution_constraint_rule_ids", "executionConstraintRuleIds", fallback=[]) or [])
        + list(_first_value(synthesis, "data_quality_rule_ids", "dataQualityRuleIds", fallback=[]) or [])
        + [
            rule_id
            for alternative in synthesis.get("alternatives") or []
            if isinstance(alternative, Mapping)
            for rule_id in _first_value(
                alternative,
                "supporting_rule_ids",
                "supportingRuleIds",
                fallback=[],
            ) or []
        ]
    )
    if synthesis_rule_ids:
        rule_ids = _strings([*rule_ids, *synthesis_rule_ids])
    synthesis_relation_slots = {
        "|".join([
            str(_first_value(alternative, "action") or ""),
            str(rule_id or ""),
            str(_first_value(synthesis, "decision_disposition", "decisionDisposition") or ""),
            str(_first_value(synthesis, "decision_effect", "decisionEffect") or ""),
        ])
        for alternative in synthesis.get("alternatives") or []
        if isinstance(alternative, Mapping)
        for rule_id in _first_value(
            alternative,
            "supporting_rule_ids",
            "supportingRuleIds",
            fallback=[],
        ) or [selected_rule_id]
        if str(_first_value(alternative, "action") or rule_id or "").strip()
    }
    relation_slots = sorted(set(relation_slots).union(synthesis_relation_slots))
    semantic_decision_facts = {
        "allowedActions": list(_strings(
            _first_value(synthesis, "allowed_actions", "allowedActions", fallback=[]) or []
        )),
        "blockedActions": list(_strings(
            _first_value(synthesis, "blocked_actions", "blockedActions", fallback=[]) or []
        )),
        "dataGaps": list(_strings(
            list(_first_value(synthesis, "data_gaps", "dataGaps", fallback=[]) or [])
            + list(_first_value(synthesis, "missing_data", "missingData", fallback=[]) or [])
        )),
        "decisionDisposition": str(
            _first_value(synthesis, "decision_disposition", "decisionDisposition") or ""
        ),
        "executionDisposition": str(
            _first_value(synthesis, "execution_disposition", "executionDisposition") or ""
        ),
        "alternatives": sorted(
            [
                {
                    "action": str(_first_value(alternative, "action") or ""),
                    "decisionEligible": bool(_first_value(
                        alternative,
                        "decision_eligible",
                        "decisionEligible",
                        fallback=False,
                    )),
                    "executionEligible": bool(_first_value(
                        alternative,
                        "execution_eligible",
                        "executionEligible",
                        fallback=False,
                    )),
                    "supportingRuleIds": list(_strings(_first_value(
                        alternative,
                        "supporting_rule_ids",
                        "supportingRuleIds",
                        fallback=[],
                    ) or [])),
                }
                for alternative in synthesis.get("alternatives") or []
                if isinstance(alternative, Mapping)
            ],
            key=lambda item: (
                item["action"],
                item["supportingRuleIds"],
                item["decisionEligible"],
                item["executionEligible"],
            ),
        ),
    }
    packet = {
        "accountId": str(
            _event_field(event, "account_id", "")
            or _first_value(synthesis, "account_id", "accountId")
            or ""
        ),
        "symbol": str(
            _event_field(event, "symbol", "")
            or _first_value(synthesis, "symbol")
            or ""
        ).upper().strip(),
        "messageType": str(_event_field(event, "rule", "") or ""),
        "candidateAction": candidate_action,
        "selectedRuleId": selected_rule_id,
        "decisionStage": str(
            decision.get("decisionStage")
            or _first_value(synthesis, "execution_disposition", "executionDisposition")
            or ""
        ).strip(),
        "decisionEffect": str(
            decision.get("decisionEffect")
            or envelope.get("selectedDecisionEffect")
            or _first_value(synthesis, "decision_effect", "decisionEffect")
            or ""
        ).strip(),
        "actionGroup": str(
            decision.get("actionGroup")
            or _first_value(synthesis, "action_authority", "actionAuthority")
            or ""
        ).strip(),
        "judgementBlocked": bool(
            decision.get("judgementBlocked")
            or _first_value(synthesis, "judgement_blocked", "judgementBlocked", fallback=False)
        ),
        "reviewLevel": str(
            state.get("reviewLevel")
            or context.get("reviewLevel")
            or _first_value(synthesis, "review_level", "reviewLevel")
            or metadata.get("reviewLevel")
            or ""
        ).strip(),
        "dataState": str(
            state.get("dataState")
            or context.get("dataState")
            or _first_value(synthesis, "data_state", "dataState")
            or metadata.get("dataState")
            or ""
        ).strip(),
        "validationState": str(
            state.get("validationState")
            or context.get("validationState")
            or _first_value(
                synthesis,
                "hypothesis_qualification_state",
                "hypothesisQualificationState",
            )
            or metadata.get("validationState")
            or ""
        ).strip(),
        "confidence": _rounded(context.get("confidence") or context.get("confidenceScore")),
        "ruleIds": list(rule_ids),
        "relationSlots": relation_slots,
        "evidenceIds": list(evidence_ids),
        "semanticFactsHash": payload_hash(semantic_decision_facts),
        "factsHash": payload_hash(
            _mapping(context.get("facts"))
            or {
                "allowedActions": list(_first_value(synthesis, "allowed_actions", "allowedActions", fallback=[]) or []),
                "blockedActions": list(_first_value(synthesis, "blocked_actions", "blockedActions", fallback=[]) or []),
                "eligibleHypothesisIds": list(_first_value(synthesis, "eligible_hypothesis_ids", "eligibleHypothesisIds", fallback=[]) or []),
                "executionEligibleHypothesisIds": list(_first_value(synthesis, "execution_eligible_hypothesis_ids", "executionEligibleHypothesisIds", fallback=[]) or []),
                "referenceHypothesisIds": list(_first_value(synthesis, "reference_hypothesis_ids", "referenceHypothesisIds", fallback=[]) or []),
            }
        ),
        "graphStore": str(context.get("graphStore") or ""),
        "graphStoreUsed": bool(context.get("graphStoreUsed")),
    }
    packet["decisionSignature"] = payload_hash({
        key: packet[key]
        for key in [
            "candidateAction", "selectedRuleId", "decisionStage", "decisionEffect",
            "actionGroup", "judgementBlocked", "reviewLevel", "dataState",
            "validationState", "ruleIds", "relationSlots",
            "semanticFactsHash",
        ]
    })
    packet["evidenceSignature"] = payload_hash({
        # Isolated TypeDB graphs generate different assertion IDs for the
        # same evidence. Compare the evidence semantics, never storage IDs.
        "semanticFactsHash": packet["semanticFactsHash"],
        "ruleIds": packet["ruleIds"],
        "relationSlots": packet["relationSlots"],
    })
    return packet


def projection_receipt_packet(account_id: str, projection: Mapping[str, object]) -> Dict[str, object]:
    values = _mapping(projection)
    inference = _mapping(values.get("inferenceBox")) or values
    runtime = _mapping(values.get("runtimeStages")) or _mapping(values.get("stages"))
    comparison_scope = _mapping(values.get("comparisonScope"))
    persisted_scope = _mapping(values.get("persistedComparisonScope"))
    execution = _mapping(values.get("ruleboxExecution"))
    model_signal_execution = _mapping(values.get("modelSignalBridgeExecution"))
    native_stage_values = _mapping(
        execution.get("typedbNativeStageTimings")
        or execution.get("nativeStageTimings")
    )
    stage_allowlist = {
        "totalMs", "graphAssemblyMs", "aboxPersistenceMs", "nativeInferenceMs",
        "inferenceDetailOutboxMs", "decisionEpisodeMs", "qualityRecordMs",
    }
    runtime_stages = {
        str(key): max(0, int(_rounded(value)))
        for key, value in runtime.items()
        if str(key) in stage_allowlist and isinstance(value, (int, float))
    }
    native_stage_timings = {
        str(key): max(0, int(_rounded(value)))
        for key, value in native_stage_values.items()
        if str(key) and isinstance(value, (int, float))
    }
    matched_rule_ids = _strings(
        list(execution.get("typedbNativeRuleMatchedRuleIds") or [])
        + list(execution.get("matchedRuleIds") or [])
        + list(model_signal_execution.get("matchedContractIds") or [])
        + [
            item.get("ruleId") or item.get("rule_id")
            for item in values.get("ruleEvaluations") or []
            if isinstance(item, Mapping)
            and (
                bool(item.get("matched"))
                or str(item.get("status") or "").lower() == "matched"
            )
        ]
    )
    return {
        "accountId": str(account_id or ""),
        "status": str(values.get("status") or ""),
        "saved": bool(values.get("saved")),
        "materialFingerprint": str(values.get("materialFingerprint") or ""),
        "comparisonScopeFingerprint": str(comparison_scope.get("fingerprint") or ""),
        "comparisonScopeCount": int(_rounded(comparison_scope.get("scopeCount"))),
        "comparisonScopeManifest": dict(comparison_scope.get("scopeManifest") or {}),
        "persistedComparisonScopeFingerprint": str(persisted_scope.get("fingerprint") or ""),
        "persistedComparisonScopeCount": int(_rounded(persisted_scope.get("scopeCount"))),
        "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
        "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
        "inferenceStatus": str(inference.get("status") or ""),
        "nativeInferenceOutcome": str(inference.get("nativeInferenceOutcome") or ""),
        "generationAligned": bool(inference.get("generationAligned")),
        "nativeTypeDbReasoningCompleted": bool(
            inference.get("nativeTypeDbReasoningCompleted")
            or inference.get("typedbNativeRuleEvaluationCompleted")
        ),
        "targetSymbols": list(_strings(inference.get("targetSymbols") or [])),
        "durationMs": int(_rounded(runtime.get("totalMs"))),
        "runtimeStages": runtime_stages,
        "nativeStageTimings": native_stage_timings,
        "nativeMatchedRuleCount": int(_rounded(
            execution.get("typedbNativeRuleMatchedCount")
            or inference.get("typedbNativeRuleMatchedCount")
            or len(matched_rule_ids)
        )),
        "nativeMatchedRuleIds": list(matched_rule_ids),
        "nativeExecutedRuleIds": list(_strings(
            execution.get("nativeRuleSelectionExecutedRuleIds") or []
        )),
        "ruleboxFingerprint": str(
            execution.get("ruleboxRulesHash")
            or execution.get("rulesHash")
            or execution.get("sourceRulesHash")
            or values.get("ruleboxFingerprint")
            or ""
        ),
        "nativeRulePreflight": {
            "status": str(execution.get("nativeRulePreflightStatus") or ""),
            "mode": str(execution.get("nativeRulePreflightMode") or ""),
            "reason": str(execution.get("nativeRulePreflightReason") or "")[:220],
            "sourceCount": int(_rounded(execution.get("nativeRulePreflightSourceCount"))),
            "loadedSourceCount": int(_rounded(
                execution.get("nativeRulePreflightLoadedSourceCount")
            )),
            "entityCount": int(_rounded(execution.get("nativeRulePreflightEntityCount"))),
            "relationCount": int(_rounded(execution.get("nativeRulePreflightRelationCount"))),
        },
    }


def engine_outcome_packet(
    deployment_id: str,
    events: Iterable[object],
    projections: Mapping[str, object],
    duration_ms: int,
    source_snapshot_ids: Mapping[str, object] = None,
    delivery_count: int = 0,
) -> Dict[str, object]:
    candidates = sorted(
        [graph_candidate_packet(event) for event in events or []],
        key=lambda item: (
            str(item.get("accountId") or ""),
            str(item.get("symbol") or ""),
            str(item.get("messageType") or ""),
            str(item.get("selectedRuleId") or ""),
        ),
    )
    receipts = sorted(
        [
            projection_receipt_packet(str(account_id or ""), _mapping(projection))
            for account_id, projection in dict(projections or {}).items()
        ],
        key=lambda item: str(item.get("accountId") or ""),
    )
    packet = {
        "contractVersion": REASONING_SHADOW_CONTRACT_VERSION,
        "deploymentId": str(deployment_id or ""),
        "durationMs": max(0, int(duration_ms or 0)),
        "deliveryCount": max(0, int(delivery_count or 0)),
        "sourceSnapshotIds": {
            str(key): str(value or "")
            for key, value in sorted(dict(source_snapshot_ids or {}).items())
            if str(key or "")
        },
        "candidates": candidates,
        "projections": receipts,
    }
    packet["outcomeHash"] = payload_hash({
        "candidates": candidates,
        "projections": [
            {
                key: item.get(key)
                for key in [
                    "accountId", "status", "materialFingerprint", "inferenceStatus",
                    "nativeInferenceOutcome", "generationAligned",
                    "nativeTypeDbReasoningCompleted", "targetSymbols",
                    "comparisonScopeFingerprint", "comparisonScopeCount",
                    "comparisonScopeManifest",
                    "persistedComparisonScopeFingerprint",
                    "persistedComparisonScopeCount",
                ]
            }
            for item in receipts
        ],
    })
    return packet


def independent_reasoning_outcome_packet(job: Mapping[str, object]) -> Dict[str, object]:
    """Adapt one durable independent V2 result to the shared comparison contract.

    The independent queue intentionally stores compact projection receipts. Its
    immutable source boundaries therefore own fact parity, while candidate and
    rule packets own decision parity. Graph-local ABox and inference IDs are
    provenance and must not create false differences between isolated stores.
    """

    values = _mapping(job)
    result = _mapping(values.get("result"))
    source_event = _mapping(values.get("sourceEvent"))
    source_payload = _mapping(source_event.get("payload"))
    boundaries = [
        _mapping(item)
        for item in values.get("sourceBoundaries") or []
        if isinstance(item, Mapping)
    ]
    if not boundaries and str(values.get("sourceSnapshotId") or ""):
        boundaries = [{
            "snapshotId": str(values.get("sourceSnapshotId") or ""),
            "generatedAt": str(values.get("sourceSnapshotAt") or ""),
        }]
    scope_manifest = {
        str(item.get("snapshotId") or "boundary:" + str(index)): payload_hash({
            "snapshotId": str(item.get("snapshotId") or ""),
            "generatedAt": str(item.get("generatedAt") or ""),
            "accountId": str(item.get("accountId") or ""),
            "symbols": list(_strings(item.get("symbols") or [])),
            "fingerprint": str(item.get("fingerprint") or ""),
        })
        for index, item in enumerate(boundaries)
    }
    source_event_id = str(values.get("comparisonSourceEventId") or values.get("sourceEventId") or "")
    if source_event_id:
        scope_manifest["source-event:" + source_event_id] = payload_hash({
            "sourceEventId": source_event_id,
            "sourceSnapshotId": str(values.get("sourceSnapshotId") or ""),
            "sourceSnapshotAt": str(values.get("sourceSnapshotAt") or ""),
            "sourcePayloadHash": str(values.get("sourcePayloadHash") or ""),
        })
    source_scope_fingerprint = payload_hash(scope_manifest)
    account_ids = _strings(
        source_payload.get("accountIds")
        or result.get("account_ids")
        or result.get("accountIds")
        or [
            item.get("accountId")
            for item in boundaries
            if item.get("accountId")
        ]
    )
    symbols = list(_strings(
        source_payload.get("affectedSymbols")
        or source_payload.get("symbols")
        or result.get("evaluated_symbols")
        or result.get("evaluatedSymbols")
        or result.get("symbols")
        or []
    ))
    projections = []
    for account_id, projection in sorted(
        _mapping(result.get("projection_results") or result.get("projectionResults")).items()
    ):
        receipt = projection_receipt_packet(str(account_id or ""), _mapping(projection))
        receipt.update({
            "comparisonScopeFingerprint": source_scope_fingerprint,
            "comparisonScopeCount": len(scope_manifest),
            "comparisonScopeManifest": dict(scope_manifest),
            "persistedComparisonScopeFingerprint": source_scope_fingerprint,
            "persistedComparisonScopeCount": len(scope_manifest),
            "materialFingerprint": source_scope_fingerprint,
            "targetSymbols": symbols,
        })
        projections.append(receipt)
    decision_syntheses = [
        _mapping(item)
        for item in result.get("decision_syntheses") or result.get("decisionSyntheses") or []
        if isinstance(item, Mapping)
    ]
    candidate_sources = (
        [
            {
                "accountId": _first_value(item, "account_id", "accountId"),
                "symbol": _first_value(item, "symbol"),
                "rule": "decisionSynthesis",
                "metadata": {"v2DecisionSynthesis": item},
            }
            for item in decision_syntheses
        ]
        if decision_syntheses
        else list(result.get("candidate_events") or result.get("candidateEvents") or [])
    )
    candidate_sources = [
        item
        for item in candidate_sources
        if isinstance(item, Mapping)
        and (
            not account_ids
            or str(
                _event_field(item, "account_id", "")
                or _first_value(
                    _mapping(_mapping(item).get("metadata")).get("v2DecisionSynthesis") or {},
                    "account_id",
                    "accountId",
                )
                or ""
            ) in account_ids
        )
        and (
            not symbols
            or str(
                _event_field(item, "symbol", "")
                or _first_value(
                    _mapping(_mapping(item).get("metadata")).get("v2DecisionSynthesis") or {},
                    "symbol",
                )
                or ""
            ).upper() in symbols
        )
    ]
    candidates = sorted(
        [graph_candidate_packet(event) for event in candidate_sources],
        key=lambda item: (
            str(item.get("accountId") or ""),
            str(item.get("symbol") or ""),
            str(item.get("messageType") or ""),
            str(item.get("selectedRuleId") or ""),
        ),
    )
    source_snapshot_id = str(values.get("sourceSnapshotId") or "")
    packet = {
        "contractVersion": REASONING_SHADOW_CONTRACT_VERSION,
        "deploymentId": str(values.get("deploymentId") or result.get("deployment_id") or ""),
        "durationMs": max(0, int(values.get("durationMs") or result.get("duration_ms") or 0)),
        "deliveryCount": len(
            result.get("delivery_events") or result.get("deliveryEvents") or []
        ) if bool(result.get("delivery_authorized") or result.get("deliveryAuthorized")) else 0,
        "sourceSnapshotIds": {
            account_id: source_snapshot_id for account_id in account_ids
        },
        "sourceScopeFingerprint": source_scope_fingerprint,
        "candidates": candidates,
        "projections": projections,
    }
    packet["outcomeHash"] = payload_hash({
        "candidates": candidates,
        "projections": [
            {
                key: item.get(key)
                for key in [
                    "accountId", "status", "materialFingerprint", "inferenceStatus",
                    "nativeInferenceOutcome", "generationAligned",
                    "nativeTypeDbReasoningCompleted", "targetSymbols",
                    "comparisonScopeFingerprint", "comparisonScopeCount",
                    "comparisonScopeManifest",
                ]
            }
            for item in projections
        ],
    })
    return packet


def _candidate_groups(outcome: Mapping[str, object]) -> Dict[str, Sequence[Dict[str, object]]]:
    grouped: Dict[str, list] = {}
    for item in _mapping(outcome).get("candidates") or []:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        key = "|".join([
            str(row.get("accountId") or ""),
            str(row.get("symbol") or ""),
            str(row.get("messageType") or ""),
        ])
        grouped.setdefault(key, []).append(row)
    return {
        key: sorted(rows, key=lambda row: (str(row.get("selectedRuleId") or ""), str(row.get("decisionSignature") or "")))
        for key, rows in grouped.items()
    }


def _projection_map(outcome: Mapping[str, object]) -> Dict[str, Dict[str, object]]:
    return {
        str(item.get("accountId") or ""): dict(item)
        for item in _mapping(outcome).get("projections") or []
        if isinstance(item, Mapping) and str(item.get("accountId") or "")
    }


def _coverage(baseline: Iterable[str], candidate: Iterable[str]) -> float:
    left, right = set(baseline or []), set(candidate or [])
    if not left and not right:
        return 100.0
    if not left:
        return 100.0
    return round(100.0 * len(left.intersection(right)) / len(left), 3)


def _market_class(symbol: object) -> str:
    value = str(symbol or "").upper().strip()
    if not value:
        return ""
    if value.isdigit() and len(value) == 6:
        return "KR-EQUITY"
    if value in {"BTC", "ETH", "SOL", "XRP"} or value.endswith(("-USD", "USDT")):
        return "CRYPTO"
    return "US-EQUITY"


def _percentile95(values: Iterable[object]) -> int:
    ordered = sorted(max(0, int(_rounded(value))) for value in values or [])
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, int(round(len(ordered) * 0.95 + 0.499)) - 1))
    return ordered[index]


def reasoning_comparison_summary(
    rows: Iterable[Mapping[str, object]],
    candidate_deployment_id: str = "",
    candidate_release_fingerprint: str = "",
    validation_cohort_id: str = "",
) -> Dict[str, object]:
    """Aggregate only one immutable validation cohort into promotion evidence."""

    all_items = [dict(row) for row in rows or [] if isinstance(row, Mapping)]
    warmup_items = [
        row
        for row in all_items
        if bool(_mapping(row.get("payload")).get("candidateWarmup"))
    ]
    items = [
        row
        for row in all_items
        if not bool(_mapping(row.get("payload")).get("candidateWarmup"))
    ]
    status_counts: Dict[str, int] = {}
    symbols, markets, actions, matched_rule_ids = set(), set(), set(), set()
    fact_values, rule_values, baseline_durations, candidate_durations, queue_waits = [], [], [], [], []
    candidate_end_to_end_durations = []
    baseline_stage_values: Dict[str, list] = {}
    candidate_stage_values: Dict[str, list] = {}
    unexplained = shadow_deliveries = nonempty_decisions = nonempty_native = decision_subjects = 0
    for row in items:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        fact_values.append(float(row.get("factParityPct") or 0.0))
        rule_values.append(float(row.get("ruleSlotCoveragePct") or 0.0))
        unexplained += int(row.get("unexplainedDecisionDifferenceCount") or 0)
        shadow_deliveries += int(row.get("shadowDeliveryCount") or 0)
        payload = _mapping(row.get("payload"))
        row_symbols = _strings(payload.get("symbols") or [])
        symbols.update(row_symbols)
        markets.update(
            str(value or "")
            for value in payload.get("marketClasses") or [_market_class(value) for value in row_symbols]
            if str(value or "")
        )
        actions.update(str(value or "") for value in payload.get("candidateActions") or [] if str(value or ""))
        matched_rule_ids.update(
            str(value or "") for value in payload.get("candidateMatchedRuleIds") or [] if str(value or "")
        )
        subject_count = int(payload.get("subjectCount") or 0)
        native_count = int(payload.get("candidateNativeMatchedRuleCount") or 0)
        decision_subjects += subject_count
        nonempty_decisions += int(subject_count > 0)
        nonempty_native += int(native_count > 0)
        baseline_durations.append(int(payload.get("baselineDurationMs") or 0))
        candidate_durations.append(int(payload.get("candidateDurationMs") or 0))
        queue_waits.append(int(payload.get("queueWaitMs") or 0))
        candidate_end_to_end_durations.append(
            int(payload.get("candidateDurationMs") or 0)
            + int(payload.get("queueWaitMs") or 0)
        )
        for stage, value in _mapping(payload.get("baselinePhaseDurationsMs")).items():
            baseline_stage_values.setdefault(str(stage), []).append(value)
        for stage, value in _mapping(payload.get("candidatePhaseDurationsMs")).items():
            candidate_stage_values.setdefault(str(stage), []).append(value)
    return {
        "candidateDeploymentId": str(candidate_deployment_id or ""),
        "candidateReleaseFingerprint": str(candidate_release_fingerprint or ""),
        "validationCohortId": str(validation_cohort_id or ""),
        "sampleCount": len(items),
        "warmupSampleCount": len(warmup_items),
        "statusCounts": status_counts,
        "equivalentCount": int(status_counts.get("equivalent") or 0),
        "equivalentPct": round(100.0 * int(status_counts.get("equivalent") or 0) / len(items), 3) if items else 0.0,
        "factParityPct": round(sum(fact_values) / len(fact_values), 3) if fact_values else 0.0,
        "minimumFactParityPct": min(fact_values) if fact_values else 0.0,
        "ruleSlotCoveragePct": round(sum(rule_values) / len(rule_values), 3) if rule_values else 0.0,
        "minimumRuleSlotCoveragePct": min(rule_values) if rule_values else 0.0,
        "unexplainedDecisionDifferenceCount": unexplained,
        "shadowDeliveryCount": shadow_deliveries,
        "distinctSymbolCount": len(symbols),
        "symbols": sorted(symbols),
        "marketClassCount": len(markets),
        "marketClasses": sorted(markets),
        "candidateActionCount": len(actions),
        "candidateActions": sorted(actions),
        "nonEmptyDecisionSampleCount": nonempty_decisions,
        "nonEmptyNativeInferenceSampleCount": nonempty_native,
        "decisionSubjectCount": decision_subjects,
        "distinctMatchedRuleCount": len(matched_rule_ids),
        "matchedRuleIds": sorted(matched_rule_ids),
        "baselineP95DurationMs": _percentile95(baseline_durations),
        "candidateP95DurationMs": _percentile95(candidate_durations),
        "queueWaitP95Ms": _percentile95(queue_waits),
        "candidateEndToEndP95Ms": _percentile95(candidate_end_to_end_durations),
        "baselinePhaseP95Ms": {key: _percentile95(values) for key, values in sorted(baseline_stage_values.items())},
        "candidatePhaseP95Ms": {key: _percentile95(values) for key, values in sorted(candidate_stage_values.items())},
        "latestComparisonAt": str(items[0].get("createdAt") or "") if items else "",
    }


@dataclass(frozen=True)
class ReasoningComparison:
    status: str
    fact_parity_pct: float
    rule_slot_coverage_pct: float
    evidence_parity_pct: float
    decision_difference_count: int
    unexplained_decision_difference_count: int
    shadow_delivery_count: int
    payload: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "factParityPct": self.fact_parity_pct,
            "ruleSlotCoveragePct": self.rule_slot_coverage_pct,
            "evidenceParityPct": self.evidence_parity_pct,
            "decisionDifferenceCount": self.decision_difference_count,
            "unexplainedDecisionDifferenceCount": self.unexplained_decision_difference_count,
            "shadowDeliveryCount": self.shadow_delivery_count,
            **dict(self.payload or {}),
        }


def compare_engine_outcomes(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    temporal_comparisons: Iterable[Mapping[str, object]] = None,
) -> ReasoningComparison:
    baseline_groups = _candidate_groups(baseline)
    candidate_groups = _candidate_groups(candidate)
    keys = sorted(set(baseline_groups) | set(candidate_groups))
    differences = []
    baseline_rule_slots, candidate_rule_slots = [], []
    baseline_decision_rule_ids, candidate_decision_rule_ids = [], []
    baseline_evidence, candidate_evidence = [], []
    for key in keys:
        left = list(baseline_groups.get(key) or [])
        right = list(candidate_groups.get(key) or [])
        left_signatures = [str(item.get("decisionSignature") or "") for item in left]
        right_signatures = [str(item.get("decisionSignature") or "") for item in right]
        baseline_rule_slots.extend(
            "relation:" + str(slot)
            for item in left for slot in item.get("relationSlots") or []
        )
        candidate_rule_slots.extend(
            "relation:" + str(slot)
            for item in right for slot in item.get("relationSlots") or []
        )
        baseline_decision_rule_ids.extend(
            str(rule_id)
            for item in left for rule_id in item.get("ruleIds") or []
            if str(rule_id or "")
        )
        candidate_decision_rule_ids.extend(
            str(rule_id)
            for item in right for rule_id in item.get("ruleIds") or []
            if str(rule_id or "")
        )
        baseline_evidence.extend(
            str(item.get("evidenceSignature") or "") for item in left
        )
        candidate_evidence.extend(
            str(item.get("evidenceSignature") or "") for item in right
        )
        if left_signatures != right_signatures:
            differences.append({
                "subjectKey": key,
                "baseline": [
                    {
                        field: item.get(field)
                        for field in [
                            "candidateAction", "selectedRuleId", "decisionStage",
                            "decisionEffect", "judgementBlocked", "decisionSignature",
                        ]
                    }
                    for item in left
                ],
                "candidate": [
                    {
                        field: item.get(field)
                        for field in [
                            "candidateAction", "selectedRuleId", "decisionStage",
                            "decisionEffect", "judgementBlocked", "decisionSignature",
                        ]
                    }
                    for item in right
                ],
            })

    temporal_rows = [dict(item) for item in temporal_comparisons or [] if isinstance(item, Mapping)]
    temporal_equal = [str(item.get("status") or "") == "equivalent" for item in temporal_rows]
    baseline_projections = _projection_map(baseline)
    candidate_projections = _projection_map(candidate)
    projection_accounts = sorted(set(baseline_projections) | set(candidate_projections))
    projection_equal = []
    projection_differences = []
    for account_id in projection_accounts:
        left = baseline_projections.get(account_id) or {}
        right = candidate_projections.get(account_id) or {}
        baseline_scope_fingerprint = str(left.get("comparisonScopeFingerprint") or "")
        candidate_scope_fingerprint = str(right.get("comparisonScopeFingerprint") or "")
        use_scope_fingerprint = bool(
            baseline_scope_fingerprint and candidate_scope_fingerprint
        )
        baseline_comparable = (
            baseline_scope_fingerprint
            if use_scope_fingerprint
            else str(left.get("materialFingerprint") or "")
        )
        candidate_comparable = (
            candidate_scope_fingerprint
            if use_scope_fingerprint
            else str(right.get("materialFingerprint") or "")
        )
        equal = bool(left and right and baseline_comparable and baseline_comparable == candidate_comparable)
        projection_equal.append(equal)
        if not equal:
            baseline_scope_manifest = _mapping(left.get("comparisonScopeManifest"))
            candidate_scope_manifest = _mapping(right.get("comparisonScopeManifest"))
            changed_scope_ids = sorted(
                scope_id
                for scope_id in set(baseline_scope_manifest).intersection(
                    candidate_scope_manifest
                )
                if str(baseline_scope_manifest.get(scope_id) or "")
                != str(candidate_scope_manifest.get(scope_id) or "")
            )
            projection_differences.append({
                "accountId": account_id,
                "baselineMaterialFingerprint": str(left.get("materialFingerprint") or ""),
                "candidateMaterialFingerprint": str(right.get("materialFingerprint") or ""),
                "baselineComparisonScopeFingerprint": baseline_scope_fingerprint,
                "candidateComparisonScopeFingerprint": candidate_scope_fingerprint,
                "baselineComparisonScopeCount": int(left.get("comparisonScopeCount") or 0),
                "candidateComparisonScopeCount": int(right.get("comparisonScopeCount") or 0),
                "baselineOnlyScopeIds": sorted(
                    set(_mapping(left.get("comparisonScopeManifest")))
                    - set(_mapping(right.get("comparisonScopeManifest")))
                )[:50],
                "candidateOnlyScopeIds": sorted(
                    set(_mapping(right.get("comparisonScopeManifest")))
                    - set(_mapping(left.get("comparisonScopeManifest")))
                )[:50],
                "changedScopes": [
                    {
                        "scopeId": scope_id,
                        "baselineFingerprint": str(
                            baseline_scope_manifest.get(scope_id) or ""
                        ),
                        "candidateFingerprint": str(
                            candidate_scope_manifest.get(scope_id) or ""
                        ),
                    }
                    for scope_id in changed_scope_ids[:50]
                ],
                "baselinePersistedComparisonScopeFingerprint": str(
                    left.get("persistedComparisonScopeFingerprint") or ""
                ),
                "candidatePersistedComparisonScopeFingerprint": str(
                    right.get("persistedComparisonScopeFingerprint") or ""
                ),
                "baselinePersistedComparisonScopeCount": int(
                    left.get("persistedComparisonScopeCount") or 0
                ),
                "candidatePersistedComparisonScopeCount": int(
                    right.get("persistedComparisonScopeCount") or 0
                ),
                "baselineStatus": str(left.get("status") or ""),
                "candidateStatus": str(right.get("status") or ""),
            })
    parity_checks = temporal_equal + projection_equal
    fact_parity = round(100.0 * sum(1 for value in parity_checks if value) / len(parity_checks), 3) if parity_checks else 0.0
    baseline_rule_slots.extend(
        "rule:" + rule_id for rule_id in baseline_decision_rule_ids
    )
    candidate_rule_slots.extend(
        "rule:" + rule_id for rule_id in candidate_decision_rule_ids
    )
    rule_coverage = _coverage(baseline_rule_slots, candidate_rule_slots)
    evidence_parity = _coverage(baseline_evidence, candidate_evidence)
    unexplained = (
        len(differences)
        if fact_parity == 100.0
        and rule_coverage == 100.0
        and evidence_parity == 100.0
        else 0
    )
    shadow_delivery_count = int(_mapping(candidate).get("deliveryCount") or 0)
    candidate_projection_ready = bool(candidate_projections) and all(
        bool(item.get("nativeTypeDbReasoningCompleted")) and bool(item.get("generationAligned"))
        for item in candidate_projections.values()
    )
    if not candidate_projection_ready:
        status = "candidate-failed"
    elif shadow_delivery_count:
        status = "delivery-violation"
    elif differences and fact_parity < 100.0:
        status = "explained-input-difference"
    elif fact_parity < 100.0:
        status = "input-parity-gap"
    elif rule_coverage < 100.0 or evidence_parity < 100.0:
        status = "reasoning-parity-gap"
    elif unexplained:
        status = "unexplained-difference"
    else:
        status = "equivalent"
    symbols = _strings(
        [item.get("symbol") for rows in baseline_groups.values() for item in rows]
        + [item.get("symbol") for rows in candidate_groups.values() for item in rows]
        + [
            symbol
            for projection in list(baseline_projections.values()) + list(candidate_projections.values())
            for symbol in projection.get("targetSymbols") or []
        ]
    )
    baseline_native_rule_ids = _strings(
        rule_id
        for projection in baseline_projections.values()
        for rule_id in projection.get("nativeMatchedRuleIds") or []
    )
    candidate_native_rule_ids = _strings(
        rule_id
        for projection in candidate_projections.values()
        for rule_id in projection.get("nativeMatchedRuleIds") or []
    )
    baseline_native_count = sum(
        int(projection.get("nativeMatchedRuleCount") or 0)
        for projection in baseline_projections.values()
    )
    candidate_native_count = sum(
        int(projection.get("nativeMatchedRuleCount") or 0)
        for projection in candidate_projections.values()
    )
    candidate_actions = _strings(
        item.get("candidateAction")
        for rows in candidate_groups.values()
        for item in rows
    )
    baseline_decision_rule_id_set = set(baseline_decision_rule_ids)
    candidate_decision_rule_id_set = set(candidate_decision_rule_ids)

    def phase_totals(projections: Mapping[str, Mapping[str, object]]) -> Dict[str, int]:
        totals: Dict[str, int] = {}
        for projection in projections.values():
            for prefix, field in (("projection.", "runtimeStages"), ("typedb.", "nativeStageTimings")):
                for stage, value in _mapping(projection.get(field)).items():
                    key = prefix + str(stage)
                    totals[key] = totals.get(key, 0) + max(0, int(_rounded(value)))
        return totals

    payload = {
        "contractVersion": REASONING_SHADOW_CONTRACT_VERSION,
        "baselineDeploymentId": str(_mapping(baseline).get("deploymentId") or ""),
        "candidateDeploymentId": str(_mapping(candidate).get("deploymentId") or ""),
        "baselineDurationMs": int(_mapping(baseline).get("durationMs") or 0),
        "candidateDurationMs": int(_mapping(candidate).get("durationMs") or 0),
        "symbols": list(symbols),
        "subjectCount": len(keys),
        "nonEmptyDecision": bool(keys),
        "baselineNativeMatchedRuleCount": baseline_native_count,
        "candidateNativeMatchedRuleCount": candidate_native_count,
        "nonEmptyNativeInference": candidate_native_count > 0,
        "baselineMatchedRuleIds": list(baseline_native_rule_ids),
        "candidateMatchedRuleIds": list(candidate_native_rule_ids),
        "baselineDecisionRuleIds": sorted(baseline_decision_rule_id_set),
        "candidateDecisionRuleIds": sorted(candidate_decision_rule_id_set),
        "baselineOnlyDecisionRuleIds": sorted(
            baseline_decision_rule_id_set - candidate_decision_rule_id_set
        ),
        "candidateOnlyDecisionRuleIds": sorted(
            candidate_decision_rule_id_set - baseline_decision_rule_id_set
        ),
        "candidateActions": list(candidate_actions),
        "marketClasses": sorted({_market_class(symbol) for symbol in symbols if _market_class(symbol)}),
        "baselinePhaseDurationsMs": phase_totals(baseline_projections),
        "candidatePhaseDurationsMs": phase_totals(candidate_projections),
        "decisionDifferences": differences,
        "projectionDifferences": projection_differences,
        "temporalComparisons": temporal_rows,
        "baselineOutcomeHash": str(_mapping(baseline).get("outcomeHash") or ""),
        "candidateOutcomeHash": str(_mapping(candidate).get("outcomeHash") or ""),
    }
    return ReasoningComparison(
        status=status,
        fact_parity_pct=fact_parity,
        rule_slot_coverage_pct=rule_coverage,
        evidence_parity_pct=evidence_parity,
        decision_difference_count=len(differences),
        unexplained_decision_difference_count=unexplained,
        shadow_delivery_count=shadow_delivery_count,
        payload=payload,
    )
