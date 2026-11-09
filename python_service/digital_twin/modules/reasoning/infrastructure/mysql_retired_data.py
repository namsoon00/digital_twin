"""Explicit, bounded cleanup of data owned only by retired reasoning engines."""

import json
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_schema_tuning import quote_identifier
from digital_twin.infrastructure.storage_values import iso_utc


class MySQLRetiredReasoningDataStore(MySQLOperationalConnection):
    # Order removes disposable children before their roots. Shared prices,
    # portfolios, model observations/outcomes and delivered notices are not here.
    OWNED_TABLES = (
        ("investment_alert_coverage", "deployment_id"),
        ("investment_alert_coverage_health", "deployment_id"),
        ("portfolio_inference_overlays", "deployment_id"),
        ("shared_instrument_inference_heads", "deployment_id"),
        ("shared_instrument_inference_snapshots", "deployment_id"),
        ("market_observation_reasoning_receipts", "deployment_id"),
        ("reasoning_engine_job_sources", "deployment_id"),
        ("reasoning_engine_jobs", "deployment_id"),
        ("reasoning_engine_shadow_jobs", "candidate_deployment_id"),
        # Comparisons owned by the current candidate remain its sealed proof,
        # even when their baseline was a now-retired engine.
        ("reasoning_engine_comparisons", "candidate_deployment_id"),
    )
    CASE_CHILDREN = ("decision_candidate_snapshots", "investment_decision_audit_entries")

    @staticmethod
    def _live_proof(connection, control):
        rows = connection.execute(
            "SELECT JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.delivery_authorized')) delivery, "
            "JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.trace_complete')) trace, "
            "JSON_EXTRACT(result_json,'$.projection_results.*.nativeTypeDbReasoningCompleted') native, "
            "JSON_EXTRACT(result_json,'$.projection_results.*.generationAligned') aligned "
            "FROM reasoning_engine_jobs WHERE deployment_id=%s AND job_status='completed' "
            "AND claimed_at >= %s AND source_event_id NOT LIKE 'reasoning-validation:%%' "
            "ORDER BY completed_at DESC LIMIT 5",
            (control["active_deployment_id"], control["updated_at"]),
        ).fetchall()
        for row in rows:
            native = json.loads(row.get("native") or "[]")
            aligned = json.loads(row.get("aligned") or "[]")
            if (row.get("delivery") == "true" and row.get("trace") == "true"
                    and native and aligned and all(v is True for v in native + aligned)):
                return True
        return False

    def _selection(self, connection, expected_version, apply, *, include_purged=False):
        control = connection.execute(
            "SELECT * FROM reasoning_engine_control WHERE control_id='global' FOR UPDATE"
        ).fetchone() or {}
        if int(control.get("version") or 0) != int(expected_version):
            raise RuntimeError("Reasoning control changed; no retired data was deleted.")
        if not control.get("active_deployment_id") or control.get("active_deployment_id") != control.get("delivery_deployment_id"):
            raise RuntimeError("Aligned active and delivery versions are required.")
        keep = sorted({control.get(k) for k in ("active_deployment_id", "delivery_deployment_id", "candidate_deployment_id") if control.get(k)})
        completed_filter = "" if include_purged else (
            " AND IF(JSON_VALID(last_health_json), "
            "JSON_EXTRACT(last_health_json,'$.retiredDataPurge.completedAt'),NULL) IS NULL"
        )
        rows = connection.execute(
            "SELECT deployment_id,graph_store_binding FROM reasoning_engine_deployments "
            "WHERE deployment_status='retired' AND deployment_id NOT IN ("
            + ",".join(["%s"] * len(keep)) + ")" + completed_filter + " ORDER BY deployment_id", tuple(keep),
        ).fetchall()
        if apply and rows and not self._live_proof(connection, control):
            raise RuntimeError("A new live native result from the active version is required before deletion.")
        return control, keep, rows

    @staticmethod
    def _disposable_subject(alias="subject"):
        return (
            alias + ".ai_request_id='' AND " + alias + ".notification_job_id='' "
            "AND NOT EXISTS (SELECT 1 FROM decision_publications p WHERE p.subject_case_id=" + alias + ".subject_case_id) "
            "AND NOT EXISTS (SELECT 1 FROM investment_ai_insight_episodes e WHERE e.subject_case_id=" + alias + ".subject_case_id)"
            " AND NOT EXISTS (SELECT 1 FROM investment_hypothesis_observation_episodes e "
            "JOIN decision_candidate_snapshots s ON s.candidate_set_id=e.candidate_set_id "
            "WHERE s.subject_case_id=" + alias + ".subject_case_id)"
            " AND NOT EXISTS (SELECT 1 FROM investment_decision_episodes e "
            "WHERE " + alias + ".inference_generation_id<>'' AND e.inference_generation_id=" + alias + ".inference_generation_id)"
        )

    def _phases(self, ids):
        placeholders = ",".join(["%s"] * len(ids))
        owned = "subject.deployment_id IN (" + placeholders + ") AND " + self._disposable_subject()
        phases = []
        for table, column in self.OWNED_TABLES:
            condition = "target." + quote_identifier(column) + " IN (" + placeholders + ")"
            if table == "shared_instrument_inference_snapshots":
                condition += " AND NOT EXISTS (SELECT 1 FROM shared_instrument_inference_heads h WHERE h.snapshot_id=target.snapshot_id)"
            phases.append((table, condition))
        # Drain indexed execution data before scanning protected historical
        # cases, so each large-table batch does not repeat that lineage scan.
        phases.extend((table, "EXISTS (SELECT 1 FROM investment_subject_decision_cases subject "
                      "WHERE subject.subject_case_id=target.subject_case_id AND " + owned + ")") for table in self.CASE_CHILDREN)
        phases.append(("investment_subject_decision_cases", "target.deployment_id IN (" + placeholders + ") AND " + self._disposable_subject("target")))
        phases.append(("investment_reasoning_cases", "target.deployment_id IN (" + placeholders + ") AND NOT EXISTS "
                       "(SELECT 1 FROM investment_subject_decision_cases subject WHERE subject.batch_case_id=target.case_id)"))
        phases.append(("reasoning_engine_release_artifacts", "target.deployment_id IN (" + placeholders + ") "
                       "AND NOT EXISTS (SELECT 1 FROM investment_subject_decision_cases s WHERE s.deployment_id=target.deployment_id) "
                       "AND NOT EXISTS (SELECT 1 FROM reasoning_engine_comparisons c WHERE c.baseline_deployment_id=target.deployment_id)"))
        return phases

    def purge(self, expected_control_version, owner_request, *, apply=False, limit=250):
        if not str(owner_request or "").strip():
            raise ValueError("An explicit owner request is required.")
        limit = max(1, min(1000, int(limit)))
        counts = {}
        compacted = 0
        with self.transaction() as connection:
            control, keep, rows = self._selection(connection, expected_control_version, apply)
            ids = [row["deployment_id"] for row in rows]
            if not ids:
                return {"status": "empty", "protectedDeploymentIds": keep, "deletedRows": 0, "tables": {}}
            for table, condition in self._phases(ids):
                quoted = quote_identifier(table)
                if not apply:
                    counts[table] = int(connection.execute("SELECT COUNT(*) n FROM " + quoted + " target WHERE " + condition, tuple(ids)).fetchone()["n"])
                    continue
                keys = connection.execute("SHOW KEYS FROM " + quoted + " WHERE Key_name='PRIMARY'").fetchall()
                columns = [r["Column_name"] for r in sorted(keys, key=lambda r:r["Seq_in_index"])]
                if not columns:
                    raise RuntimeError("A bounded primary key is required for retired cleanup: " + table)
                selection = ",".join("target." + quote_identifier(col) for col in columns)
                try:
                    selected = connection.execute("SELECT " + selection + " FROM " + quoted + " target WHERE "
                        + condition + " LIMIT %s FOR UPDATE", (*ids, limit)).fetchall()
                except Exception as error:
                    raise RuntimeError("Retired cleanup selection failed for " + table + "; this batch was not deleted.") from error
                if selected:
                    fields = "(" + ",".join(quote_identifier(c) for c in columns) + ")"
                    values = ",".join("(" + ",".join(["%s"] * len(columns)) + ")" for _ in selected)
                    params = tuple(row[col] for row in selected for col in columns)
                    counts[table] = connection.execute("DELETE FROM " + quoted + " WHERE " + fields + " IN (" + values + ")", params).rowcount
                    break  # One bounded table batch per transaction.
                counts[table] = 0
            if apply and not any(counts.values()):
                metadata = connection.execute("SELECT deployment_id,last_health_json FROM reasoning_engine_deployments "
                    "WHERE deployment_id IN (" + ",".join(["%s"] * len(ids)) + ")", tuple(ids)).fetchall()
                for row in metadata:
                    health = json.loads(row.get("last_health_json") or "{}")
                    if health.get("retiredDataPurge"):
                        continue
                    retained = {key: health[key] for key in ("ruleboxFingerprint", "tboxFingerprint", "releaseFingerprint",
                        "candidateReleaseFingerprint", "candidateReleaseId", "validationCohortId") if key in health}
                    retained.update(status="retired", retiredDataPurge={"completedAt": iso_utc(), "ownerRequest": str(owner_request)[:500]})
                    connection.execute("UPDATE reasoning_engine_deployments SET last_health_json=%s WHERE deployment_id=%s",
                                       (json.dumps(retained), row["deployment_id"]))
                    compacted += 1
        return {"status": "deleted" if apply and any(counts.values()) else "complete" if apply else "preview",
                "protectedDeploymentIds": keep, "retiredDeploymentCount": len(ids),
                "deletedRows": sum(counts.values()) if apply else 0, "tables": counts,
                "compactedMetadataCount": compacted,
                "retiredGraphBindings": sorted({row["graph_store_binding"] for row in rows if row.get("graph_store_binding")})}

    def prune_graphs(self, expected_control_version, owner_request, exists, delete, *, limit=1):
        """Drop only project-owned databases with no current/experimental owner."""
        if not str(owner_request or "").strip():
            raise ValueError("An explicit owner request is required.")
        removed, absent = [], []
        with self.transaction() as connection:
            control, keep, retired = self._selection(connection, expected_control_version, True, include_purged=True)
            owners = connection.execute("SELECT deployment_id,graph_store_binding,deployment_status "
                "FROM reasoning_engine_deployments FOR UPDATE").fetchall()
            protected = {r["graph_store_binding"] for r in owners
                         if r["deployment_id"] in keep or r["deployment_status"] != "retired"}
            candidates = sorted({r["graph_store_binding"] for r in retired
                                 if r.get("graph_store_binding") and r["graph_store_binding"].startswith("orbit_")})
            for database in candidates:
                if database in protected:
                    continue
                if not exists(database):
                    absent.append(database)
                    continue
                delete(database)
                if exists(database):
                    raise RuntimeError("The retired graph database still exists after deletion.")
                removed.append(database)
                if len(removed) >= max(1, min(3, int(limit))):
                    break
        return {"removedDatabases": removed, "alreadyAbsent": absent, "protectedDatabases": sorted(protected)}


def run_retired_reasoning_cleanup(settings, *, owner_request, apply=False, max_batches=1, limit=250):
    """Explicit policy entry point shared by local commands and maintenance."""
    configured = {**dict(settings or {}), "_skipOperationalSchemaBootstrap": "1", "_skipOperationalHistoryRetention": "1"}
    store = MySQLRetiredReasoningDataStore(configured)
    with store.connect() as connection:
        control = connection.execute("SELECT version FROM reasoning_engine_control WHERE control_id='global'").fetchone() or {}
    counts, result = {}, {}
    for _ in range(max(1, min(1000, int(max_batches)))):
        result = store.purge(control.get("version", 0), owner_request, apply=apply, limit=limit)
        for table, count in result.get("tables", {}).items():
            counts[table] = counts.get(table, 0) + count
        if not apply or result.get("status") != "deleted":
            break
    return {**result, "tables": counts, "deletedRows": sum(counts.values()) if apply else 0}


def run_retired_reasoning_graph_cleanup(settings, *, owner_request, limit=1):
    from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
    configured = {**dict(settings or {}), "_skipOperationalSchemaBootstrap": "1", "_skipOperationalHistoryRetention": "1"}
    store = MySQLRetiredReasoningDataStore(configured)
    with store.connect() as connection:
        version = connection.execute("SELECT version FROM reasoning_engine_control WHERE control_id='global'").fetchone()["version"]
    repository = ontology_repository_from_settings(configured)
    imported = repository.driver_imports()
    if imported[1] is not None:
        raise RuntimeError("The TypeDB driver is unavailable; no graph database was deleted.")
    with repository.create_driver(imported, request_timeout_seconds=20) as driver:
        return store.prune_graphs(version, owner_request, driver.databases.contains,
                                  lambda name: driver.databases.get(name).delete(), limit=limit)
