"""Compatibility surface for independently owned engine and temporal stores."""

from digital_twin.modules._exports import resolve_export

_EXPORTS = {'MySQLReasoningEngineComparisonStore': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                                         'MySQLReasoningEngineComparisonStore'),
 'MySQLReasoningEngineJobStore': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                                  'MySQLReasoningEngineJobStore'),
 'MySQLReasoningEngineRegistryStore': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                                       'MySQLReasoningEngineRegistryStore'),
 'MySQLReasoningShadowJobStore': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                                  'MySQLReasoningShadowJobStore'),
 'MySQLTemporalFeatureSnapshotStore': ('digital_twin.modules.market_data.infrastructure.mysql_temporal_runtime',
                                       'MySQLTemporalFeatureSnapshotStore'),
 'MySQLTimeSeriesBackendRegistryStore': ('digital_twin.modules.market_data.infrastructure.mysql_temporal_runtime',
                                         'MySQLTimeSeriesBackendRegistryStore'),
 'MySQLTimeSeriesProjectionOutboxStore': ('digital_twin.modules.market_data.infrastructure.mysql_temporal_runtime',
                                          'MySQLTimeSeriesProjectionOutboxStore'),
 'REASONING_HEALTH_LIFECYCLE_KEYS': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                                     'REASONING_HEALTH_LIFECYCLE_KEYS'),
 'iso_utc': ('digital_twin.infrastructure.storage_values', 'iso_utc'),
 'json_value': ('digital_twin.infrastructure.storage_values', 'json_value'),
 'local_process_is_alive': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                            'local_process_is_alive'),
 'merge_reasoning_deployment_health': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                                       'merge_reasoning_deployment_health'),
 'reasoning_failure_recovery_allowed': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                                        'reasoning_failure_recovery_allowed'),
 'reasoning_queue_deadlock_retry': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                                    'reasoning_queue_deadlock_retry'),
 'reasoning_worker_process_owner': ('digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime',
                                    'reasoning_worker_process_owner'),
 'utc_now': ('digital_twin.infrastructure.storage_values', 'utc_now')}

__all__ = list(_EXPORTS)

def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
