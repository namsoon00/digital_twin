"""One locked daily row; no scan of historical prompts or call JSON is needed."""
import json
from datetime import datetime

from ..domain import token_budget


class AITokenUsagePersistence:
    def wake_token_waiters(self, connection, now):
        """An increased allowance reopens only token-deferred work, in batches.

        New sources/retirement are checked by the normal leased workflow; this
        is not a forced judgment or a reset of failure/research attempt limits.
        """
        view = self.token_usage(connection, now)
        if view["exhausted"]:
            return
        key = "token-budget-wake"
        state = self._locked_execution_state(connection, key, now)
        allocation = view.get("allocation", {})
        signature = [view["day"], view["enabled"], view["dailyLimit"],
                     allocation.get("version"), allocation.get("releasedTokens")]
        if state.get("signature") == signature and state.get("complete"):
            return
        stamp = now.isoformat().replace("+00:00", "Z")
        rows = connection.execute("SELECT task_id FROM ai_control_tasks FORCE INDEX (ai_control_due) "
            "WHERE status='pending' AND available_at>%s AND last_error IN "
            "('ai-token-budget-exhausted','ai-token-allocation-budget-exhausted') "
            "ORDER BY available_at,task_id LIMIT 100 FOR UPDATE SKIP LOCKED", (stamp,)).fetchall()
        for row in rows:
            connection.execute("UPDATE ai_control_tasks SET available_at=%s,updated_at=%s WHERE task_id=%s "
                               "AND status='pending'", (stamp, stamp, row["task_id"]))
        self._save_execution_state(connection, key, {"signature": signature, "complete": len(rows) < 100,
                                                     "lastWakeCount": len(rows)}, now)

    def reserve_tokens(self, connection, workload, now, pool):
        clock = datetime.fromisoformat(now.replace("Z", "+00:00"))
        scope = "token-usage:" + now[:10]
        state = self._locked_execution_state(connection, scope, clock)
        state = token_budget.reserve(self.runtime_settings, state, workload, clock, pool)
        self._save_execution_state(connection, scope, state, clock)

    def settle_tokens(self, connection, call, usage, now, not_started=False, pool=None):
        clock = datetime.fromisoformat(now.replace("Z", "+00:00"))
        scope = "token-usage:" + call["started_at"][:10]
        state = self._locked_execution_state(connection, scope, clock)
        state = token_budget.settle(state, call["workload"], usage, not_started, pool)
        self._save_execution_state(connection, scope, state, clock)

    def token_usage(self, connection, now):
        row = connection.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key=%s",
                                 ("token-usage:" + now.date().isoformat(),)).fetchone()
        return token_budget.usage_view(self.runtime_settings, json.loads(row["state_json"]) if row else {}, now)
