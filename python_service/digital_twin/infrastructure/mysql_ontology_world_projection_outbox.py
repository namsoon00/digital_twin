"""Durable outbox for account-independent ontology world projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Dict, Iterable, List, Mapping

from ..domain.ontology_projection_payload import (
    deserialize_portfolio_ontology,
    serialize_portfolio_ontology,
)
from ..domain.ontology_projection_fingerprint import material_graph_fingerprint
from ..domain.ontology_worlds import (
    KNOWLEDGE_WORLD_TYPE,
    MARKET_WORLD_TYPE,
    OntologyWorld,
    world_from_metadata,
    world_metadata,
)
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


PENDING = "pending"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"
SUPERSEDED = "superseded"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _sha(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _job_ids(rows) -> List[str]:
    values = []
    for row in rows or []:
        job_id = _clean(row.get("job_id") if isinstance(row, dict) else row[0])
        if job_id:
            values.append(job_id)
    return values


def _timestamp_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds or 0)))).isoformat().replace("+00:00", "Z")


def _projection_payload_symbols(payload: Mapping[str, object]) -> set:
    symbols = set()
    for item in payload.get("entities") or []:
        if not isinstance(item, Mapping):
            continue
        properties = item.get("properties") if isinstance(item.get("properties"), Mapping) else {}
        symbol = _clean(properties.get("symbol") or properties.get("ticker")).upper()
        if symbol:
            symbols.add(symbol)
    for item in payload.get("opinions") or []:
        if isinstance(item, Mapping) and _clean(item.get("symbol")):
            symbols.add(_clean(item.get("symbol")).upper())
    return symbols


class MySQLOntologyWorldProjectionOutboxStore(MySQLOperationalConnection):
    """Queue only verified shared-world projection inputs.

    Different symbol slices remain isolated by their material fingerprint.
    An exact shared-world material match is deduplicated across source
    PortfolioWorlds so adding accounts does not multiply identical work.
    """

    @staticmethod
    def _material_receipt(connection, kind: str, world_id: str, fingerprint: str):
        return connection.execute(
            """
            SELECT job_id, status, completed_at
            FROM ontology_world_projection_outbox
            WHERE projection_kind = %s AND world_id = %s
              AND material_fingerprint = %s
              AND status IN (%s, %s, %s)
            ORDER BY
              CASE status WHEN %s THEN 0 WHEN %s THEN 1 ELSE 2 END,
              updated_at DESC
            LIMIT 1
            """,
            (
                kind,
                world_id,
                fingerprint,
                COMPLETED,
                PROCESSING,
                PENDING,
                COMPLETED,
                PROCESSING,
            ),
        ).fetchone()

    @staticmethod
    def _material_receipt_response(target, kind: str, fingerprint: str, row, graph):
        if not row:
            return None
        status = _clean(row.get("status")) or COMPLETED
        completed = status == COMPLETED
        return {
            **target,
            "status": (
                "already-projected-material"
                if completed
                else "already-queued-identical-material"
            ),
            "saved": False,
            "eventuallyConsistent": True,
            "projectionKind": kind,
            "jobId": _clean(row.get("job_id")),
            "materialFingerprint": fingerprint,
            "completedAt": _clean(row.get("completed_at")),
            "existingJobStatus": status,
            "payloadBytes": 0,
            "entityCount": len(getattr(graph, "entities", []) or []),
            "relationCount": len(getattr(graph, "relations", []) or []),
            "evidenceCount": len(getattr(graph, "evidence", []) or []),
            "payloadSerializationSkipped": True,
            "deduplicatedAcrossPortfolioWorlds": True,
        }

    def max_payload_bytes(self) -> int:
        try:
            value = int(float(str(self.runtime_settings.get("ontologyWorldProjectionMaxPayloadBytes") or 5 * 1024 * 1024)))
        except (TypeError, ValueError):
            value = 5 * 1024 * 1024
        return max(256 * 1024, min(32 * 1024 * 1024, value))

    def enqueue(
        self,
        projection_kind: str,
        world: OntologyWorld,
        graph,
        source_world_id: str = "",
        source_account_id: str = "",
        source_observed_at: str = "",
    ) -> Dict[str, object]:
        kind = _clean(projection_kind).lower() or "market"
        target = world_metadata(world)
        source_world = _clean(source_world_id) or _clean((getattr(graph, "worldview", {}) or {}).get("sourcePortfolioWorldId"))
        source_account = _clean(source_account_id)
        source_observed = _clean(source_observed_at) or _clean((getattr(graph, "worldview", {}) or {}).get("sourceObservedAt"))
        dedupe_key = _sha("|".join([kind, world.world_id, source_world or "unknown"]))[:64]

        # Most PortfolioWorld updates do not alter their shared MarketWorld or
        # KnowledgeWorld facts. Check the durable material receipt before
        # serializing the graph; the transaction below repeats this lookup to
        # preserve idempotency when another worker completes concurrently.
        if kind != "scope-repair":
            fingerprint = material_graph_fingerprint(graph)
            with self.connect() as connection:
                receipt = self._material_receipt(
                    connection,
                    kind,
                    world.world_id,
                    fingerprint,
                )
            response = self._material_receipt_response(
                target,
                kind,
                fingerprint,
                receipt,
                graph,
            )
            if response:
                return response

        payload = serialize_portfolio_ontology(graph)
        payload_json = json_dumps(payload)
        payload_bytes = len(payload_json.encode("utf-8"))
        payload_summary = {
            "payloadBytes": payload_bytes,
            "entityCount": len(payload.get("entities") or []),
            "relationCount": len(payload.get("relations") or []),
            "evidenceCount": len(payload.get("evidence") or []),
        }
        if payload_bytes > self.max_payload_bytes():
            return {
                **target,
                "status": "deferred-oversized-world-projection-payload",
                "saved": False,
                "eventuallyConsistent": True,
                "projectionKind": kind,
                "maxPayloadBytes": self.max_payload_bytes(),
                **payload_summary,
                "reason": "Shared-world projection packet exceeded the durable outbox safety limit.",
            }
        # Repair commands intentionally keep the same semantic facts while
        # changing their physical generation. Include the command packet in
        # idempotency instead of treating it as already projected material.
        fingerprint = (
            _sha(payload_json)
            if kind == "scope-repair"
            else fingerprint
        )
        payload_hash = _sha("|".join([dedupe_key, fingerprint, source_observed, payload_json]))
        job_id = "world-projection:" + payload_hash[:48]
        stamp = utc_now()
        with self.transaction() as connection:
            receipt = self._material_receipt(
                connection,
                kind,
                world.world_id,
                fingerprint,
            )
            response = self._material_receipt_response(
                target,
                kind,
                fingerprint,
                receipt,
                graph,
            )
            if response:
                response.update(payload_summary)
                response["payloadSerializationSkipped"] = False
                return response
            pending = connection.execute(
                """
                SELECT job_id FROM ontology_world_projection_outbox
                WHERE dedupe_key = %s AND status = %s
                ORDER BY updated_at DESC LIMIT 1 FOR UPDATE
                """,
                (dedupe_key, PENDING),
            ).fetchone()
            if pending:
                active_job_id = _clean(pending.get("job_id"))
                connection.execute(
                    """
                    UPDATE ontology_world_projection_outbox
                    SET material_fingerprint = %s, source_observed_at = %s,
                        payload_json = %s, available_at = %s, last_error = '',
                        updated_at = %s
                    WHERE job_id = %s AND status = %s
                    """,
                    (fingerprint, source_observed, payload_json, stamp, stamp, active_job_id, PENDING),
                )
                return {
                    **target,
                    "status": "queued-coalesced-durable-world-projection",
                    "saved": True,
                    "eventuallyConsistent": True,
                    "projectionKind": kind,
                    "jobId": active_job_id,
                    "materialFingerprint": fingerprint,
                    "sourceObservedAt": source_observed,
                    "coalescedPendingUpdate": True,
                    **payload_summary,
                }
            connection.execute(
                """
                INSERT INTO ontology_world_projection_outbox (
                    job_id, dedupe_key, projection_kind, world_id, world_type,
                    tenant_id, market_id, source_world_id, source_account_id,
                    source_observed_at, material_fingerprint, payload_json,
                    status, attempts, available_at, lease_owner, lease_expires_at,
                    last_error, result_json, created_at, updated_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, '', '', '', '{}', %s, %s, '')
                """,
                (
                    job_id,
                    dedupe_key,
                    kind,
                    world.world_id,
                    world.world_type,
                    world.tenant_id,
                    world.market_id,
                    source_world,
                    source_account,
                    source_observed,
                    fingerprint,
                    payload_json,
                    PENDING,
                    stamp,
                    stamp,
                    stamp,
                ),
            )
        return {
            **target,
            "status": "queued-durable-world-projection",
            "saved": True,
            "eventuallyConsistent": True,
            "projectionKind": kind,
            "jobId": job_id,
            "materialFingerprint": fingerprint,
            "sourceObservedAt": source_observed,
            "coalescedPendingUpdate": False,
            **payload_summary,
        }

    def enqueue_scope_repair(
        self,
        world_id: str,
        request_id: str,
        repair_requests_by_symbol: Mapping[str, object],
        source_account_id: str = "",
        source_observed_at: str = "",
    ) -> Dict[str, object]:
        """Clone the newest verified shared-world packets into repair work.

        Scope repair changes storage generations, not investment facts. The
        latest completed projection packet already contains the verified ABox
        slice needed to rebuild a damaged subject, so the maintenance worker
        can stay independent from the live reasoning queue.
        """
        clean_world_id = _clean(world_id)
        world = world_from_metadata({"worldId": clean_world_id})
        if world.world_type not in {MARKET_WORLD_TYPE, KNOWLEDGE_WORLD_TYPE}:
            return {
                "status": "manual-portfolio-scope-repair-required",
                "saved": False,
                "worldId": clean_world_id,
                "reason": "PortfolioWorld repair requires an account snapshot and is not routed through shared-world projection work.",
                "missingSymbols": sorted(_clean(symbol).upper() for symbol in repair_requests_by_symbol if _clean(symbol)),
            }
        requested = {
            _clean(symbol).upper(): dict(value or {})
            for symbol, value in dict(repair_requests_by_symbol or {}).items()
            if _clean(symbol) and isinstance(value, Mapping)
        }
        if not requested:
            return {"status": "not-required", "saved": False, "worldId": clean_world_id}

        base_kind = "knowledge" if world.world_type == KNOWLEDGE_WORLD_TYPE else "market"
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ontology_world_projection_outbox
                WHERE world_id = %s AND projection_kind = %s
                  AND status IN (%s, %s, %s)
                ORDER BY updated_at DESC, job_id DESC LIMIT 200
                """,
                (clean_world_id, base_kind, COMPLETED, PENDING, PROCESSING),
            ).fetchall()

        remaining = set(requested)
        selected = []
        seen_sources = set()
        for row in rows or []:
            payload = _json_loads(row.get("payload_json"), {})
            if not isinstance(payload, dict):
                continue
            matched = remaining.intersection(_projection_payload_symbols(payload))
            source_key = _clean(row.get("source_world_id")) or _clean(row.get("dedupe_key"))
            if not matched or source_key in seen_sources:
                continue
            selected.append((dict(row), payload, sorted(matched)))
            seen_sources.add(source_key)
            remaining.difference_update(matched)
            if not remaining:
                break

        job_ids = []
        queued_symbols = set()
        statuses = []
        for row, payload, symbols in selected:
            graph = deserialize_portfolio_ontology(payload)
            graph.worldview["scopeRepairRequestId"] = _clean(request_id)
            graph.worldview["scopeRepairRequestsBySymbol"] = {
                symbol: requested[symbol]
                for symbol in symbols
            }
            graph.worldview["scopeRepairSourceProjectionKind"] = base_kind
            result = self.enqueue(
                "scope-repair",
                world,
                graph,
                source_world_id=_clean(row.get("source_world_id")),
                source_account_id=_clean(row.get("source_account_id")) or _clean(source_account_id),
                source_observed_at=_clean(source_observed_at) or _clean(row.get("source_observed_at")),
            )
            statuses.append(_clean(result.get("status")))
            job_id = _clean(result.get("jobId"))
            if job_id:
                job_ids.append(job_id)
                queued_symbols.update(symbols)

        return {
            "status": (
                "queued-durable-scope-repair"
                if job_ids and not remaining
                else "queued-partial-scope-repair"
                if job_ids
                else "deferred-scope-repair-source-packet"
            ),
            "saved": bool(job_ids),
            "worldId": clean_world_id,
            "requestId": _clean(request_id),
            "jobIds": job_ids,
            "queuedSymbolCount": len(queued_symbols),
            "queuedSymbols": sorted(queued_symbols),
            "missingSymbols": sorted(remaining),
            "sourcePacketStatuses": statuses,
            "reason": (
                "No verified shared-world projection packet contains the requested symbols."
                if not job_ids
                else ""
            ),
        }

    def claim(self, worker_id: str, limit: int = 4, lease_seconds: int = 300) -> List[Dict[str, object]]:
        worker = _clean(worker_id) or "ontology-world-projection"
        bounded = max(1, min(50, int(limit or 4)))
        stamp = utc_now()
        lease_expires = _timestamp_after(max(30, min(3600, int(lease_seconds or 300))))
        claimed: List[Dict[str, object]] = []
        with self.transaction() as connection:
            # A worker can be force-stopped while a newer observation from the
            # same PortfolioWorld is already pending. Do not replay the stale
            # payload after its lease expires; the newest packet has the same
            # target/dedupe boundary and is the only useful source of truth.
            connection.execute(
                """
                UPDATE ontology_world_projection_outbox AS expired
                INNER JOIN ontology_world_projection_outbox AS newer
                    ON newer.dedupe_key = expired.dedupe_key
                    AND newer.status = %s
                    AND newer.created_at >= expired.created_at
                SET expired.status = %s, expired.lease_owner = '', expired.lease_expires_at = '',
                    expired.last_error = 'superseded after expired lease by newer pending projection',
                    expired.updated_at = %s, expired.completed_at = %s
                WHERE expired.status = %s AND expired.lease_expires_at != ''
                    AND expired.lease_expires_at < %s
                """,
                (PENDING, SUPERSEDED, stamp, stamp, PROCESSING, stamp),
            )
            connection.execute(
                """
                UPDATE ontology_world_projection_outbox
                SET status = %s, lease_owner = '', lease_expires_at = '', updated_at = %s
                WHERE status = %s AND lease_expires_at != '' AND lease_expires_at < %s
                """,
                (PENDING, stamp, PROCESSING, stamp),
            )
            rows = connection.execute(
                """
                SELECT * FROM ontology_world_projection_outbox
                WHERE status = %s AND available_at <= %s
                ORDER BY created_at ASC, job_id ASC LIMIT %s FOR UPDATE
                """,
                (PENDING, stamp, bounded),
            ).fetchall()
            for row in rows or []:
                job_id = _clean(row.get("job_id"))
                if not job_id:
                    continue
                cursor = connection.execute(
                    """
                    UPDATE ontology_world_projection_outbox
                    SET status = %s, attempts = attempts + 1, lease_owner = %s,
                        lease_expires_at = %s, updated_at = %s
                    WHERE job_id = %s AND status = %s
                    """,
                    (PROCESSING, worker, lease_expires, stamp, job_id, PENDING),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                    continue
                claimed.append(self.row_payload({
                    **dict(row),
                    "status": PROCESSING,
                    "attempts": int(row.get("attempts") or 0) + 1,
                    "lease_owner": worker,
                    "lease_expires_at": lease_expires,
                }))
        return claimed

    def complete(self, job_id: str, worker_id: str, result: Mapping[str, object] = None) -> bool:
        stamp = utc_now()
        with self.transaction() as connection:
            current = connection.execute(
                """
                SELECT dedupe_key FROM ontology_world_projection_outbox
                WHERE job_id = %s AND status = %s AND lease_owner = %s
                LIMIT 1 FOR UPDATE
                """,
                (_clean(job_id), PROCESSING, _clean(worker_id)),
            ).fetchone()
            dedupe_key = _clean((current or {}).get("dedupe_key"))
            cursor = connection.execute(
                """
                UPDATE ontology_world_projection_outbox
                SET status = %s, lease_owner = '', lease_expires_at = '',
                    last_error = '', result_json = %s, updated_at = %s, completed_at = %s
                WHERE job_id = %s AND status = %s AND lease_owner = %s
                """,
                (COMPLETED, json_dumps(dict(result or {})), stamp, stamp, _clean(job_id), PROCESSING, _clean(worker_id)),
            )
            completed = int(getattr(cursor, "rowcount", 0) or 0) == 1
            if completed and dedupe_key:
                # Terminal packets from an older projection contract are not
                # replay sources once the same boundary succeeds. Keep their
                # compact audit row but release the potentially large graph.
                connection.execute(
                    """
                    UPDATE ontology_world_projection_outbox
                    SET status = %s, payload_json = '{}',
                        last_error = CONCAT('superseded by successful projection: ', last_error),
                        updated_at = %s, completed_at = %s
                    WHERE dedupe_key = %s AND job_id != %s AND status = %s
                    """,
                    (SUPERSEDED, stamp, stamp, dedupe_key, _clean(job_id), FAILED),
                )
        return completed

    def yield_claimed(self, job_id: str, worker_id: str, reason: object = "") -> bool:
        """Return a just-claimed background write without charging a retry.

        This is an admission-control handoff, not an execution failure. It is
        used when live reasoning arrives between the preflight queue check and
        the shared-world worker's TypeDB write boundary.
        """

        stamp = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ontology_world_projection_outbox
                SET status = %s, attempts = GREATEST(attempts - 1, 0),
                    available_at = %s, lease_owner = '', lease_expires_at = '',
                    last_error = %s, updated_at = %s
                WHERE job_id = %s AND status = %s AND lease_owner = %s
                """,
                (
                    PENDING,
                    stamp,
                    _clean(reason)[:1000],
                    stamp,
                    _clean(job_id),
                    PROCESSING,
                    _clean(worker_id),
                ),
            )
        return int(getattr(cursor, "rowcount", 0) or 0) == 1

    def defer(
        self,
        job_id: str,
        worker_id: str,
        reason: object,
        retry_after_seconds: int = 10,
    ) -> Dict[str, object]:
        """Postpone a healthy contention state without consuming failure attempts."""

        clean_job_id = _clean(job_id)
        clean_worker = _clean(worker_id)
        delay_seconds = max(5, min(300, int(retry_after_seconds or 10)))
        stamp = utc_now()
        available_at = _timestamp_after(delay_seconds)
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ontology_world_projection_outbox
                SET status = %s, attempts = GREATEST(attempts - 1, 0),
                    available_at = %s, lease_owner = '', lease_expires_at = '',
                    last_error = %s, updated_at = %s
                WHERE job_id = %s AND status = %s AND lease_owner = %s
                """,
                (
                    PENDING,
                    available_at,
                    _clean(reason)[:1000],
                    stamp,
                    clean_job_id,
                    PROCESSING,
                    clean_worker,
                ),
            )
        applied = int(getattr(cursor, "rowcount", 0) or 0) == 1
        return {
            "status": "deferred" if applied else "lease-lost",
            "jobId": clean_job_id,
            "retryAfterSeconds": delay_seconds if applied else 0,
            "failureAttemptCharged": False,
        }

    def retry(
        self,
        job_id: str,
        worker_id: str,
        reason: object,
        max_attempts: int = 8,
    ) -> Dict[str, object]:
        clean_job_id = _clean(job_id)
        clean_worker = _clean(worker_id)
        stamp = utc_now()
        bounded_attempts = max(1, min(32, int(max_attempts or 8)))
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT attempts FROM ontology_world_projection_outbox
                WHERE job_id = %s AND status = %s AND lease_owner = %s FOR UPDATE
                """,
                (clean_job_id, PROCESSING, clean_worker),
            ).fetchone()
            if not row:
                return {"status": "lease-lost", "jobId": clean_job_id}
            attempts = int(row.get("attempts") or 0)
            terminal = attempts >= bounded_attempts
            delay_seconds = min(300, max(5, 2 ** min(8, attempts)))
            status = FAILED if terminal else PENDING
            available_at = "" if terminal else _timestamp_after(delay_seconds)
            connection.execute(
                """
                UPDATE ontology_world_projection_outbox
                SET status = %s, available_at = %s, lease_owner = '', lease_expires_at = '',
                    last_error = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (status, available_at, _clean(reason)[:1000], stamp, clean_job_id),
            )
        return {
            "status": status,
            "jobId": clean_job_id,
            "attempts": attempts,
            "retryAfterSeconds": 0 if terminal else delay_seconds,
        }

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count, MIN(created_at) AS oldest_at
                FROM ontology_world_projection_outbox GROUP BY status
                """
            ).fetchall()
        states = {
            _clean(row.get("status")): {
                "count": int(row.get("count") or 0),
                "oldestAt": _clean(row.get("oldest_at")),
            }
            for row in rows or []
        }
        return {
            "enabled": True,
            "states": states,
            "pendingCount": int((states.get(PENDING) or {}).get("count") or 0),
            "processingCount": int((states.get(PROCESSING) or {}).get("count") or 0),
            "failedCount": int((states.get(FAILED) or {}).get("count") or 0),
        }

    def requeue_failed(self, limit: int = 100) -> int:
        bounded = max(1, min(5000, int(limit or 100)))
        stamp = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ontology_world_projection_outbox
                SET status = %s, available_at = %s, lease_owner = '', lease_expires_at = '', updated_at = %s
                WHERE status = %s ORDER BY updated_at ASC LIMIT %s
                """,
                (PENDING, stamp, stamp, FAILED, bounded),
            )
        return int(getattr(cursor, "rowcount", 0) or 0)

    @staticmethod
    def _payload_is_replayable(value: object) -> bool:
        payload = _json_loads(value, {})
        if not isinstance(payload, dict):
            return False
        return bool(payload.get("entities") or payload.get("relations") or payload.get("evidence"))

    @classmethod
    def _latest_replayable_rows(cls, rows, limit: int) -> List[Dict[str, object]]:
        selected = []
        seen_dedupe_keys = set()
        for raw in rows or []:
            row = dict(raw or {})
            dedupe_key = _clean(row.get("dedupe_key")) or _clean(row.get("job_id"))
            if not dedupe_key or dedupe_key in seen_dedupe_keys:
                continue
            if not cls._payload_is_replayable(row.get("payload_json")):
                continue
            seen_dedupe_keys.add(dedupe_key)
            selected.append(row)
            if len(selected) >= limit:
                break
        return selected

    def requeue_latest_replayable(self, limit: int = 100) -> Dict[str, object]:
        """Replay the newest valid packet per source after a TypeDB reset.

        TypeDB stores the materialized shared worlds, while this outbox is the
        durable source for their verified inputs. A projection-contract defect
        can prevent a valid packet from ever reaching COMPLETED, so recovery
        selects the newest nonempty completed, pending, or failed packet per
        source boundary. Historical packets are never replayed in bulk.
        """
        bounded = max(1, min(5000, int(limit or 100)))
        stamp = utc_now()
        selected: List[str] = []
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT job_id, dedupe_key, payload_json, status
                FROM ontology_world_projection_outbox
                WHERE status IN (%s, %s, %s)
                  AND projection_kind IN ('market', 'knowledge')
                ORDER BY updated_at DESC, job_id DESC
                FOR UPDATE
                """,
                (COMPLETED, PENDING, FAILED),
            ).fetchall()
            replayable = self._latest_replayable_rows(rows, bounded)
            selected = [_clean(row.get("job_id")) for row in replayable if _clean(row.get("job_id"))]
            for job_id in selected:
                connection.execute(
                    """
                    UPDATE ontology_world_projection_outbox
                    SET status = %s, attempts = 0, available_at = %s,
                        lease_owner = '', lease_expires_at = '',
                        last_error = 'requeued after TypeDB shared-world rebuild',
                        result_json = '{}', updated_at = %s, completed_at = ''
                    WHERE job_id = %s AND status = %s
                    """,
                    (PENDING, stamp, stamp, job_id, COMPLETED),
                )
        return {
            "status": "ok",
            "requeuedCount": len(selected),
            "requeuedJobIds": selected,
            "selection": "latest-replayable-per-dedupe-key",
        }

    def requeue_latest_completed(self, limit: int = 100) -> Dict[str, object]:
        """Compatibility alias for reset tooling deployed before replay v2."""
        return self.requeue_latest_replayable(limit)

    def latest_replayable(self, limit: int = 100) -> List[Dict[str, object]]:
        """Read the newest valid packet per source without changing queue state.

        Blue/green TypeDB preparation runs while the active projection worker
        is still online. Requeueing completed rows in that phase would let the
        active and candidate stores race for the same MySQL job. A read-only
        snapshot gives the candidate identical source facts while preserving
        the live outbox contract.
        """
        bounded = max(1, min(5000, int(limit or 100)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ontology_world_projection_outbox
                WHERE status IN (%s, %s, %s)
                  AND projection_kind IN ('market', 'knowledge')
                ORDER BY updated_at DESC, job_id DESC
                """,
                (COMPLETED, PENDING, FAILED),
            ).fetchall()
        return [self.row_payload(row) for row in self._latest_replayable_rows(rows, bounded)]

    def latest_completed(self, limit: int = 100) -> List[Dict[str, object]]:
        """Compatibility alias for read-only candidate rebuild tooling."""
        return self.latest_replayable(limit)

    def supersede_oversized_pending(self, limit: int = 500) -> int:
        """Retire packets produced by an older unbounded projection shape."""
        bounded = max(1, min(5000, int(limit or 500)))
        stamp = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ontology_world_projection_outbox
                SET status = %s, last_error = 'superseded oversized legacy projection payload',
                    updated_at = %s, completed_at = %s
                WHERE status = %s AND LENGTH(payload_json) > %s
                ORDER BY created_at ASC LIMIT %s
                """,
                (SUPERSEDED, stamp, stamp, PENDING, self.max_payload_bytes(), bounded),
            )
        return int(getattr(cursor, "rowcount", 0) or 0)

    def purge_oversized_superseded(self, limit: int = 1) -> int:
        """Remove legacy raw-account packets once a bounded replacement exists.

        Superseded packets over the current ceiling were produced by the old
        full-ABox queue shape. They are neither replayable nor useful audit
        material and may contain account-private facts, so retaining them for
        normal outbox history is the wrong default.
        """
        bounded = max(1, min(1, int(limit or 1)))

        def delete(connection):
            rows = connection.execute(
                """
                SELECT job_id FROM ontology_world_projection_outbox
                WHERE status = %s AND LENGTH(payload_json) > %s
                ORDER BY completed_at ASC, job_id ASC LIMIT %s
                """,
                (SUPERSEDED, self.max_payload_bytes(), bounded),
            ).fetchall()
            job_ids = _job_ids(rows)
            if not job_ids:
                return 0
            cursor = connection.execute(
                """
                DELETE FROM ontology_world_projection_outbox
                WHERE job_id IN (""" + ", ".join(["%s"] * len(job_ids)) + """ )
                  AND status = %s AND LENGTH(payload_json) > %s
                """,
                tuple(job_ids) + (SUPERSEDED, self.max_payload_bytes()),
            )
            return int(getattr(cursor, "rowcount", 0) or 0)

        return int(self.transaction_with_deadlock_retry("world-projection-purge-oversized", delete) or 0)

    def prune_completed(self, retention_hours: int = 24, limit: int = 1) -> int:
        """Retain audit results long enough for operations without unbounded growth."""
        bounded_hours = max(1, min(24, int(retention_hours or 24)))
        bounded_limit = max(1, min(1, int(limit or 1)))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=bounded_hours)).isoformat().replace("+00:00", "Z")

        def delete(connection):
            rows = connection.execute(
                """
                SELECT job_id FROM ontology_world_projection_outbox
                WHERE status IN (%s, %s) AND completed_at != '' AND completed_at < %s
                ORDER BY completed_at ASC, job_id ASC LIMIT %s
                """,
                (COMPLETED, SUPERSEDED, cutoff, bounded_limit),
            ).fetchall()
            job_ids = _job_ids(rows)
            if not job_ids:
                return 0
            cursor = connection.execute(
                """
                DELETE FROM ontology_world_projection_outbox
                WHERE job_id IN (""" + ", ".join(["%s"] * len(job_ids)) + """ )
                  AND status IN (%s, %s) AND completed_at != '' AND completed_at < %s
                """,
                tuple(job_ids) + (COMPLETED, SUPERSEDED, cutoff),
            )
            return int(getattr(cursor, "rowcount", 0) or 0)

        return int(self.transaction_with_deadlock_retry("world-projection-prune-completed", delete) or 0)

    @staticmethod
    def row_payload(row: Mapping[str, object]) -> Dict[str, object]:
        values = dict(row or {})
        return {
            "jobId": _clean(values.get("job_id")),
            "dedupeKey": _clean(values.get("dedupe_key")),
            "projectionKind": _clean(values.get("projection_kind")),
            "worldId": _clean(values.get("world_id")),
            "worldType": _clean(values.get("world_type")),
            "tenantId": _clean(values.get("tenant_id")),
            "marketId": _clean(values.get("market_id")),
            "sourceWorldId": _clean(values.get("source_world_id")),
            "sourceAccountId": _clean(values.get("source_account_id")),
            "sourceObservedAt": _clean(values.get("source_observed_at")),
            "materialFingerprint": _clean(values.get("material_fingerprint")),
            "payload": _json_loads(values.get("payload_json"), {}),
            "status": _clean(values.get("status")),
            "attempts": int(values.get("attempts") or 0),
            "availableAt": _clean(values.get("available_at")),
            "leaseOwner": _clean(values.get("lease_owner")),
            "leaseExpiresAt": _clean(values.get("lease_expires_at")),
            "lastError": _clean(values.get("last_error")),
            "result": _json_loads(values.get("result_json"), {}),
            "createdAt": _clean(values.get("created_at")),
            "updatedAt": _clean(values.get("updated_at")),
            "completedAt": _clean(values.get("completed_at")),
        }
