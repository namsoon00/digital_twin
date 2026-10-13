"""Commit watchlist membership, its event and monitor wake atomically."""
from digital_twin.modules.market_data.infrastructure.monitor_refresh import record_monitor_refresh


def record_watchlist_refresh(connection, event):
    record_monitor_refresh(connection.execute, event.aggregate_id, event.occurred_at)
