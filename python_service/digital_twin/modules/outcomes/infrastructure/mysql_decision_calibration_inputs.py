"""Bounded repair of a disposable read model; never runs in inference."""

from digital_twin.infrastructure.mysql_operational_connection import (
    MySQLOperationalConnection,
)
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.modules.outcomes.domain.decision_calibration_input import (
    DECISION_CALIBRATION_INPUT_VERSION,
)
from digital_twin.modules.outcomes.infrastructure.transaction_writes import (
    upsert_decision_calibration_input,
)


class MySQLDecisionCalibrationInputStore(MySQLOperationalConnection):
    def repair(self, limit: int = 25) -> dict:
        maximum = max(1, min(100, int(limit)))
        with self.transaction() as connection:
            candidates = connection.execute(
                "SELECT episodes.episode_id "
                "FROM investment_decision_episodes AS episodes "
                "LEFT JOIN investment_decision_calibration_inputs AS calibration "
                "ON calibration.episode_id = episodes.episode_id "
                "WHERE calibration.episode_id IS NULL "
                "OR calibration.source_updated_at <> episodes.updated_at "
                "OR calibration.format_version <> %s "
                "ORDER BY episodes.episode_id LIMIT %s",
                (DECISION_CALIBRATION_INPUT_VERSION, maximum),
            ).fetchall()
            repaired = 0
            # Lock only selected primary keys. Locking the anti-join scan
            # would also block unrelated already-projected decisions.
            for candidate in candidates:
                row = connection.execute(
                    "SELECT episode_id, updated_at, "
                    "JSON_EXTRACT(payload_json, '$.hypothesisSet.hypotheses') AS hypotheses_json "
                    "FROM investment_decision_episodes WHERE episode_id = %s FOR UPDATE SKIP LOCKED",
                    (candidate["episode_id"],),
                ).fetchone()
                if not row:
                    continue
                upsert_decision_calibration_input(
                    connection,
                    row["episode_id"],
                    {"hypothesisSet": {
                        "hypotheses": _json_loads(row.get("hypotheses_json"), []),
                    }},
                    row["updated_at"],
                )
                repaired += 1
            orphans = connection.execute(
                "SELECT calibration.episode_id FROM investment_decision_calibration_inputs AS calibration "
                "LEFT JOIN investment_decision_episodes AS episodes ON episodes.episode_id = calibration.episode_id "
                "WHERE episodes.episode_id IS NULL ORDER BY calibration.episode_id LIMIT %s",
                (maximum,),
            ).fetchall()
            for row in orphans:
                connection.execute(
                    "DELETE FROM investment_decision_calibration_inputs WHERE episode_id = %s "
                    "AND NOT EXISTS (SELECT 1 FROM investment_decision_episodes WHERE episode_id = %s)",
                    (row["episode_id"], row["episode_id"]),
                )
        return {
            "status": "repaired" if repaired or orphans else ("locked" if candidates else "current"),
            "repairedCount": repaired,
            "orphanCount": len(orphans),
            "batchLimit": maximum,
        }
