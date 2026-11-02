"""Wire the report reader without opening AI execution or source collectors."""
from types import SimpleNamespace


def build_portfolio_report_service(*, writable=False, settings=None):
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.accounts.infrastructure.mysql_watchlist_account_reader import MySQLWatchlistAccountReader
    from digital_twin.modules.ai_orchestration.application.portfolio_reports import PortfolioReportService
    from digital_twin.modules.ai_orchestration.infrastructure.portfolio_report_sources import PortfolioReportSourceReader
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_portfolio_reports import MySQLPortfolioReportStore
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_portfolio_analysis import MySQLPortfolioAnalysisStore
    from digital_twin.modules.read_models.public import InstrumentValuationQueryService
    from digital_twin.modules.portfolio.contracts import InstrumentValuationQuery
    from digital_twin.modules.news_intelligence.contracts import build_company_change_report
    from digital_twin.infrastructure.transactions.ai_report_publication import AIReportPublication
    from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore

    configured = dict(settings if settings is not None else runtime_settings(fast_operational_read=True, require_store_read=True))
    read_settings = {**configured, "_skipOperationalSchemaBootstrap": "1", "_skipOperationalHistoryRetention": "1",
                     "_skipNotificationRuleDefaultsSeed": "1"}

    def company(state, account_id, symbol):
        # Every symbol uses the same captured account snapshot; no live reload.
        query = InstrumentValuationQueryService(monitor_store=SimpleNamespace(previous={account_id: state}), settings=read_settings)
        payload = query.query(InstrumentValuationQuery(symbol=symbol, account_id=account_id))
        return build_company_change_report(payload) if payload.get("companyReportEvidence") else {}

    reports = MySQLPortfolioReportStore(read_settings, initialize=writable)
    source = PortfolioReportSourceReader(read_settings, lambda: MySQLWatchlistAccountReader(read_settings).load_all(), company)
    publication = AIReportPublication(reports, lambda: MySQLNotificationJobStore(read_settings))
    return PortfolioReportService(reports, source, publication, analyses=MySQLPortfolioAnalysisStore(read_settings, initialize=writable))
