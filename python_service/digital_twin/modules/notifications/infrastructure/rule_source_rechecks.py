"""Small indexed recheck ledger, consumed by existing reasoning/delivery workers."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from digital_twin.infrastructure.settings import utc_now
from ..domain.rule_delivery_comparison import rule_delivery_comparison
from ..domain.rule_source_recheck import recheck_request, reevaluation, receipt_matches
from ..domain.rule_dispatch_validity import timestamp


class RuleSourceRecheckStore:
    def register_rule_recheck(self, connection, job):
        if job.message_type != "investmentInsight":
            return {}
        linkage = self.notification_linkage(job)
        if linkage["isMock"] or linkage["dataQuality"] != "actual":
            return {}
        request = recheck_request(job.context.get("relationChangeEvidence") or {},
                                  job.context.get("deliverySuppressionReason"), job.job_id,
                                  job.context.get("ruleDispatchValidity"))
        if not request or request["accountId"] != job.account_id or request["symbol"] != linkage["symbol"]:
            return {}
        serialized = json.dumps(request, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > 48 * 1024:
            job.context["freshDataRecheck"] = {"requested": False, "status": "untracked-proof-limit",
                                             "reason": "재검사 근거 기록의 크기 제한을 초과해 자동 추적하지 못했습니다."}
            return {}
        stamp = utc_now()
        deadline = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat().replace("+00:00", "Z")
        connection.execute(
            "INSERT IGNORE INTO rule_notification_rechecks "
            "(source_job_id,account_id,symbol,world_id,status,created_at,updated_at,deadline_at,request_json,result_json,successor_job_id) "
            "VALUES (%s,%s,%s,%s,'pending',%s,%s,%s,%s,'{}','')",
            (job.job_id, request["accountId"], request["symbol"], request["worldId"], stamp, stamp, deadline,
             serialized),
        )
        stored = connection.execute(
            "SELECT status,created_at,deadline_at FROM rule_notification_rechecks WHERE source_job_id=%s", (job.job_id,)
        ).fetchone()
        audit = {**(job.context.get("freshDataRecheck") or {}), "requested": True,
                 "status": stored["status"], "requestId": job.job_id, "scheduledAt": stored["created_at"],
                 "scheduleMode": "durable-next-source-evaluation", "deadlineAt": stored["deadline_at"],
                 "reason": "다음 정상 수집·추론에서 새 원천 근거와 전달 결과를 추적합니다."}
        # A monitor interval is not a promise of a new upstream observation.
        audit.pop("maxDelaySeconds", None)
        job.context["freshDataRecheck"] = audit
        return audit

    def observe_rule_source_evaluation(self, context, outcome):
        linkage = self.notification_linkage(SimpleNamespace(context=context, account_id=context.get("accountId")))
        if linkage["isMock"] or linkage["dataQuality"] != "actual":
            return 0
        packet = rule_delivery_comparison(context, stage="source-recheck")["relationChangeEvidence"]
        current = {**packet["current"], "customerEvents": packet["customerEvents"]}
        return self.record_rule_source_snapshot(current, outcome)

    def record_rule_source_snapshot(self, current, outcome):
        if not current.get("proofIdentityVerified"):
            return 0
        with self.transaction() as connection:
            # Scope/index first; never scan notification payload history.
            rows = connection.execute(
                "SELECT /*+ MAX_EXECUTION_TIME(1500) */ source_job_id,request_json FROM rule_notification_rechecks "
                "WHERE account_id=%s AND symbol=%s AND world_id=%s AND status='pending' AND deadline_at>%s "
                "ORDER BY updated_at,source_job_id LIMIT 16 FOR UPDATE",
                (current["accountId"], current["symbol"], current["worldId"], utc_now()),
            ).fetchall()
            updated = 0
            for row in rows:
                result = reevaluation(json.loads(row["request_json"]), current, outcome)
                if not result:
                    connection.execute("UPDATE rule_notification_rechecks SET updated_at=%s WHERE source_job_id=%s",
                                       (utc_now(), row["source_job_id"]))
                    continue
                connection.execute(
                    "UPDATE rule_notification_rechecks SET status=%s,updated_at=%s,result_json=%s,successor_job_id=%s "
                    "WHERE source_job_id=%s AND status='pending'",
                    (result["status"], utc_now(), json.dumps(result, ensure_ascii=False), result["successorJobId"], row["source_job_id"]),
                )
                updated += 1
            return updated

    def reconcile_rule_source_rechecks(self):
        """Resolve receipts by exact successor ID; no sending, AI or forced refresh."""
        result = {"delivered": 0, "deliveryStopped": 0, "unresolved": 0}
        stamp = utc_now()
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT /*+ MAX_EXECUTION_TIME(1500) */ source_job_id,successor_job_id,account_id,symbol,result_json "
                "FROM rule_notification_rechecks WHERE status='awaiting-delivery' "
                "ORDER BY updated_at,source_job_id LIMIT 16 FOR UPDATE"
            ).fetchall()
            for row in rows:
                evaluation = json.loads(row["result_json"])
                job = connection.execute(
                    "SELECT status,data_quality,is_mock,account_id,symbol FROM notification_jobs WHERE job_id=%s", (row["successor_job_id"],)
                ).fetchone()
                attempts = connection.execute(
                    "SELECT status,audience,metadata_json,completed_at FROM notification_delivery_attempts "
                    "WHERE job_id=%s AND status='delivered' ORDER BY started_at DESC,attempt_id DESC LIMIT 8",
                    (row["successor_job_id"],),
                ).fetchall()
                receipt = None
                for attempt in attempts:
                    metadata = json.loads(attempt["metadata_json"] or '{}')
                    if (attempt["audience"] == "accountNotification" and timestamp(attempt["completed_at"])
                            and metadata.get("receiptVerified") is True
                            and receipt_matches(evaluation, metadata.get("relationChangeSnapshot") or {})):
                        receipt = attempt
                        break
                valid = bool(receipt and job and not job["is_mock"] and job["data_quality"] == "actual"
                             and job["account_id"] == row["account_id"] and job["symbol"] == row["symbol"])
                # Failed jobs can be retried by the existing recovery worker.
                # Keep following them until a receipt or the bounded deadline.
                stopped = bool(job and job["status"] in {"suppressed", "superseded"})
                if valid or stopped:
                    state = "delivered" if valid else "delivery-stopped"
                    audit = {**evaluation, "status": state, "receiptVerified": valid,
                             "deliveredAt": receipt["completed_at"] if valid else ""}
                    connection.execute(
                        "UPDATE rule_notification_rechecks SET status=%s,updated_at=%s,result_json=%s WHERE source_job_id=%s",
                        (state, stamp, json.dumps(audit, ensure_ascii=False), row["source_job_id"]),
                    )
                    result["delivered" if valid else "deliveryStopped"] += 1
                else:
                    # Fair rotation among pending receipts, including missing jobs.
                    connection.execute("UPDATE rule_notification_rechecks SET updated_at=%s WHERE source_job_id=%s",
                                       (stamp, row["source_job_id"]))
            for status in ("pending", "awaiting-delivery"):
                expired = connection.execute(
                    "SELECT source_job_id,result_json FROM rule_notification_rechecks WHERE status=%s AND deadline_at<=%s "
                    "ORDER BY deadline_at,source_job_id LIMIT 16 FOR UPDATE", (status, stamp),
                ).fetchall()
                for row in expired:
                    audit = {**json.loads(row["result_json"]), "status": "unresolved", "receiptVerified": False,
                             "reasonCode": "new-source-proof-not-confirmed" if status == "pending" else "delivery-receipt-not-confirmed"}
                    connection.execute(
                        "UPDATE rule_notification_rechecks SET status='unresolved',updated_at=%s,result_json=%s WHERE source_job_id=%s",
                        (stamp, json.dumps(audit), row["source_job_id"]),
                    )
                    result["unresolved"] += 1
        return result

    def rule_source_recheck(self, source_job_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status,created_at,updated_at,deadline_at,result_json FROM rule_notification_rechecks WHERE source_job_id=%s",
                (source_job_id,),
            ).fetchone()
        if row:
            row["result"] = json.loads(row.pop("result_json"))
        return row or {}
