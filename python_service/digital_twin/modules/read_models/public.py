"""Explicit, lazy public surface for the read_models module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'ConsoleReadModelService': ('digital_twin.modules.read_models.application.console_read_model_service',
                             'ConsoleReadModelService'),
 'FlowLensService': ('digital_twin.modules.read_models.application.flow_lens_service', 'FlowLensService'),
 'InstrumentTimelineQueryService': ('digital_twin.modules.read_models.application.instrument_timeline_query_service',
                                    'InstrumentTimelineQueryService'),
 'InstrumentValuationQueryService': ('digital_twin.modules.read_models.application.instrument_valuation_query_service',
                                     'InstrumentValuationQueryService'),
 'InvestmentAnalysisService': ('digital_twin.modules.read_models.application.investment_analysis_service',
                               'InvestmentAnalysisService'),
 'InvestmentCaseQueryService': ('digital_twin.modules.read_models.application.investment_case_query_service',
                                'InvestmentCaseQueryService'),
 'InvestmentFlowQueryService': ('digital_twin.modules.read_models.application.investment_flow_query_service',
                                'InvestmentFlowQueryService'),
 'OntologyCatalogQueryService': ('digital_twin.modules.read_models.application.ontology_catalog_query_service',
                                 'OntologyCatalogQueryService'),
 'OntologyDiagnosticsService': ('digital_twin.modules.read_models.application.ontology_diagnostics_service',
                                'OntologyDiagnosticsService'),
 'enrich_symbol_display_records': ('digital_twin.modules.read_models.application.symbol_display_projection',
                                   'enrich_symbol_display_records')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
