"""Explicit V2 reasoning delivery composition phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from digital_twin.modules.decisions.public import (
        InvestmentAIInsightHandoffService,
        InvestmentInsightDispatchService,
    )
    from digital_twin.modules.reasoning.public import InvestmentReasoningOrchestrator


@dataclass(frozen=True)
class ReasoningDelivery:
    delivery_authorized: Callable[[], bool]
    subject_decision_orchestrator: InvestmentReasoningOrchestrator
    ai_insight_handoff_service: InvestmentAIInsightHandoffService
    insight_dispatch_service: InvestmentInsightDispatchService


def wire_v2_decision_services(
    registry_store,
    descriptor,
    store_settings,
    candidate_settings,
    subscription_state_store,
    delivery_time_series_store,
    account_repository,
) -> ReasoningDelivery:
    from digital_twin.modules.market_data.domain.monitoring import RealtimeMonitor
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.decisions import build_investment_brain_service
    from digital_twin.infrastructure.disclosure_analyzer import disclosure_analyzer_from_settings
    from digital_twin.modules.decisions.public import (
        DecisionContinuityService,
        InvestmentAIInsightHandoffService,
        InvestmentInsightDispatchService,
        NotificationAIDecisionContextEnricher,
        NotificationAIRequestEnqueuer,
    )
    from digital_twin.modules.notifications.public import (
        CompositeNotificationContextEnricher,
        DisclosureAnalysisNotificationEnricher,
        NotificationAIOpinionEnricher,
        NotificationHoldingSnapshotEnricher,
        NotificationHypothesisResearchEnricher,
        NotificationIngressService,
        NotificationInstrumentIdentityEnricher,
    )
    from digital_twin.modules.reasoning.public import InvestmentReasoningOrchestrator

    def delivery_authorized():
        control = registry_store.control()
        deployment = registry_store.get(descriptor.deployment_id)
        return bool(
            str(control.delivery_deployment_id or "") == descriptor.deployment_id
            and str(control.active_deployment_id or "") == descriptor.deployment_id
            and str(deployment.get("status") or "") == "active"
        )

    decision_episode_store = stores.investment_decision_episode_store(store_settings)
    subject_decision_orchestrator = InvestmentReasoningOrchestrator(
        stores.investment_reasoning_case_store(store_settings),
        decision_episode_store=decision_episode_store,
        hypothesis_proposal_request_store=stores.investment_research_store(store_settings),
        subject_case_repository=stores.subject_decision_case_store(store_settings),
    )
    decision_continuity = DecisionContinuityService(
        decision_episode_store,
        stores.investment_domain_store(store_settings),
    )
    ai_context_preparer = CompositeNotificationContextEnricher(
        NotificationInstrumentIdentityEnricher(stores.symbol_universe_store(store_settings)),
        NotificationHoldingSnapshotEnricher(
            subscription_state_store.load_previous,
            RealtimeMonitor(candidate_settings),
        ),
        DisclosureAnalysisNotificationEnricher(
            disclosure_analyzer_from_settings(candidate_settings),
            candidate_settings,
        ),
        NotificationHypothesisResearchEnricher(
            build_investment_brain_service(store_settings),
            candidate_settings,
        ),
        NotificationAIOpinionEnricher(candidate_settings),
        NotificationAIDecisionContextEnricher(
            delivery_time_series_store,
            candidate_settings,
            investment_domain_store=stores.investment_domain_store(store_settings),
        ),
    )
    detached_ai_enqueuer = NotificationAIRequestEnqueuer(
        stores.ai_inference_queue_store(store_settings),
        ai_context_preparer,
        candidate_settings,
        decision_episode_store=decision_episode_store,
        continuity_service=decision_continuity,
        reasoning_orchestrator=subject_decision_orchestrator,
    )
    insight_notification_ingress = NotificationIngressService(
        template_renderer=stores.notification_template_store(store_settings).render,
        settings=candidate_settings,
    )
    insight_notification_queue = stores.notification_job_store(store_settings)
    ai_insight_handoff_service = InvestmentAIInsightHandoffService(
        insight_notification_ingress,
        detached_ai_enqueuer,
        account_repository=account_repository,
    )
    insight_dispatch_service = InvestmentInsightDispatchService(
        insight_notification_ingress,
        insight_notification_queue,
        ai_insight_handoff_service,
        subject_decision_orchestrator,
        account_repository=account_repository,
    )
    return ReasoningDelivery(
        delivery_authorized,
        subject_decision_orchestrator,
        ai_insight_handoff_service,
        insight_dispatch_service,
    )
