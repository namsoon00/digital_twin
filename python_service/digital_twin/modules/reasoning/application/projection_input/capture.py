"""Capture bounded source observations and a secret-free replay context."""

from __future__ import annotations

from dataclasses import replace
from digital_twin.domain.ontology_projection_input import (
    compact_external_signals_for_ontology,
)
from digital_twin.domain.ontology_projection_input import projection_input_summary
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.reasoning_shadow import frozen_projection_runtime_context
from digital_twin.domain.reasoning_shadow import pack_projection_runtime_contexts
import time
from .ports import CaptureInputs, PreparedGraphInput


def capture_graph_input(
    _inputs: CaptureInputs,
    snapshot: AccountSnapshot,
    target_symbols,
    target_scoped_input,
    reasoning_context,
    stage_timings,
    emit,
) -> PreparedGraphInput:
    emit("observation_input.start")
    observation_input = snapshot.projection_observation_input(
        target_symbols if target_scoped_input else None
    )
    input_mode = str(observation_input.get("mode") or "full")
    input_symbols = list(observation_input.get("targetSymbols") or [])
    emit(
        "observation_input.done",
        inputMode=input_mode,
        targetSymbolCount=len(input_symbols),
    )
    # TypeDB rules consume facts, research claims, and bounded summaries.
    # The full provider archive stays on the monitor snapshot for the
    # research/notification read models and is never copied into this live
    # ABox assembly path.
    emit("external_signal_compaction.start")
    projection_external_signals = compact_external_signals_for_ontology(
        snapshot.external_signals,
        target_symbols=input_symbols if input_mode == "target-scoped" else None,
        settings=_inputs.settings,
    )
    input_projection = projection_input_summary(
        snapshot.external_signals,
        projection_external_signals,
        target_symbols=input_symbols if input_mode == "target-scoped" else [],
    )
    emit(
        "external_signal_compaction.done",
        retainedBytes=int(input_projection.get("projectedExternalSignalBytes") or 0),
        sourceBytes=int(input_projection.get("sourceExternalSignalBytes") or 0),
    )
    graph_input_snapshot = replace(
        snapshot, external_signals=projection_external_signals
    )
    emit("active_tbox.start")
    active_tbox_started = time.perf_counter()
    active_tbox = _inputs.active_tbox_context()
    stage_timings["activeTBoxReadMs"] = int(
        (time.perf_counter() - active_tbox_started) * 1000
    )
    emit(
        "active_tbox.done",
        runtimeMs=stage_timings["activeTBoxReadMs"],
        status=str(active_tbox.get("status") or ""),
    )
    emit("runtime_context.start")
    runtime_context_started = time.perf_counter()
    runtime_context = _inputs.runtime_context(
        snapshot,
        active_tbox=active_tbox,
        target_symbols=input_symbols if input_mode == "target-scoped" else None,
        progress_callback=lambda stage, **details: emit(
            "runtime_context." + str(stage or "unknown"), **details
        ),
    )
    source_facts = [
        dict(item)
        for item in (reasoning_context or {}).get("sourceFacts") or []
        if isinstance(item, dict)
    ]
    if source_facts:
        runtime_context = {
            **dict(runtime_context or {}),
            "reasoningSourceFacts": source_facts,
            "semanticChangeSet": dict(
                (reasoning_context or {}).get("semanticChangeSet") or {}
            ),
        }
        _inputs.last_runtime_contexts[snapshot.account_id] = (
            frozen_projection_runtime_context(runtime_context)
        )
    try:
        runtime_context_packet = pack_projection_runtime_contexts(
            {
                snapshot.account_id: _inputs.last_runtime_contexts.get(
                    snapshot.account_id
                )
                or frozen_projection_runtime_context(runtime_context),
            }
        )
    except ValueError:
        # Graph assembly remains decision-critical. An oversized optional
        # shadow replay packet may skip V2 sampling, but must never block
        # the active V1 projection.
        runtime_context_packet = {}
    stage_timings["runtimeContextMs"] = int(
        (time.perf_counter() - runtime_context_started) * 1000
    )
    runtime_context_cache = dict(
        _inputs.last_runtime_context_cache_status.get(str(snapshot.account_id or ""))
        or {}
    )
    stage_timings["runtimeContextCacheHit"] = (
        1 if str(runtime_context_cache.get("status") or "") == "hit" else 0
    )
    stage_timings["runtimeContextCacheAgeMs"] = int(
        runtime_context_cache.get("ageMs") or 0
    )
    emit("runtime_context.done", runtimeMs=stage_timings["runtimeContextMs"])
    decision_memory = (
        runtime_context.get("decisionEpisodeProjection")
        if isinstance(runtime_context, dict)
        else {}
    )
    if isinstance(decision_memory, dict):
        stage_timings["decisionEpisodeSourceCount"] = int(
            decision_memory.get("sourceEpisodeCount") or 0
        )
        stage_timings["decisionEpisodeIncludedCount"] = int(
            decision_memory.get("includedEpisodeCount") or 0
        )
        stage_timings["decisionEpisodeDroppedCount"] = int(
            decision_memory.get("droppedEpisodeCount") or 0
        )
    lifecycle_projection = (
        runtime_context.get("hypothesisLifecycleAboxProjection")
        if isinstance(runtime_context, dict)
        else {}
    )
    if isinstance(lifecycle_projection, dict):
        stage_timings["hypothesisLifecycleAboxProjectionMs"] = int(
            lifecycle_projection.get("readMs") or 0
        )
        stage_timings["hypothesisLifecycleAboxRecordCount"] = int(
            lifecycle_projection.get("recordCount") or 0
        )
        stage_timings["hypothesisLifecycleAboxProjectionEnabled"] = (
            1 if lifecycle_projection.get("enabled") else 0
        )

    return PreparedGraphInput(
        observation_input=observation_input,
        input_mode=input_mode,
        input_symbols=input_symbols,
        projection_external_signals=projection_external_signals,
        input_projection=input_projection,
        graph_input_snapshot=graph_input_snapshot,
        active_tbox=active_tbox,
        runtime_context=runtime_context,
        runtime_context_packet=runtime_context_packet,
    )
