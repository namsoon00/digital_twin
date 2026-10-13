"""Bounded durable per-subject progress and a restart-safe rotation cursor."""
from contextlib import contextmanager
import hashlib
from digital_twin.infrastructure.mysql_operational_core_stores import MySQLAppStore


class MySQLCompanyPreparationStore(MySQLAppStore):
    @staticmethod
    def key(account_id, symbol):
        return "company-preparation:" + hashlib.sha256((account_id + ":" + symbol).encode()).hexdigest()[:40]

    def state(self, account_id, symbol):
        state = MySQLAppStore(self.runtime_settings)
        state.store_id = self.key(account_id, symbol)
        return state

    def record(self, account_id, symbol):
        return self.state(account_id, symbol).load()

    @contextmanager
    def subject(self, account_id, symbol):
        state = self.state(account_id, symbol)
        # Named locks are server-wide, so test and production databases must
        # never contend for the same rotation or subject lock.
        lock_id = "company-prep:" + hashlib.sha256((str(self.mysql_config["database"]) + ":" + state.store_id).encode()).hexdigest()[:40]
        with self.connect() as connection:
            row = connection.execute("SELECT GET_LOCK(%s,0) AS acquired", (lock_id,)).fetchone()
            if not row or row.get("acquired") != 1:
                yield None
                return
            try:
                yield state
            finally:
                connection.execute("SELECT RELEASE_LOCK(%s)", (lock_id,)).fetchone()
