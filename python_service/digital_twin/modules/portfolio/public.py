"""Explicit, lazy public surface for the portfolio module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'DecisionActionPlanningService': ('digital_twin.modules.portfolio.application.portfolio_lifecycle_service',
                                   'DecisionActionPlanningService'),
 'InvestmentDomainService': ('digital_twin.modules.portfolio.application.investment_domain_service',
                             'InvestmentDomainService'),
 'PortfolioAccountingService': ('digital_twin.modules.portfolio.application.portfolio_lifecycle_service',
                                'PortfolioAccountingService'),
 'TradeExecutionService': ('digital_twin.modules.portfolio.application.portfolio_lifecycle_service',
                           'TradeExecutionService')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
