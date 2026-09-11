"""Decisions runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.decisions.public import (
        AIInferenceQueueRunner,
        DecisionEpisodeReconciliationService,
        InvestmentBrainService,
    )


def build_ai_inference_queue_runner(worker_id: str = "") -> AIInferenceQueueRunner:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.portfolio import build_decision_action_planning_service
    from digital_twin.infrastructure.notification_ai_reviewer import notification_ai_reviewer_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.decisions.public import AIInferenceQueueRunner, DecisionContinuityService
    from digital_twin.modules.reasoning.public import InvestmentReasoningOrchestrator

    settings = runtime_settings()
    decision_episode_store = stores.investment_decision_episode_store(settings)
    continuity_service = DecisionContinuityService(
        decision_episode_store,
        stores.investment_domain_store(settings),
    )
    return AIInferenceQueueRunner(
        queue=stores.ai_inference_queue_store(settings),
        reviewer=notification_ai_reviewer_from_settings(settings, allow_local_fallback=False),
        settings=settings,
        decision_episode_store=decision_episode_store,
        continuity_service=continuity_service,
        action_planning_service=build_decision_action_planning_service(settings),
        reasoning_orchestrator=InvestmentReasoningOrchestrator(
            stores.investment_reasoning_case_store(settings),
            decision_episode_store=decision_episode_store,
            hypothesis_proposal_request_store=stores.investment_research_store(settings),
            subject_case_repository=stores.subject_decision_case_store(settings),
        ),
        worker_id=worker_id,
    )


def build_decision_episode_reconciliation_service(settings=None) -> DecisionEpisodeReconciliationService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.decisions.public import DecisionEpisodeReconciliationService

    configured_settings = settings or runtime_settings()
    return DecisionEpisodeReconciliationService(
        stores.investment_decision_episode_store(configured_settings),
        stores.notification_job_store(configured_settings),
    )


def build_investment_brain_service(settings=None) -> InvestmentBrainService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.model_registry import build_hypothesis_proposal_service
    from digital_twin.infrastructure.composition.news_intelligence import build_investment_research_orchestrator
    from digital_twin.infrastructure.notification_ai_reviewer import notification_ai_reviewer_from_settings
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.decisions.public import InvestmentBrainService
    from digital_twin.modules.model_registry.public import (
        HypothesisLifecyclePolicyService,
        HypothesisPolicyGovernanceService,
    )
    from digital_twin.modules.outcomes.public import (
        HypothesisOutcomeReplayService,
        HypothesisQualityReviewService,
        HypothesisReviewService,
    )

    configured_settings = settings or runtime_settings()
    research_store = stores.investment_research_store(configured_settings)
    ontology_repository = ontology_repository_from_settings(configured_settings)
    decision_episode_store = stores.investment_decision_episode_store(configured_settings)
    lifecycle_store = stores.hypothesis_lifecycle_store(configured_settings)
    hypothesis_review_service = HypothesisReviewService(
        hypothesis_lifecycle_store=lifecycle_store,
        decision_episode_store=decision_episode_store,
        ontology_repository=ontology_repository,
        settings=configured_settings,
    )
    hypothesis_lifecycle_policy_service = HypothesisLifecyclePolicyService(ontology_repository)
    hypothesis_quality_review_service = HypothesisQualityReviewService(decision_episode_store=decision_episode_store)
    return InvestmentBrainService(
        monitor_store=stores.monitor_store(configured_settings),
        ontology_repository=ontology_repository,
        reviewer=notification_ai_reviewer_from_settings(configured_settings),
        decision_episode_store=decision_episode_store,
        research_orchestrator=build_investment_research_orchestrator(configured_settings, research_store),
        hypothesis_proposal_service=build_hypothesis_proposal_service(configured_settings, research_store),
        research_store=research_store,
        settings=configured_settings,
        hypothesis_lifecycle_store=lifecycle_store,
        hypothesis_review_service=hypothesis_review_service,
        hypothesis_lifecycle_policy_service=hypothesis_lifecycle_policy_service,
        hypothesis_quality_review_service=hypothesis_quality_review_service,
        hypothesis_policy_governance_service=HypothesisPolicyGovernanceService(
            ontology_repository=ontology_repository,
            lifecycle_policy_service=hypothesis_lifecycle_policy_service,
        ),
        hypothesis_outcome_replay_service=HypothesisOutcomeReplayService(
            decision_episode_store=decision_episode_store,
            hypothesis_review_service=hypothesis_review_service,
            quality_review_service=hypothesis_quality_review_service,
        ),
    )
