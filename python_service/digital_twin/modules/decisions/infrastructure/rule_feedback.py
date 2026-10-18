"""Read scoped publication decisions, including cases with no notification job."""

import json


class RuleBoxCaseFeedbackReader:
    def __init__(self, store):
        self.store = store

    def read(self, account_id, symbol, world_id, cutoff_at):
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT /*+ MAX_EXECUTION_TIME(5000) */ subject_case_id,updated_at,deployment_id,"
                "JSON_EXTRACT(payload_json,'$.inferenceDispatchDecision') AS dispatch,"
                "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.publication.outcomeKind')) AS publication_kind,"
                "JSON_LENGTH(payload_json,'$.synthesis.management_decision.responses') AS response_count "
                "FROM investment_subject_decision_cases WHERE account_id=%s AND symbol=%s "
                "AND created_at<=%s AND updated_at<=%s "
                "AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.synthesis.rulebox_judgment.worldId')),"
                "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.synthesis.management_decision.worldId')))=%s "
                "ORDER BY updated_at DESC,subject_case_id DESC LIMIT 6",
                (account_id, symbol, cutoff_at, cutoff_at, world_id),
            ).fetchall()
        return [{"subjectCaseId": row["subject_case_id"], "observedAt": row["updated_at"],
                 "accountId": account_id, "symbol": symbol, "worldId": world_id,
                 "deploymentId": row["deployment_id"],
                 "dispatch": json.loads(row["dispatch"]) if isinstance(row["dispatch"], (str, bytes)) else row["dispatch"] or {},
                 "publicationKind": row["publication_kind"] or "", "managementResponseCount": int(row["response_count"] or 0)}
                for row in rows]
