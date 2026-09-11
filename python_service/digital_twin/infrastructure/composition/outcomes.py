"""Outcomes runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.outcomes.public import (
        HistoricalDecisionReplayService,
        HistoricalReplayJobService,
        HypothesisLifecycleService,
    )


def build_hypothesis_lifecycle_service(settings=None, event_publisher=None) -> HypothesisLifecycleService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.outcomes.public import HypothesisLifecycleService

    configured_settings = settings or runtime_settings()
    return HypothesisLifecycleService(
        store=stores.hypothesis_lifecycle_store(configured_settings),
        event_publisher=event_publisher,
        settings=configured_settings,
    )


def build_historical_decision_replay_service(settings=None) -> HistoricalDecisionReplayService:
    """Build the read-only point-in-time replay audit without heavy brain dependencies."""
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.outcomes.public import HistoricalDecisionReplayService

    configured_settings = settings or runtime_settings()
    return HistoricalDecisionReplayService(
        decision_episode_store=stores.investment_decision_episode_store(configured_settings),
    )


def build_historical_replay_job_service(
    settings=None,
    *,
    execution_enabled: bool = True,
) -> HistoricalReplayJobService:
    """Build isolated replay work without notification or ABox writers."""
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.decisions import build_investment_brain_service
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.outcomes.public import HistoricalReplayJobService

    configured_settings = settings or runtime_settings()
    brain = build_investment_brain_service(configured_settings) if execution_enabled else None
    return HistoricalReplayJobService(
        store=stores.historical_replay_job_store(configured_settings),
        decision_replay_service=(
            build_historical_decision_replay_service(configured_settings)
            if execution_enabled else None
        ),
        hypothesis_replay_service=(
            brain.hypothesis_outcome_replay_service if brain else None
        ),
    )
