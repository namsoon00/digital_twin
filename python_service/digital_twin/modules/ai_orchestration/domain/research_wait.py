"""Source cooldowns defer the same question; they are not missing answers."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import math


class AIResearchWait(RuntimeError):
    code = "research-cooldown"

    def __init__(self, run, answer=None):
        now = datetime.now(timezone.utc)
        try:
            completed = datetime.fromisoformat(str(run.get("completedAt") or "").replace("Z", "+00:00"))
            completed = completed if completed.tzinfo else now
        except ValueError:
            completed = now
        delays = [float(row.get("remainingMinutes") or 0) for row in run.get("providerStatuses", [])
                  if row.get("status") == "cooldown"]
        minutes = max(1, min(1440, math.ceil(max(delays or [30]))))
        due = max(now + timedelta(seconds=30), completed + timedelta(minutes=minutes))
        self.retry_at = due.isoformat().replace("+00:00", "Z")
        self.run_id = str(run.get("runId") or "")
        self.answer = deepcopy(answer) if isinstance(answer, dict) and answer.get("status") in {"partial", "answered"} else None
        super().__init__(self.code)

    def result(self):
        return {"status": "research-wait", "reason": self.code,
                "runId": self.run_id, "nextCheckAt": self.retry_at,
                "explanation": "자료 수집 간격을 기다린 뒤 같은 질문의 조사를 이어갑니다.",
                **({"documentaryAnswer": deepcopy(self.answer)} if self.answer else {})}
