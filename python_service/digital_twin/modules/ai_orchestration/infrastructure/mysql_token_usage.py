"""One locked daily row; no scan of historical prompts or call JSON is needed."""
import json
from datetime import datetime

from ..domain import token_budget


class AITokenUsagePersistence:
    def reserve_tokens(self, connection, workload, now):
        clock = datetime.fromisoformat(now.replace("Z", "+00:00"))
        scope = "token-usage:" + now[:10]
        state = self._locked_execution_state(connection, scope, clock)
        state = token_budget.reserve(self.runtime_settings, state, workload, clock)
        self._save_execution_state(connection, scope, state, clock)

    def settle_tokens(self, connection, call, usage, now, not_started=False):
        clock = datetime.fromisoformat(now.replace("Z", "+00:00"))
        scope = "token-usage:" + call["started_at"][:10]
        state = self._locked_execution_state(connection, scope, clock)
        state = token_budget.settle(state, call["workload"], usage, not_started)
        self._save_execution_state(connection, scope, state, clock)

    def token_usage(self, connection, now):
        row = connection.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key=%s",
                                 ("token-usage:" + now.date().isoformat(),)).fetchone()
        return token_budget.usage_view(self.runtime_settings, json.loads(row["state_json"]) if row else {}, now)
