"""AI chooses the next source operation; code enforces budgets and audit lineage."""
import hashlib
import json
import time
from copy import deepcopy

from ..domain.source_exploration import VERSION, exploration_action, public_url
from ..domain.investment_research import research_evidence_from_payload
from ..domain.research_progress import fingerprint, task_contract
from ..domain.source_recovery import VERSION as RECOVERY_VERSION, source_recovery_summary


class SourceExplorationService:
    def __init__(self, advisor, tools, settings=None, clock=time.monotonic, deferred_errors=()):
        self.advisor, self.tools = advisor, tools
        self.settings, self.clock = dict(settings or {}), clock
        # Runtime composition supplies errors that mean no provider execution
        # was admitted. The owner of the central queue handles their retry.
        self.deferred_errors = deferred_errors

    def enabled(self):
        return str(self.settings.get("researchSourceExplorationEnabled", "1")).lower() not in {"0", "false", "off"}

    def explore(self, target, tasks, packets, assessments, *, private_terms=(), checkpoint=None, resume=None,
                provider_statuses=(), previous_recovery=None):
        audit = {"version": VERSION, "taskIds": [row["taskId"] for row in tasks],
                 "taskFingerprint": fingerprint([task_contract(row) for row in tasks]),
                 "steps": [], "stopReason": "step-budget-exhausted", "missingRequirements": [],
                 "searchCount": 0, "readCount": 0, "extractionCount": 0, "evidenceIds": [],
                 "recoveryVersion": RECOVERY_VERSION}
        if not self.enabled():
            audit["stopReason"] = "exploration-disabled"
            return [], audit
        started, candidates, documents, evidence, attempted = self.clock(), {}, [], [], set()
        read_documents, passage_keys = {}, set()
        carried_seconds = 0
        if resume:
            if (resume.get("version") != VERSION or resume.get("taskFingerprint") != audit["taskFingerprint"]
                    or resume.get("taskIds") != audit["taskIds"]):
                raise ValueError("exploration-resume-contract-mismatch")
            audit = deepcopy(resume)
            carried_seconds = max(0, float(audit.get("elapsedSeconds") or 0))
            evidence = [research_evidence_from_payload(row, target.normalized_symbol())
                        for row in audit.get("collectedEvidence", [])]
            if audit.get("complete"):
                return evidence, audit
        pending_step = audit["steps"][-1] if audit["steps"] and audit["steps"][-1].get("status") == "deferred" else None
        audit["stopReason"] = "step-budget-exhausted"
        def add_candidates(rows):
            added = []
            for raw in rows:
                try:
                    url = public_url(raw.get("url"))
                except (ValueError, AttributeError):
                    continue
                key = hashlib.sha256(url.encode()).hexdigest()[:20]
                row = {**raw, "url": url, "candidateId": key}
                if key in candidates:
                    # A later original index can supply publication/issuer
                    # provenance that a search lead was never allowed to assert.
                    if row.get("discovery") == "official-page-link" and row.get("parentDocumentHash"):
                        candidates[key].update(row)
                    continue
                if len(candidates) < 48:
                    candidates[key] = row
                    added.append(row)
            return added
        add_candidates(self.tools.seeds(target, packets))
        add_candidates(audit.get("seedCandidates", []))
        audit.setdefault("seedCandidates", deepcopy(list(candidates.values())))
        for step in audit["steps"]:
            decision = step.get("decision", {})
            if decision.get("action") in {"search", "read", "extract"}:
                attempted.add((decision["action"], str(decision.get("query") or decision.get("candidateId")).casefold()))
            result = step.get("result") or {}
            add_candidates(result.get("candidates") or [])
            if result.get("document"):
                documents.append(result["document"])
                read_documents[decision.get("candidateId")] = result["document"]
                passage_keys.update((result["document"].get("sourceDocumentHash"), row.get("passageHash"))
                                    for row in result["document"].get("passages", []))
            if step.get("status") in {"deciding", "executing"}:
                step.update(status="interrupted", errorKind="execution-result-unconfirmed")
        def save():
            audit["evidenceIds"] = [row.evidence_id for row in evidence]
            audit["collectedEvidence"] = [row.to_dict() for row in evidence]
            audit["elapsedSeconds"] = round(carried_seconds + self.clock() - started, 2)
            audit["recovery"] = source_recovery_summary(audit, provider_statuses)
            if checkpoint:
                checkpoint(deepcopy(audit))
        # A run can spend at most six decisions, two searches and three reads.
        # Provider/model timeouts are also capped by the remaining wall budget.
        for step_number in range(len(audit["steps"]) - int(pending_step is not None), 6):
            remaining = 360 - carried_seconds - (self.clock() - started)
            if remaining <= 0:
                audit["stopReason"] = "time-budget-exhausted"
                break
            allowed = ["finish"]
            if audit["searchCount"] < 2 and audit["readCount"] < 3:
                allowed.append("search")
            if audit["readCount"] < 3 and candidates:
                allowed.append("read")
            if (audit["readCount"] < 3 and audit.get("extractionCount", 0) < 1 and read_documents
                    and callable(getattr(self.tools, "extract", None))):
                allowed.append("extract")
            context = {"version": VERSION, "subject": {"symbol": target.normalized_symbol(), "name": target.name},
                "publicSourcePolicy": "registered-public-documents-v1",
                "tasks": [{key: row.get(key) for key in ("taskId", "question", "purpose", "sourceTypes", "queryTerms", "maxAgeMinutes", "requiredPeriodEnds", "requiredMetrics")}
                          for row in tasks],
                "assessments": assessments, "existingEvidence": packets[:6],
                "candidates": list(candidates.values()), "documents": documents,
                "recoveryVersion": RECOVERY_VERSION, "recovery": source_recovery_summary(audit, provider_statuses),
                "previousRecovery": deepcopy(previous_recovery or {}),
                "extractableCandidateIds": list(read_documents),
                "previousSteps": [{"decision": row.get("decision"), "status": row.get("status"),
                                   "errorKind": row.get("errorKind")} for row in audit["steps"]],
                "budget": {"allowedActions": allowed, "remainingSeconds": int(remaining),
                           "searchesRemaining": 2 - audit["searchCount"], "readsRemaining": 3 - audit["readCount"]}}
            # Omission is explicit; source packets and immutable receipts remain
            # in the parent ResearchRun. Never cut a table in the middle.
            while len(json.dumps(context, ensure_ascii=False).encode()) > 90000 and context["existingEvidence"]:
                context["existingEvidence"].pop()
            context["omittedExistingEvidenceCount"] = len(packets) - len(context["existingEvidence"])
            if len(json.dumps(context, ensure_ascii=False).encode()) > 100000:
                audit["stopReason"] = "context-budget-exhausted"
                break
            resuming_operation = bool(pending_step and pending_step.get("deferredPhase") == "executing")
            if pending_step:
                step, pending_step = pending_step, None
                step["status"] = step["deferredPhase"]
                step.pop("errorKind", None)
                context = step["input"]
            else:
                step = {"step": step_number + 1, "input": deepcopy(context), "status": "deciding"}
                audit["steps"].append(step)
            save()  # Capture the exact input before spending a model call.
            try:
                action = step["decision"] if resuming_operation else exploration_action(
                    self.advisor.choose(context, min(90, remaining)), candidates, private_terms)
                step["decision"] = action
                audit["missingRequirements"] = action["missingRequirements"]
                if action["action"] not in allowed and not resuming_operation:
                    raise ValueError("operation-budget-exhausted")
                if action["action"] == "finish":
                    step["status"], audit["stopReason"] = "finished", "ai-finished"
                    break
                operation = (action["action"], str(action.get("query") or action.get("candidateId")).casefold())
                if operation in attempted and not resuming_operation:
                    raise ValueError("duplicate-exploration-operation")
                attempted.add(operation)
                remaining = 360 - carried_seconds - (self.clock() - started)
                if remaining <= 0:
                    audit["stopReason"] = "time-budget-exhausted"
                    step["status"] = "not-executed"
                    break
                if action["action"] == "search":
                    audit["searchCount"] += int(not resuming_operation)
                    step["status"] = "executing"
                    save()
                    result = self.tools.search(target, action["query"], min(90, remaining))
                else:
                    if action["action"] == "extract" and action["candidateId"] not in read_documents:
                        raise ValueError("extraction-requires-read-document")
                    audit["readCount"] += int(not resuming_operation)
                    if action["action"] == "extract":
                        audit["extractionCount"] = audit.get("extractionCount", 0) + int(not resuming_operation)
                    step["status"] = "executing"
                    step["sourceUrl"] = candidates[action["candidateId"]]["url"]
                    save()
                    if action["action"] == "extract":
                        result = self.tools.extract(target, candidates[action["candidateId"]], tasks,
                            read_documents[action["candidateId"]], action["focus"], min(25, remaining))
                    else:
                        result = self.tools.read(target, candidates[action["candidateId"]], tasks, min(25, remaining))
                found = result.pop("evidence", None)
                if found is not None and found.evidence_id not in {row.evidence_id for row in evidence}:
                    evidence.append(found)
                step["result"] = deepcopy(result)
                step["status"] = str(result.get("status") or "completed")
                add_candidates(result.get("candidates") or [])
                if result.get("document"):
                    documents.append(result["document"])
                    read_documents[action["candidateId"]] = result["document"]
                    keys = {(result["document"].get("sourceDocumentHash"), row.get("passageHash"))
                            for row in result["document"].get("passages", [])}
                    step["progress"] = {"newPassageCount": len(keys - passage_keys),
                        "documentAdmitted": found is not None, "answerCompletion": "requires-independent-review"}
                    passage_keys.update(keys)
            except self.deferred_errors as error:
                step.update(deferredPhase=step["status"], status="deferred", errorKind=type(error).__name__,
                            lastDeferredErrorKind=type(error).__name__,
                            deferrals=int(step.get("deferrals", 0)) + 1)
                audit.update(complete=False, stopReason="execution-deferred")
                save()
                raise
            except Exception as error:
                # A failed source may have an alternative. Never interpret a
                # transport/parser/model failure as proof that data is absent.
                step.update(status="failed", errorKind=str(error)[:120] if isinstance(error, ValueError) else type(error).__name__)
                if "decision" not in step:
                    audit["stopReason"] = "exploration-model-failed"
                    break
            save()
        audit["complete"] = True
        save()
        return evidence, audit
