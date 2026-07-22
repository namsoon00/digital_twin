from datetime import datetime, timedelta, timezone
from typing import Dict, List

from ..domain.ontology_projection_audit import OntologyProjectionRun
from ..domain.ontology_runtime_operations import summarize_projection_runtime_observations
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


class MySQLOntologyProjectionRunStore(MySQLOperationalConnection):
    """Durable MySQL audit for the source data behind an active ABox generation."""

    def begin(self, run: OntologyProjectionRun) -> OntologyProjectionRun:
        stamp = utc_now()
        with self.transaction() as connection:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
            connection.execute(
                """
                UPDATE ontology_projection_runs
                SET status = 'aborted-stale', completed_at = %s, updated_at = %s,
                    result_payload_json = %s
                WHERE status = 'projecting' AND started_at < %s
                """,
                (
                    stamp,
                    stamp,
                    json_dumps({
                        "status": "aborted-stale",
                        "reason": "Projection worker ended before activation was audited.",
                    }),
                    cutoff,
                ),
            )
            connection.execute(
                """
                INSERT INTO ontology_projection_runs (
                    run_id, portfolio_id, account_id, tenant_id, world_id,
                    world_type, market_world_id, source_snapshot_at,
                    source_snapshot_fingerprint, first_observed_at, last_observed_at,
                    started_at, completed_at, activated_at, status, graph_store,
                    projection_mode, material_fingerprint, abox_snapshot_id,
                    active_abox_snapshot_id, tbox_version, tbox_fingerprint,
                    rulebox_rules_hash, entity_count, relation_count,
                    inference_generation_id, inference_status, source_symbols_json,
                    context_payload_json, result_payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    portfolio_id = VALUES(portfolio_id),
                    account_id = VALUES(account_id),
                    tenant_id = VALUES(tenant_id),
                    world_id = VALUES(world_id),
                    world_type = VALUES(world_type),
                    market_world_id = VALUES(market_world_id),
                    source_snapshot_at = VALUES(source_snapshot_at),
                    source_snapshot_fingerprint = VALUES(source_snapshot_fingerprint),
                    last_observed_at = VALUES(last_observed_at),
                    started_at = VALUES(started_at),
                    status = VALUES(status),
                    graph_store = VALUES(graph_store),
                    projection_mode = VALUES(projection_mode),
                    material_fingerprint = VALUES(material_fingerprint),
                    abox_snapshot_id = VALUES(abox_snapshot_id),
                    tbox_version = VALUES(tbox_version),
                    tbox_fingerprint = VALUES(tbox_fingerprint),
                    rulebox_rules_hash = VALUES(rulebox_rules_hash),
                    entity_count = VALUES(entity_count),
                    relation_count = VALUES(relation_count),
                    source_symbols_json = VALUES(source_symbols_json),
                    context_payload_json = VALUES(context_payload_json),
                    updated_at = VALUES(updated_at)
                """,
                self.values(run, stamp),
            )
        return run

    def complete(self, run: OntologyProjectionRun) -> OntologyProjectionRun:
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE ontology_projection_runs
                SET last_observed_at = %s,
                    completed_at = %s,
                    activated_at = %s,
                    status = %s,
                    graph_store = %s,
                    projection_mode = %s,
                    tenant_id = %s,
                    world_id = %s,
                    world_type = %s,
                    market_world_id = %s,
                    material_fingerprint = %s,
                    abox_snapshot_id = %s,
                    active_abox_snapshot_id = %s,
                    tbox_version = %s,
                    tbox_fingerprint = %s,
                    rulebox_rules_hash = %s,
                    entity_count = %s,
                    relation_count = %s,
                    inference_generation_id = %s,
                    inference_status = %s,
                    source_symbols_json = %s,
                    context_payload_json = %s,
                    result_payload_json = %s,
                    updated_at = %s
                WHERE run_id = %s
                """,
                (
                    run.last_observed_at,
                    run.completed_at,
                    run.activated_at,
                    run.status,
                    run.graph_store,
                    run.projection_mode,
                    run.tenant_id,
                    run.world_id,
                    run.world_type,
                    run.market_world_id,
                    run.material_fingerprint,
                    run.abox_snapshot_id,
                    run.active_abox_snapshot_id,
                    run.tbox_version,
                    run.tbox_fingerprint,
                    run.rulebox_rules_hash,
                    int(run.entity_count or 0),
                    int(run.relation_count or 0),
                    run.inference_generation_id,
                    run.inference_status,
                    json_dumps(run.source_symbols),
                    json_dumps(run.context_payload),
                    json_dumps(run.result_payload),
                    stamp,
                    run.run_id,
                ),
            )
        return run

    def latest(self, account_id: str = "", limit: int = 50, world_id: str = "") -> List[Dict[str, object]]:
        clauses = []
        params: List[object] = []
        if account_id:
            clauses.append("account_id = %s")
            params.append(str(account_id or ""))
        if world_id:
            clauses.append("world_id = %s")
            params.append(str(world_id or ""))
        sql = "SELECT * FROM ontology_projection_runs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, run_id DESC LIMIT %s"
        params.append(max(1, min(500, int(limit or 50))))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self.row_payload(row) for row in rows or []]

    def runtime_summary(self, account_id: str = "", limit: int = 80, world_id: str = "") -> Dict[str, object]:
        """Read bounded operational telemetry from the durable projection audit.

        The runtime sample is embedded in the same row that already proves
        source capture and ABox activation, avoiding a second write path or a
        duplicate operational history table.
        """

        query = {
            "account_id": account_id,
            "limit": max(5, min(500, int(limit or 80))),
        }
        if str(world_id or "").strip():
            query["world_id"] = str(world_id).strip()
        rows = self.latest(**query)
        observations = []
        for row in rows:
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            observation = result.get("runtimeObservation") if isinstance(result, dict) else {}
            if isinstance(observation, dict) and observation:
                observations.append(observation)
        return summarize_projection_runtime_observations(observations, self.runtime_settings)

    def values(self, run: OntologyProjectionRun, stamp: str):
        return (
            run.run_id,
            run.portfolio_id,
            run.account_id,
            run.tenant_id,
            run.world_id,
            run.world_type,
            run.market_world_id,
            run.source_snapshot_at,
            run.source_snapshot_fingerprint,
            run.first_observed_at,
            run.last_observed_at,
            run.started_at,
            run.completed_at,
            run.activated_at,
            run.status,
            run.graph_store,
            run.projection_mode,
            run.material_fingerprint,
            run.abox_snapshot_id,
            run.active_abox_snapshot_id,
            run.tbox_version,
            run.tbox_fingerprint,
            run.rulebox_rules_hash,
            int(run.entity_count or 0),
            int(run.relation_count or 0),
            run.inference_generation_id,
            run.inference_status,
            json_dumps(run.source_symbols),
            json_dumps(run.context_payload),
            json_dumps(run.result_payload),
            stamp,
            stamp,
        )

    @staticmethod
    def row_payload(row: Dict[str, object]) -> Dict[str, object]:
        return {
            "runId": str(row.get("run_id") or ""),
            "portfolioId": str(row.get("portfolio_id") or ""),
            "accountId": str(row.get("account_id") or ""),
            "tenantId": str(row.get("tenant_id") or ""),
            "worldId": str(row.get("world_id") or ""),
            "worldType": str(row.get("world_type") or ""),
            "marketWorldId": str(row.get("market_world_id") or ""),
            "sourceSnapshotAt": str(row.get("source_snapshot_at") or ""),
            "sourceSnapshotFingerprint": str(row.get("source_snapshot_fingerprint") or ""),
            "firstObservedAt": str(row.get("first_observed_at") or ""),
            "lastObservedAt": str(row.get("last_observed_at") or ""),
            "startedAt": str(row.get("started_at") or ""),
            "completedAt": str(row.get("completed_at") or ""),
            "activatedAt": str(row.get("activated_at") or ""),
            "status": str(row.get("status") or ""),
            "graphStore": str(row.get("graph_store") or ""),
            "projectionMode": str(row.get("projection_mode") or ""),
            "materialFingerprint": str(row.get("material_fingerprint") or ""),
            "aboxSnapshotId": str(row.get("abox_snapshot_id") or ""),
            "activeAboxSnapshotId": str(row.get("active_abox_snapshot_id") or ""),
            "tboxVersion": str(row.get("tbox_version") or ""),
            "tboxFingerprint": str(row.get("tbox_fingerprint") or ""),
            "ruleboxRulesHash": str(row.get("rulebox_rules_hash") or ""),
            "entityCount": int(row.get("entity_count") or 0),
            "relationCount": int(row.get("relation_count") or 0),
            "inferenceGenerationId": str(row.get("inference_generation_id") or ""),
            "inferenceStatus": str(row.get("inference_status") or ""),
            "sourceSymbols": _json_loads(row.get("source_symbols_json"), []),
            "context": _json_loads(row.get("context_payload_json"), {}),
            "result": _json_loads(row.get("result_payload_json"), {}),
            "createdAt": str(row.get("created_at") or ""),
            "updatedAt": str(row.get("updated_at") or ""),
        }
