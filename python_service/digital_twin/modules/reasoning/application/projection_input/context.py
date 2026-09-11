"""Freeze the source context before factual graph construction; cache is injected."""

from __future__ import annotations

from copy import deepcopy
from digital_twin.modules.outcomes.contracts import evaluate_decision_performance
from digital_twin.modules.model_registry.contracts import HYPOTHESIS_LIFECYCLE_KEY_PREFIX
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from digital_twin.modules.reasoning.domain.reasoning_shadow import frozen_projection_runtime_context
from typing import Callable
from typing import Dict
from typing import List
import hashlib
import time
from digital_twin.modules.reasoning.application.projection_input.ports import (
    RuntimeContextInputs,
)


def runtime_context(
    _inputs: RuntimeContextInputs,
    snapshot: AccountSnapshot,
    active_tbox: Dict[str, object] = None,
    target_symbols: List[str] = None,
    progress_callback: Callable[..., None] = None,
) -> Dict[str, object]:
    account_id = str(snapshot.account_id or "")
    override = _inputs.runtime_context_overrides.get(account_id)
    if override:
        frozen = frozen_projection_runtime_context(override)
        _inputs.last_runtime_contexts[account_id] = frozen
        _inputs.last_runtime_context_cache_status[account_id] = {"status": "override"}
        return deepcopy(frozen)

    def emit(stage: str, **details) -> None:
        if not callable(progress_callback):
            return
        try:
            progress_callback(str(stage or "unknown"), **details)
        except Exception:
            return

    if active_tbox is None:
        active_tbox = _inputs.active_tbox_context()
    cache_enabled = _inputs.runtime_context_cache_enabled()
    cache_key = (
        _inputs.runtime_context_cache_key(
            snapshot,
            active_tbox,
            target_symbols=target_symbols,
        )
        if cache_enabled
        else ""
    )
    cache_result = (
        _inputs.runtime_cache.get(
            cache_key,
            _inputs.runtime_context_cache_ttl_seconds(),
        )
        if cache_enabled
        else {"status": "disabled"}
    )
    _inputs.last_runtime_context_cache_status[account_id] = {
        "status": str(cache_result.get("status") or "miss"),
        "ageMs": int(cache_result.get("ageMs") or 0),
    }
    emit(
        "cache." + str(cache_result.get("status") or "miss"),
        ageMs=int(cache_result.get("ageMs") or 0),
    )
    if str(cache_result.get("status") or "") == "hit":
        frozen = frozen_projection_runtime_context(cache_result.get("context") or {})
        _inputs.last_runtime_contexts[account_id] = frozen
        return deepcopy(frozen)
    as_of = str(snapshot.generated_at or "").strip()
    snapshot_seed = "|".join([str(snapshot.account_id or ""), as_of or "unknown"])
    selected_symbols = {
        str(symbol or "").upper().strip()
        for symbol in target_symbols or []
        if str(symbol or "").strip()
    }
    available_symbols = {
        str(getattr(position, "symbol", "") or "").upper().strip()
        for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
        if str(getattr(position, "symbol", "") or "").strip() and not position.is_cash()
    }
    selected_symbols.intersection_update(available_symbols)
    decision_memory_symbols = selected_symbols or available_symbols
    emit("decision_episodes.start", symbolCount=len(decision_memory_symbols))
    decision_memory = _inputs.decision_episode_projection_context(
        snapshot,
        target_symbols=decision_memory_symbols,
    )
    decision_episodes = list(decision_memory.get("episodes") or [])
    decision_outcome_history = list(decision_memory.get("outcomeHistoryEpisodes") or [])
    emit(
        "decision_episodes.done",
        episodeCount=len(decision_episodes),
        outcomeHistoryEpisodeCount=len(decision_outcome_history),
    )
    emit("metadata.start")
    metadata = _inputs.factual_runtime_metadata(
        snapshot.metadata,
        target_symbols=selected_symbols or available_symbols,
        settings=_inputs.settings,
    )
    emit("metadata.done", metadataKeyCount=len(metadata))
    # Projection output is derived state, not a new market observation.
    # Feeding the previous ABox result back into the next ABox makes an
    # otherwise unchanged snapshot look materially different.
    metadata.pop("ontology", None)
    account_context = (
        metadata.get("accountContext")
        if isinstance(metadata.get("accountContext"), dict)
        else {}
    )
    emit("decision_performance.start")
    decision_performance = {}
    if hasattr(_inputs.decision_episode_store, "performance"):
        try:
            decision_performance = _inputs.decision_episode_store.performance(
                account_id=snapshot.account_id,
                limit=2000,
                as_of=as_of,
            )
        except TypeError:
            # Compatibility stores may not yet expose the point-in-time
            # parameter. Their bounded result remains diagnostic only.
            decision_performance = _inputs.decision_episode_store.performance(
                account_id=snapshot.account_id,
                limit=2000,
            )
        except (
            Exception
        ):  # noqa: BLE001 - calibration history still supports the current subject.
            decision_performance = {}
    if not decision_performance:
        decision_performance = evaluate_decision_performance(
            decision_outcome_history or decision_episodes,
            minimum_sample_count=int(
                _inputs.performance_setting(
                    "investmentBrainPerformanceMinimumSamples", 5
                )
            ),
        )
    emit("decision_performance.done")
    emit("hypothesis_proposals.start")
    hypothesis_proposals = _inputs.hypothesis_proposal_context(
        snapshot,
        target_symbols=selected_symbols,
    )
    emit("hypothesis_proposals.done", proposalCount=len(hypothesis_proposals))
    lifecycle_projection_started = time.perf_counter()
    lifecycle_projection = {
        "mode": "excluded-from-live-abox",
        "enabled": False,
        "recordCount": 0,
        "payloadBytesRead": 0,
        "keyPrefix": HYPOTHESIS_LIFECYCLE_KEY_PREFIX,
    }
    emit("hypothesis_lifecycles.start", mode=lifecycle_projection["mode"])
    hypothesis_lifecycles = []
    if _inputs.hypothesis_lifecycle_abox_projection_enabled():
        hypothesis_lifecycles = _inputs.hypothesis_lifecycle_context(
            snapshot,
            target_symbols=selected_symbols,
        )
        lifecycle_projection.update(
            {
                "mode": "compact-opt-in-audit",
                "enabled": True,
                "recordCount": len(hypothesis_lifecycles),
            }
        )
    lifecycle_projection["readMs"] = int(
        (time.perf_counter() - lifecycle_projection_started) * 1000
    )
    emit(
        "hypothesis_lifecycles.done",
        lifecycleCount=len(hypothesis_lifecycles),
        mode=lifecycle_projection["mode"],
        runtimeMs=lifecycle_projection["readMs"],
    )
    emit("pipeline_health.start")
    data_pipeline_health = _inputs.data_pipeline_health_context(snapshot)
    emit("pipeline_health.done")
    emit("temporal_windows.start")
    temporal_windows = _inputs.temporal_observation_windows(
        snapshot,
        target_symbols=selected_symbols,
    )
    emit("temporal_windows.done", symbolCount=len(temporal_windows))
    # Model scoring runs after the factual ABox is complete. This lets all
    # six model families inspect the exact company, valuation, event,
    # cross-asset, price and flow facts that TypeDB will receive.
    statistical_signal_context = (
        {
            "statisticalSignalPipeline": {
                "status": "pending-factual-abox",
            },
        }
        if _inputs.statistical_signal_service
        else {}
    )
    portfolio_lifecycle = {}
    if _inputs.investment_domain_store and hasattr(
        _inputs.investment_domain_store, "ontology_portfolio_lifecycle_context"
    ):
        emit("portfolio_lifecycle.start")
        try:
            portfolio_lifecycle = (
                _inputs.investment_domain_store.ontology_portfolio_lifecycle_context(
                    "portfolio:" + str(snapshot.account_id or "default")
                )
            )
        except (
            Exception
        ):  # noqa: BLE001 - lifecycle enrichment must not invalidate market inference.
            portfolio_lifecycle = {}
        emit(
            "portfolio_lifecycle.done",
            status=str(portfolio_lifecycle.get("status") or "unavailable"),
        )
    result = {
        "settings": dict(_inputs.settings),
        "snapshotId": "abox-snapshot:"
        + hashlib.sha256(snapshot_seed.encode("utf-8")).hexdigest()[:16],
        "asOf": as_of,
        "activeTBox": active_tbox,
        "account": {
            **dict(account_context),
            "accountId": snapshot.account_id,
            "accountLabel": snapshot.account_label,
            "provider": snapshot.provider,
            "mode": snapshot.mode,
            "status": snapshot.status,
        },
        "metadata": metadata,
        # DecisionItem is an output projection, not a new observation.
        # The native ABox uses the aligned InferenceBox for prior
        # reasoning context and keeps this input empty to avoid feedback.
        "decisionItems": [],
        "decisionEpisodes": decision_episodes,
        # Historical outcomes are aggregated into calibration facts only;
        # their old decisions and AI prose are not reintroduced as live
        # reasoning premises.
        "decisionOutcomeHistory": decision_outcome_history,
        "decisionEpisodeProjection": dict(decision_memory.get("projection") or {}),
        "decisionPerformance": decision_performance,
        "hypothesisProposals": hypothesis_proposals,
        "hypothesisLifecycles": hypothesis_lifecycles,
        "hypothesisLifecycleAboxProjection": lifecycle_projection,
        # A live pipeline health row may change while a delayed retry is
        # rebuilding the same account snapshot. Only health captured with
        # the snapshot is causal ABox input; current worker telemetry is
        # exposed through operational monitoring instead.
        "dataPipelineHealth": data_pipeline_health,
        "temporalObservationWindows": temporal_windows,
        **statistical_signal_context,
        "portfolioLifecycle": portfolio_lifecycle,
    }
    # V1 and any replay engine must consume the same ontology-owned
    # context. Returning the unfiltered runtime settings here while only
    # storing the filtered replay packet made shadow parity impossible and
    # could let infrastructure wiring affect factual graph construction.
    frozen = frozen_projection_runtime_context(result)
    _inputs.last_runtime_contexts[account_id] = frozen
    if cache_enabled:
        _inputs.runtime_cache.put(
            cache_key,
            frozen,
            _inputs.runtime_context_cache_max_entries(),
        )
    return deepcopy(frozen)
