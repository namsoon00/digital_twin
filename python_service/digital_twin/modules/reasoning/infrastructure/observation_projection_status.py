"""Read source-projection health for a live observation without writing graphs."""
import json

from .observation_binding import active_observation_binding


def observation_projection_status(connection, subject):
    binding = active_observation_binding(connection)
    row = connection.execute(
        "SELECT /*+ MAX_EXECUTION_TIME(5000) */ job_id,job_status,terminal_reason_code,"
        "source_snapshot_id,source_snapshot_at FROM reasoning_engine_jobs "
        "WHERE deployment_id=%s AND source_snapshot_id<>'' "
        "AND JSON_CONTAINS(request_json,%s,'$.request.account_ids') "
        "AND JSON_CONTAINS(request_json,%s,'$.request.symbols') "
        "AND JSON_EXTRACT(request_json,'$.request.context.validationOnly')=false "
        "AND JSON_CONTAINS(request_json,%s,%s) "
        "AND job_status<>'superseded' ORDER BY source_snapshot_at DESC,created_at DESC,job_id DESC LIMIT 1",
        (binding["deploymentId"], json.dumps(subject["accountId"]), json.dumps(subject["symbol"]),
         json.dumps("external.cryptoMarkets"), '$.request.context.changedFieldsBySymbol.' + json.dumps(subject["symbol"])),
    ).fetchone()
    if not row:
        return {"status": "unknown", "deploymentId": binding["deploymentId"]}
    status = ("blocked" if row["job_status"] == "failed" and row["terminal_reason_code"]
              else "ready" if row["job_status"] == "completed" else "pending")
    return {"status": status,
            "deploymentId": binding["deploymentId"], "jobId": row["job_id"],
            "jobStatus": row["job_status"], "reasonCode": row["terminal_reason_code"],
            "sourceSnapshotId": row["source_snapshot_id"], "sourceObservedAt": row["source_snapshot_at"]}
