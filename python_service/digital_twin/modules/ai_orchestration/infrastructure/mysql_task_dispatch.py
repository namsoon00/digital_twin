"""Short transactional dispatch and a payload-free view of every active queue."""
import json
from datetime import timedelta

from ..domain.budget import admission_wait
from ..domain.execution_resilience import stamp
from ..domain.task_dispatch import AGING_MINUTES, CAPABILITIES, CYCLE, SCOPE, VERSION, dispatch_order


class AITaskDispatchPersistence:
    @staticmethod
    def select_dispatch(connection, cursor, budget, now, subjects=()):
        wait = None
        # An observation needs two calls; source work can use the final call.
        eligible = [capability for capability in CAPABILITIES if not admission_wait(budget, capability)]
        excluded = " AND capability NOT IN ('observe','market-observe')" if "observe" not in eligible else ""
        cutoff = stamp(now - timedelta(minutes=AGING_MINUTES))
        clock = stamp(now)
        for slot, lane in [*dispatch_order(cursor), (len(CYCLE) - 1, "other")]:
            if lane == "recovery":
                where = "status='processing' AND lease_until<%s"
                params = (clock,)
                order = "lease_until,available_at,task_id"
            else:
                where = "status='pending' AND (available_at<=%s OR last_error='ai-call-budget-exhausted')"
                params = (clock,)
                if lane == "other":
                    where += " AND capability NOT IN (" + ",".join(["%s"] * len(CAPABILITIES)) + ")"
                    params += CAPABILITIES
                else:
                    where += " AND capability=%s"
                    params += (lane,)
                # Priority remains meaningful for new work. Old work cannot be
                # overtaken indefinitely by fresh higher-priority arrivals.
                order = "(available_at<=%s) DESC,CASE WHEN available_at<=%s THEN available_at END,priority DESC,available_at,task_id"
                params += (cutoff, cutoff)
            # Only exclude observations when another capability is admissible.
            # Otherwise inspect the candidate and raise the original budget wait.
            from ..domain.work_attention import WINDOW, select
            limit = WINDOW if subjects and lane not in {"recovery", "other"} else 1
            rows = connection.execute("SELECT task_id,account_id,symbol,capability,status,priority,available_at,"
                "lease_until,attempts,last_error,payload_json FROM ai_control_tasks WHERE " + where
                + (excluded if eligible else "") + " ORDER BY " + order
                + f" LIMIT {limit} FOR UPDATE SKIP LOCKED", params).fetchall()
            row = rows[0] if rows else None
            if rows and limit > 1:
                jobs = [{**json.loads(r["payload_json"]), "taskId": r["task_id"], "accountId": r["account_id"],
                         "symbol": r["symbol"], "capability": r["capability"]} for r in rows]
                ids = sorted({j.get("brainCaseId") for j in jobs} - {None, ""})
                cases = {}
                if ids:
                    found = connection.execute("SELECT case_id,account_id,symbol,status,"
                        "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId')) AS world_id,"
                        "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.latestSourceAnswer.status')) AS answer_status,"
                        "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.questionAssessment.status')) AS review_status,"
                        "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.questionAssessment.researchReviewInputId')) AS review_input,"
                        "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.questionAssessment.judgmentImpact.effect')) AS effect "
                        "FROM ai_brain_cases FORCE INDEX(PRIMARY) WHERE kind='question' AND case_id IN ("
                        + ",".join(["%s"] * len(ids)) + ") LIMIT 20", tuple(ids)).fetchall()
                    cases = {r["case_id"]: {"caseId": r["case_id"], "accountId": r["account_id"], "symbol": r["symbol"],
                        "worldId": r["world_id"], "status": r["status"], "latestSourceAnswer": {"status": r["answer_status"]},
                        "questionAssessment": {"status": r["review_status"], "researchReviewInputId": r["review_input"],
                                               "judgmentImpact": {"effect": r["effect"]}}} for r in found}
                index, attention = select(jobs, subjects, cases, cursor, lane, now)
                row = {**rows[index], "work_attention": attention}
            if row:
                blocked = admission_wait(budget, row["capability"])
                if not blocked:
                    return row, slot, lane
                wait = wait or blocked
        if wait:
            raise wait
        # With one call left, excluded observations still explain the wait.
        if excluded and eligible:
            pending = connection.execute("SELECT task_id FROM ai_control_tasks WHERE capability IN ('observe','market-observe') AND "
                "((status='pending' AND (available_at<=%s OR last_error='ai-call-budget-exhausted')) "
                "OR (status='processing' AND lease_until<%s)) LIMIT 1", (clock, clock)).fetchone()
            if pending:
                raise admission_wait(budget, "observe")
        return None, None, None

    @staticmethod
    def task_scheduling(connection, now, account_id=""):
        clock = stamp(now)
        rows = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(5000) */ capability,"
            "SUM(status='pending') AS pending,"
            "SUM(status='pending' AND (available_at<=%s OR last_error='ai-call-budget-exhausted')) AS due,"
            "MIN(CASE WHEN status='pending' AND available_at<=%s THEN available_at END) AS oldest_due,"
            "SUM(status='processing' AND lease_until>=%s) AS processing,"
            "SUM(status='processing' AND lease_until<%s) AS expired "
            "FROM ai_control_tasks WHERE status IN ('pending','processing') "
            "AND (%s='' OR account_id=%s) GROUP BY capability",
            (clock, clock, clock, clock, account_id, account_id)).fetchall()
        row = connection.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key=%s", (SCOPE,)).fetchone()
        state = json.loads(row["state_json"]) if row else {}
        # Cursor is global; do not leak another account's last task identifier.
        return {"version": VERSION, "cycle": list(CYCLE), "nextLane": dispatch_order(state)[0][1],
                "dispatchCount": int(state.get("dispatchCount", 0)),
                "recoveredLeaseCount": int(state.get("recoveredLeaseCount", 0)),
                "queues": [{"capability": r["capability"], "pending": int(r["pending"]), "due": int(r["due"]),
                            "processing": int(r["processing"]), "expiredLeases": int(r["expired"]),
                            "oldestDueAt": r["oldest_due"] or ""} for r in rows]}
