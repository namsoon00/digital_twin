"""Commit compiled evidence once; normalize diagnostic traces off the live path."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from digital_twin.infrastructure.settings import utc_now
from digital_twin.modules.reasoning.domain.ontology_execution_trace import reasoning_execution_trace_payload
from digital_twin.modules.reasoning.domain.ontology_projection_audit import projection_run_from_payload
from digital_twin.modules.reasoning.domain.rule_execution_proof import compiled_rule_execution_completed


# Exactly the order accepted by MySQLOntologyProjectionRunStore.values().
RUN_COLUMNS = (
    "run_id", "portfolio_id", "account_id", "tenant_id", "world_id", "world_type",
    "market_world_id", "source_snapshot_at", "source_snapshot_fingerprint",
    "first_observed_at", "last_observed_at", "started_at", "completed_at",
    "activated_at", "status", "graph_store", "execution_namespace_id",
    "engine_deployment_id", "graph_database", "release_fingerprint",
    "validation_cohort_id", "projection_mode", "material_fingerprint",
    "abox_snapshot_id", "active_abox_snapshot_id", "tbox_version", "tbox_fingerprint",
    "rulebox_rules_hash", "entity_count", "relation_count", "inference_generation_id",
    "inference_status", "source_symbols_json", "context_payload_json",
    "result_payload_json", "created_at", "updated_at",
)


class MySQLCompiledProjectionAuditMixin:
    def commit_compiled_projection(self, run, result):
        """Facts, complete result and retryable detail work share one commit.

        A native begin/update protocol protects a separate mutable graph store.
        Compiled execution has no external write to recover, so its immutable
        evidence can be inserted in its final form without an earlier commit.
        """
        inference = result.get("inferenceBox") or {}
        if not compiled_rule_execution_completed(inference):
            raise ValueError("Compiled audit requires complete source-bound execution proof")
        if (run.account_id != inference.get("accountId")
                or run.abox_snapshot_id != inference.get("sourceAboxSnapshotId")
                or not run.context_payload.get("compiledSourceGraph")):
            raise ValueError("Compiled audit source or account mismatch")
        trace_input = {key: result[key] for key in (
            "status", "runtimeStages", "ruleboxExecution", "graphInput",
            "reasoningContext", "performanceAssessment",
        ) if key in result}
        # Inference and facts are already preserved in the canonical run. Do
        # not duplicate them into the outbox or a second large result object.
        completed = replace(run, result_payload={**run.result_payload,
            "compiledTraceInput": trace_input, "traceMaterialization": "deferred"})
        stamp = utc_now()
        values = self.values(completed, stamp)
        if len(values) != len(RUN_COLUMNS):
            raise ValueError("Compiled audit column contract mismatch")
        sql = ("INSERT INTO ontology_projection_runs /* operation:compiled-evidence-commit */ ("
               + ", ".join(RUN_COLUMNS) + ") VALUES ("
               + ", ".join(["%s"] * len(RUN_COLUMNS)) + ") ON DUPLICATE KEY UPDATE "
               + ", ".join(key + " = VALUES(" + key + ")" for key in RUN_COLUMNS
                           if key not in {"run_id", "created_at"}))
        with self.transaction() as connection:
            connection.execute(sql, values)
            connection.execute(
                "INSERT INTO ontology_projection_trace_outbox "
                "(run_id, status, available_at, created_at, updated_at) "
                "VALUES (%s, 'pending', %s, %s, %s) ON DUPLICATE KEY UPDATE run_id = VALUES(run_id)",
                (run.run_id, stamp, stamp, stamp),
            )
        return completed

    def materialize_compiled_trace(self):
        """One bounded, idempotent diagnostic turn; never publishes an alert."""
        run_id = ""
        stamp = utc_now()
        try:
            with self.transaction() as connection:
                job = connection.execute(
                    "SELECT run_id FROM ontology_projection_trace_outbox "
                    "WHERE status = 'pending' AND available_at <= %s "
                    "ORDER BY available_at, run_id LIMIT 1 FOR UPDATE SKIP LOCKED", (stamp,),
                ).fetchone()
                if not job:
                    return {"status": "idle", "completedCount": 0}
                run_id = job["run_id"]
                row = connection.execute(
                    "SELECT * FROM ontology_projection_runs WHERE run_id = %s", (run_id,),
                ).fetchone()
                if not row:
                    raise ValueError("Compiled trace source audit is unavailable")
                run = projection_run_from_payload(self.row_payload(row))
                inference = run.result_payload.get("compiledInferenceBox") or {}
                if not compiled_rule_execution_completed(inference):
                    raise ValueError("Compiled trace source proof is incomplete")
                result = {**run.result_payload.get("compiledTraceInput", {}), "inferenceBox": inference}
                trace = reasoning_execution_trace_payload(run, result, settings=self.runtime_settings)
                self._replace_execution_trace_with_connection(connection, trace, stamp)
                connection.execute("DELETE FROM ontology_projection_trace_outbox WHERE run_id = %s", (run_id,))
            return {"status": "completed", "completedCount": 1, "runId": run_id,
                    "ruleCount": trace["summary"]["ruleOutcomeCount"]}
        except Exception as error:
            if not run_id:
                raise
            # The failed trace transaction rolled back; a restart can retry its
            # exact committed input without rerunning a rule or notification.
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
            with self.transaction() as connection:
                connection.execute(
                    "UPDATE ontology_projection_trace_outbox SET "
                    "status = CASE WHEN attempts >= 7 THEN 'failed' ELSE 'pending' END, "
                    "attempts = attempts + 1, available_at = %s, last_error = %s, updated_at = %s "
                    "WHERE run_id = %s AND status = 'pending'",
                    (retry_at, type(error).__name__, stamp, run_id),
                )
            return {"status": "retry", "completedCount": 0, "runId": run_id,
                    "errorType": type(error).__name__}

    def compiled_trace_summary(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS item_count FROM ontology_projection_trace_outbox GROUP BY status"
            ).fetchall()
        return {row["status"]: int(row["item_count"]) for row in rows}

    def retry_compiled_traces(self, limit=10):
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE ontology_projection_trace_outbox SET status = 'pending', attempts = 0, "
                "available_at = %s, last_error = '' WHERE status = 'failed' "
                "ORDER BY available_at, run_id LIMIT %s", (utc_now(), max(1, min(100, int(limit)))),
            )
        return max(0, int(cursor.rowcount))
