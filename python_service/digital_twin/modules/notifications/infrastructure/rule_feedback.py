"""Bounded, read-only delivery feedback; no message bodies or credentials."""

import json


class RuleBoxNotificationFeedbackReader:
    def __init__(self, store):
        self.store = store

    def read(self, account_id, symbol, world_id, cutoff_at):
        paths = {"reasonCode": "deliverySuppressionReason", "comparison": "ruleNotificationComparison",
                 "validity": "ruleDispatchValidity", "recheck": "freshDataRecheck"}
        projection = ", ".join("JSON_EXTRACT(j.payload_json,'$.context." + path + "') AS " + key
                               for key, path in paths.items())
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT /*+ MAX_EXECUTION_TIME(5000) */ j.job_id,j.status,j.created_at,j.updated_at," + projection + ","
                "(SELECT MAX(a.completed_at) FROM notification_delivery_attempts a WHERE a.job_id=j.job_id "
                "AND a.status='delivered' AND a.completed_at<=%s) AS delivered_at "
                "FROM notification_jobs j WHERE j.account_id=%s AND j.symbol=%s AND j.message_type='investmentInsight' "
                "AND j.created_at<=%s AND j.updated_at<=%s AND j.status IN ('done','suppressed','failed') "
                "AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(j.payload_json,'$.context.investmentSubjectDecisionCase.worldId')),"
                "JSON_UNQUOTE(JSON_EXTRACT(j.payload_json,'$.context.ontologyRelationContext.worldId')))=%s "
                "ORDER BY j.updated_at DESC,j.job_id DESC LIMIT 12",
                (cutoff_at, account_id, symbol, cutoff_at, cutoff_at, world_id),
            ).fetchall()
        result = []
        for row in rows:
            value = {"jobId": row["job_id"], "status": row["status"], "observedAt": row["updated_at"],
                     "deliveredAt": row["delivered_at"] or "", "accountId": account_id, "symbol": symbol, "worldId": world_id}
            for key in paths:
                raw = row.get(key)
                value[key] = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            result.append(value)
        return result
