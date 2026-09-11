"""Explicit business-router composition in the original HTTP precedence order."""

from dataclasses import dataclass, field

from .router import ApiRouter
from .routes.accounts import AccountsRoutes
from .routes.calendar import CalendarRoutes
from .routes.configuration import ConfigurationRoutes
from .routes.decisions import DecisionsRoutes
from .routes.instruments import InstrumentsRoutes
from .routes.market_data import MarketDataRoutes
from .routes.model_registry import ModelRegistryRoutes
from .routes.notifications import NotificationsRoutes
from .routes.operations import OperationsRoutes
from .routes.outcomes import OutcomesRoutes
from .routes.portfolio import PortfolioRoutes
from .routes.read_models import ReadModelsRoutes
from .routes.reasoning import ReasoningRoutes
from .routes.research_evidence import ResearchEvidenceRoutes
from .routes.share import ShareRoutes
from .routes.workspace import WorkspaceRoutes


@dataclass(frozen=True)
class WebRoutes:
    accounts: AccountsRoutes = field(default_factory=AccountsRoutes)
    calendar: CalendarRoutes = field(default_factory=CalendarRoutes)
    configuration: ConfigurationRoutes = field(default_factory=ConfigurationRoutes)
    decisions: DecisionsRoutes = field(default_factory=DecisionsRoutes)
    instruments: InstrumentsRoutes = field(default_factory=InstrumentsRoutes)
    market_data: MarketDataRoutes = field(default_factory=MarketDataRoutes)
    model_registry: ModelRegistryRoutes = field(default_factory=ModelRegistryRoutes)
    notifications: NotificationsRoutes = field(default_factory=NotificationsRoutes)
    operations: OperationsRoutes = field(default_factory=OperationsRoutes)
    outcomes: OutcomesRoutes = field(default_factory=OutcomesRoutes)
    portfolio: PortfolioRoutes = field(default_factory=PortfolioRoutes)
    read_models: ReadModelsRoutes = field(default_factory=ReadModelsRoutes)
    reasoning: ReasoningRoutes = field(default_factory=ReasoningRoutes)
    research_evidence: ResearchEvidenceRoutes = field(default_factory=ResearchEvidenceRoutes)
    share: ShareRoutes = field(default_factory=ShareRoutes)
    workspace: WorkspaceRoutes = field(default_factory=WorkspaceRoutes)


def build_api_router(routes: WebRoutes = None) -> ApiRouter:
    routes = routes if routes is not None else WebRoutes()
    return ApiRouter((
        routes.share.route_share_access,
        routes.operations.route_version,
        routes.accounts.route_service_accounts,
        routes.instruments.route_service_accounts_watchlist,
        routes.accounts.route_remove_service_account,
        routes.configuration.route_settings,
        routes.operations.route_time_series_platform_status,
        routes.reasoning.route_reasoning_engine_status,
        routes.model_registry.route_investment_strategy_proposals,
        routes.instruments.route_symbol_universe,
        routes.notifications.route_notification_templates,
        routes.research_evidence.route_research_evidence,
        routes.calendar.route_investment_calendar_events,
        routes.research_evidence.route_delete_research_evidence,
        routes.notifications.route_notification_schedules,
        routes.market_data.route_data_api_fred_observations,
        routes.read_models.route_flow_lens,
        routes.operations.route_operations_health,
        routes.model_registry.route_investment_model,
        routes.read_models.route_investment_cases,
        routes.workspace.route_bootstrap,
        routes.operations.route_realtime_status,
        routes.workspace.route_profile,
        routes.decisions.route_investment_brain_questions,
        routes.read_models.route_instruments_valuation,
        routes.decisions.route_investment_brain_episodes,
        routes.portfolio.route_portfolio_lifecycle,
        routes.outcomes.route_investment_brain_performance,
        routes.model_registry.route_investment_brain_hypothesis_templates,
        routes.outcomes.route_investment_brain_replay_jobs,
        routes.model_registry.route_investment_brain_hypothesis_policies_preview,
        routes.decisions.route_investment_brain_research_runs,
        routes.model_registry.route_investment_brain_hypothesis_proposals,
        routes.workspace.route_memories,
        routes.market_data.route_stocks,
    ))
