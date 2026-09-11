"""Fixed, bounded operational SELECTs. Never import application store factories."""

import ipaddress
import os
import re


def database_options(environ, timeout):
    host = environ.get("MYSQL_HOST", "127.0.0.1")
    if host == "localhost":
        host = "127.0.0.1"
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError("MySQL must be loopback")
    database = environ.get("MYSQL_DATABASE", "")
    user = environ.get("MYSQL_USER", "")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", database) or not user:
        raise ValueError("Explicit MYSQL_DATABASE and MYSQL_USER are required")
    port = int(environ.get("MYSQL_PORT", "3306"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid MySQL port")
    options = dict(host=host, port=port, user=user, database=database,
                   password=environ.get("MYSQL_PASSWORD", ""), charset="utf8mb4",
                   connect_timeout=timeout, read_timeout=timeout, write_timeout=timeout,
                   autocommit=False, local_infile=False)
    if environ.get("MYSQL_UNIX_SOCKET"):
        if not os.path.isabs(environ["MYSQL_UNIX_SOCKET"]):
            raise ValueError("MySQL socket must be absolute")
        options["unix_socket"] = environ["MYSQL_UNIX_SOCKET"]
    return options


class ReadOnlyDatabase:
    """One short rollback-only snapshot; both returned rows and server time bounded."""

    def __init__(self, options, row_limit, query_ms, connect=None):
        if connect is None:
            import pymysql
            connect = pymysql.connect
            options = dict(options, cursorclass=pymysql.cursors.DictCursor)
        self.connection = connect(**options)
        self.row_limit = row_limit
        self.queries = 0
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (query_ms,))
                cursor.execute("SET SESSION lock_wait_timeout = 1")
                cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        except BaseException:
            self.close()
            raise

    def read(self, sql, params=(), limit=None):
        # Only fixed internal SELECTs enter here; this is not a public SQL shell.
        if not sql.lstrip().startswith("SELECT ") or any(
            token in sql.upper() for token in ("FOR UPDATE", "INTO OUTFILE", "INTO DUMPFILE", ";")
        ):
            raise ValueError("Only nonlocking SELECTs are allowed")
        if self.queries >= 40:
            raise ValueError("Observation query budget exceeded")
        limit = min(self.row_limit, limit or self.row_limit)
        self.queries += 1
        with self.connection.cursor() as cursor:
            cursor.execute(sql + " LIMIT %s", tuple(params) + (limit + 1,))
            rows = cursor.fetchmany(limit + 1)
        return {"rows": rows[:limit], "truncated": len(rows) > limit}

    def close(self):
        try:
            self.connection.rollback()
        finally:
            self.connection.close()


CONTROL = """SELECT active_deployment_id, delivery_deployment_id,
candidate_deployment_id, version FROM reasoning_engine_control WHERE control_id = 'global'"""

DEPLOYMENT = """SELECT deployment_id, deployment_status,
LEFT(JSON_UNQUOTE(JSON_EXTRACT(last_health_json, '$.status')), 64) AS health,
LEFT(JSON_UNQUOTE(JSON_EXTRACT(last_health_json, '$.lastRunAt')), 40) AS last_run_at,
(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(last_health_json, '$.lastError')), '') NOT IN ('', 'null')) AS has_error,
SHA2(release_bundle_json, 256) AS release_hash
FROM reasoning_engine_deployments WHERE deployment_id = %s"""

SOURCES = """SELECT snapshot_id, generated_at, created_at, mode
FROM verified_reasoning_source_snapshots ORDER BY created_at DESC, snapshot_id DESC"""

# A represented-event/account cross product is not source authorization. Both
# declarations constrain identity; global scope still needs the exact boundary.
SOURCE_SCOPE_MATCH_SQL = """CASE
 WHEN e.event_id IS NULL OR e.payload_json IS NULL OR JSON_TYPE(e.payload_json) <> 'OBJECT'
   OR NULLIF(s.account_id, '') IS NULL
   OR NULLIF(b.account_id, '') IS NULL OR NULLIF(b.snapshot_id, '') IS NULL
   OR NULLIF(b.generated_at, '') IS NULL OR NULLIF(v.snapshot_id, '') IS NULL
   OR NULLIF(v.account_id, '') IS NULL OR NULLIF(v.generated_at, '') IS NULL THEN NULL
 ELSE (COALESCE(l.account_id, '') = '' OR CAST(s.account_id AS BINARY) = CAST(l.account_id AS BINARY))
 AND CAST(s.account_id AS BINARY) = CAST(b.account_id AS BINARY)
 AND CAST(v.account_id AS BINARY) = CAST(b.account_id AS BINARY)
 AND CAST(v.snapshot_id AS BINARY) = CAST(b.snapshot_id AS BINARY)
 AND CAST(v.generated_at AS BINARY) = CAST(b.generated_at AS BINARY)
 AND CASE
   WHEN JSON_TYPE(JSON_EXTRACT(e.payload_json, '$.accountIds')) NOT IN ('ARRAY', 'NULL') THEN NULL
   WHEN COALESCE(JSON_LENGTH(e.payload_json, '$.accountIds'), 0) > 0
     AND JSON_TYPE(JSON_EXTRACT(e.payload_json, '$.accountIds')) = 'ARRAY'
   THEN JSON_CONTAINS(e.payload_json, JSON_QUOTE(s.account_id), '$.accountIds')
   WHEN COALESCE(JSON_UNQUOTE(JSON_EXTRACT(e.payload_json, '$.accountId')), '') NOT IN ('', 'null')
   THEN CAST(s.account_id AS BINARY) = CAST(JSON_UNQUOTE(JSON_EXTRACT(e.payload_json, '$.accountId')) AS BINARY)
   ELSE 1 END
 END"""

# The survivor lineage table preserves coalesced predecessors. Only an exact
# snapshot match can count as executed-input evidence, not mere representation.
# Cast boundary operands at joins, leaving indexed lookup columns unwrapped.
# The proof predicate casts both sides to enforce byte-exact identity even when
# an explicit operand collation would otherwise override a one-sided cast.
LINEAGE = """SELECT j.job_id, b.snapshot_id AS executed_snapshot_id,
j.completed_at AS reasoning_at,
COALESCE(l.source_event_id, j.source_event_id) AS source_event_id,
v.snapshot_id AS source_snapshot_id,
e.event_id AS stored_event_id, e.occurred_at AS source_at,
v.snapshot_id AS stored_snapshot_id, v.generated_at AS snapshot_at, v.mode AS source_mode,
c.case_id, s.subject_case_id, s.ai_request_id, s.inference_generation_id,
s.source_abox_snapshot_id, s.candidate_fingerprint, s.stage AS subject_stage,
s.outcome_kind, r.request_id AS stored_ai_request_id, r.status AS ai_status,
a.result_id, a.created_at AS ai_at, a.ai_authored, a.publication_contract_passed,
a.publication_mode, a.validation_state,
i.episode_id AS insight_id, p.publication_id, p.outcome_kind AS publication_outcome,
d.episode_id AS decision_id,
n.job_id AS notification_id, n.status AS notification_status, n.is_mock, n.data_quality,
t.attempt_id, t.status AS delivery_status, t.channel, t.completed_at AS delivery_at,
(v.account_id = s.account_id AND s.account_id <> '' AND r.account_id = s.account_id
 AND r.symbol = s.symbol AND s.symbol <> '') AS account_symbol_match,
(i.account_id = s.account_id AND i.symbol = s.symbol
 AND i.subject_case_id = s.subject_case_id AND i.result_id = a.result_id
 AND i.source_abox_snapshot_id = s.source_abox_snapshot_id
 AND i.inference_generation_id = s.inference_generation_id
 AND i.candidate_fingerprint = s.candidate_fingerprint
 AND r.inference_generation_id = s.inference_generation_id) AS insight_match,
(k.subject_case_id = s.subject_case_id AND k.account_id = s.account_id
 AND k.symbol = s.symbol AND k.fingerprint = s.candidate_fingerprint
 AND k.inference_generation_id = s.inference_generation_id
 AND k.source_abox_snapshot_id = s.source_abox_snapshot_id) AS candidate_match,
(d.account_id = s.account_id AND d.symbol = s.symbol
 AND d.inference_generation_id = s.inference_generation_id) AS decision_match,
(n.account_id = s.account_id AND n.symbol = s.symbol
 AND n.decision_episode_id = p.decision_episode_id) AS notification_match,
JSON_CONTAINS(j.result_json, JSON_QUOTE(s.inference_generation_id), '$.inference_generation_ids') AS generation_in_result,
JSON_CONTAINS(j.result_json, JSON_QUOTE(s.source_abox_snapshot_id), '$.source_abox_snapshot_ids') AS abox_in_result,
JSON_UNQUOTE(JSON_EXTRACT(j.result_json, '$.trace_complete')) = 'true' AS trace_complete,
(c.request_id = JSON_UNQUOTE(JSON_EXTRACT(j.result_json, '$.request_id'))) AS batch_request_match,
""" + SOURCE_SCOPE_MATCH_SQL + """ AS source_scope_match,
CASE WHEN v.snapshot_id IS NOT NULL THEN v.account_id = CAST(b.account_id AS BINARY)
 AND v.generated_at = CAST(b.generated_at AS BINARY) ELSE NULL END AS source_boundary_match,
(l.source_event_id IS NULL OR l.source_event_id = j.source_event_id
 OR (l.source_snapshot_id = CAST(b.snapshot_id AS BINARY)
 AND l.source_snapshot_at = CAST(b.generated_at AS BINARY))) AS executed_source_match
FROM (SELECT job_id, source_event_id, source_snapshot_id, source_boundary_json, deployment_id, result_json, completed_at
      FROM reasoning_engine_jobs
      WHERE deployment_id = %s AND job_status = 'completed' AND completed_at >= %s
      ORDER BY completed_at DESC LIMIT %s) j
LEFT JOIN investment_reasoning_cases c
 ON c.case_id = JSON_UNQUOTE(JSON_EXTRACT(j.result_json, '$.reasoning_case_id'))
 AND c.deployment_id = j.deployment_id
LEFT JOIN investment_subject_decision_cases s ON s.batch_case_id = c.case_id
LEFT JOIN JSON_TABLE(j.source_boundary_json, '$[*]' COLUMNS (
 snapshot_id VARCHAR(191) PATH '$.snapshotId', account_id VARCHAR(191) PATH '$.accountId',
 generated_at VARCHAR(40) PATH '$.generatedAt')) b ON s.account_id = CAST(b.account_id AS BINARY)
LEFT JOIN reasoning_engine_job_sources l ON l.survivor_job_id = j.job_id
 AND l.deployment_id = j.deployment_id
 AND (l.account_id = s.account_id OR l.account_id = '')
 AND (l.symbol = s.symbol OR l.symbol = '')
LEFT JOIN domain_events e ON e.event_id = COALESCE(l.source_event_id, j.source_event_id)
LEFT JOIN verified_reasoning_source_snapshots v
 ON v.snapshot_id = CAST(b.snapshot_id AS BINARY)
LEFT JOIN decision_candidate_snapshots k ON k.candidate_set_id = s.candidate_set_id
LEFT JOIN ai_inference_requests r ON r.request_id = s.ai_request_id
LEFT JOIN ai_inference_results a ON a.request_id = r.request_id
LEFT JOIN investment_ai_insight_episodes i ON i.request_id = r.request_id
LEFT JOIN decision_publications p ON p.subject_case_id = s.subject_case_id
LEFT JOIN investment_decision_episodes d ON d.episode_id = p.decision_episode_id
LEFT JOIN notification_jobs n ON n.job_id = COALESCE(NULLIF(p.notification_job_id, ''), NULLIF(i.notification_job_id, ''), r.notification_job_id)
LEFT JOIN notification_delivery_attempts t ON t.job_id = n.job_id
ORDER BY j.completed_at DESC, j.job_id, s.subject_case_id, source_event_id, t.started_at DESC"""


QUEUES = {
    "reasoning": ("reasoning_engine_jobs", "job_id", "job_status", (
        "queued", "retry", "processing", "awaiting_source", "awaiting_world_projection", "failed")),
    "ai": ("ai_inference_requests", "request_id", "status", ("pending", "retry", "processing", "failed")),
    "notification": ("notification_jobs", "job_id", "status", ("pending", "awaiting_ai", "processing", "failed")),
}


def queue_read(db, kind, state, cutoff, deployment=None):
    table, identity, status, _states = QUEUES[kind]
    lease = "'' AS lease_expires_at, '' AS heartbeat_at, '' AS lease_owner"
    if kind != "notification":
        lease = "lease_expires_at, heartbeat_at, lease_owner"
    sql = ("SELECT " + identity + " AS id, created_at, updated_at, " + lease
           + ", (last_error <> '') AS has_error FROM " + table + " WHERE " + status + " = %s")
    params = [state]
    if kind == "reasoning":
        sql += " AND deployment_id = %s"
        params.append(deployment)
    if state == "failed":
        sql += " AND updated_at >= %s"
        params.append(cutoff)
    return db.read(sql + " ORDER BY created_at, " + identity, params)
