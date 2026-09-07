import json
from typing import Dict

from .mysql_operational_connection import MySQLOperationalConnection
from .settings import utc_now


class MySQLRuntimeCheckpointStore(MySQLOperationalConnection):
    def load(self, checkpoint_id: str) -> Dict[str, object]:
        normalized_id = str(checkpoint_id or "").strip()
        if not normalized_id:
            return {}
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM service_runtime_checkpoints WHERE checkpoint_id = %s",
                (normalized_id,),
            ).fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save(self, checkpoint_id: str, payload: Dict[str, object]) -> None:
        normalized_id = str(checkpoint_id or "").strip()
        if not normalized_id:
            raise ValueError("checkpoint_id is required")
        encoded = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO service_runtime_checkpoints (checkpoint_id, payload_json, updated_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (normalized_id, encoded, stamp),
            )
