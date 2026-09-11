"""Explicit, lazy public surface for the investment_calendar module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'InvestmentCalendarCandidateService': ('digital_twin.modules.investment_calendar.application.investment_calendar_candidate_service',
                                        'InvestmentCalendarCandidateService'),
 'InvestmentCalendarDiscoveryService': ('digital_twin.modules.investment_calendar.application.investment_calendar_discovery_service',
                                        'InvestmentCalendarDiscoveryService'),
 'InvestmentCalendarExtractionService': ('digital_twin.modules.investment_calendar.application.investment_calendar_extraction_service',
                                         'InvestmentCalendarExtractionService'),
 'InvestmentCalendarResearchRecommendationService': ('digital_twin.modules.investment_calendar.application.investment_calendar_research_service',
                                                     'InvestmentCalendarResearchRecommendationService'),
 'InvestmentCalendarRunner': ('digital_twin.modules.investment_calendar.application.investment_calendar_service',
                              'InvestmentCalendarRunner'),
 'InvestmentCalendarService': ('digital_twin.modules.investment_calendar.application.investment_calendar_service',
                               'InvestmentCalendarService'),
 'OfficialCalendarSyncService': ('digital_twin.modules.investment_calendar.application.official_calendar_sync_service',
                                 'OfficialCalendarSyncService')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
