"""Bounded, read-only audit. No store initialization, migrations or model calls."""
from collections import defaultdict
from datetime import datetime, timezone
import json

from digital_twin.modules.ai_orchestration.contracts import ai_call_metrics, ai_judgment_metrics, ai_task_metrics, ai_research_metrics


def clock(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("an explicit timezone is required")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def batches(values, size=20):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def collect(connection, since, until, limit=5000):
    since, until = clock(since), clock(until)
    if since >= until or not 1 <= limit <= 10000:
        raise ValueError("invalid audit window or limit")
    warnings = []
    with connection.cursor() as cursor:
        cursor.execute("START TRANSACTION READ ONLY")

        def select(sql, args=(), cap=None):
            cursor.execute(sql, args)
            rows = cursor.fetchall()
            if cap is not None and len(rows) > cap:
                raise ValueError("audit row limit exceeded; narrow the time window")
            return rows

        def by_ids(table, key, ids, columns="*", index="PRIMARY", cap_per_batch=None):
            rows = []
            for batch in batches(list(dict.fromkeys(ids))):
                cap = cap_per_batch or len(batch)
                rows.extend(select("SELECT " + columns + " FROM " + table + " FORCE INDEX (" + index + ") WHERE " +
                    key + " IN (" + ",".join(["%s"] * len(batch)) + ") LIMIT %s", (*batch, cap + 1), cap))
            return rows

        selected = select("SELECT call_id FROM ai_control_calls FORCE INDEX (ai_calls_started) "
            "WHERE started_at>=%s AND started_at<%s ORDER BY started_at,call_id LIMIT %s", (since, until, limit + 1), limit)
        calls = by_ids("ai_control_calls", "call_id", [row["call_id"] for row in selected],
                       "call_id,task_id,workload,status,started_at,completed_at")
        selected = select("SELECT task_id FROM ai_control_tasks FORCE INDEX (ai_control_recent) "
            "WHERE updated_at>=%s AND updated_at<%s ORDER BY updated_at,task_id LIMIT %s", (since, until, limit + 1), limit)
        # Read selected result paths, not full private prompts or account payloads.
        fields = ("summary", "status", "executionPromptVersion", "quality", "researchQuality", "publication", "memoryCoverage",
                  "companyValue", "repair", "evidenceIds", "questionResolutions", "followUpEvaluations", "resumption", "failure")
        expression = ",".join("'%s',JSON_EXTRACT(result_json,'$.%s')" % (key, key) for key in fields)
        expression += ", 'input',JSON_OBJECT('facts',JSON_EXTRACT(result_json,'$.input.facts')," \
                      "'retrieval',JSON_OBJECT('version',JSON_EXTRACT(result_json,'$.input.retrieval.version')))"
        expression += ", 'documentaryAnswer',JSON_OBJECT('status',JSON_EXTRACT(result_json,'$.documentaryAnswer.status'))"
        expression += ", 'changedEvidenceCount',JSON_EXTRACT(result_json,'$.changedEvidenceCount')"
        expression += ", 'stopReason',JSON_EXTRACT(result_json,'$.stopReason')"
        expression += ", 'planningReuse',JSON_EXTRACT(result_json,'$.planningReuse')"
        expression += ", 'sourceRecovery',JSON_OBJECT('progress',JSON_EXTRACT(result_json,'$.sourceRecovery.progress'))"
        expression += ", 'judgmentLinks',JSON_CONTAINS_PATH(result_json,'one','$.judgmentLinks')"
        expression += ", 'documentaryReview',JSON_CONTAINS_PATH(result_json,'one','$.documentaryReview')"
        tasks = by_ids("ai_control_tasks", "task_id", [row["task_id"] for row in selected],
                      "task_id,status,JSON_OBJECT(" + expression + ") AS result_json")
        for row in tasks:
            row["result"] = {k: v for k, v in json.loads(row.pop("result_json")).items() if v is not None}
            if row["result"].get("input", {}).get("facts") is None:
                row["result"]["input"]["facts"] = []
            if row["result"].get("documentaryAnswer", {}).get("status") is None:
                row["result"].pop("documentaryAnswer", None)
            if row["result"].get("sourceRecovery", {}).get("progress") is None:
                row["result"].pop("sourceRecovery", None)
        authored_ids = [row["task_id"] for row in tasks if row["result"].get("summary")]
        inputs = by_ids("ai_control_inputs", "task_id", authored_ids, "input_id,task_id", "ai_input_task", 2000)
        owners = {row["input_id"]: row["task_id"] for row in inputs}
        links = by_ids("ai_control_input_calls", "input_id", list(owners), "call_id,input_id", "ai_input_call", 1000)
        linked = by_ids("ai_control_calls", "call_id", [row["call_id"] for row in links],
                        "call_id,task_id,workload,status,started_at,completed_at")
        all_calls = {row["call_id"]: row for row in [*calls, *linked]}
        metrics = by_ids("ai_control_call_metrics", "call_id", list(all_calls))
        for row in metrics:
            all_calls[row["call_id"]]["metrics"] = json.loads(row["metrics_json"])
        calls_by_task = defaultdict(list)
        for row in linked:
            calls_by_task[row["task_id"]].append(all_calls[row["call_id"]])
        job_ids = [row["result"].get("publication", {}).get("jobId") for row in tasks]
        attempts = by_ids("notification_delivery_attempts", "job_id", [key for key in job_ids if key],
            "job_id,status,JSON_EXTRACT(metadata_json,'$.receiptVerified') AS verified,"
            "JSON_EXTRACT(metadata_json,'$.destinationVerified') AS destination",
            "idx_notification_delivery_job_time", 1000)
        receipts = {row["job_id"]: True for row in attempts if row["status"] == "delivered"
                    and str(row["verified"]).lower() in {"true", "1"}
                    and str(row["destination"]).lower() in {"true", "1"}}
        scopes = ("token-usage:" + until[:10], "central-observation-runtime", "shared-local-model")
        states = by_ids("ai_control_execution_state", "scope_key", scopes)
        state_summary = {}
        for row in states:
            state = json.loads(row["state_json"])
            keys = ("trackedSince", "workloads", "pools") if row["scope_key"].startswith("token-usage:") else (
                "startedAt", "lastSeenAt", "heartbeatAt", "blockedUntil", "lastOutcome", "consecutiveFailures", "quotaFailures")
            state_summary[row["scope_key"]] = {k: state[k] for k in keys if k in state}
        window_calls = [all_calls[row["call_id"]] for row in calls]
        output = {"version": "ai-judgment-audit-v2", "since": since, "until": until,
            "windowBasis": "calls started and tasks last updated in [since,until)",
            "calls": ai_call_metrics(window_calls),
            "authored": ai_judgment_metrics(tasks, calls_by_task, receipts), "tasks": ai_task_metrics(tasks),
            "research": ai_research_metrics(tasks, window_calls),
            "stateAtCollection": state_summary, "warnings": warnings,
            "billing": "unmeasured", "investmentAccuracy": "unmeasured",
            "limitations": ["Retained central call cohorts exclude independent source-provider jobs and deleted history.",
                "Old prompt versions absent from call metrics remain unrecorded; authored versions use result root.",
                "Receipt totals cover these authored task publications only, not all deliveries in the window.",
                "Research costs cover window calls linked by task ID; missing usage and older calls are not zero cost.",
                "Runtime pulse gaps are unknown, not measured downtime. Memory metadata is not a fresh replay."]}
    connection.rollback()
    return output
