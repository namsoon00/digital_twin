"""Ordered input, factual graph, evidence and cache stages; no TypeDB writes."""

from __future__ import annotations

from copy import deepcopy
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Dict
import time
from .ports import AssemblyInputs
from .capture import capture_graph_input
from .cache_flow import load_cached_assembly, store_cached_assembly
from .model_evidence import attach_model_evidence
from digital_twin.modules.reasoning.domain.projection_facts import (
    build_factual_graph,
    verify_calibration_lineage,
)


def build_graph_assembly(
    _inputs: AssemblyInputs,
    snapshot: AccountSnapshot,
    rule_catalog: Dict[str, object],
    target_symbols=None,
    target_scoped_input=False,
    progress_callback=None,
    reasoning_context=None,
) -> tuple:
    """Build or safely clone the immutable pre-identity ABox graph pair."""
    stage_timings: Dict[str, int] = {}

    def emit(stage: str, **details) -> None:
        if not callable(progress_callback):
            return
        try:
            progress_callback("graph_assembly." + str(stage or "unknown"), **details)
        except Exception:
            return

    prepared = capture_graph_input(
        _inputs.capture,
        snapshot,
        target_symbols,
        target_scoped_input,
        reasoning_context,
        stage_timings,
        emit,
    )
    graph_input_snapshot = prepared.graph_input_snapshot
    active_tbox = prepared.active_tbox
    runtime_context = prepared.runtime_context
    input_symbols = prepared.input_symbols
    input_mode = prepared.input_mode
    observation_input = prepared.observation_input
    input_projection = prepared.input_projection
    cache_enabled = _inputs.graph_assembly_cache_enabled()
    emit("cache_key.start")
    cache_key = (
        _inputs.graph_assembly_cache_key(
            graph_input_snapshot,
            rule_catalog,
            active_tbox,
            runtime_context,
            target_symbols=input_symbols,
            input_mode=input_mode,
        )
        if cache_enabled
        else ""
    )
    emit("cache_key.done", enabled=cache_enabled)

    cached = load_cached_assembly(
        _inputs.cache, prepared, snapshot, cache_enabled, cache_key, stage_timings, emit
    )
    if cached is not None:
        return cached
    emit("ontology_graph.start")
    assembly_started = time.perf_counter()

    graph = build_factual_graph(
        snapshot,
        observation_input,
        prepared.projection_external_signals,
        runtime_context,
        active_tbox,
        stage_timings,
    )
    runtime_context_packet = attach_model_evidence(
        _inputs.model, prepared, snapshot, graph, rule_catalog, stage_timings, emit
    )
    emit(
        "ontology_graph.done",
        runtimeMs=int((time.perf_counter() - assembly_started) * 1000),
    )
    emit("persistence_graph.start")
    persistence_graph = _inputs.graph_for_graph_store_persistence(graph, rule_catalog)

    verify_calibration_lineage(graph, persistence_graph, stage_timings)
    persistence_graph.worldview["activeTBox"] = deepcopy(active_tbox)
    stage_timings["ontologyGraphAssemblyMs"] = int(
        (time.perf_counter() - assembly_started) * 1000
    )
    emit("persistence_graph.done", runtimeMs=stage_timings["ontologyGraphAssemblyMs"])

    store_cached_assembly(
        _inputs.cache,
        cache_enabled,
        cache_key,
        graph,
        persistence_graph,
        runtime_context_packet,
        stage_timings,
        emit,
    )
    return (
        graph,
        persistence_graph,
        {
            "status": "miss" if cache_enabled else "disabled",
            "cacheLayer": "none",
            "inputMode": input_mode,
            "targetSymbols": input_symbols,
            "sourcePositionCount": len(observation_input.get("positions") or []),
            "referencePositionCount": len(
                observation_input.get("referencePositions") or []
            ),
            "externalSignalProjection": input_projection,
            "runtimeStages": stage_timings,
        },
    )
