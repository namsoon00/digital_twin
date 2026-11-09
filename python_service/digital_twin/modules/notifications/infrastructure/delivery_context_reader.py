"""Read exact successful receipt bodies, including across notification routes."""
from datetime import timedelta
from ..domain.delivered_insight_context import TYPES, WINDOW_DAYS, context, instant


class DeliveredInsightContextReader:
    def __init__(self, store):
        self.store = store

    def read(self, account_id, symbol, cutoff):
        at = instant(cutoff)
        if at is None:
            raise ValueError("invalid delivery context cutoff")
        start = (at-timedelta(days=WINDOW_DAYS)).isoformat().replace("+00:00", "Z")
        # Subject identity is retained independently of large job payloads.
        # For older research receipts only, an existing scoped job supplies it;
        # its mutable text is never used as a delivered message.
        symbol_sql = ("COALESCE(m.subjectSymbol,m.conversationSymbol,m.aiSymbol,"
                      "CASE WHEN j.account_id=%s THEN j.symbol ELSE NULL END)")
        # Narrow account and eligible routes before decoding large receipts.
        # A time-only index also scans every research-activity receipt.
        projection = " JOIN JSON_TABLE(a.metadata_json, '$' COLUMNS (" + ",".join(
            name + " " + column_type + " PATH '$." + path + "' NULL ON EMPTY NULL ON ERROR"
            for name, column_type, path in (
                ("accountId", "VARCHAR(191)", "accountId"),
                ("messageType", "VARCHAR(64)", "messageType"),
                ("subjectSymbol", "VARCHAR(191)", "notificationSubject.symbol"),
                ("conversationSymbol", "VARCHAR(191)", "notificationConversation.symbol"),
                ("aiSymbol", "VARCHAR(191)", "aiControlObservation.symbol"),
                ("body", "LONGTEXT", "renderedMessage"),
                ("messageSha256", "VARCHAR(128)", "messageSha256"))) + ")) AS m ON TRUE "
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT /*+ MAX_EXECUTION_TIME(5000) */ recent.* FROM ("
                "SELECT a.job_id AS jobId,a.status,a.completed_at AS deliveredAt,"
                "m.accountId,m.messageType," + symbol_sql + " AS symbol,m.body,m.messageSha256,"
                "ROW_NUMBER() OVER (PARTITION BY m.messageType ORDER BY a.completed_at DESC,a.attempt_id DESC) AS routeRank "
                "FROM notification_delivery_attempts a FORCE INDEX (idx_notification_delivery_subject_scope) "
                + projection + "LEFT JOIN notification_jobs j ON j.job_id=a.job_id "
                "WHERE a.receipt_account_id=%s AND a.receipt_message_type IN ("
                + ",".join(["%s"] * len(TYPES)) + ") "
                "AND (a.receipt_subject_symbol=%s OR a.receipt_subject_symbol IS NULL) "
                "AND a.status='delivered' AND a.completed_at>=%s AND a.completed_at<=%s "
                "AND m.accountId=%s AND m.messageType IN (" + ",".join(["%s"] * len(TYPES)) + ") AND "
                + symbol_sql + "=%s) recent WHERE routeRank<=6",
                (account_id, account_id, *TYPES, symbol, start, cutoff, account_id, *TYPES, account_id, symbol)).fetchall()
        # Unavailable DB history raises for the existing task retry path; it
        # must not become an apparently empty first-observation context.
        return context(rows, account_id, symbol, cutoff,
                       query_limited=any(row["routeRank"] == 6 for row in rows))
