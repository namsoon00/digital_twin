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


_EXPORTS.update({
    'AccountSnapshot': ('digital_twin.modules.portfolio.domain.portfolio', 'AccountSnapshot'),
    'AlertEvent': ('digital_twin.modules.portfolio.domain.portfolio', 'AlertEvent'),
    'DEFAULT_FX_RATES': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'DEFAULT_FX_RATES'),
    'DecisionItem': ('digital_twin.modules.portfolio.domain.portfolio', 'DecisionItem'),
    'InstrumentValuationQuery': ('digital_twin.modules.portfolio.domain.instrument_valuation', 'InstrumentValuationQuery'),
    'InvestmentMandate': ('digital_twin.modules.portfolio.domain.investment_mandate', 'InvestmentMandate'),
    'PortfolioSummary': ('digital_twin.modules.portfolio.domain.portfolio', 'PortfolioSummary'),
    'Position': ('digital_twin.modules.portfolio.domain.portfolio', 'Position'),
    'ValuationModelRequest': ('digital_twin.modules.portfolio.domain.valuation.service', 'ValuationModelRequest'),
    'ValuationModelService': ('digital_twin.modules.portfolio.domain.valuation.service', 'ValuationModelService'),
    'account_snapshot_from_monitor_state': ('digital_twin.modules.portfolio.domain.portfolio', 'account_snapshot_from_monitor_state'),
    'add_position_valuation_concepts': ('digital_twin.modules.portfolio.domain.valuation.projection', 'add_position_valuation_concepts'),
    'empty_market': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'empty_market'),
    'evaluate_valuation_models': ('digital_twin.modules.portfolio.domain.valuation.service', 'evaluate_valuation_models'),
    'expects_kr_microstructure_signals': ('digital_twin.modules.portfolio.domain.portfolio', 'expects_kr_microstructure_signals'),
    'external_valuation_rows': ('digital_twin.modules.portfolio.domain.valuation.projection', 'external_valuation_rows'),
    'fx_rates_with_external_signals': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'fx_rates_with_external_signals'),
    'is_symbol_placeholder_name': ('digital_twin.modules.portfolio.domain.position_identity', 'is_symbol_placeholder_name'),
    'market_key': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'market_key'),
    'monitor_state_has_live_account_data': ('digital_twin.modules.portfolio.domain.portfolio', 'monitor_state_has_live_account_data'),
    'normalize_dividend_yield': ('digital_twin.modules.portfolio.domain.valuation.quality', 'normalize_dividend_yield'),
    'normalized_fx_rates': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'normalized_fx_rates'),
    'portfolio_summary': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'portfolio_summary'),
    'position_account_value_in_base': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'position_account_value_in_base'),
    'position_runtime_valuation_rows': ('digital_twin.modules.portfolio.domain.valuation.projection', 'position_runtime_valuation_rows'),
    'position_with_symbol_identity': ('digital_twin.modules.portfolio.domain.position_identity', 'position_with_symbol_identity'),
    'preferred_instrument_name': ('digital_twin.modules.portfolio.domain.position_identity', 'preferred_instrument_name'),
    'quality_checked_valuation_row': ('digital_twin.modules.portfolio.domain.valuation.projection', 'quality_checked_valuation_row'),
    'runtime_fx_currencies_from_external_signals': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'runtime_fx_currencies_from_external_signals'),
    'serialize_dataclass': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'serialize_dataclass'),
    'status_has_account_data_failure': ('digital_twin.modules.portfolio.domain.portfolio', 'status_has_account_data_failure'),
    'utc_now_iso': ('digital_twin.modules.portfolio.domain.portfolio', 'utc_now_iso'),
    'value_in_base': ('digital_twin.modules.portfolio.domain.portfolio_calculations', 'value_in_base'),
})

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
