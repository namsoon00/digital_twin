"""Explicit, lazy public surface for the market_data module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'verified_bls_statistics': ('digital_twin.modules.market_data.domain.bls_statistics', 'verified_bls_statistics'),
 'InformationObservationService': ('digital_twin.modules.market_data.application.information_observation_service', 'InformationObservationService'),
 'CapitalFlowService': ('digital_twin.modules.market_data.application.capital_flow_service',
                        'CapitalFlowService'),
 'CollectionJob': ('digital_twin.modules.market_data.application.external_data.contracts', 'CollectionJob'),
 'CollectionPartition': ('digital_twin.modules.market_data.application.external_data.contracts',
                         'CollectionPartition'),
 'DatasetDescriptor': ('digital_twin.modules.market_data.application.external_data.contracts',
                       'DatasetDescriptor'),
 'ExternalDataCollectionService': ('digital_twin.modules.market_data.application.external_data.collection_service',
                                   'ExternalDataCollectionService'),
 'ExternalDataConfigurationRecoveryService': ('digital_twin.modules.market_data.application.external_data.configuration_recovery_service',
                                              'ExternalDataConfigurationRecoveryService'),
 'ExternalDatasetRegistry': ('digital_twin.modules.market_data.application.external_data.registry',
                             'ExternalDatasetRegistry'),
 'ExternalFactResearchEvidenceReconciler': ('digital_twin.modules.market_data.application.external_data.research_evidence_projection_service',
                                            'ExternalFactResearchEvidenceReconciler'),
 'ExternalOfficialEvidenceProjectionService': ('digital_twin.modules.market_data.application.external_data.research_evidence_projection_service',
                                               'ExternalOfficialEvidenceProjectionService'),
 'ExternalSignalsReadModelService': ('digital_twin.modules.market_data.application.external_data.read_model_service',
                                     'ExternalSignalsReadModelService'),
 'ExternalSubject': ('digital_twin.modules.market_data.application.external_data.contracts',
                     'ExternalSubject'),
 'FollowupCollectionRequest': ('digital_twin.modules.market_data.application.external_data.contracts',
                               'FollowupCollectionRequest'),
 'KISRealtimeWebSocketRunner': ('digital_twin.modules.market_data.application.kis_realtime_service',
                                'KISRealtimeWebSocketRunner'),
 'MarketDataCollectionRunner': ('digital_twin.modules.market_data.application.market_data_collection_service',
                                'MarketDataCollectionRunner'),
 'MonitorRunner': ('digital_twin.modules.market_data.application.monitoring_service', 'MonitorRunner'),
 'SourceObservation': ('digital_twin.modules.market_data.application.external_data.contracts',
                       'SourceObservation'),
 'TemporalFeatureSnapshotService': ('digital_twin.modules.market_data.application.time_series_platform',
                                    'TemporalFeatureSnapshotService'),
 'TimeSeriesBackendPlatformService': ('digital_twin.modules.market_data.application.time_series_platform',
                                      'TimeSeriesBackendPlatformService'),
 'TimeSeriesProjectionRunner': ('digital_twin.modules.market_data.application.time_series_platform',
                                'TimeSeriesProjectionRunner'),
 'VersionedMarketTimeSeriesStore': ('digital_twin.modules.market_data.application.time_series_platform',
                                    'VersionedMarketTimeSeriesStore'),
 'bounded_int': ('digital_twin.modules.market_data.application.external_data.contracts', 'bounded_int'),
 'merge_external_signal_read_models': ('digital_twin.modules.market_data.application.external_data.read_model_service',
                                       'merge_external_signal_read_models'),
 'sec_metadata_access_ready': ('digital_twin.modules.market_data.application.external_data.configuration_recovery_service',
                               'sec_metadata_access_ready'),
 'setting_enabled': ('digital_twin.modules.market_data.application.external_data.contracts',
                     'setting_enabled'),
 'truthy': ('digital_twin.modules.market_data.application.time_series_platform', 'truthy')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
