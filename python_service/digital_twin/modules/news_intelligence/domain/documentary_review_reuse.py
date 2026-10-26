"""Reuse a source review only inside the same run and exact evidence contract.

Eligibility is reassessed by the caller before lookup and after reuse. A cached
partial or rejected review never becomes an answered question. Transport errors
are not cached, and a changed packet or requirement gets a new review key.
"""
from copy import deepcopy

from .research_progress import fingerprint, task_contract

VERSION = "documentary-review-reuse-v1"
MAX_ENTRIES = 4  # Initial review plus at most three collection rounds.


class DocumentaryReviewReuse:
    def __init__(self, run_id, account_id, symbol, question, tasks, brief, checkpoint=None):
        self.scope = fingerprint({"version": VERSION, "runId": run_id, "accountId": account_id,
            "symbol": symbol, "question": {k: v for k, v in question.items() if k not in {"questionId", "askedAt"}},
            "tasks": [task_contract(row) for row in tasks], "brief": brief})
        self.entries, self.skipped, self.repair_used = [], 0, False
        if not isinstance(checkpoint, dict) or checkpoint.get("version") != VERSION or checkpoint.get("scopeHash") != self.scope:
            return
        entries = checkpoint.get("entries")
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            return
        for entry in entries:
            if not isinstance(entry, dict) or not self.valid(entry):
                return
        self.entries = deepcopy(entries)
        count = checkpoint.get("skippedPlanningCalls")
        self.skipped = count if type(count) is int and count >= 0 else 0
        self.repair_used = checkpoint.get("citationRepairUsed") is True or any(row["repairAttempted"] for row in entries)

    @staticmethod
    def key(assessments):
        return fingerprint([{key: row[key] for key in ("taskId", "assessmentFingerprint")}
                            for row in assessments])

    @staticmethod
    def valid(entry):
        payload = {key: entry.get(key) for key in ("fingerprint", "taskReviews", "planningAudit", "repairAttempted")}
        return (set(entry) == {*payload, "receiptHash"}
                and isinstance(payload["taskReviews"], list)
                and all(isinstance(row, dict) for row in payload["taskReviews"])
                and isinstance(payload["planningAudit"], dict)
                and payload["planningAudit"].get("planningSource") == "ai-hypothesis-research-planner"
                and type(payload["repairAttempted"]) is bool
                and entry["receiptHash"] == fingerprint(payload))

    def lookup(self, assessments):
        key = self.key(assessments)
        entry = next((row for row in self.entries if row["fingerprint"] == key), None)
        if entry:
            self.skipped += 1
            return deepcopy(entry)
        return None

    def record(self, assessments, plan, repair_attempted=False):
        self.repair_used = self.repair_used or bool(repair_attempted)
        audit = deepcopy(plan.get("planningAudit") or {})
        if audit.get("planningSource") != "ai-hypothesis-research-planner":
            return  # An unavailable, failed or disabled planner is not a review.
        audit.pop("reuse", None)
        entry = {"fingerprint": self.key(assessments), "taskReviews": deepcopy(plan.get("taskReviews") or []),
                 "planningAudit": audit, "repairAttempted": bool(repair_attempted)}
        entry["receiptHash"] = fingerprint(entry)
        self.entries = [row for row in self.entries if row["fingerprint"] != entry["fingerprint"]]
        self.entries = [*self.entries, entry][-MAX_ENTRIES:]

    def checkpoint(self):
        return {"version": VERSION, "scopeHash": self.scope, "entries": deepcopy(self.entries),
                "skippedPlanningCalls": self.skipped, "citationRepairUsed": self.repair_used}
