"""Short-lived durable cache for exact ontology ABox assembly retries.

The cache stores only the already-derived local ABox payload for an exact
source snapshot fingerprint. It never decides an investment action and never
reuses a graph after its source, TypeDB TBox, RuleBox, or runtime settings
fingerprint changes. Its role is limited to avoiding repeated Python graph
assembly when an isolated reasoning worker is restarted or retries the same
mailbox revision.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict

from ..domain.ontology_projection_payload import (
    deserialize_portfolio_ontology,
    serialize_portfolio_ontology,
)
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


CACHE_PAYLOAD_VERSION = "ontology-graph-assembly-cache-v1"


def _timestamp_after(seconds: float) -> str:
    bounded = max(1.0, min(300.0, float(seconds or 1.0)))
    return (datetime.now(timezone.utc) + timedelta(seconds=bounded)).isoformat().replace("+00:00", "Z")


def _parsed_timestamp(value: object):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class MySQLOntologyGraphAssemblyCacheStore(MySQLOperationalConnection):
    """Persist a bounded, exact-source ABox assembly cache in local MySQL."""

    def get(self, cache_key: str, ttl_seconds: float) -> Dict[str, object]:
        key = str(cache_key or "").strip()
        if not key or ttl_seconds <= 0:
            return {"status": "disabled"}
        stamp = utc_now()
        try:
            with self.connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json, created_at, expires_at, payload_bytes
                    FROM ontology_graph_assembly_cache
                    WHERE cache_key = %s AND expires_at > %s
                    """,
                    (key, stamp),
                ).fetchone()
            if not row:
                return {"status": "miss"}
            payload = _json_loads(row.get("payload_json"), {})
            if not isinstance(payload, dict) or str(payload.get("version") or "") != CACHE_PAYLOAD_VERSION:
                return {"status": "miss", "reason": "invalid-cache-payload"}
            graph = deserialize_portfolio_ontology(payload.get("graph") or {})
            persistence_graph = deserialize_portfolio_ontology(payload.get("persistenceGraph") or {})
            if not graph.portfolio_id or not persistence_graph.portfolio_id:
                return {"status": "miss", "reason": "empty-cache-graph"}
            created = _parsed_timestamp(row.get("created_at"))
            age_ms = 0
            if created:
                age_ms = max(0, int((datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() * 1000))
            return {
                "status": "hit",
                "ageMs": age_ms,
                "payloadBytes": int(row.get("payload_bytes") or 0),
                "graph": graph,
                "persistenceGraph": persistence_graph,
            }
        except Exception as error:  # noqa: BLE001 - cache failure must not block a live TypeDB cycle.
            return {"status": "miss", "reason": str(error)[:180]}

    def put(
        self,
        cache_key: str,
        graph,
        persistence_graph,
        ttl_seconds: float,
        max_entries: int,
        max_payload_bytes: int,
    ) -> Dict[str, object]:
        key = str(cache_key or "").strip()
        if not key or ttl_seconds <= 0:
            return {"status": "disabled"}
        try:
            payload = {
                "version": CACHE_PAYLOAD_VERSION,
                "graph": serialize_portfolio_ontology(graph),
                "persistenceGraph": serialize_portfolio_ontology(persistence_graph),
            }
            payload_json = json_dumps(payload)
        except Exception as error:  # noqa: BLE001 - cache serialisation is never a correctness dependency.
            return {"status": "error", "reason": str(error)[:180]}
        payload_bytes = len(payload_json.encode("utf-8"))
        maximum = max(64 * 1024, min(32 * 1024 * 1024, int(max_payload_bytes or 0)))
        if payload_bytes > maximum:
            return {
                "status": "skipped-oversized",
                "payloadBytes": payload_bytes,
                "maxPayloadBytes": maximum,
            }
        stamp = utc_now()
        expires_at = _timestamp_after(ttl_seconds)
        bounded_entries = max(1, min(256, int(max_entries or 1)))
        try:
            with self.transaction() as connection:
                connection.execute(
                    "DELETE FROM ontology_graph_assembly_cache WHERE expires_at <= %s",
                    (stamp,),
                )
                connection.execute(
                    """
                    INSERT INTO ontology_graph_assembly_cache (
                        cache_key, payload_json, payload_bytes, created_at, expires_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        payload_json = VALUES(payload_json), payload_bytes = VALUES(payload_bytes),
                        created_at = VALUES(created_at), expires_at = VALUES(expires_at), updated_at = VALUES(updated_at)
                    """,
                    (key, payload_json, payload_bytes, stamp, expires_at, stamp),
                )
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM ontology_graph_assembly_cache"
                ).fetchone() or {}
                overflow = max(0, int(row.get("count") or 0) - bounded_entries)
                if overflow:
                    connection.execute(
                        """
                        DELETE FROM ontology_graph_assembly_cache
                        WHERE cache_key <> %s
                        ORDER BY updated_at ASC, cache_key ASC
                        LIMIT %s
                        """,
                        (key, overflow),
                    )
            return {
                "status": "stored",
                "payloadBytes": payload_bytes,
                "expiresAt": expires_at,
            }
        except Exception as error:  # noqa: BLE001 - cache write is best effort.
            return {"status": "error", "reason": str(error)[:180], "payloadBytes": payload_bytes}
