"""Wire research preparation to existing collectors, evidence and central AI."""


def preparation_subjects(settings):
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.modules.accounts.infrastructure.mysql_watchlist_account_reader import MySQLWatchlistAccountReader
    from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state
    from digital_twin.modules.reasoning.contracts import world_from_snapshot, market_world
    states = stores.monitor_store(settings).load_previous()
    result = {}
    for account in MySQLWatchlistAccountReader(settings).load_all():
        if not account.enabled:
            continue
        snapshot = account_snapshot_from_monitor_state(states.get(account.account_id, {}))
        live = bool(snapshot and snapshot.has_live_account_data())
        positions = snapshot.positions if snapshot else []
        watches = snapshot.watchlist if snapshot else []
        configured = set(account.watchlist_symbols or [])
        by_symbol = {row.symbol: row for row in positions + watches}
        held = {row.symbol for row in positions}
        for symbol in sorted(configured | held):
            row = by_symbol.get(symbol)
            world = world_from_snapshot(snapshot, settings) if live and row else None
            result[(account.account_id, symbol)] = {
                "accountId": account.account_id, "symbol": symbol, "name": (row.name if row else "") or symbol,
                "roles": (["holding"] if symbol in held else []) + (["watchlist"] if symbol in configured else []),
                "worldId": world.world_id if world else "",
                "marketWorldId": market_world(world.market_id, settings.get("ontologySharedMarketTenantId") or "shared").world_id if world else "",
                "observedAt": str((states.get(account.account_id) or {}).get("generatedAt") or ""),
            }
    return list(result.values())


def build_company_preparation(settings):
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.events import news_event_bus
    from digital_twin.infrastructure.composition.read_models import build_instrument_valuation_query_service
    from digital_twin.infrastructure.settings import utc_now
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_company_preparation import MySQLPreparationAI
    from digital_twin.modules.news_intelligence.application.company_preparation_service import CompanyPreparationService
    from digital_twin.modules.news_intelligence.application.company_relationship_research import CompanyRelationshipResearch
    from digital_twin.modules.news_intelligence.application.company_relationship_backfill import CompanyRelationshipBackfill
    from digital_twin.modules.news_intelligence.domain.company_preparation import relationship_events
    from digital_twin.modules.news_intelligence.infrastructure.mysql_company_preparation import MySQLCompanyPreparationStore
    from digital_twin.modules.portfolio.contracts import InstrumentValuationQuery
    evidence = stores.research_evidence_store(settings)
    publisher = news_event_bus(settings)

    def persist(subject, items):
        saved, events = evidence.upsert_many_with_events(items, relationship_events)
        for event in events:
            publisher.dispatch_recorded(event)
        return saved

    def report(subject):
        # Recreate the read-only snapshot reader for each bounded turn; workers
        # must not hold the account's startup snapshot indefinitely.
        return build_instrument_valuation_query_service(settings).query(
            InstrumentValuationQuery(symbol=subject["symbol"], account_id=subject["accountId"]))

    return CompanyPreparationService(
        subjects=lambda: preparation_subjects(settings), store=MySQLCompanyPreparationStore(settings), reports=report,
        relationships=CompanyRelationshipBackfill(evidence.relationship_documents,
            CompanyRelationshipResearch(stores.symbol_universe_store(settings)), persist, utc_now),
        ai=MySQLPreparationAI(settings), refresh=lambda account: stores.monitor_account_job_store(settings).request_refresh(account),
        settings=settings)
