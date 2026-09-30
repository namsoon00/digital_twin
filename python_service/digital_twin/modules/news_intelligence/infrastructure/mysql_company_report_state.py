"""One durable notification baseline per account/symbol; no outbox for quiet reads."""

from contextlib import contextmanager
import hashlib

from digital_twin.infrastructure.mysql_operational_core_stores import MySQLAppStore


class MySQLCompanyReportStateStore(MySQLAppStore):
    @staticmethod
    def _key(account_id, symbol):
        return "company-report:" + hashlib.sha256((account_id + ":" + symbol).encode()).hexdigest()[:48]

    def research_record(self, account_id, symbol):
        state = MySQLAppStore(self.runtime_settings)
        state.store_id = self._key(account_id, symbol)
        return state.load().get("researchRecord", {})

    def research_memory(self, account_id, symbol, cutoff_at):
        from ..domain.company_research_record import company_research_memory
        return company_research_memory(self.research_record(account_id, symbol), account_id, symbol, cutoff_at)

    @contextmanager
    def subject(self, account_id, symbol):
        # Separate instance avoids a mutable shared store_id across requests.
        state = MySQLAppStore(self.runtime_settings)
        state.store_id = self._key(account_id, symbol)
        with self.connect() as connection:
            row = connection.execute("SELECT GET_LOCK(%s, 0) AS acquired", (state.store_id,)).fetchone()
            if not row or row.get("acquired") != 1:
                yield None
                return
            try:
                yield state
            finally:
                connection.execute("SELECT RELEASE_LOCK(%s)", (state.store_id,)).fetchone()
