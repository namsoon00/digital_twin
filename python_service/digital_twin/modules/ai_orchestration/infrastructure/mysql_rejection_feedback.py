"""Bounded, point-in-time reads of same-subject rejected execution audits."""
from datetime import timedelta
import gzip
import json
from ..domain import rejection_feedback as feedback
from ..domain.execution_input import validate_execution_input
from ..domain.insight_contract import instant


class RejectionFeedbackPersistence:
    def review_feedback(self, account, symbol, world, capability, as_of):
        if capability != "market-observe":
            return []
        cutoff = instant(as_of)
        since = (cutoff - timedelta(days=7)).isoformat().replace("+00:00", "Z")
        with self.connect() as c:
            rows = c.execute("SELECT task_id,updated_at FROM ai_control_tasks FORCE INDEX(ai_control_subject) "
                "WHERE account_id=%s AND symbol=%s AND updated_at>=%s AND updated_at<=%s "
                "AND capability=%s AND status='completed' ORDER BY updated_at DESC,task_id DESC LIMIT 16",
                (account, symbol, since, as_of, capability)).fetchall()
            result = []
            for row in rows:
                saved = c.execute("SELECT account_id,symbol,capability,status,payload_json,result_json,updated_at "
                    "FROM ai_control_tasks WHERE task_id=%s", (row["task_id"],)).fetchone()
                if (not saved or (saved["account_id"], saved["symbol"], saved["capability"], saved["status"]) !=
                        (account, symbol, capability, "completed") or instant(saved["updated_at"]) > cutoff
                        or json.loads(saved["payload_json"]).get("worldId") != world):
                    continue
                value = json.loads(saved["result_json"])
                if not value.get("summary"):
                    continue
                scope = (account, symbol, world)
                if tuple(value.get("input", {}).get(k) for k in ("accountId", "symbol", "worldId")) != scope:
                    continue
                if value.get("quality", {}).get("status") == "accepted" and not value["quality"].get("errors"):
                    break  # A newer approved insight supersedes older draft criticism.
                if value.get("quality", {}).get("status") != "rejected":
                    continue
                author = self._feedback_input(c, value.get("executionInputId"), row["task_id"], cutoff, scope)
                if not author or author["current"] != value["input"]:
                    continue
                review_id = value.get("quality", {}).get("reviewInputId")
                review = self._feedback_input(c, review_id, row["task_id"], cutoff, scope) if review_id else None
                if review_id and (not review or "draft" not in review):
                    continue
                item = feedback.extract(value, row["task_id"], saved["updated_at"], review)
                if item:
                    result.append(item)
                if len(result) == feedback.MAX_ROWS:
                    break
        return result

    @staticmethod
    def _feedback_input(c, input_id, task, cutoff, scope):
        if not input_id:
            return None
        row = c.execute("SELECT artifact_gzip,input_hash,prompt_hash,created_at FROM ai_control_inputs "
            "WHERE input_id=%s AND task_id=%s", (input_id, task)).fetchone()
        if not row or instant(row["created_at"]) > cutoff:
            return None
        envelope = json.loads(gzip.decompress(row["artifact_gzip"]))
        try:
            if validate_execution_input(envelope) != row["input_hash"]:
                return None
        except ValueError:
            return None
        packet = envelope["current"]
        if (packet.get("taskId") != task or packet.get("executionLane") != "market-observe"
                or tuple(packet.get(k) for k in ("accountId", "symbol", "worldId")) != scope
                or envelope["promptHash"] != row["prompt_hash"]):
            return None
        called = c.execute("SELECT c.call_id FROM ai_control_input_calls l JOIN ai_control_calls c ON c.call_id=l.call_id "
            "WHERE l.input_id=%s AND c.task_id=%s AND c.prompt_hash=%s AND c.status='completed' "
            "AND c.completed_at<>'' AND c.completed_at<=%s LIMIT 1",
            (input_id, task, row["prompt_hash"], cutoff.isoformat().replace("+00:00", "Z"))).fetchone()
        return envelope if called else None
