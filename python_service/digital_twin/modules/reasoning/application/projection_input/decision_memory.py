"""Bounded decision and outcome memory reads; outcome observation is an explicit capability."""

from __future__ import annotations

from digital_twin.modules.decisions.contracts import decision_episode_ontology_context
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Dict
from digital_twin.modules.reasoning.application.projection_input.ports import (
    DecisionMemoryInputs,
)


def decision_episode_projection_context(
    _inputs: DecisionMemoryInputs,
    snapshot: AccountSnapshot,
    target_symbols=None,
) -> Dict[str, object]:
    """Load a bounded, current-subject decision-memory slice for the ABox.

    The decision repository is the complete audit record. Realtime TypeDB
    projection only needs recent episode links and outcomes for subjects in
    the current snapshot. Keeping those two concerns separate prevents an
    old AI/research payload from expanding every live inference graph.
    """
    projection = {
        "mode": "bounded-current-subject-memory",
        "sourceEpisodeCount": 0,
        "includedEpisodeCount": 0,
        "droppedEpisodeCount": 0,
        "targetSymbolCount": 0,
        "perSymbolLimit": _inputs.decision_episode_context_per_symbol_limit(),
        "maximumEpisodeCount": _inputs.decision_episode_context_maximum_episodes(),
        "outcomeObservation": {},
        "outcomeHistory": {
            "mode": "point-in-time-compact-calibration-history",
            "status": "pending",
            "includedEpisodeCount": 0,
            "perSymbolLimit": _inputs.decision_outcome_history_per_symbol_limit(),
            "maximumEpisodeCount": _inputs.decision_outcome_history_maximum_episodes(),
        },
    }
    if not _inputs.decision_episode_store:
        projection["status"] = "unavailable"
        projection["outcomeHistory"]["status"] = "unavailable"
        return {"episodes": [], "outcomeHistoryEpisodes": [], "projection": projection}
    try:
        observation = _inputs.outcome_observation_service.observe_snapshot(snapshot)
        snapshot.metadata.setdefault("investmentBrain", {})[
            "outcomeObservation"
        ] = observation
        projection["outcomeObservation"] = dict(observation or {})
    except (
        Exception
    ) as error:  # noqa: BLE001 - feedback memory must not block ABox projection.
        observation = {
            "status": "error",
            "reason": str(error)[:180],
        }
        snapshot.metadata.setdefault("investmentBrain", {})[
            "outcomeObservation"
        ] = observation
        projection["outcomeObservation"] = observation
    symbols = sorted(
        {
            str(symbol or "").upper().strip()
            for symbol in target_symbols or []
            if str(symbol or "").strip()
        }
    )
    projection["targetSymbolCount"] = len(symbols)
    per_symbol_limit = int(projection["perSymbolLimit"] or 1)
    maximum_episode_count = int(projection["maximumEpisodeCount"] or 1)
    try:
        if symbols and hasattr(_inputs.decision_episode_store, "list_for_symbols"):
            source_episodes = _inputs.decision_episode_store.list_for_symbols(
                symbols,
                account_id=snapshot.account_id,
                limit_per_symbol=per_symbol_limit,
                as_of=str(snapshot.generated_at or ""),
            )
        elif symbols:
            source_episodes = []
            for symbol in symbols:
                source_episodes.extend(
                    _inputs.decision_episode_store.list(
                        snapshot.account_id,
                        symbol=symbol,
                        limit=per_symbol_limit,
                    )
                )
        else:
            source_episodes = _inputs.decision_episode_store.list(
                snapshot.account_id,
                limit=maximum_episode_count,
            )
    except (
        Exception
    ):  # noqa: BLE001 - projection remains valid without historical memory.
        projection["status"] = "unavailable"
        projection["outcomeHistory"]["status"] = "unavailable"
        return {"episodes": [], "outcomeHistoryEpisodes": [], "projection": projection}
    outcome_history_episodes = []
    outcome_history_projection = projection["outcomeHistory"]
    try:
        if symbols and hasattr(
            _inputs.decision_episode_store, "outcome_history_for_symbols"
        ):
            outcome_history_episodes = (
                _inputs.decision_episode_store.outcome_history_for_symbols(
                    symbols,
                    account_id=snapshot.account_id,
                    as_of=str(snapshot.generated_at or ""),
                    limit_per_symbol=int(
                        outcome_history_projection["perSymbolLimit"] or 120
                    ),
                    maximum_episode_count=int(
                        outcome_history_projection["maximumEpisodeCount"] or 600
                    ),
                )
            )
        elif symbols and hasattr(
            _inputs.decision_episode_store, "performance_episodes"
        ):
            for symbol in symbols:
                outcome_history_episodes.extend(
                    _inputs.decision_episode_store.performance_episodes(
                        account_id=snapshot.account_id,
                        symbol=symbol,
                        limit=int(outcome_history_projection["perSymbolLimit"] or 120),
                        as_of=str(snapshot.generated_at or ""),
                    )
                )
            outcome_history_episodes = outcome_history_episodes[
                : int(outcome_history_projection["maximumEpisodeCount"] or 600)
            ]
        outcome_history_episodes = [
            dict(item)
            for item in outcome_history_episodes or []
            if isinstance(item, dict)
        ]
        outcome_history_projection["includedEpisodeCount"] = len(
            outcome_history_episodes
        )
        outcome_history_projection["status"] = "ok"
    except (
        Exception
    ) as error:  # noqa: BLE001 - recent decision memory remains independently usable.
        outcome_history_episodes = []
        outcome_history_projection["status"] = "unavailable"
        outcome_history_projection["reason"] = str(error)[:180]
    source_by_id = {}
    for item in source_episodes or []:
        episode_id = str(getattr(item, "episode_id", "") or "").strip()
        symbol = str(getattr(item, "symbol", "") or "").upper().strip()
        if not episode_id or (symbols and symbol not in symbols):
            continue
        source_by_id[episode_id] = item
    ordered = sorted(
        source_by_id.values(),
        key=lambda item: (
            str(getattr(item, "decided_at", "") or ""),
            str(getattr(item, "episode_id", "") or ""),
        ),
        reverse=True,
    )
    projection["sourceEpisodeCount"] = len(ordered)
    selected_episodes = ordered[:maximum_episode_count]
    rows = [
        decision_episode_ontology_context(
            item,
            maximum_hypotheses=_inputs.decision_episode_context_hypothesis_limit(),
            maximum_outcomes=_inputs.decision_episode_context_outcome_limit(),
        )
        for item in selected_episodes
    ]
    rows = [item for item in rows if item]
    if rows and _inputs.investment_domain_store:
        try:
            feedback = _inputs.investment_domain_store.lifecycle_feedback_for_decisions(
                item.get("episodeId") for item in rows
            )
            for item in rows:
                item.update(dict(feedback.get(str(item.get("episodeId") or "")) or {}))
        except Exception:
            pass
    projection["includedEpisodeCount"] = len(rows)
    projection["droppedEpisodeCount"] = max(0, len(ordered) - len(rows))
    projection["status"] = "ok"
    return {
        "episodes": rows,
        "outcomeHistoryEpisodes": outcome_history_episodes,
        "projection": projection,
    }
