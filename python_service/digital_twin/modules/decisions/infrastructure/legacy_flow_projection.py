"""Repair legacy flow projections in bounded owner transactions.

Current decisions already write these projections atomically. Reuse the exact
legacy expressions, but constrain their source identities before reading audit
payloads or ranking heads. INSERT IGNORE preserves concurrent current writes.
"""

from digital_twin.infrastructure.mysql_operational_connection import MYSQL_SCHEMA, MySQLOperationalConnection


class MySQLLegacyDecisionFlowRepairStore(MySQLOperationalConnection):
    def repair(self, limit=25):
        maximum = max(1, min(100, int(limit)))
        head_sql = next(sql for sql in MYSQL_SCHEMA
                        if sql.lstrip().startswith("INSERT IGNORE INTO investment_flow_heads ("))
        current_sql = next(sql for sql in MYSQL_SCHEMA
                           if sql.lstrip().startswith("INSERT IGNORE INTO investment_flow_current ("))
        head_count = current_count = 0
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT episodes.episode_id FROM investment_decision_episodes AS episodes "
                "WHERE NOT EXISTS (SELECT 1 FROM investment_flow_heads AS heads "
                "WHERE heads.account_id = episodes.account_id AND heads.symbol = UPPER(episodes.symbol) "
                "AND heads.decision_episode_id = episodes.episode_id) "
                "ORDER BY episodes.episode_id LIMIT %s", (maximum,),
            ).fetchall()
            for row in rows:
                head_count += connection.execute(
                    head_sql + " AND episodes.episode_id = %s", (row["episode_id"],),
                ).rowcount
            subjects = connection.execute(
                "SELECT heads.account_id, heads.symbol FROM investment_flow_heads AS heads "
                "WHERE NOT EXISTS (SELECT 1 FROM investment_flow_current AS current "
                "WHERE current.account_id = heads.account_id AND current.symbol = heads.symbol) "
                "GROUP BY heads.account_id, heads.symbol ORDER BY heads.account_id, heads.symbol LIMIT %s",
                (maximum,),
            ).fetchall()
            for subject in subjects:
                # Backfill every missing head for this subject before choosing
                # its current row. An incomplete legacy batch cannot make an
                # older episode look like the latest decision.
                missing = connection.execute(
                    "SELECT episode_id FROM investment_decision_episodes AS episodes "
                    "WHERE account_id = %s AND UPPER(symbol) = %s AND NOT EXISTS "
                    "(SELECT 1 FROM investment_flow_heads AS heads WHERE heads.account_id = episodes.account_id "
                    "AND heads.symbol = UPPER(episodes.symbol) AND heads.decision_episode_id = episodes.episode_id) LIMIT 1",
                    (subject["account_id"], subject["symbol"]),
                ).fetchone()
                if missing:
                    continue
                current_count += connection.execute(current_sql.replace(
                    "FROM investment_flow_heads AS heads",
                    "FROM investment_flow_heads AS heads WHERE heads.account_id = %s AND heads.symbol = %s",
                ), (subject["account_id"], subject["symbol"])).rowcount
        return {"status": "repaired" if head_count or current_count else "current",
                "headCount": head_count, "currentCount": current_count, "batchLimit": maximum}
