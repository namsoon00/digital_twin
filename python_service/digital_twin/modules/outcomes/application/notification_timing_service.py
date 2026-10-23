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
        loaded = self._observations(account_id, pending, now)
        for row in pending:
            self.store.save(advance_timing(row, self._for_receipt(row, loaded), now), now)
        return {"status": "observed", "registeredReceiptCount": registered, "checkedReceiptCount": len(pending)}

    def repair_missing_entries(self, account_id, receipt_ids, now):
        """Explicit, receipt-scoped recovery using the original historical window."""
        rows = self.store.repairable_entries(account_id, receipt_ids)
        loaded = self._observations(account_id, rows, now)
        repaired = 0
        for row in rows:
            updated = advance_timing(row, self._for_receipt(row, loaded), now)
            if not updated.get("entry"):
                continue
            updated.pop("reason", None)
            updated["entryRecovery"] = {"repairedAt": now, "previousState": row["state"],
                "previousReason": row["reason"], "reason": "stored-minute-granularity-recovery-v1",
                "allowedGranularities": ["1m", "3m"]}
            repaired += bool(self.store.save(updated, now))
        return {"checkedReceiptCount": len(rows), "repairedReceiptCount": repaired}

    @staticmethod
    def _for_receipt(row, loaded):
        prefix = row["receiptId"] + "/"
        return {key[len(prefix):]: value for key, value in loaded.items() if key.startswith(prefix)}

    def _observations(self, account_id, rows, now):
        requests = []
        for row in rows:
            delivered = clock(row.get("deliveredAt"))
            if not delivered:
                continue
            targets = ([] if row.get("entry") else [("entry", delivered)]) + [
                (str(h), delivered + timedelta(minutes=h)) for h in row["contract"]["horizons"]
                if str(h) not in row.get("outcomes", {})]
            for key, target in targets:
                if target <= clock(now):
                    requests.append({"requestId": row["receiptId"] + "/" + key, "symbol": row["contract"]["symbol"],
                                     "targetAt": target.isoformat(), "allowedGranularities": ["1m", "3m"],
                                     "maximumObservationAt": now,
                                     "maximumObservationDelayMinutes": MAX_QUOTE_DELAY_MINUTES})
        return self.series.load_outcome_observations(account_id, requests, max_delay_minutes=MAX_QUOTE_DELAY_MINUTES) if requests else {}

    def performance(self, account_id="", symbol="", limit=100):
        return timing_report(self.store.report_rows(account_id, symbol, limit))
