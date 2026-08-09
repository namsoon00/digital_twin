from datetime import datetime, timedelta, timezone
from typing import Dict, List, Mapping

from ..domain.ontology_execution_trace import reasoning_execution_trace_payload
from ..domain.ontology_projection_audit import OntologyProjectionRun
from ..domain.ontology_runtime_operations import summarize_projection_runtime_observations
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


class MySQLOntologyProjectionRunStore(MySQLOperationalConnection):
    """Durable MySQL audit for the source data behind an active ABox generation."""

    def projection_audit_stale_after_seconds(self) -> int:
        """Return the recovery boundary for an interrupted projection audit.

        A fixed thirty-minute cutoff can leave a stopped local worker blocking
        recovery long after its own TypeDB timeout has elapsed. By default the
        boundary follows that execution contract plus a small handoff buffer.
        An explicit runtime value overrides it for an operator-managed host.
        """
        settings = getattr(self, "runtime_settings", {}) or {}

        def seconds(key: str, fallback: int) -> int:
            try:
                value = int(float(str(settings.get(key) or "").strip()))
            except (TypeError, ValueError):
                value = fallback
            return max(0, min(24 * 60 * 60, value))

        configured = seconds("ontologyProjectionAuditStaleAfterSeconds", 0)
        if configured:
            return max(120, min(3600, configured))
        execution_timeout = seconds("ontologyReasoningExecutionTimeoutSeconds", 360) or 360
        execution_grace = seconds("ontologyReasoningExecutionTimeoutGraceSeconds", 10)
        return max(120, min(3600, execution_timeout + execution_grace + 60))

    def _recover_stale_runs(
        self,
        connection,
        stamp: str,
        world_id: str = "",
        stale_after_seconds: int = 0,
    ) -> Dict[str, object]:
        stale_after = int(stale_after_seconds or self.projection_audit_stale_after_seconds())
        stale_after = max(120, min(3600, stale_after))
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after)).isoformat().replace("+00:00", "Z")
        clauses = ["status = 'projecting'", "started_at < %s"]
        params: List[object] = [cutoff]
        clean_world_id = str(world_id or "").strip()
        if clean_world_id:
            clauses.append("world_id = %s")
            params.append(clean_world_id)
        cursor = connection.execute(
            """
            UPDATE ontology_projection_runs
            SET status = 'aborted-stale', completed_at = %s, updated_at = %s,
                result_payload_json = %s
            WHERE """ + " AND ".join(clauses),
            (
                stamp,
                stamp,
                json_dumps({
                    "status": "aborted-stale",
                    "reason": "Projection worker ended before activation was audited.",
                    "staleAfterSeconds": stale_after,
                }),
                *params,
            ),
        )
        try:
            aborted_count = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        except (TypeError, ValueError):
            aborted_count = 0
        return {
            "status": "ok",
            "worldId": clean_world_id,
            "staleAfterSeconds": stale_after,
            "cutoff": cutoff,
            "abortedCount": aborted_count,
        }

    def recover_stale_runs(
        self,
        world_id: str = "",
        stale_after_seconds: int = 0,
    ) -> Dict[str, object]:
        """Mark only elapsed in-progress audit rows as recoverable.

        This never mutates TypeDB. The recorder still requires an aligned
        active ABox/InferenceBox before it treats an interrupted run as
        recovered.
        """
        stamp = utc_now()
        with self.transaction() as connection:
            recovery = self._recover_stale_runs(
                connection,
                stamp,
                world_id=world_id,
                stale_after_seconds=stale_after_seconds,
            )
        self.last_stale_recovery = dict(recovery)
        return recovery

    def begin(self, run: OntologyProjectionRun) -> OntologyProjectionRun:
        stamp = utc_now()
        with self.transaction() as connection:
            self.last_stale_recovery = self._recover_stale_runs(
                connection,
                stamp,
                world_id=str(run.world_id or ""),
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
            self._complete_with_connection(connection, run, stamp)
        return run

    @staticmethod
    def _complete_with_connection(connection, run: OntologyProjectionRun, stamp: str) -> None:
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

    def complete_with_execution_trace(
        self,
        run: OntologyProjectionRun,
        result: Dict[str, object],
    ) -> OntologyProjectionRun:
        """Commit the run result and its normalized stage/rule trace together."""
        stamp = utc_now()
        trace = reasoning_execution_trace_payload(
            run,
            result,
            settings=getattr(self, "runtime_settings", {}) or {},
        )
        with self.transaction() as connection:
            self._complete_with_connection(connection, run, stamp)
            self._replace_execution_trace_with_connection(connection, trace, stamp)
        return run

    @staticmethod
    def _bulk_execute(connection, sql: str, rows: List[tuple]) -> None:
        if not rows:
            return
        executemany = getattr(connection, "executemany", None)
        if callable(executemany):
            executemany(sql, rows)
            return
        for row in rows:
            connection.execute(sql, row)

    def _replace_execution_trace_with_connection(
        self,
        connection,
        trace: Dict[str, object],
        stamp: str,
    ) -> None:
        run_id = str(trace.get("runId") or "").strip()
        if not run_id:
            return
        connection.execute(
            "DELETE FROM ontology_reasoning_run_stages WHERE run_id = %s",
            (run_id,),
        )
        connection.execute(
            "DELETE FROM ontology_reasoning_rule_runs WHERE run_id = %s",
            (run_id,),
        )
        stage_rows = [
            (
                run_id,
                str(item.get("stageKey") or ""),
                str(item.get("version") or ""),
                str(item.get("worldId") or ""),
                str(item.get("accountId") or ""),
                str(item.get("inferenceGenerationId") or trace.get("inferenceGenerationId") or ""),
                str(item.get("lane") or "CORE_REASONING"),
                int(item.get("stageOrder") or 0),
                str(item.get("status") or ""),
                str(item.get("startedAt") or ""),
                str(item.get("completedAt") or ""),
                int(item.get("durationMs") or 0),
                int(item.get("inputCount") or 0),
                int(item.get("outputCount") or 0),
                json_dumps(item.get("detail") or {}),
                stamp,
                stamp,
            )
            for item in trace.get("stages") or []
            if isinstance(item, dict) and str(item.get("stageKey") or "").strip()
        ]
        self._bulk_execute(
            connection,
            """
            INSERT INTO ontology_reasoning_run_stages (
                run_id, stage_key, trace_version, world_id, account_id,
                inference_generation_id, lane,
                stage_order, status, started_at, completed_at, duration_ms,
                input_count, output_count, detail_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            stage_rows,
        )
        rule_rows = [
            (
                run_id,
                str(item.get("ruleRunKey") or ""),
                str(item.get("version") or ""),
                str(item.get("worldId") or ""),
                str(item.get("accountId") or ""),
                str(item.get("inferenceGenerationId") or trace.get("inferenceGenerationId") or ""),
                str(item.get("lane") or "CORE_REASONING"),
                str(item.get("stageKey") or "native-rule-evaluation"),
                str(item.get("ruleId") or ""),
                str(item.get("ruleVersion") or ""),
                str(item.get("status") or ""),
                str(item.get("selectedReason") or ""),
                str(item.get("queryMode") or ""),
                int(item.get("queryCount") or 0),
                int(item.get("durationMs") or 0),
                int(item.get("queryDurationMs") or 0),
                json_dumps(item.get("targetSymbols") or []),
                1 if item.get("matched") else 0,
                1 if item.get("reused") else 0,
                str(item.get("costClass") or "fast"),
                str(item.get("failureReason") or "")[:1000],
                json_dumps(item.get("detail") or {}),
                stamp,
                stamp,
            )
            for item in trace.get("rules") or []
            if isinstance(item, dict)
            and str(item.get("ruleRunKey") or "").strip()
            and str(item.get("ruleId") or "").strip()
        ]
        self._bulk_execute(
            connection,
            """
            INSERT INTO ontology_reasoning_rule_runs (
                run_id, rule_run_key, trace_version, world_id, account_id,
                inference_generation_id, lane, stage_key, rule_id, rule_version,
                status, selected_reason,
                query_mode, query_count, duration_ms, query_duration_ms,
                target_symbols_json, matched, reused, cost_class,
                failure_reason, detail_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rule_rows,
        )

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

    def execution_trace(
        self,
        run_id: str = "",
        account_id: str = "",
        world_id: str = "",
        limit: int = 20,
    ) -> Dict[str, object]:
        """Read normalized execution history without querying TypeDB."""
        clauses = []
        params: List[object] = []
        if str(run_id or "").strip():
            clauses.append("run_id = %s")
            params.append(str(run_id).strip())
        if str(account_id or "").strip():
            clauses.append("account_id = %s")
            params.append(str(account_id).strip())
        if str(world_id or "").strip():
            clauses.append("world_id = %s")
            params.append(str(world_id).strip())
        bounded = max(1, min(100, int(limit or 20)))
        stage_sql = "SELECT * FROM ontology_reasoning_run_stages"
        if clauses:
            stage_sql += " WHERE " + " AND ".join(clauses)
        stage_sql += " ORDER BY updated_at DESC, run_id DESC, stage_order ASC LIMIT %s"
        stage_params = [*params, bounded * 40]
        with self.connect() as connection:
            stage_rows = connection.execute(stage_sql, stage_params).fetchall()
        run_ids = []
        for row in stage_rows or []:
            candidate = str(row.get("run_id") or "")
            if candidate and candidate not in run_ids:
                run_ids.append(candidate)
            if len(run_ids) >= bounded:
                break
        stage_rows = [row for row in stage_rows or [] if str(row.get("run_id") or "") in run_ids]
        rule_rows = []
        if run_ids:
            placeholders = ", ".join(["%s"] * len(run_ids))
            with self.connect() as connection:
                rule_rows = connection.execute(
                    "SELECT * FROM ontology_reasoning_rule_runs WHERE run_id IN ("
                    + placeholders
                    + ") ORDER BY updated_at DESC, duration_ms DESC, rule_id ASC",
                    run_ids,
                ).fetchall()
        grouped = {
            item: {
                "runId": item,
                "worldId": "",
                "accountId": "",
                "inferenceGenerationId": "",
                "lane": "",
                "updatedAt": "",
                "stages": [],
                "rules": [],
            }
            for item in run_ids
        }
        for row in stage_rows or []:
            item = self.stage_trace_row_payload(row)
            target = grouped.get(item["runId"])
            if not target:
                continue
            target.update({
                "worldId": item["worldId"],
                "accountId": item["accountId"],
                "inferenceGenerationId": item["inferenceGenerationId"],
                "lane": item["lane"],
                "updatedAt": max(str(target.get("updatedAt") or ""), item["updatedAt"]),
            })
            target["stages"].append(item)
        for row in rule_rows or []:
            item = self.rule_trace_row_payload(row)
            target = grouped.get(item["runId"])
            if target:
                target["rules"].append(item)
        runs = [grouped[item] for item in run_ids if item in grouped]
        return {
            "status": "ok",
            "runCount": len(runs),
            "runs": runs,
        }

    def execution_trace_for_inference_generation(
        self,
        inference_generation_id: str,
        account_id: str = "",
    ) -> Dict[str, object]:
        generation_id = str(inference_generation_id or "").strip()
        if not generation_id:
            return {
                "status": "unavailable",
                "reason": "Inference generation ID is missing.",
                "runCount": 0,
                "runs": [],
            }
        clauses = ["inference_generation_id = %s"]
        params: List[object] = [generation_id]
        if str(account_id or "").strip():
            clauses.append("account_id = %s")
            params.append(str(account_id).strip())
        with self.connect() as connection:
            cursor = connection.execute(
                "SELECT run_id FROM ontology_reasoning_run_stages WHERE "
                + " AND ".join(clauses)
                + " ORDER BY updated_at DESC LIMIT 1",
                params,
            )
            if hasattr(cursor, "fetchone"):
                row = cursor.fetchone()
            else:
                rows = cursor.fetchall()
                row = rows[0] if rows else {}
        run_id = str((row or {}).get("run_id") or "") if isinstance(row, Mapping) else ""
        if not run_id:
            return {
                "status": "not-found",
                "reason": "No projection run was found for the inference generation.",
                "inferenceGenerationId": generation_id,
                "runCount": 0,
                "runs": [],
            }
        payload = self.execution_trace(run_id=run_id, limit=1)
        payload["inferenceGenerationId"] = generation_id
        return payload

    @staticmethod
    def stage_trace_row_payload(row: Dict[str, object]) -> Dict[str, object]:
        return {
            "runId": str(row.get("run_id") or ""),
            "stageKey": str(row.get("stage_key") or ""),
            "version": str(row.get("trace_version") or ""),
            "worldId": str(row.get("world_id") or ""),
            "accountId": str(row.get("account_id") or ""),
            "inferenceGenerationId": str(row.get("inference_generation_id") or ""),
            "lane": str(row.get("lane") or ""),
            "stageOrder": int(row.get("stage_order") or 0),
            "status": str(row.get("status") or ""),
            "startedAt": str(row.get("started_at") or ""),
            "completedAt": str(row.get("completed_at") or ""),
            "durationMs": int(row.get("duration_ms") or 0),
            "inputCount": int(row.get("input_count") or 0),
            "outputCount": int(row.get("output_count") or 0),
            "detail": _json_loads(row.get("detail_json"), {}),
            "updatedAt": str(row.get("updated_at") or ""),
        }

    @staticmethod
    def rule_trace_row_payload(row: Dict[str, object]) -> Dict[str, object]:
        return {
            "runId": str(row.get("run_id") or ""),
            "ruleRunKey": str(row.get("rule_run_key") or ""),
            "version": str(row.get("trace_version") or ""),
            "worldId": str(row.get("world_id") or ""),
            "accountId": str(row.get("account_id") or ""),
            "inferenceGenerationId": str(row.get("inference_generation_id") or ""),
            "lane": str(row.get("lane") or ""),
            "stageKey": str(row.get("stage_key") or ""),
            "ruleId": str(row.get("rule_id") or ""),
            "ruleVersion": str(row.get("rule_version") or ""),
            "status": str(row.get("status") or ""),
            "selectedReason": str(row.get("selected_reason") or ""),
            "queryMode": str(row.get("query_mode") or ""),
            "queryCount": int(row.get("query_count") or 0),
            "durationMs": int(row.get("duration_ms") or 0),
            "queryDurationMs": int(row.get("query_duration_ms") or 0),
            "targetSymbols": _json_loads(row.get("target_symbols_json"), []),
            "matched": bool(row.get("matched")),
            "reused": bool(row.get("reused")),
            "costClass": str(row.get("cost_class") or ""),
            "failureReason": str(row.get("failure_reason") or ""),
            "detail": _json_loads(row.get("detail_json"), {}),
            "updatedAt": str(row.get("updated_at") or ""),
        }

    def rule_runtime_summary(
        self,
        world_id: str = "",
        account_id: str = "",
        limit: int = 5000,
    ) -> Dict[str, object]:
        clauses = []
        params: List[object] = []
        if str(world_id or "").strip():
            clauses.append("world_id = %s")
            params.append(str(world_id).strip())
        if str(account_id or "").strip():
            clauses.append("account_id = %s")
            params.append(str(account_id).strip())
        sql = "SELECT * FROM ontology_reasoning_rule_runs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT %s"
        params.append(max(1, min(10000, int(limit or 5000))))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        grouped: Dict[str, List[Dict[str, object]]] = {}
        for row in rows or []:
            item = self.rule_trace_row_payload(row)
            if item["ruleId"]:
                grouped.setdefault(item["ruleId"], []).append(item)
        summaries = []
        for rule_id, samples in grouped.items():
            durations = sorted(int(item.get("durationMs") or 0) for item in samples)
            p95_index = min(
                len(durations) - 1,
                max(0, ((95 * len(durations) + 99) // 100) - 1),
            )
            failed_samples = [
                item
                for item in samples
                if any(
                    token in str(item.get("status") or "").lower()
                    for token in ["error", "timeout", "failed", "blocked"]
                )
            ]
            summaries.append({
                "ruleId": rule_id,
                "sampleCount": len(samples),
                "matchedCount": len([item for item in samples if item.get("matched")]),
                "failureCount": len(failed_samples),
                "averageDurationMs": int(sum(durations) / len(durations)) if durations else 0,
                "p95DurationMs": durations[p95_index] if durations else 0,
                "maxDurationMs": durations[-1] if durations else 0,
                "lastStatus": str(samples[0].get("status") or ""),
                "lastUpdatedAt": str(samples[0].get("updatedAt") or ""),
            })
        summaries.sort(
            key=lambda item: (
                item["failureCount"],
                item["p95DurationMs"],
                item["sampleCount"],
                item["ruleId"],
            ),
            reverse=True,
        )
        return {
            "status": "ok",
            "sampleCount": len(rows or []),
            "ruleCount": len(summaries),
            "rules": summaries,
        }

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
        summary = summarize_projection_runtime_observations(
            observations,
            getattr(self, "runtime_settings", {}) or {},
        )
        statuses = [str(row.get("status") or "") for row in rows]
        summary["auditHealth"] = {
            "staleAfterSeconds": self.projection_audit_stale_after_seconds(),
            "windowProjectingCount": sum(1 for status in statuses if status == "projecting"),
            "windowAbortedStaleCount": sum(1 for status in statuses if status == "aborted-stale"),
            "lastRecovery": dict(getattr(self, "last_stale_recovery", {}) or {}),
        }
        return summary

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
