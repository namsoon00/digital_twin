"""Explicit, lazy contracts surface for the portfolio module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'INFERRED_CORPORATE_ACTION': ('digital_twin.modules.portfolio.domain.portfolio_ledger',
                               'INFERRED_CORPORATE_ACTION'),
 'INFERRED_POSITION_DECREASE': ('digital_twin.modules.portfolio.domain.portfolio_ledger',
                                'INFERRED_POSITION_DECREASE'),
 'INFERRED_POSITION_EXIT': ('digital_twin.modules.portfolio.domain.portfolio_ledger',
                            'INFERRED_POSITION_EXIT'),
 'INFERRED_POSITION_INCREASE': ('digital_twin.modules.portfolio.domain.portfolio_ledger',
                                'INFERRED_POSITION_INCREASE'),
 'INFERRED_SNAPSHOT_ENTRY_TYPES': ('digital_twin.modules.portfolio.domain.portfolio_ledger',
                                   'INFERRED_SNAPSHOT_ENTRY_TYPES'),
 'PortfolioLedgerEntry': ('digital_twin.modules.portfolio.domain.portfolio_ledger', 'PortfolioLedgerEntry'),
 'PortfolioLedgerState': ('digital_twin.modules.portfolio.domain.portfolio_ledger', 'PortfolioLedgerState'),
 'PortfolioReconciliation': ('digital_twin.modules.portfolio.domain.portfolio_ledger',
                             'PortfolioReconciliation'),
 'SNAPSHOT_CASH_ADJUSTMENT': ('digital_twin.modules.portfolio.domain.portfolio_ledger',
                              'SNAPSHOT_CASH_ADJUSTMENT'),
 'decimal_value': ('digital_twin.modules.portfolio.domain.portfolio_ledger', 'decimal_value'),
 'execution_ledger_entries': ('digital_twin.modules.portfolio.domain.portfolio_ledger',
                              'execution_ledger_entries')}

_EXPORTS['DEFAULT_INVESTMENT_STRATEGY_PROFILE'] = ('digital_twin.modules.portfolio.domain.strategy_profile', 'DEFAULT_INVESTMENT_STRATEGY_PROFILE')
_EXPORTS['INVESTMENT_STRATEGY_PROFILES'] = ('digital_twin.modules.portfolio.domain.strategy_profile', 'INVESTMENT_STRATEGY_PROFILES')
_EXPORTS['normalize_investment_strategy_profile'] = ('digital_twin.modules.portfolio.domain.strategy_profile', 'normalize_investment_strategy_profile')
_EXPORTS['investment_strategy_profile'] = ('digital_twin.modules.portfolio.domain.strategy_profile', 'investment_strategy_profile')

_EXPORTS['InvestmentDomainRepository'] = ('digital_twin.modules.portfolio.domain.repositories', 'InvestmentDomainRepository')
_EXPORTS['SnapshotProvider'] = ('digital_twin.modules.portfolio.domain.repositories', 'SnapshotProvider')
_EXPORTS['MonitorStateRepository'] = ('digital_twin.modules.portfolio.domain.repositories', 'MonitorStateRepository')
_EXPORTS['MonitorSnapshotReader'] = ('digital_twin.modules.portfolio.domain.repositories', 'MonitorSnapshotReader')
_EXPORTS['SnapshotMonitor'] = ('digital_twin.modules.portfolio.domain.repositories', 'SnapshotMonitor')
_EXPORTS['MonitoringCycleRecordResult'] = ('digital_twin.modules.portfolio.domain.repositories', 'MonitoringCycleRecordResult')
_EXPORTS['MonitoringCycleRecorder'] = ('digital_twin.modules.portfolio.domain.repositories', 'MonitoringCycleRecorder')
_EXPORTS['MonitorAccountJob'] = ('digital_twin.modules.portfolio.domain.repositories', 'MonitorAccountJob')
_EXPORTS['MonitorAccountJobRepository'] = ('digital_twin.modules.portfolio.domain.repositories', 'MonitorAccountJobRepository')

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
