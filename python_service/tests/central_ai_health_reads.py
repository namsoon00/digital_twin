"""Bounded health samples, explicitly NOT cumulative end-to-end success totals."""
from collections import Counter


def collect(db, state):
    since = state.get("postChangeSince") or state["deploymentSince"]
    cursor = str(state.get("centralAiTaskCursor") or "")
    page = db.read("SELECT task_id FROM ai_control_tasks WHERE task_id > %s ORDER BY task_id", (cursor,), 64)
    ids = [row["task_id"] for row in page["rows"]]
    tasks = []
    if ids:
        placeholders = ",".join(["%s"] * len(ids))
        tasks = db.read("SELECT status, LEFT(JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.quality.status')),64) AS quality, "
                        "CASE WHEN created_at < %s THEN 'carried' ELSE 'new' END AS cohort, updated_at "
                        "FROM ai_control_tasks WHERE task_id IN (" + placeholders + ") AND updated_at >= %s",
                        (since, *ids, since), 64)["rows"]
    calls = db.read("SELECT workload,status,error_kind,completed_at FROM ai_control_calls "
                    "WHERE started_at >= %s ORDER BY started_at DESC,call_id DESC", (since,), 128)
    # Select a bounded index-ordered page before looking at JSON/receipts.
    notifications = db.read("SELECT job_id FROM notification_jobs WHERE created_at >= %s "
                            "ORDER BY created_at DESC,job_id DESC", (since,), 64)
    job_ids = [row["job_id"] for row in notifications["rows"]]
    jobs = []
    if job_ids:
        placeholders = ",".join(["%s"] * len(job_ids))
        jobs = db.read("SELECT n.status, EXISTS(SELECT 1 FROM notification_delivery_attempts t "
            "WHERE t.job_id=n.job_id AND t.status='delivered' AND t.provider='Telegram' "
            "AND t.audience='account' AND t.channel='accountNotification' AND n.is_mock=0 "
            "AND n.data_quality='actual' AND JSON_UNQUOTE(JSON_EXTRACT(t.metadata_json,'$.receiptVerified'))='true') "
            "AS verified FROM notification_jobs n WHERE n.job_id IN (" + placeholders + ") "
            "AND n.message_type='aiObservation'", tuple(job_ids), 64)["rows"]
    retention = db.read("SELECT profile,next_policy,updated_at FROM mysql_retention_progress")
    # Advance only after the complete read succeeds. No success on partial errors.
    state["centralAiTaskCursor"] = ids[-1] if ids and page["truncated"] else ""
    task_counts = Counter((r["status"], r.get("quality"), r["cohort"]) for r in tasks)
    call_counts = Counter((r["workload"], r["status"], r["error_kind"]) for r in calls["rows"])
    return {"readOnly": True, "since": since, "scope": "bounded-health-sample-v1",
            "notCumulativeTotals": True,
            "coverage": {"taskPageSize": len(ids), "taskSweepContinues": page["truncated"],
                         "callsTruncated": calls["truncated"], "notificationPageSize": len(job_ids),
                         "notificationsTruncated": notifications["truncated"],
                         "retentionTruncated": retention["truncated"]},
            "tasks": [dict(status=k[0], quality=k[1], cohort=k[2], n=n) for k,n in task_counts.items()],
            "calls": [dict(workload=k[0], status=k[1], error_kind=k[2], n=n) for k,n in call_counts.items()],
            "notifications": [dict(status=status, jobs=sum(r["status"] == status for r in jobs),
                verifiedDelivered=sum(bool(r["verified"]) for r in jobs if r["status"] == status))
                for status in sorted({r["status"] for r in jobs})],
            "retentionProgress": retention["rows"]}
