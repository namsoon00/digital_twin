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


REASONING_SHADOW_CONTRACT_VERSION = "reasoning-shadow-comparison-v1"
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


def _event_field(event: object, name: str, fallback: object = "") -> object:
    if isinstance(event, Mapping):
        return event.get(name, fallback)
    return getattr(event, name, fallback)


def graph_candidate_packet(event: object) -> Dict[str, object]:
    """Return only TypeDB-owned decision semantics from one alert candidate."""

    metadata = _mapping(_event_field(event, "metadata", {}))
    context = _mapping(metadata.get("ontologyRelationContext"))
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
    )
    selected_rule_id = str(
        decision.get("selectedRuleId")
        or envelope.get("selectedRuleId")
        or ""
    ).strip()
    candidate_action = str(
        decision.get("action")
        or decision.get("candidateAction")
        or envelope.get("candidateAction")
        or envelope.get("selectedDecisionEffect")
        or ""
    ).strip()
    packet = {
        "accountId": str(_event_field(event, "account_id", "") or ""),
        "symbol": str(_event_field(event, "symbol", "") or "").upper().strip(),
        "messageType": str(_event_field(event, "rule", "") or ""),
        "candidateAction": candidate_action,
        "selectedRuleId": selected_rule_id,
        "decisionStage": str(decision.get("decisionStage") or "").strip(),
        "decisionEffect": str(
            decision.get("decisionEffect")
            or envelope.get("selectedDecisionEffect")
            or ""
        ).strip(),
        "actionGroup": str(decision.get("actionGroup") or "").strip(),
        "judgementBlocked": bool(decision.get("judgementBlocked")),
        "reviewLevel": str(state.get("reviewLevel") or context.get("reviewLevel") or "").strip(),
        "dataState": str(state.get("dataState") or context.get("dataState") or "").strip(),
        "validationState": str(state.get("validationState") or context.get("validationState") or "").strip(),
        "confidence": _rounded(context.get("confidence") or context.get("confidenceScore")),
        "ruleIds": list(rule_ids),
        "relationSlots": relation_slots,
        "evidenceIds": list(evidence_ids),
        "factsHash": payload_hash(_mapping(context.get("facts"))),
        "graphStore": str(context.get("graphStore") or ""),
        "graphStoreUsed": bool(context.get("graphStoreUsed")),
    }
    packet["decisionSignature"] = payload_hash({
        key: packet[key]
        for key in [
            "candidateAction", "selectedRuleId", "decisionStage", "decisionEffect",
            "actionGroup", "judgementBlocked", "reviewLevel", "dataState",
            "validationState", "ruleIds", "relationSlots",
        ]
    })
    packet["evidenceSignature"] = payload_hash({
        "evidenceIds": packet["evidenceIds"],
        "factsHash": packet["factsHash"],
    })
    return packet


def projection_receipt_packet(account_id: str, projection: Mapping[str, object]) -> Dict[str, object]:
    values = _mapping(projection)
    inference = _mapping(values.get("inferenceBox"))
    runtime = _mapping(values.get("runtimeStages"))
    comparison_scope = _mapping(values.get("comparisonScope"))
    persisted_scope = _mapping(values.get("persistedComparisonScope"))
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
    baseline_evidence, candidate_evidence = [], []
    for key in keys:
        left = list(baseline_groups.get(key) or [])
        right = list(candidate_groups.get(key) or [])
        left_signatures = [str(item.get("decisionSignature") or "") for item in left]
        right_signatures = [str(item.get("decisionSignature") or "") for item in right]
        baseline_rule_slots.extend(
            slot for item in left for slot in item.get("relationSlots") or []
        )
        candidate_rule_slots.extend(
            slot for item in right for slot in item.get("relationSlots") or []
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
    rule_coverage = _coverage(baseline_rule_slots, candidate_rule_slots)
    evidence_parity = _coverage(baseline_evidence, candidate_evidence)
    unexplained = len(differences) if fact_parity == 100.0 else 0
    shadow_delivery_count = int(_mapping(candidate).get("deliveryCount") or 0)
    candidate_projection_ready = bool(candidate_projections) and all(
        bool(item.get("nativeTypeDbReasoningCompleted")) and bool(item.get("generationAligned"))
        for item in candidate_projections.values()
    )
    if not candidate_projection_ready:
        status = "candidate-failed"
    elif shadow_delivery_count:
        status = "delivery-violation"
    elif unexplained:
        status = "unexplained-difference"
    elif differences:
        status = "explained-input-difference"
    elif fact_parity < 100.0:
        status = "input-parity-gap"
    elif rule_coverage < 100.0 or evidence_parity < 100.0:
        status = "reasoning-parity-gap"
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
    payload = {
        "contractVersion": REASONING_SHADOW_CONTRACT_VERSION,
        "baselineDeploymentId": str(_mapping(baseline).get("deploymentId") or ""),
        "candidateDeploymentId": str(_mapping(candidate).get("deploymentId") or ""),
        "baselineDurationMs": int(_mapping(baseline).get("durationMs") or 0),
        "candidateDurationMs": int(_mapping(candidate).get("durationMs") or 0),
        "symbols": list(symbols),
        "subjectCount": len(keys),
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
