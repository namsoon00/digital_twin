"""Short transactional dispatch and a payload-free view of every active queue."""
import json
from datetime import timedelta

from ..domain.budget import admission_wait
from ..domain.execution_resilience import stamp
from ..domain.task_dispatch import AGING_MINUTES, CAPABILITIES, CYCLE, SCOPE, VERSION, dispatch_order


class AITaskDispatchPersistence:
    @staticmethod
    def select_dispatch(connection, cursor, budget, now):
        wait = None
        # An observation needs two calls; source work can use the final call.
        eligible = [capability for capability in CAPABILITIES if not admission_wait(budget, capability)]
        excluded = " AND capability<>'observe'" if "observe" not in eligible else ""
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
                    where += " AND capability NOT IN (%s,%s,%s)"
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
            row = connection.execute("SELECT * FROM ai_control_tasks WHERE " + where
                + (excluded if eligible else "") + " ORDER BY " + order
                + " LIMIT 1 FOR UPDATE SKIP LOCKED", params).fetchone()
            if row:
                blocked = admission_wait(budget, row["capability"])
                if not blocked:
                    return row, slot, lane
                wait = wait or blocked
        if wait:
            raise wait
        # With one call left, excluded observations still explain the wait.
        if excluded and eligible:
            pending = connection.execute("SELECT task_id FROM ai_control_tasks WHERE capability='observe' AND "
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
