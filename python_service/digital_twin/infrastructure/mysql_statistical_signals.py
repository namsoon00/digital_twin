"""MySQL persistence for immutable model-signal snapshots and latest heads."""

import json
from datetime import datetime, timezone
from typing import Dict, List

from ..domain.statistical_signals import ModelSignalSnapshot
from .mysql_operational_connection import MySQLOperationalConnection


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: object, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


class MySQLStatisticalModelSignalStore(MySQLOperationalConnection):
    """Keep immutable changed snapshots and one latest head per signal slot."""

    def save(self, snapshot: ModelSignalSnapshot) -> Dict[str, object]:
        payload = snapshot.to_dict()
        stamp = _iso_utc()

        def mutate(connection):
            existing = {}
            if snapshot.subjects:
                params: List[object] = [snapshot.account_id, snapshot.model_release_id]
                params.extend(snapshot.subjects)
                rows = connection.execute(
                    "SELECT account_id, subject_id, signal_type, model_release_id, material_hash "
                    "FROM statistical_model_signal_heads WHERE account_id = %s AND model_release_id = %s "
                    "AND subject_id IN (" + ",".join(["%s"] * len(snapshot.subjects)) + ")",
                    params,
                ).fetchall()
                existing = {
                    (
                        str(row.get("account_id") or ""),
                        str(row.get("subject_id") or ""),
                        str(row.get("signal_type") or ""),
                        str(row.get("model_release_id") or ""),
                    ): str(row.get("material_hash") or "")
                    for row in rows or []
                }
            changed = [
                signal
                for signal in snapshot.signals
                if existing.get((
                    snapshot.account_id,
                    signal.subject_id,
                    signal.signal_type,
                    signal.model_release_id,
                )) != signal.material_hash
            ]
            current_keys = {
                (snapshot.account_id, signal.subject_id, signal.signal_type, signal.model_release_id)
                for signal in snapshot.signals
            }
            removed_keys = [key for key in existing if key not in current_keys]
            inserted = 0
            if changed or removed_keys:
                cursor = connection.execute(
                    """
                    INSERT IGNORE INTO statistical_model_signal_snapshots (
                        snapshot_id, account_id, as_of, source_feature_snapshot_id,
                        feature_set_version, model_release_id, signal_count,
                        subjects_json, payload_json, material_hash, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.account_id,
                        snapshot.as_of,
                        snapshot.source_feature_snapshot_id,
                        snapshot.feature_set_version,
                        snapshot.model_release_id,
                        len(snapshot.signals),
                        _json(payload.get("subjects") or []),
                        _json(payload),
                        snapshot.material_hash,
                        stamp,
                    ),
                )
                inserted = int(getattr(cursor, "rowcount", 0) or 0)
                if changed:
                    connection.executemany(
                        """
                        INSERT INTO statistical_model_signal_heads (
                            account_id, subject_id, signal_type, model_release_id,
                            snapshot_id, signal_id, material_hash, observed_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            snapshot_id = VALUES(snapshot_id),
                            signal_id = VALUES(signal_id),
                            material_hash = VALUES(material_hash),
                            observed_at = VALUES(observed_at),
                            updated_at = VALUES(updated_at)
                        """,
                        [(
                            snapshot.account_id,
                            signal.subject_id,
                            signal.signal_type,
                            signal.model_release_id,
                            snapshot.snapshot_id,
                            signal.signal_id,
                            signal.material_hash,
                            signal.observed_at,
                            stamp,
                        ) for signal in changed],
                    )
                if removed_keys:
                    connection.executemany(
                        "DELETE FROM statistical_model_signal_heads WHERE "
                        "account_id = %s AND subject_id = %s AND signal_type = %s AND model_release_id = %s",
                        removed_keys,
                    )
            return {
                "status": "changed" if changed or removed_keys else "unchanged",
                "snapshotInserted": bool(inserted),
                "changedSignalCount": len(changed),
                "unchangedSignalCount": len(snapshot.signals) - len(changed),
                "removedSignalCount": len(removed_keys),
                "snapshotId": snapshot.snapshot_id,
                "materialHash": snapshot.material_hash,
            }

        return self.transaction_with_deadlock_retry("statistical-model-signal-save", mutate)

    def latest(self, account_id: str, subject_id: str = "", model_release_id: str = "") -> Dict[str, object]:
        clauses = ["h.account_id = %s"]
        params: List[object] = [str(account_id or "")]
        if subject_id:
            clauses.append("h.subject_id = %s")
            params.append(str(subject_id or "").upper())
        if model_release_id:
            clauses.append("h.model_release_id = %s")
            params.append(str(model_release_id or ""))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT s.* FROM statistical_model_signal_heads h "
                "JOIN statistical_model_signal_snapshots s ON s.snapshot_id = h.snapshot_id "
                "WHERE " + " AND ".join(clauses)
                + " ORDER BY s.as_of DESC, s.created_at DESC LIMIT 20",
                params,
            ).fetchall()
        snapshots = []
        for row in rows or []:
            payload = _loads(row.get("payload_json"), {})
            if subject_id:
                payload["signals"] = [
                    item for item in payload.get("signals") or []
                    if str(item.get("subjectId") or "").upper() == str(subject_id or "").upper()
                ]
                payload["signalCount"] = len(payload["signals"])
            payload["createdAt"] = str(row.get("created_at") or "")
            snapshots.append(payload)
        return snapshots[0] if snapshots else {}

    def status(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS head_count, COUNT(DISTINCT subject_id) AS subject_count, "
                "MAX(updated_at) AS latest_updated_at FROM statistical_model_signal_heads"
            ).fetchone() or {}
        return {
            "status": "ready",
            "headCount": int(row.get("head_count") or 0),
            "subjectCount": int(row.get("subject_count") or 0),
            "latestUpdatedAt": str(row.get("latest_updated_at") or ""),
        }
