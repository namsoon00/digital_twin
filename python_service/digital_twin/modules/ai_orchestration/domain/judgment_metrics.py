"""Measured operational quality, without interpreting model reviews as accuracy."""
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
            **{key: distribution([row.get("metrics", {}).get(key) for row in rows])
               for key in ("promptBytes", "capacityWaitMs", "modelProcessMs", "totalMs")}})
    return result


def judgments_summary(tasks, calls_by_task, receipts):
    groups = defaultdict(list)
    for task in tasks:
        if task["result"].get("summary"):
            groups[task["result"].get("executionPromptVersion") or "unrecorded"].append(task)
    result = []
    for version, rows in sorted(groups.items()):
        values = [row["result"] for row in rows]
        calls = [call for row in rows for call in calls_by_task.get(row["task_id"], [])]
        measured = [usage_counts(call.get("metrics", {}).get("usage")) for call in calls]
        per_task = []
        for row in rows:
            linked = calls_by_task.get(row["task_id"], [])
            usage = [usage_counts(call.get("metrics", {}).get("usage")) for call in linked]
            if linked and all(u is not None for u in usage) and all(c["status"] != "running" for c in linked):
                per_task.append(sum(u["input_tokens"] + u["output_tokens"] for u in usage))
        result.append({"executionPromptVersion": version, "authored": len(rows),
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
                for v in values if v.get("researchQuality", {}).get("status") == "accepted"
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
