"""Bounded restoration of legacy decision receipts outside worker startup."""

from digital_twin.infrastructure.mysql_operational_connection import MYSQL_SCHEMA, MySQLOperationalConnection


class MySQLLegacyDecisionReceiptRepairStore(MySQLOperationalConnection):
    def repair(self, limit=25):
        maximum = max(1, min(100, int(limit)))
        statement = next(sql for sql in MYSQL_SCHEMA
                         if sql.lstrip().startswith("INSERT IGNORE INTO decision_notification_receipts ("))
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT jobs.job_id FROM notification_jobs AS jobs WHERE decision_episode_id <> '' "
                "AND NOT EXISTS (SELECT 1 FROM decision_notification_receipts AS receipts "
                "WHERE receipts.job_id = jobs.job_id) ORDER BY jobs.job_id LIMIT %s", (maximum,),
            ).fetchall()
            count = 0
            if rows:
                count = connection.execute(
                    statement + " AND job_id IN (" + ",".join("%s" for _ in rows) + ")",
                    tuple(row["job_id"] for row in rows),
                ).rowcount
        return {"status": "repaired" if count else "current", "receiptCount": count, "batchLimit": maximum}
