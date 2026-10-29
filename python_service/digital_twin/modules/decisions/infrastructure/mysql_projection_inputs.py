"""Bounded migration/repair of decision memory, outside live inference."""

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from ..domain.decision_projection_input import DECISION_PROJECTION_INPUT_VERSION
from .projection_input_write import upsert_decision_projection_input


class MySQLDecisionProjectionInputStore(MySQLOperationalConnection):
    def repair(self, limit=25):
        maximum = max(1, min(100, int(limit)))
        with self.transaction() as connection:
            candidates = connection.execute(
                "SELECT episodes.episode_id FROM investment_decision_episodes AS episodes "
                "LEFT JOIN investment_decision_projection_inputs AS projection "
                "ON projection.episode_id = episodes.episode_id "
                "WHERE projection.episode_id IS NULL "
                "OR projection.source_updated_at <> episodes.updated_at "
                "OR projection.format_version <> %s "
                "ORDER BY episodes.decided_at DESC, episodes.episode_id DESC LIMIT %s",
                (DECISION_PROJECTION_INPUT_VERSION, maximum),
            ).fetchall()
            repaired = 0
            for candidate in candidates:
                row = connection.execute(
                    "SELECT episode_id, updated_at, payload_json FROM investment_decision_episodes "
                    "WHERE episode_id = %s FOR UPDATE SKIP LOCKED", (candidate["episode_id"],),
                ).fetchone()
                if row:
                    upsert_decision_projection_input(connection, row["episode_id"],
                                                     _json_loads(row["payload_json"], {}), row["updated_at"])
                    repaired += 1
            orphans = connection.execute(
                "SELECT projection.episode_id FROM investment_decision_projection_inputs AS projection "
                "LEFT JOIN investment_decision_episodes AS episodes ON episodes.episode_id = projection.episode_id "
                "WHERE episodes.episode_id IS NULL ORDER BY projection.episode_id LIMIT %s", (maximum,),
            ).fetchall()
            for row in orphans:
                connection.execute(
                    "DELETE FROM investment_decision_projection_inputs WHERE episode_id = %s "
                    "AND NOT EXISTS (SELECT 1 FROM investment_decision_episodes WHERE episode_id = %s)",
                    (row["episode_id"], row["episode_id"]),
                )
        return {"status": "repaired" if repaired or orphans else ("locked" if candidates else "current"),
                "repairedCount": repaired, "orphanCount": len(orphans), "batchLimit": maximum}
