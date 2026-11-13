"""Wire an independent, quote-driven source boundary into compiled delivery."""
from copy import deepcopy


def build_market_source_refresh(settings, registry, deployment_id):
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import data_dir
    from digital_twin.infrastructure.kis_market_signals import KISMarketSignalProvider, KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID
    from digital_twin.infrastructure.mysql_realtime_workload_guard import MySQLRealtimeWorkloadGuard
    from digital_twin.modules.market_data.public import MarketSourceRefreshService
    from digital_twin.modules.market_data.contracts import RealtimeMonitor
    from digital_twin.modules.market_data.domain.market_source_refresh import market_position_from_cache

    state_store = stores.monitor_store(settings)
    quote_store = stores.market_quote_cache(settings)
    kis = KISMarketSignalProvider(settings=settings, quote_cache=quote_store)
    recorder = stores.monitoring_cycle_recorder(settings, state_store, stores.market_time_series_store(settings))

    def quotes(account_id, positions):
        symbols = [p.symbol for p in positions if not p.is_cash()]
        toss_rows = quote_store.load_many("toss", account_id, symbols)
        kis_rows = quote_store.load_many(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, symbols)
        result = {}
        for position in positions:
            proposals = []
            if position.symbol in toss_rows:
                proposals.append(market_position_from_cache(position, toss_rows[position.symbol]))
            if position.symbol in kis_rows:
                # Merge an already durable provider packet, without REST calls.
                proposals.append(kis.merge_position(deepcopy(position), kis_rows[position.symbol]))
            result[position.symbol] = proposals
        return result

    def owns_delivery():
        control = registry.control()
        return control.active_deployment_id == deployment_id == control.delivery_deployment_id

    return MarketSourceRefreshService(
        accounts=stores.account_reader(settings).load,
        state_reader=state_store.latest_account_state, quote_reader=quotes,
        recorder=recorder, monitor=RealtimeMonitor(settings), state_store=state_store,
        authority=owns_delivery, deployment_id=deployment_id, settings=settings,
        guard=MySQLRealtimeWorkloadGuard(data_dir() / "mysql-realtime-workload.lock").market_source_turn,
    )
