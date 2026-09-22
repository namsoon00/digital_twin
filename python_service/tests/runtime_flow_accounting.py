"""Pure accounting of bounded real request samples, separate from lineage proof."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import re


def reason_code(value):
    value = str(value or "")
    return value if re.fullmatch(r"[a-zA-Z0-9_.:-]{1,120}", value) else "unknown"


def request_evidence(row, redactor):
    status = row.get("ai_status")
    if status in {"pending", "processing", "retry"}:
        disposition = "in-flight"
    elif status == "superseded" and row.get("superseded_by"):
        disposition = "superseded"
    elif status == "failed":
        disposition = "failed" if row.get("has_error") else "unexplained-failure"
    elif status == "completed":
        if not row.get("result_id"):
            disposition = "missing-result"
        elif row.get("publication_mode") == "typedb-fallback":
            disposition = "typedb-fallback"
        elif (row.get("publication_mode") == "ai-authored" and row.get("ai_authored") == 1
              and row.get("publication_contract_passed") == 1
              and row.get("validation_state") not in {"blocked", "invalid", "failed", "error"}):
            disposition = "ai-authored"
        else:
            disposition = "unvalidated-result"
    else:
        disposition = "unclassified"
    delivery = "not-applicable"
    if disposition == "ai-authored":
        if row.get("scope_match") != 1 or not row.get("publication_id"):
            delivery = "missing-publication-or-scope"
        elif row.get("receipt_verified") == 1:
            delivery = "verified-telegram-receipt"
        elif row.get("delivery_state") in {"suppressed", "archived"} and reason_code(row.get("delivery_reason_code")) not in {"unknown", "null"}:
            delivery = "explained-withheld"
        elif row.get("notification_status") in {"pending", "processing", "retry", "awaiting_ai"}:
            delivery = "awaiting-delivery"
        elif row.get("notification_status") == "failed":
            delivery = "delivery-failed"
        else:
            delivery = "unresolved-delivery"
    return {
        "id": redactor.identity(row.get("request_id")),
        "subject": redactor.identity([row.get("account_id"), row.get("symbol")]),
        "release": redactor.identity([row.get("deployment_id"), row.get("release_fingerprint"), row.get("prompt_version")]),
        "disposition": disposition, "delivery": delivery,
        "reasonCode": reason_code(row.get("contract_failure_code") or row.get("delivery_reason_code")),
        "scopeConflict": row.get("scope_match") == 0,
    }


def outcome_evidence(row, now, redactor):
    try:
        target = datetime.fromisoformat(str(row.get("target_at") or "").replace("Z", "+00:00"))
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
    except ValueError:
        target = None
    if row.get("status") == "observed":
        state = "observed" if row.get("stored_outcome_id") and row.get("scope_match") == 1 else "invalid-outcome-link"
    elif row.get("status") == "needs-data" and row.get("exclusion_reason"):
        state = "explained-data-gap"
    elif target and target > now:
        state = "not-due"
    elif target and target + timedelta(minutes=int(row.get("maximum_delay_minutes") or 0)) >= now:
        state = "within-observation-window"
    else:
        state = "overdue-or-unexplained"
    return {"id": redactor.identity(row.get("target_id")), "state": state,
            "reasonCode": reason_code(row.get("exclusion_reason"))}


def summarize_cohort(samples, minimum):
    # The latest observation of a request wins; polling is not a new case.
    rows, outcomes = {}, {}
    truncated, unread = False, False
    for sample in samples:
        if not sample:
            unread = True
            continue
        truncated |= bool(sample.get("truncated") or sample.get("outcomesTruncated"))
        rows.update({row["id"]: row for row in sample.get("requests", [])})
        outcomes.update({row["id"]: row for row in sample.get("outcomes", [])})
    dispositions = Counter(row["disposition"] for row in rows.values())
    deliveries = Counter(row["delivery"] for row in rows.values() if row["disposition"] == "ai-authored")
    failures = sum(count for key, count in dispositions.items() if key not in {"ai-authored", "in-flight", "superseded"})
    delivery_gaps = sum(deliveries[key] for key in {"missing-publication-or-scope", "delivery-failed", "unresolved-delivery"})
    conflicts = sum(row["scopeConflict"] for row in rows.values())
    states = Counter(row["state"] for row in outcomes.values())
    outcome_errors = states["invalid-outcome-link"] + states["overdue-or-unexplained"]
    gaps = []
    if truncated or unread:
        gaps.append("sample-incomplete")
    if dispositions["ai-authored"] < minimum:
        gaps.append("insufficient-authored-samples")
    if deliveries["awaiting-delivery"]:
        gaps.append("delivery-still-pending")
    if dispositions["in-flight"]:
        gaps.append("ai-still-pending")
    if len({row["release"] for row in rows.values()}) > 1:
        gaps.append("mixed-release-cohort")
    return {
        "status": "degraded" if failures or delivery_gaps or conflicts or outcome_errors else "inconclusive" if gaps else "pass",
        "scope": "bounded-request-cohort-not-whole-service-or-investment-quality",
        "sampledRequests": len(rows), "sampledSubjects": len({r["subject"] for r in rows.values()}),
        "releaseCohorts": len({r["release"] for r in rows.values()}),
        "minimumAuthoredSamples": minimum, "dispositions": dict(dispositions),
        "delivery": dict(deliveries), "gaps": gaps, "truncated": truncated,
        "failureReasons": dict(Counter(r["reasonCode"] for r in rows.values() if r["disposition"] in {"typedb-fallback", "failed"})),
        "scopeConflicts": conflicts,
        "outcomeClosure": {
            "status": "degraded" if outcome_errors else "pass" if states["observed"] and len(states) == 1 else "inconclusive",
            "states": dict(states), "explainedGapsAreNotValidatedInvestmentOutcomes": True,
        },
    }
