"""Governed model evidence enrichment; never selects an investment action."""

from __future__ import annotations

from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rulebox_catalog import governed_graph_inference_rules
from digital_twin.domain.ontology_schema import abox_lifecycle_metadata
from digital_twin.domain.ontology_schema import apply_abox_lifecycle
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.portfolio_ontology_outputs import dedupe_entities
from digital_twin.domain.portfolio_ontology_outputs import dedupe_relations
from digital_twin.domain.portfolio_ontology_statistical_concepts import (
    add_position_statistical_signal_concepts,
)
from digital_twin.domain.reasoning_shadow import frozen_projection_runtime_context
from digital_twin.domain.reasoning_shadow import pack_projection_runtime_contexts
from typing import Mapping
import time
from .ports import ModelEvidenceInputs, PreparedGraphInput
from digital_twin.modules.reasoning.domain.projection_facts import rule_id_from_payload


def rule_catalog_requires_statistical_signal_scoring(
    rule_catalog: Mapping[str, object] = None,
) -> bool:
    """Return whether this world phase owns market-model scoring.

    An omitted catalog keeps the compatibility path enabled. A partitioned
    account overlay has an explicit catalog without ``HAS_MODEL_SIGNAL`` and
    consumes shared premises instead of scoring the market ABox again.
    """

    catalog = dict(rule_catalog or {})
    relation_types = {
        str(value or "").upper().strip()
        for value in catalog.get("inputRelationTypes") or []
        if str(value or "").strip()
    }
    has_contract = bool(catalog.get("rules") or relation_types)
    return not has_contract or "HAS_MODEL_SIGNAL" in relation_types


def governed_statistical_rules_for_catalog(
    rule_catalog: Mapping[str, object] = None,
):
    """Keep model-contract scoring inside the active world RuleBox boundary."""

    catalog = dict(rule_catalog or {})
    active_rule_ids = {
        rule_id_from_payload(item)
        for item in catalog.get("rules") or []
        if isinstance(item, dict) and item.get("enabled") is not False
    }
    rules = governed_graph_inference_rules()
    if not active_rule_ids:
        return rules
    return tuple(rule for rule in rules if rule.rule_id in active_rule_ids)


def attach_model_evidence(
    _inputs: ModelEvidenceInputs,
    prepared: PreparedGraphInput,
    snapshot: AccountSnapshot,
    graph: PortfolioOntology,
    rule_catalog,
    stage_timings,
    emit,
):
    input_symbols = prepared.input_symbols
    runtime_context = prepared.runtime_context
    runtime_context_packet = prepared.runtime_context_packet
    active_tbox = prepared.active_tbox
    statistical_scoring_required = bool(
        _inputs.statistical_signal_service
        and rule_catalog_requires_statistical_signal_scoring(rule_catalog)
    )
    stage_timings["statisticalSignalScoringRequired"] = (
        1 if statistical_scoring_required else 0
    )
    if statistical_scoring_required:
        emit("statistical_signals.start", symbolCount=len(input_symbols))
        signal_started = time.perf_counter()
        statistical_signal_context = {}
        statistical_result = {}
        try:
            statistical_rules = governed_statistical_rules_for_catalog(rule_catalog)
            statistical_result = _inputs.statistical_signal_service.run(
                account_id=snapshot.account_id,
                backend_id=str(
                    _inputs.settings.get("_reasoningTimeSeriesBackendId")
                    or _inputs.settings.get("timeSeriesActiveBackendId")
                    or "market-time-series"
                ),
                windows=runtime_context.get("temporalObservationWindows") or {},
                as_of=str(snapshot.generated_at or runtime_context.get("asOf") or ""),
                source_event_id=str(
                    ((runtime_context.get("metadata") or {}).get("sourceEventId") or "")
                    if isinstance(runtime_context.get("metadata"), dict)
                    else ""
                ),
                graph=graph,
                rules=statistical_rules,
            )
            feature_snapshot = statistical_result.get("featureSnapshot")
            signal_snapshot = statistical_result.get("signalSnapshot")
            signal_bundle = statistical_result.get("signalBundle")
            statistical_signal_context = {
                "temporalFeatureSnapshot": (
                    feature_snapshot.to_dict(include_windows=False)
                    if hasattr(feature_snapshot, "to_dict")
                    else {}
                ),
                "statisticalSignalSnapshot": (
                    signal_bundle.to_dict()
                    if hasattr(signal_bundle, "to_dict")
                    else (
                        signal_snapshot.to_dict()
                        if hasattr(signal_snapshot, "to_dict")
                        else {}
                    )
                ),
                "statisticalSignalPipeline": {
                    "status": str(statistical_result.get("status") or ""),
                    "decisionEligible": bool(
                        statistical_result.get("decisionEligible")
                    ),
                    "diagnosticReady": bool(statistical_result.get("diagnosticReady")),
                    "decisionBlockers": list(
                        statistical_result.get("decisionBlockers") or []
                    ),
                    "timings": dict(statistical_result.get("timings") or {}),
                    "persistence": dict(statistical_result.get("persistence") or {}),
                    "pointInTime": dict(statistical_result.get("pointInTime") or {}),
                    "skippedModelReleaseIds": list(
                        statistical_result.get("skippedModelReleaseIds") or []
                    ),
                    "activatedPredictiveRuleCount": len(statistical_rules),
                    "assessmentCount": int(
                        getattr(signal_bundle, "assessments", ())
                        and len(signal_bundle.assessments)
                        or 0
                    ),
                },
            }
        except (
            Exception
        ) as error:  # noqa: BLE001 - fail closed: no model contract, no predictive rule.
            statistical_signal_context = {
                "statisticalSignalPipeline": {
                    "status": "error",
                    "reason": str(error)[:300],
                },
            }
        runtime_context = {
            **dict(runtime_context or {}),
            **statistical_signal_context,
        }
        if bool(
            statistical_result.get("decisionEligible")
            or statistical_result.get("diagnosticReady")
        ):
            stock_entities = [item for item in graph.entities if item.kind == "stock"]
            for stock in stock_entities:
                symbol = (
                    str((stock.properties or {}).get("symbol") or "").upper().strip()
                )
                if symbol:
                    add_position_statistical_signal_concepts(
                        graph,
                        stock.entity_id,
                        symbol,
                        runtime_context,
                    )
        graph.entities = dedupe_entities(graph.entities)
        graph.relations = dedupe_relations(graph.relations)
        apply_abox_lifecycle(
            graph,
            abox_lifecycle_metadata(
                graph.portfolio_id,
                runtime_context,
                active_tbox,
            ),
        )
        frozen_context = frozen_projection_runtime_context(runtime_context)
        _inputs.last_runtime_contexts[snapshot.account_id] = frozen_context
        try:
            runtime_context_packet = pack_projection_runtime_contexts(
                {
                    snapshot.account_id: frozen_context,
                }
            )
        except ValueError:
            runtime_context_packet = {}
        stage_timings["statisticalSignalPipelineMs"] = int(
            (time.perf_counter() - signal_started) * 1000
        )
        emit(
            "statistical_signals.done",
            runtimeMs=stage_timings["statisticalSignalPipelineMs"],
            status=str(
                statistical_signal_context.get("statisticalSignalPipeline", {}).get(
                    "status"
                )
                or "unavailable"
            ),
            signalCount=int(
                statistical_signal_context.get("statisticalSignalSnapshot", {}).get(
                    "signalCount"
                )
                or 0
            ),
        )
    elif _inputs.statistical_signal_service:
        runtime_context = {
            **dict(runtime_context or {}),
            "statisticalSignalPipeline": {
                "status": "not-required-account-overlay",
                "reason": (
                    "The PortfolioWorld consumes verified shared-premise references; "
                    "market model contracts are scored once in SharedPremiseWorld."
                ),
            },
        }
        frozen_context = frozen_projection_runtime_context(runtime_context)
        _inputs.last_runtime_contexts[snapshot.account_id] = frozen_context
        try:
            runtime_context_packet = pack_projection_runtime_contexts(
                {
                    snapshot.account_id: frozen_context,
                }
            )
        except ValueError:
            runtime_context_packet = {}

    return runtime_context_packet
