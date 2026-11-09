"""Bounded PK reads of completed source-read audits from the same failed work."""
import gzip
import json
from ..domain.planning import identity, stamp
from ..domain.execution_input import validate_execution_input
from ..domain import read_continuation as policy
from digital_twin.modules.reasoning.contracts import content_hash


class ReadContinuationPersistence:
    def retrieval_read_plans(self, job):
        if int(job["attempts"]) <= 1 and not job.get("recoveryOf"):
            return []
        with self.connect() as c:
            owned = c.execute("SELECT payload_json,capability FROM ai_control_tasks WHERE task_id=%s AND status='processing' "
                "AND attempts=%s AND lease_token=%s AND lease_until>=%s AND account_id=%s AND symbol=%s",
                (job["taskId"], job["attempts"], job["leaseToken"], stamp(), job["accountId"], job["symbol"])).fetchone()
            if (not owned or owned["capability"] != job["capability"]
                    or json.loads(owned["payload_json"]).get("worldId") != job["worldId"]):
                return []
            source, before = job["taskId"], job["attempts"]
            rows = c.execute("SELECT input_id FROM ai_control_inputs FORCE INDEX(ai_input_task) "
                "WHERE task_id=%s AND attempt<%s ORDER BY attempt DESC,created_at DESC LIMIT 12", (source, before)).fetchall()
            if not rows and job.get("recoveryOf") and identity(job["recoveryOf"], "recovery") == job["taskId"]:
                parent = c.execute("SELECT payload_json,attempts,capability FROM ai_control_tasks WHERE task_id=%s "
                    "AND status='failed' AND account_id=%s AND symbol=%s",
                    (job["recoveryOf"], job["accountId"], job["symbol"])).fetchone()
                if parent and parent["capability"] == job["capability"] and json.loads(parent["payload_json"]).get("worldId") == job["worldId"]:
                    source = job["recoveryOf"]
                    rows = c.execute("SELECT input_id FROM ai_control_inputs FORCE INDEX(ai_input_task) "
                        "WHERE task_id=%s AND attempt<=%s ORDER BY attempt DESC,created_at DESC LIMIT 12", (source, parent["attempts"])).fetchall()
            plans, seen = [], set()
            for row in rows:
                # No source text or model response leaves this bounded reader.
                saved = c.execute("SELECT artifact_gzip,artifact_hash FROM ai_control_retrieval_rounds WHERE input_id=%s", (row["input_id"],)).fetchone()
                if not saved:
                    continue
                frozen = c.execute("SELECT artifact_gzip,attempt,prompt_hash FROM ai_control_inputs WHERE input_id=%s AND task_id=%s", (row["input_id"], source)).fetchone()
                env = json.loads(gzip.decompress(frozen["artifact_gzip"]))
                validate_execution_input(env)
                if env["current"].get("taskId") != source or any(env["current"].get(k) != job[k] for k in ("accountId", "symbol", "worldId")):
                    continue
                called = c.execute("SELECT c.call_id FROM ai_control_input_calls l JOIN ai_control_calls c ON c.call_id=l.call_id "
                    "WHERE l.input_id=%s AND c.task_id=%s AND c.prompt_hash=%s AND c.status='completed' LIMIT 1",
                    (row["input_id"], source, frozen["prompt_hash"])).fetchone()
                step = json.loads(gzip.decompress(saved["artifact_gzip"]))
                if not called or content_hash(step) != saved["artifact_hash"] or step.get("inputId") != row["input_id"]:
                    continue
                reads = list(step.get("reads", []))
                resumed = env.get("retrievalContext", {}).get("resumedSourceReads", {})
                if resumed.get("version") == policy.VERSION:
                    current_facts = {f["id"]: f for f in env["current"]["facts"]}
                    for read in resumed.get("reads", []):
                        if read.get("status") == "current-source-read-needs-assessment":
                            reads.append({"request": read["request"], "result": {"facts": [current_facts[k]
                                for k in read["factIds"] if k in current_facts]}})
                for read in reads:
                    request = read.get("request", {})
                    facts = read.get("result", {}).get("facts", [])
                    if request.get("tool") not in {"read_document", "read_report"} or not facts:
                        continue
                    key = content_hash(request)
                    if key in seen:
                        continue
                    seen.add(key)
                    plans.append({"request": request, "fromInputId": row["input_id"], "fromAttempt": frozen["attempt"],
                        "questionScope": policy.question_scope(env.get("retrievalContext", {}).get("questionIndex", [])),
                        "sourceFactHash": facts[0].get("evidenceView", {}).get("sourceFactHash", "")})
                    if len(plans) == policy.MAX_PLANS:
                        return plans
            return plans
