"""Explicit, lazy contracts surface for the market_data module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'CapitalFlowObservation': ('digital_twin.modules.market_data.domain.capital_flow',
                            'CapitalFlowObservation'),
 'boolean_value': ('digital_twin.modules.market_data.domain.capital_flow', 'boolean_value'),
 'canonical_observations': ('digital_twin.modules.market_data.domain.capital_flow',
                            'canonical_observations'),
 'merge_capital_flow_rows': ('digital_twin.modules.market_data.domain.capital_flow',
                             'merge_capital_flow_rows'),
 'observation_from_row': ('digital_twin.modules.market_data.domain.capital_flow', 'observation_from_row'),
 'observed_fields_from_coverage': ('digital_twin.modules.market_data.domain.capital_flow',
                                   'observed_fields_from_coverage')}

_EXPORTS['MarketQuoteRepository'] = ('digital_twin.modules.market_data.domain.repositories', 'MarketQuoteRepository')
_EXPORTS['MarketTimeSeriesRepository'] = ('digital_twin.modules.market_data.domain.repositories', 'MarketTimeSeriesRepository')
_EXPORTS['MarketDataProvider'] = ('digital_twin.modules.market_data.domain.repositories', 'MarketDataProvider')

_EXPORTS['MarketDataProviderFactory'] = ('digital_twin.modules.market_data.domain.repositories', 'MarketDataProviderFactory')

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
