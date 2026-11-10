"""Indexed, bounded tails per source group without ranking full histories."""

from contextlib import contextmanager

GROUP_BATCH = 4


@contextmanager
def temporal_read_snapshot(connection):
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ", ())
    connection.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY", ())
    try:
        yield
    finally:
        import sys
        failing = sys.exc_info()[0] is not None
        try:
            connection.execute("ROLLBACK", ())
        except Exception:
            if not failing:
                raise


def latest_group_rows(connection, accounts, symbols, granularity, cutoff, limit):
    unique_accounts = list({str(account).casefold(): str(account) for account in accounts}.values())
    groups = [(account, symbol) for account in unique_accounts for symbol in symbols]
    rows = []
    for offset in range(0, len(groups), GROUP_BATCH):
        branches = []
        params = []
        for account, symbol in groups[offset:offset + GROUP_BATCH]:
            branches.append(
                "(SELECT observations.* FROM market_time_series_observations AS observations FORCE INDEX (PRIMARY) "
                "WHERE observations.account_id=%s AND observations.symbol=%s AND observations.granularity=%s"
                + (" AND observations.observed_at <= %s" if cutoff else "")
                + " ORDER BY observations.bucket_at DESC LIMIT %s)"
            )
            params.extend([account, symbol, granularity])
            if cutoff:
                params.append(cutoff)
            params.append(limit)
        rows.extend(connection.execute(
            "SELECT /* operation:temporal-group-tail */ * FROM (" + " UNION ALL ".join(branches)
            + ") selected_groups ORDER BY bucket_at DESC", tuple(params),
        ).fetchall())
    return rows
