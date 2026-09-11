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


_EXPORTS.update({
    'CAPABILITY_LABELS': ('digital_twin.modules.market_data.domain.market_evidence_profiles', 'CAPABILITY_LABELS'),
    'COMPLETION_MODE_VERIFIED_LATER_BOUNDARY': ('digital_twin.modules.market_data.domain.market_observation_reasoning', 'COMPLETION_MODE_VERIFIED_LATER_BOUNDARY'),
    'CRYPTO_TRANSITION_BASELINE_METADATA_KEY': ('digital_twin.modules.market_data.domain.crypto_market_signals', 'CRYPTO_TRANSITION_BASELINE_METADATA_KEY'),
    'EXTERNAL_FACT_CHANGED': ('digital_twin.modules.market_data.domain.event_types', 'EXTERNAL_FACT_CHANGED'),
    'INVESTOR_PARTY_FIELDS': ('digital_twin.modules.market_data.domain.investor_flow_psychology', 'INVESTOR_PARTY_FIELDS'),
    'MARKET_OBSERVATION_REASONING_RECEIPT_VERSION': ('digital_twin.modules.market_data.domain.market_observation_reasoning', 'MARKET_OBSERVATION_REASONING_RECEIPT_VERSION'),
    'MARKET_SIGNAL_TRANSITION_RESULTS_KEY': ('digital_twin.modules.market_data.domain.market_signal_transitions', 'MARKET_SIGNAL_TRANSITION_RESULTS_KEY'),
    'MARKET_SIGNAL_TRANSITION_STATE_KEY': ('digital_twin.modules.market_data.domain.market_signal_transitions', 'MARKET_SIGNAL_TRANSITION_STATE_KEY'),
    'MONITORING_ALERTS_DETECTED': ('digital_twin.modules.market_data.domain.event_types', 'MONITORING_ALERTS_DETECTED'),
    'MONITORING_SNAPSHOT_COLLECTED': ('digital_twin.modules.market_data.domain.event_types', 'MONITORING_SNAPSHOT_COLLECTED'),
    'MarketObservationReasoningReceipt': ('digital_twin.modules.market_data.domain.market_observation_reasoning', 'MarketObservationReasoningReceipt'),
    'RealtimeMonitor': ('digital_twin.modules.market_data.domain.monitoring', 'RealtimeMonitor'),
    'TEMPORAL_FEATURE_SET_VERSION': ('digital_twin.modules.market_data.domain.time_series_storage', 'TEMPORAL_FEATURE_SET_VERSION'),
    'TemporalFeatureSnapshot': ('digital_twin.modules.market_data.domain.time_series_storage', 'TemporalFeatureSnapshot'),
    'TimeSeriesWatermark': ('digital_twin.modules.market_data.domain.time_series_storage', 'TimeSeriesWatermark'),
    'age_minutes': ('digital_twin.modules.market_data.domain.data_freshness', 'age_minutes'),
    'aggregate_freshness': ('digital_twin.modules.market_data.domain.data_freshness', 'aggregate_freshness'),
    'alerts_detected_event': ('digital_twin.modules.market_data.domain.events', 'alerts_detected_event'),
    'bool_setting': ('digital_twin.modules.market_data.domain.data_freshness', 'bool_setting'),
    'candle_close': ('digital_twin.modules.market_data.domain.market_data', 'candle_close'),
    'canonical_json': ('digital_twin.modules.market_data.domain.time_series_storage', 'canonical_json'),
    'clamp': ('digital_twin.modules.market_data.domain.market_data', 'clamp'),
    'completion_mode': ('digital_twin.modules.market_data.domain.market_observation_reasoning', 'completion_mode'),
    'crypto_freshness': ('digital_twin.modules.market_data.domain.crypto_market_signals', 'crypto_freshness'),
    'crypto_freshness_is_usable': ('digital_twin.modules.market_data.domain.crypto_market_signals', 'crypto_freshness_is_usable'),
    'crypto_market_observation_events': ('digital_twin.modules.market_data.domain.crypto_market_signals', 'crypto_market_observation_events'),
    'crypto_market_positions': ('digital_twin.modules.market_data.domain.crypto_market_signals', 'crypto_market_positions'),
    'crypto_market_transitions': ('digital_twin.modules.market_data.domain.crypto_market_signals', 'crypto_market_transitions'),
    'crypto_markets_by_symbol': ('digital_twin.modules.market_data.domain.crypto_market_signals', 'crypto_markets_by_symbol'),
    'crypto_transition_materiality_assessment': ('digital_twin.modules.market_data.domain.crypto_market_signals', 'crypto_transition_materiality_assessment'),
    'crypto_transition_targets': ('digital_twin.modules.market_data.domain.crypto_market_signals', 'crypto_transition_targets'),
    'data_freshness_required': ('digital_twin.modules.market_data.domain.data_freshness', 'data_freshness_required'),
    'default_market_hours_enabled': ('digital_twin.modules.market_data.domain.market_hours', 'default_market_hours_enabled'),
    'default_market_hours_markets': ('digital_twin.modules.market_data.domain.market_hours', 'default_market_hours_markets'),
    'default_off_hours_delivery_mode': ('digital_twin.modules.market_data.domain.market_hours', 'default_off_hours_delivery_mode'),
    'evaluate_market_hours': ('digital_twin.modules.market_data.domain.market_hours', 'evaluate_market_hours'),
    'evaluate_notification_data_freshness': ('digital_twin.modules.market_data.domain.data_freshness', 'evaluate_notification_data_freshness'),
    'external_api_source_line': ('digital_twin.modules.market_data.domain.external_api_sources', 'external_api_source_line'),
    'first_number': ('digital_twin.modules.market_data.domain.market_data', 'first_number'),
    'freshness_from_snapshot_subject': ('digital_twin.modules.market_data.domain.data_freshness', 'freshness_from_snapshot_subject'),
    'freshness_record': ('digital_twin.modules.market_data.domain.data_freshness', 'freshness_record'),
    'int_setting': ('digital_twin.modules.market_data.domain.data_freshness', 'int_setting'),
    'investor_flow_contract': ('digital_twin.modules.market_data.domain.investor_flow_psychology', 'investor_flow_contract'),
    'investor_flow_observation': ('digital_twin.modules.market_data.domain.investor_flow_psychology', 'investor_flow_observation'),
    'investor_flow_observed_fields': ('digital_twin.modules.market_data.domain.investor_flow_psychology', 'investor_flow_observed_fields'),
    'investor_flow_values_reliable': ('digital_twin.modules.market_data.domain.investor_flow_psychology', 'investor_flow_values_reliable'),
    'investor_net_volume': ('digital_twin.modules.market_data.domain.market_data', 'investor_net_volume'),
    'kis_stage_freshness_records': ('digital_twin.modules.market_data.domain.data_freshness', 'kis_stage_freshness_records'),
    'known_stock': ('digital_twin.modules.market_data.domain.market_data', 'known_stock'),
    'macro_context_facts': ('digital_twin.modules.market_data.domain.macro_context', 'macro_context_facts'),
    'market_evidence_profile': ('digital_twin.modules.market_data.domain.market_evidence_profiles', 'market_evidence_profile'),
    'market_observation_completion_scope': ('digital_twin.modules.market_data.domain.market_observation_reasoning', 'market_observation_completion_scope'),
    'market_session_date': ('digital_twin.modules.market_data.domain.market_time_series', 'market_session_date'),
    'market_signal_transition_policies': ('digital_twin.modules.market_data.domain.market_signal_transitions', 'market_signal_transition_policies'),
    'market_signal_transition_policy_snapshot': ('digital_twin.modules.market_data.domain.market_signal_transitions', 'market_signal_transition_policy_snapshot'),
    'market_timezone': ('digital_twin.modules.market_data.domain.market_time_series', 'market_timezone'),
    'monitoring_cycle_completed_event': ('digital_twin.modules.market_data.domain.events', 'monitoring_cycle_completed_event'),
    'moving_average': ('digital_twin.modules.market_data.domain.market_data', 'moving_average'),
    'normalize_market_key': ('digital_twin.modules.market_data.domain.market_hours', 'normalize_market_key'),
    'normalize_off_hours_delivery_mode': ('digital_twin.modules.market_data.domain.market_hours', 'normalize_off_hours_delivery_mode'),
    'normalize_position': ('digital_twin.modules.market_data.domain.market_data', 'normalize_position'),
    'number': ('digital_twin.modules.market_data.domain.market_data', 'number'),
    'observable_follow_up_fields': ('digital_twin.modules.market_data.domain.market_evidence_profiles', 'observable_follow_up_fields'),
    'optional_number': ('digital_twin.modules.market_data.domain.market_data', 'optional_number'),
    'parse_datetime': ('digital_twin.modules.market_data.domain.data_freshness', 'parse_datetime'),
    'parse_timestamp': ('digital_twin.modules.market_data.domain.market_time_series', 'parse_timestamp'),
    'payload_fingerprint': ('digital_twin.modules.market_data.domain.time_series_storage', 'payload_fingerprint'),
    'pct_distance': ('digital_twin.modules.market_data.domain.market_data', 'pct_distance'),
    'previous_moving_average': ('digital_twin.modules.market_data.domain.market_data', 'previous_moving_average'),
    'sanitize_notification_context_for_freshness': ('digital_twin.modules.market_data.domain.data_freshness', 'sanitize_notification_context_for_freshness'),
    'sector_from_symbol': ('digital_twin.modules.market_data.domain.market_data', 'sector_from_symbol'),
    'snapshot_collected_event': ('digital_twin.modules.market_data.domain.events', 'snapshot_collected_event'),
    'sorted_candles': ('digital_twin.modules.market_data.domain.market_data', 'sorted_candles'),
    'technical_indicators_from_candles': ('digital_twin.modules.market_data.domain.market_data', 'technical_indicators_from_candles'),
    'trading_value_snapshot': ('digital_twin.modules.market_data.domain.volume_time_adjustment', 'trading_value_snapshot'),
    'utc_iso': ('digital_twin.modules.market_data.domain.data_freshness', 'utc_iso'),
    'volume_pace_snapshot': ('digital_twin.modules.market_data.domain.volume_time_adjustment', 'volume_pace_snapshot'),
})

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
