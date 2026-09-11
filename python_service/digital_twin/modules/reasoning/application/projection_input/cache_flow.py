"""Optional cache reuse cannot activate an ABox or publish inference."""

from __future__ import annotations

from copy import deepcopy
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.reasoning_shadow import unpack_projection_runtime_contexts
import time
from .ports import CacheFlowInputs, PreparedGraphInput


def load_cached_assembly(
    _inputs: CacheFlowInputs,
    prepared: PreparedGraphInput,
    snapshot: AccountSnapshot,
    cache_enabled,
    cache_key,
    stage_timings,
    emit,
):
    observation_input = prepared.observation_input
    input_mode = prepared.input_mode
    input_symbols = prepared.input_symbols
    input_projection = prepared.input_projection
    emit("memory_cache.start")
    cache_read_started = time.perf_counter()
    cache_result = (
        _inputs.graph_cache.get(
            cache_key,
            _inputs.graph_assembly_cache_ttl_seconds(),
        )
        if cache_enabled
        else {"status": "disabled"}
    )
    stage_timings["graphAssemblyCacheReadMs"] = int(
        (time.perf_counter() - cache_read_started) * 1000
    )
    emit(
        "memory_cache.done",
        runtimeMs=stage_timings["graphAssemblyCacheReadMs"],
        status=str(cache_result.get("status") or ""),
    )
    if str(cache_result.get("status") or "") == "hit":
        try:
            cached_contexts = unpack_projection_runtime_contexts(
                cache_result.get("runtimeContextPacket") or {}
            )
            if snapshot.account_id in cached_contexts:
                _inputs.last_runtime_contexts[snapshot.account_id] = cached_contexts[
                    snapshot.account_id
                ]
        except ValueError:
            pass
        return (
            cache_result["graph"],
            cache_result["persistenceGraph"],
            {
                "status": "hit",
                "cacheLayer": "memory",
                "ageMs": int(cache_result.get("ageMs") or 0),
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

    emit("persistent_cache.start")
    persistent_cache_started = time.perf_counter()
    persistent_cache_result = (
        _inputs.persistent_graph_assembly_cache_get(cache_key)
        if cache_enabled
        else {"status": "disabled"}
    )
    stage_timings["graphAssemblyPersistentCacheReadMs"] = int(
        (time.perf_counter() - persistent_cache_started) * 1000
    )
    stage_timings["graphAssemblyPersistentCacheHit"] = (
        1 if str(persistent_cache_result.get("status") or "") == "hit" else 0
    )
    emit(
        "persistent_cache.done",
        runtimeMs=stage_timings["graphAssemblyPersistentCacheReadMs"],
        status=str(persistent_cache_result.get("status") or ""),
    )
    if str(persistent_cache_result.get("status") or "") == "hit":
        graph = persistent_cache_result["graph"]
        persistence_graph = persistent_cache_result["persistenceGraph"]
        _inputs.graph_cache.put(
            cache_key,
            graph,
            persistence_graph,
            _inputs.graph_assembly_cache_max_entries(),
            persistent_cache_result.get("runtimeContextPacket") or {},
        )
        try:
            cached_contexts = unpack_projection_runtime_contexts(
                persistent_cache_result.get("runtimeContextPacket") or {}
            )
            if snapshot.account_id in cached_contexts:
                _inputs.last_runtime_contexts[snapshot.account_id] = cached_contexts[
                    snapshot.account_id
                ]
        except ValueError:
            pass
        return (
            deepcopy(graph),
            deepcopy(persistence_graph),
            {
                "status": "hit",
                "cacheLayer": "persistent",
                "ageMs": int(persistent_cache_result.get("ageMs") or 0),
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

    return None


def store_cached_assembly(
    _inputs: CacheFlowInputs,
    cache_enabled,
    cache_key,
    graph,
    persistence_graph,
    runtime_context_packet,
    stage_timings,
    emit,
):
    if cache_enabled:
        _inputs.graph_cache.put(
            cache_key,
            graph,
            persistence_graph,
            _inputs.graph_assembly_cache_max_entries(),
            runtime_context_packet,
        )
        emit("persistent_cache_write.start")
        persistent_cache_write_started = time.perf_counter()
        persistent_cache_write = _inputs.persistent_graph_assembly_cache_put(
            cache_key,
            graph,
            persistence_graph,
            runtime_context_packet,
        )
        stage_timings["graphAssemblyPersistentCacheWriteMs"] = int(
            (time.perf_counter() - persistent_cache_write_started) * 1000
        )
        if str(persistent_cache_write.get("status") or "") == "stored":
            stage_timings["graphAssemblyPersistentCacheStored"] = 1
        emit(
            "persistent_cache_write.done",
            runtimeMs=stage_timings["graphAssemblyPersistentCacheWriteMs"],
            status=str(persistent_cache_write.get("status") or ""),
        )
