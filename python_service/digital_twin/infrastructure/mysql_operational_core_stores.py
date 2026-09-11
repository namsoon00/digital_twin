from typing import Dict, Iterable, List, Optional

from .operational_common import json_dumps
from .settings import settings_path, utc_now
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads


class MySQLRuntimeSettingsStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None, legacy_path: Optional[object] = None):
        self.legacy_path = legacy_path or settings_path()
        store_settings = dict(settings or {})
        store_settings["_skipOperationalHistoryRetention"] = "1"
        super().__init__(store_settings)

    def load(self) -> Dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT `key`, value FROM runtime_settings ORDER BY `key`").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def replace(self, settings: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute("DELETE FROM runtime_settings")
            for key, value in (settings or {}).items():
                connection.execute(
                    "INSERT INTO runtime_settings (`key`, value, updated_at) VALUES (%s, %s, %s)",
                    (str(key), str(value or ""), stamp),
                )

    def save(self, settings: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            for key, value in (settings or {}).items():
                connection.execute(
                    """
                    INSERT INTO runtime_settings (`key`, value, updated_at)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)
                    """,
                    (str(key), str(value or ""), stamp),
                )


class MySQLAppStore(MySQLOperationalConnection):
    store_id = "default"

    def load(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM app_store WHERE store_id = %s", (self.store_id,)).fetchone()
        return _json_loads(row["payload_json"], {}) if row else {}

    def replace(self, payload: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO app_store (store_id, payload_json, updated_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (self.store_id, json_dumps(payload if isinstance(payload, dict) else {}), stamp),
            )

class MySQLExternalSignalCache(MySQLAppStore):
    store_id = "external_signals"


class MySQLCompanyKnowledgeCache(MySQLAppStore):
    """Shared current company facts reused by every account and world."""

    store_id = "company_knowledge"


class MySQLCryptoMarketSignalCache(MySQLAppStore):
    """Small global CoinGecko snapshot, independent from portfolio cache keys."""

    store_id = "external_signals_coingecko_markets"


class MySQLDataPipelineHealthStore(MySQLAppStore):
    store_id = "data_pipeline_health"


class MySQLNewsDigestReconciliationStateStore(MySQLAppStore):
    """Keeps the durable research-event cursor used by notification recovery."""

    store_id = "news_digest_reconciliation_state"


class MySQLExternalEvidenceProjectionStateStore(MySQLAppStore):
    """Keeps the durable external-fact projection cursor across restarts."""

    store_id = "external_evidence_projection_state"


class MySQLOperationalStorageCapacityStateStore(MySQLAppStore):
    """Keeps one compact capacity incident state across worker restarts."""

    store_id = "operational_storage_capacity_state"


class MySQLOntologyMaintenanceStateStore(MySQLAppStore):
    """Keeps only the cursor and latest result for background ABox retention."""

    store_id = "ontology_maintenance_state"


class MySQLOntologyWorldProjectionStateStore(MySQLAppStore):
    """Keeps bounded fairness state for shared-world projection work."""

    store_id = "ontology_world_projection_state"


class MySQLOntologyInferenceDetailStateStore(MySQLAppStore):
    """Keeps bounded fairness state for deferred InferenceBox readback."""

    store_id = "ontology_inference_detail_state"


class MySQLOntologyReasoningCursorStore(MySQLAppStore):
    store_id = "ontology_reasoning_cursor"

    def load(self) -> Dict[str, object]:
        payload = super().load()
        payload.setdefault("processedEventIds", [])
        payload.setdefault("supersededEventIds", [])
        return payload

    def processed_event_ids(self) -> List[str]:
        return [str(item or "").strip() for item in self.load().get("processedEventIds", []) if str(item or "").strip()]

    def save(self, payload: Dict[str, object]) -> None:
        next_payload = dict(payload or {})
        next_payload.setdefault("processedEventIds", self.processed_event_ids())
        next_payload["updatedAt"] = utc_now()
        self.replace(next_payload)

    def mark_processed(self, event_ids: Iterable[str]) -> None:
        existing = self.processed_event_ids()
        seen = set(existing)
        merged = list(existing)
        for event_id in event_ids or []:
            clean = str(event_id or "").strip()
            if clean and clean not in seen:
                seen.add(clean)
                merged.append(clean)
        payload = self.load()
        try:
            retention_limit = int(float(str(
                self.runtime_settings.get("ontologyReasoningProcessedEventLimit") or "10000"
            )))
        except (TypeError, ValueError):
            retention_limit = 10000
        retention_limit = max(1000, min(50000, retention_limit))
        payload["processedEventIds"] = merged[-retention_limit:]
        self.save(payload)

    def mark_superseded(self, event_ids: Iterable[str]) -> None:
        """Persist stale realtime triggers without leaving partial cursor work."""
        payload = self.load()
        clean_ids = []
        for event_id in event_ids or []:
            clean = str(event_id or "").strip()
            if clean and clean not in clean_ids:
                clean_ids.append(clean)
        if not clean_ids:
            return
        try:
            retention_limit = int(float(str(
                self.runtime_settings.get("ontologyReasoningProcessedEventLimit") or "10000"
            )))
        except (TypeError, ValueError):
            retention_limit = 10000
        retention_limit = max(1000, min(50000, retention_limit))
        processed = [str(item or "").strip() for item in payload.get("processedEventIds") or [] if str(item or "").strip()]
        superseded = [str(item or "").strip() for item in payload.get("supersededEventIds") or [] if str(item or "").strip()]
        for event_id in clean_ids:
            if event_id not in processed:
                processed.append(event_id)
            if event_id not in superseded:
                superseded.append(event_id)
        progress = dict(payload.get("eventSymbolProgress") or {})
        for event_id in clean_ids:
            progress.pop(event_id, None)
        payload["processedEventIds"] = processed[-retention_limit:]
        payload["supersededEventIds"] = superseded[-retention_limit:]
        payload["eventSymbolProgress"] = progress
        payload["lastSupersededAt"] = utc_now()
        self.save(payload)
