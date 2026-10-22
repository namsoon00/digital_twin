"""Observe successful deliveries using the existing stored-quote read port."""
from datetime import timedelta
from ..domain.notification_timing import VERSION, MAX_QUOTE_DELAY_MINUTES, advance_timing, clock, timing_report


class NotificationTimingService:
    def __init__(self, store, receipt_source=None, market_series=None):
        self.store, self.receipts, self.series = store, receipt_source, market_series

    def observe(self, account_id, now):
        registered = 0
        for receipt in self.receipts.after(account_id, self.store.cursor(account_id)):
            contract = receipt.get("contract") or {}
            if contract.get("version") != VERSION or contract.get("accountId") != account_id or not contract.get("symbol"):
                continue
            self.store.register(receipt, now)
            registered += 1
        pending = self.store.pending(account_id, now)
        requests = []
        for row in pending:
            delivered = clock(row.get("deliveredAt"))
            if not delivered:
                continue
            targets = ([] if row.get("entry") else [("entry", delivered)]) + [
                (str(h), delivered + timedelta(minutes=h)) for h in row["contract"]["horizons"]
                if str(h) not in row.get("outcomes", {})]
            for key, target in targets:
                if target <= clock(now):
                    requests.append({"requestId": row["receiptId"] + "/" + key, "symbol": row["contract"]["symbol"],
                                     "targetAt": target.isoformat(), "allowedGranularities": ["1m"],
                                     "maximumObservationDelayMinutes": MAX_QUOTE_DELAY_MINUTES})
        loaded = self.series.load_outcome_observations(account_id, requests, max_delay_minutes=MAX_QUOTE_DELAY_MINUTES) if requests else {}
        for row in pending:
            prefix = row["receiptId"] + "/"
            observations = {key[len(prefix):]: value for key, value in loaded.items() if key.startswith(prefix)}
            self.store.save(advance_timing(row, observations, now), now)
        return {"status": "observed", "registeredReceiptCount": registered, "checkedReceiptCount": len(pending)}

    def performance(self, account_id="", symbol="", limit=100):
        return timing_report(self.store.report_rows(account_id, symbol, limit))
