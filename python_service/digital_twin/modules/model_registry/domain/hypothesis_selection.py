"""Preregistered whole-cohort selection utility, never actual trading P&L."""
import math
from datetime import timedelta
from .ontology_evolution import timestamp, validate_plan


def evaluate_selection(plan, evidence, *, now, observed_after=""):
    policy = validate_plan(plan)
    cutoff, current = timestamp(observed_after or plan["createdAt"]), timestamp(now)
    if not current or not cutoff:
        raise ValueError("selection-clock-required")
    horizon = plan["baseline"]["comparisonHorizonMinutes"]
    frozen, accepted, excluded, buckets, events = [], [], [], set(), set()
    next_at = cutoff
    for row in sorted(evidence.get("pairs") or [], key=lambda item: (str(item.get("observedFromAt") or ""), str(item.get("id") or ""))):
        start = timestamp(row.get("observedFromAt"))
        if (row.get("accountId"), row.get("symbol"), row.get("candidateFingerprint")) != (plan["accountId"], plan["symbol"], plan["fingerprint"]):
            excluded.append({"id": row.get("id"), "reason": "wrong-scope-or-revision"}); continue
        if not start or start < cutoff or start > current:
            excluded.append({"id": row.get("id"), "reason": "invalid-anchor"}); continue
        bucket = int(start.timestamp() // (policy["independenceMinutes"] * 60))
        if bucket in buckets or row.get("independenceKey") in events or start < next_at:
            excluded.append({"id": row.get("id"), "reason": "overlapping-or-duplicate-opportunity"}); continue
        if len(frozen) >= policy["minimumIndependentPairs"]:
            break
        buckets.add(bucket); events.add(row.get("independenceKey")); frozen.append(row)
        next_at = start + timedelta(minutes=max(horizon, policy["independenceMinutes"]))
        end = timestamp(row.get("observedAt"))
        # Delayed measurements still occupy their original outcome window.
        # Reserve it before eligibility checks, so a failed first opportunity
        # cannot be replaced by a later, overlapping winner.
        if end and start <= end <= current:
            next_at = max(next_at, end)
        reason = ""
        if not row.get("id") or not row.get("independenceKey") or not row.get("sourceSnapshotId") or not row.get("datasetFingerprint") or row.get("inputState") != "ready":
            reason = "frozen-inputs-required"
        elif not end or end < start + timedelta(minutes=horizon) or end > current:
            reason = "outcome-not-due-or-unavailable"
        elif row.get("eligible") is not True or row.get("baselineOutcome") not in {"corroborated", "contradicted"}:
            reason = "baseline-outcome-unavailable"
        elif row.get("selectionState") not in {"selected", "skipped"}:
            reason = "selection-receipt-unavailable"
        elif row["selectionState"] == "selected" and row.get("candidateOutcome") != row.get("baselineOutcome"):
            reason = "same-prediction-contract-mismatch"
        if reason:
            excluded.append({"id": row.get("id"), "reason": reason}); continue
        accepted.append(row)
    selected = [row for row in accepted if row["selectionState"] == "selected"]
    skipped = [row for row in accepted if row["selectionState"] == "skipped"]
    avoided = sum(row["baselineOutcome"] == "contradicted" for row in skipped)
    missed = len(skipped) - avoided
    wins = sum(row["baselineOutcome"] == "corroborated" for row in selected)
    n, sn, discordant = len(accepted), len(selected), len(skipped)
    pvalue = sum(math.comb(discordant, k) for k in range(avoided, discordant + 1)) / 2 ** discordant if discordant else 1.0
    p, z = wins / sn if sn else 0, 1.959963984540054
    lower = (p + z*z/(2*sn) - z*math.sqrt(p*(1-p)/sn + z*z/(4*sn*sn))) / (1+z*z/sn) if sn else 0
    gain = (avoided - missed) / n if n else None
    days = len({timestamp(row["observedFromAt"]).date() for row in accepted})
    complete = evidence.get("status") == "ok" and n >= policy["minimumIndependentPairs"] and days >= policy["minimumDistinctDays"]
    qualified = bool(complete and sn >= plan["selectionContract"]["minimumSelected"] and discordant
                     and gain >= policy["minimumImprovement"] and lower >= policy["minimumWinLowerBound"]
                     and (sn-wins)/sn <= policy["maximumContradictionRate"]
                     and pvalue <= .05 / policy["maximumAuthoringAttempts"])
    return {"status": "qualified" if qualified else "not-better" if complete else "needs-data",
            "reason": "conditional-selection-qualified" if qualified else "whole-cohort-selection-evidence-required",
            "contract": plan["selectionContract"], "independentPairCount": n, "frozenOpportunityCount": len(frozen),
            "selectedCount": sn, "skippedCount": len(skipped), "selectionRate": sn/n if n else None,
            "avoidedAdverseCount": avoided, "missedOpportunityCount": missed, "selectedAdverseCount": sn-wins,
            "improvement": gain, "contradictionRate": (sn-wins)/sn if sn else None, "pairedTestPValue": pvalue, "winLowerBound95": lower,
            "exclusions": excluded, "excludedCount": len(excluded), "evidenceIds": [row["id"] for row in frozen],
            "candidateFingerprint": plan["fingerprint"], "policyFingerprint": plan["policyFingerprint"],
            "evaluatedAt": now, "automaticDeployment": False, "financialReturn": None,
            "dataSummary": dict(evidence.get("dataSummary") or {})}
