"""Measured operational quality, without interpreting model reviews as accuracy."""
from .research_review_parts import approved
from collections import Counter, defaultdict
from statistics import mean, median

from .token_budget import usage_counts


def distribution(values):
    values = [v for v in values if type(v) in (int, float) and v >= 0]
    return {"samples": len(values), "total": sum(values),
            "mean": round(mean(values), 2) if values else None,
            "median": median(values) if values else None,
            "maximum": max(values) if values else None}


def calls_summary(calls):
    groups = defaultdict(list)
    for row in calls:
        metrics = row.get("metrics", {})
        groups[(row["workload"], metrics.get("promptVersion") or "unrecorded",
                metrics.get("tokenPool") or "unrecorded")].append(row)
    result = []
    for (workload, version, pool), rows in sorted(groups.items()):
        measured = [u for row in rows if (u := usage_counts(row.get("metrics", {}).get("usage"))) is not None]
        result.append({"workload": workload, "promptVersion": version, "tokenPool": pool,
            "calls": len(rows), "states": dict(Counter(row["status"] for row in rows)),
            "reportedUsageCalls": len(measured), "unmeasuredUsageCalls": len(rows) - len(measured),
            "reportedTokens": sum(u["input_tokens"] + u["output_tokens"] for u in measured),
            "inputTokens": distribution([u["input_tokens"] for u in measured]),
            "outputTokens": distribution([u["output_tokens"] for u in measured]),
            "cachedInputTokens": distribution([u.get("cached_input_tokens") for u in measured]),
            "tokenBasis": "input-plus-output; cached-input-is-a-subset-not-additional",
            **{key: distribution([row.get("metrics", {}).get(key) for row in rows])
               for key in ("promptBytes", "capacityWaitMs", "modelProcessMs", "totalMs")}})
    return result


def judgments_summary(tasks, calls_by_task, receipts):
    groups = defaultdict(list)
    for task in tasks:
        if task["result"].get("summary"):
            groups[(task["result"].get("executionPromptVersion") or "unrecorded",
                    task["result"].get("input", {}).get("retrieval", {}).get("version") or "unrecorded")].append(task)
    result = []
    for (version, retrieval_version), rows in sorted(groups.items()):
        values = [row["result"] for row in rows]
        calls = [call for row in rows for call in calls_by_task.get(row["task_id"], [])]
        measured = [usage_counts(call.get("metrics", {}).get("usage")) for call in calls]
        per_task = []
        for row in rows:
            linked = calls_by_task.get(row["task_id"], [])
            usage = [usage_counts(call.get("metrics", {}).get("usage")) for call in linked]
            if linked and all(u is not None for u in usage) and all(c["status"] != "running" for c in linked):
                per_task.append(sum(u["input_tokens"] + u["output_tokens"] for u in usage))
        result.append({"executionPromptVersion": version, "retrievalVersion": retrieval_version, "authored": len(rows),
            "taskStates": dict(Counter(row["status"] for row in rows)),
            "quality": dict(Counter(v.get("quality", {}).get("status", "unknown") for v in values)),
            "publication": dict(Counter(v.get("publication", {}).get("status", "unknown") for v in values)),
            "receiptVerifiedDeliveries": sum(bool(receipts.get(v.get("publication", {}).get("jobId"))) for v in values),
            "repairAttempted": sum(bool(v.get("repair", {}).get("attempted")) for v in values),
            "withQualityErrors": sum(bool(v.get("quality", {}).get("errors")) for v in values),
            "withMemoryCoverage": sum(bool(v.get("memoryCoverage")) for v in values),
            "withAuditLineage": sum(bool(v.get("judgmentLinks")) for v in values),
            "documentaryPlans": sum(bool(v.get("documentaryReview")) for v in values),
            "companyValueStates": dict(Counter(v.get("companyValue", {}).get("status", "not-applicable") for v in values)),
            "macroIncluded": sum(any(f.get("kind") == "macro-print" for f in v.get("input", {}).get("facts", [])) for v in values),
            "macroCited": sum(any(f.get("kind") == "macro-print" and f.get("id") in v.get("evidenceIds", [])
                                  for f in v.get("input", {}).get("facts", [])) for v in values),
            "questionImpacts": dict(Counter(q.get("judgmentImpact", {}).get("effect", "unknown")
                                            for v in values for q in v.get("questionResolutions", []))),
            "researchReviewStates": dict(Counter(v.get("researchQuality", {}).get("status", "not-required") for v in values)),
            "reviewedQuestionImpacts": dict(Counter(q.get("judgmentImpact", {}).get("effect", "unknown")
                for v in values if approved(v)
                for q in v.get("questionResolutions", []))),
            "linkedCentralCalls": len(calls), "linkedCallsWithUsage": sum(u is not None for u in measured),
            "tokensPerFullyMeasuredAuthoredTask": distribution(per_task),
            "costScope": "retained central calls across all attempts; excludes separate source-provider workloads",
            "qualification": "stored-operational-results-not-investment-accuracy"})
    return result


def task_summary(tasks):
    return {"states": dict(Counter(row["status"] for row in tasks)),
            "nonAuthoredResults": dict(Counter(row["result"].get("status", "unrecorded")
                for row in tasks if not row["result"].get("summary"))),
            "failureStages": dict(Counter(row["result"].get("failure", {}).get("stage", "unknown")
                for row in tasks if row["status"] == "failed")),
            "sourceAnswerStates": dict(Counter(row["result"]["documentaryAnswer"].get("status", "unknown")
                for row in tasks if row["result"].get("documentaryAnswer"))),
            "observationGaps": sum(e.get("status") in {"observation-window-missed", "unevaluable"}
                for row in tasks for e in row["result"].get("followUpEvaluations", [])),
            "coalescedReservations": sum(row["result"].get("resumption", {}).get("coalescedTaskCount", 0) for row in tasks)}


def research_summary(tasks, window_calls):
    """Relate measured calls to source outcomes without treating partial as done.

    Calls are restricted to the audit window, not the full lifetime of a task.
    Missing usage/progress stays unmeasured. Collection and passage extraction
    are useful work but are neither an answered question nor investment proof.
    """
    by_task, groups = defaultdict(list), defaultdict(list)
    for call in window_calls:
        by_task[call.get("task_id")].append(call)
    for row in tasks:
        result = row["result"]
        if result.get("documentaryAnswer"):
            groups[(result["documentaryAnswer"].get("status", "unknown"),
                    result.get("planningReuse", {}).get("version", "unrecorded"))].append(row)
    summaries = []
    for (status, policy), rows in sorted(groups.items()):
        values = [row["result"] for row in rows]
        calls = [call for row in rows for call in by_task[row["task_id"]]]
        measured = [usage_counts(call.get("metrics", {}).get("usage")) for call in calls]
        # changedEvidenceCount is the repository's written-row count and may
        # include refreshed governance metadata. It does not prove new sources.
        writes = [v.get("changedEvidenceCount") for v in values]
        progress = [v.get("sourceRecovery", {}).get("progress", {}) for v in values]
        collected = [p.get("collectedEvidenceCount") for p in progress]
        passages = [p.get("newPassageCount") for p in progress]
        summaries.append({"answerStatus": status, "reusePolicyVersion": policy, "tasks": len(rows),
            "taskStates": dict(Counter(row["status"] for row in rows)),
            "stopReasons": dict(Counter(v.get("stopReason", "unrecorded") for v in values)),
            "tasksWithWindowCalls": sum(bool(by_task[row["task_id"]]) for row in rows),
            "windowCalls": len(calls), "callsWithUsage": sum(u is not None for u in measured),
            "callsWithoutUsage": sum(u is None for u in measured),
            "windowReportedTokens": sum(u["input_tokens"] + u["output_tokens"] for u in measured if u is not None),
            "workloads": calls_summary(calls),
            "sourceRowsWritten": distribution(writes),
            "tasksWithSourceWrites": sum(type(n) is int and n > 0 for n in writes),
            "explorationCollectedEvidence": distribution(collected),
            "tasksWithExplorationEvidence": sum(type(n) is int and n > 0 for n in collected),
            "newPassages": distribution(passages),
            "tasksWithNewPassages": sum(type(n) is int and n > 0 for n in passages),
            "skippedPlanningCalls": distribution([v.get("planningReuse", {}).get("skippedPlanningCalls") for v in values]),
            "costScope": "task outcomes updated in window; only calls started in window, not lifetime cost",
            "qualification": "source-progress-and-documentary-answer-status-not-investment-accuracy"})
    return summaries
