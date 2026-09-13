"""Model Registry runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.model_registry.public import (
        HypothesisDevelopmentService,
        HypothesisProposalService,
        InvestmentStrategyProposalService,
        ModelReviewRunner,
        OntologyLabService,
        RuleChangeCandidateProposalService,
    )


def build_model_review_runner(dry_run: bool = False) -> ModelReviewRunner:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.model_registry.infrastructure.model_reviewer import reviewer_from_settings
    from digital_twin.modules.model_registry.public import ModelReviewRunner
    from digital_twin.modules.notifications.infrastructure.notification.ingress import queued_notifier_for_account

    settings = runtime_settings()
    return ModelReviewRunner(
        queue=stores.model_review_job_store(settings),
        reviewer=reviewer_from_settings(settings),
        account_repository=stores.account_reader(settings),
        notifier_factory=lambda account: queued_notifier_for_account(account, message_type="modelReview"),
        dry_run=dry_run,
        settings=settings,
    )


def build_hypothesis_proposal_service(settings=None, research_store=None) -> HypothesisProposalService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.infrastructure.hypothesis_proposal_ai import hypothesis_proposal_advisor_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.model_registry.public import HypothesisProposalService

    configured_settings = settings or runtime_settings()
    return HypothesisProposalService(
        store=research_store or stores.investment_research_store(configured_settings),
        advisor=hypothesis_proposal_advisor_from_settings(configured_settings),
        event_publisher=default_event_bus(),
        settings=configured_settings,
        development_service=build_hypothesis_development_service(configured_settings, research_store),
    )


def build_hypothesis_development_service(settings=None, research_store=None) -> HypothesisDevelopmentService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.model_registry.public import HypothesisDevelopmentService
    from digital_twin.modules.model_registry.public import OntologyEvolutionService
    from digital_twin.modules.model_registry.infrastructure.evolution_policy import evolution_policy
    from digital_twin.infrastructure.ontology_evolution_runtime import OntologyEvolutionRuntime
    from digital_twin.modules.model_registry.infrastructure.mysql_experiment_observations import MySQLExperimentObservationStore
    from digital_twin.infrastructure.reasoning_engine_factory import build_reasoning_engine_platform
    from digital_twin.modules.portfolio.contracts import utc_now_iso

    configured_settings = settings or runtime_settings()
    case_store = stores.hypothesis_development_store(configured_settings)
    evolution = OntologyEvolutionService(
        OntologyEvolutionRuntime(build_reasoning_engine_platform(configured_settings),
                                 stores.investment_decision_episode_store(configured_settings), case_store,
                                 MySQLExperimentObservationStore(configured_settings)),
        evolution_policy(configured_settings), utc_now_iso,
    )
    return HypothesisDevelopmentService(
        case_store=case_store,
        proposal_store=research_store or stores.investment_research_store(configured_settings),
        experiment_store=stores.ontology_experiment_store(configured_settings),
        rule_candidate_service=build_rule_change_candidate_service(configured_settings),
        ontology_repository=ontology_repository_from_settings(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        event_publisher=default_event_bus(),
        settings=configured_settings,
        evolution_service=evolution,
    )


def build_rule_change_candidate_service(settings=None) -> RuleChangeCandidateProposalService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.rule_change_candidate_ai import rule_change_candidate_advisor_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.model_registry.public import RuleChangeCandidateProposalService

    configured_settings = settings or runtime_settings()
    return RuleChangeCandidateProposalService(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        advisor=rule_change_candidate_advisor_from_settings(configured_settings),
        event_reader=stores.event_log(configured_settings),
        settings=configured_settings,
        strategy_proposal_service=build_investment_strategy_proposal_service(configured_settings),
        model_signal_store=stores.statistical_model_signal_store(configured_settings),
    )


def build_investment_strategy_proposal_service(settings=None, event_publisher=None) -> InvestmentStrategyProposalService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.model_registry.public import InvestmentStrategyProposalService

    configured_settings = settings or runtime_settings()
    return InvestmentStrategyProposalService(
        proposal_store=stores.investment_strategy_proposal_store(configured_settings),
        ontology_repository=ontology_repository_from_settings(configured_settings),
        event_publisher=event_publisher or default_event_bus(),
        settings=configured_settings,
    )


def build_ontology_lab_service(settings=None) -> OntologyLabService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.reasoning_health import build_ontology_reasoning_queue_probe
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.model_registry.public import OntologyLabService

    configured_settings = settings or runtime_settings()
    return OntologyLabService(
        ontology_repository=ontology_repository_from_settings(configured_settings),
        experiment_store=stores.ontology_experiment_store(configured_settings),
        monitor_store=stores.monitor_store(configured_settings),
        rule_candidate_service=build_rule_change_candidate_service(configured_settings),
        strategy_proposal_service=build_investment_strategy_proposal_service(configured_settings),
        notification_queue=stores.notification_job_store(configured_settings),
        reasoning_queue_probe=build_ontology_reasoning_queue_probe(configured_settings),
        hypothesis_development_service=build_hypothesis_development_service(configured_settings),
        settings=configured_settings,
    )
