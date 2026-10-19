"""Bounded indexed status reads; research activity is not market alert delivery."""
from collections import Counter
from datetime import timedelta
import json

from ..domain.execution_lanes import MARKET, VERSION
from ..domain.execution_resilience import stamp


def lane_status(connection, now, account_id=""):
    since = stamp(now - timedelta(hours=24))
    index = "ai_control_account_recent" if account_id else "ai_control_recent"
    scope, params = ("account_id=%s AND ", (account_id,)) if account_id else ("", ())
    rows = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ task_id,capability,status,updated_at "
        "FROM ai_control_tasks FORCE INDEX (" + index + ") WHERE " + scope + "updated_at>=%s AND updated_at<=%s "
        "ORDER BY updated_at DESC,task_id DESC LIMIT 2001", params + (since, stamp(now))).fetchall()
    sampled = rows[:2000]
    selected = [row for capability in (MARKET, "observe")
                for row in [r for r in sampled if r["capability"] == capability][:300]]
    results = {}
    # Only bounded PK batches read selected result fields, never full inputs.
    for start in range(0, len(selected), 100):
        ids = [row["task_id"] for row in selected[start:start+100]]
        fetched = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ task_id,"
            "JSON_EXTRACT(result_json,'$.summary') AS summary,"
            "JSON_EXTRACT(result_json,'$.status') AS result_status,"
            "JSON_EXTRACT(result_json,'$.processingOutcome') AS outcome,"
            "JSON_EXTRACT(result_json,'$.quality.status') AS quality,"
            "JSON_EXTRACT(result_json,'$.notification') AS notification,"
            "JSON_EXTRACT(result_json,'$.publication') AS publication,"
            "JSON_EXTRACT(result_json,'$.failure') AS failure "
            "FROM ai_control_tasks FORCE INDEX (PRIMARY) WHERE task_id IN (" + ",".join(["%s"]*len(ids)) + ")",
            tuple(ids)).fetchall()
        results.update({row["task_id"]: {key: json.loads(value) if value is not None else None
            for key, value in row.items() if key != "task_id"} for row in fetched})
    lanes = []
    for capability in (MARKET, "observe"):
        tasks = [{**row, "result": results.get(row["task_id"], {})} for row in selected if row["capability"] == capability]
        authored = [row for row in tasks if row["status"] == "completed" and row["result"].get("summary")]
        last = tasks[0] if tasks else None
        alert = next((row for row in tasks if (row["result"].get("publication") or {}).get("jobId")), None)
        def view(row):
            if not row:
                return None
            result = row["result"]
            publication, notification = result.get("publication") or {}, result.get("notification") or {}
            return {"taskId": row["task_id"], "updatedAt": row["updated_at"], "status": row["status"],
                "resultStatus": result.get("result_status"), "summary": result.get("summary"),
                "quality": result.get("quality"), "processingOutcome": result.get("outcome"),
                "failure": result.get("failure"), "publication": publication,
                "notificationProposed": notification.get("send", False),
                "reason": publication.get("reason") or notification.get("reason") or ""}
        lanes.append({"capability": capability, "taskStates": dict(Counter(row["status"] for row in tasks)),
            "authored": len(authored), "internalResearch": sum(row["result"].get("quality") == "not-applicable" for row in authored),
            "checksWithoutJudgment": sum(row["status"] == "completed" and row["result"].get("result_status") in
                {"unchanged", "awaiting-evidence", "unchanged-wait"} for row in tasks),
            "notificationProposals": sum(bool((row["result"].get("notification") or {}).get("send")) for row in authored),
            "latestTask": view(last), "latestJudgment": view(authored[0] if authored else None), "latestAlert": view(alert),
            "truncated": len([row for row in sampled if row["capability"] == capability]) > 300})
    return {"version": VERSION, "since": since, "until": stamp(now), "scope": "updated-tasks-in-last-24h",
            "truncated": len(rows) > 2000, "lanes": lanes}
