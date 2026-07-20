from typing import Dict, List

from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads


class MySQLPsychologyShadowReadStore(MySQLOperationalConnection):
    def load_latest(self, account_id: str = "", limit: int = 50) -> Dict[str, object]:
        return dict(self.load_latest_page(account_id, limit).get("states") or {})

    def load_latest_page(self, account_id: str = "", limit: int = 50) -> Dict[str, object]:
        clean_account_id = str(account_id or "").strip()
        safe_limit = max(1, min(500, int(limit or 50)))
        where = "WHERE account_id = %s" if clean_account_id else ""
        query_limit = safe_limit + 1
        params = (clean_account_id, query_limit) if clean_account_id else (query_limit,)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT account_id, payload_json
                FROM monitor_snapshots
                %s
                ORDER BY updated_at DESC, account_id ASC
                LIMIT %%s
                """ % where,
                params,
            ).fetchall()
        states = {
            str(row["account_id"]): _json_loads(row["payload_json"], {})
            for row in rows[:safe_limit]
        }
        return {"states": states, "truncated": len(rows) > safe_limit}

    def load_history(self, account_id: str, limit: int = 20) -> List[Dict[str, object]]:
        safe_limit = max(1, min(100, int(limit or 20)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM monitor_snapshot_history
                WHERE account_id = %s
                ORDER BY generated_at DESC
                LIMIT %s
                """,
                (str(account_id or ""), safe_limit),
            ).fetchall()
        return [
            payload
            for payload in reversed([_json_loads(row["payload_json"], {}) for row in rows])
            if payload
        ]
