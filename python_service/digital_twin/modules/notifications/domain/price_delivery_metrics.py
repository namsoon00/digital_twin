"""Receipt-backed latency summaries, with explicit sample scope."""
from collections import Counter
from datetime import datetime
from math import ceil
from statistics import median


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def latency_summary(values):
    if not values:
        return {"sampleCount": 0}
    ordered = sorted(values)
    return {"sampleCount": len(values), "medianSeconds": round(median(values), 3),
            "p95Seconds": round(ordered[max(0, ceil(len(ordered) * .95) - 1)], 3), "maxSeconds": round(max(values), 3)}


def summarize_price_delivery(jobs, attempts):
    actual = {row["job_id"]: row for row in jobs if row.get("data_quality") == "actual" and row.get("is_mock") == 0}
    receipts = {}
    for row in attempts:
        meta = row.get("metadata") or {}
        clock = timestamp(row.get("completed_at"))
        if (row.get("job_id") not in actual or row.get("status") != "delivered"
                or row.get("audience") != "accountNotification" or meta.get("receiptVerified") is not True or not clock):
            continue
        previous = receipts.get(row["job_id"])
        if previous is None or clock < previous:
            receipts[row["job_id"]] = clock
    queue, source = [], []
    invalid = 0
    for job_id, delivered_at in receipts.items():
        job = actual[job_id]
        for field, values in (("created_at", queue), ("source_at", source)):
            start = timestamp(job.get(field))
            if start and start <= delivered_at:
                values.append((delivered_at - start).total_seconds())
            else:
                invalid += 1
    return {"scope": "recent-retained-price-jobs", "jobSampleCount": len(jobs),
            "actualJobCount": len(actual), "verifiedDeliveredJobs": len(receipts),
            "jobStatusCounts": dict(Counter(str(row.get("status") or "unknown") for row in jobs)),
            "queueToReceipt": latency_summary(queue), "sourceToReceipt": latency_summary(source),
            "missingOrInvalidClockCount": invalid}
