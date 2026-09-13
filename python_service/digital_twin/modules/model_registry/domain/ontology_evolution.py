"""Versioned change plans and empirical admission, never investment actions."""

import hashlib
import json
import math
from datetime import datetime, timezone


EVOLUTION_CONTRACT = "ontology-evolution-plan-v1"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def validate_policy(value):
    policy = dict(value)
    if policy.get("mode") not in {"automatic", "shadow", "disabled"} or not policy.get("version"):
        raise ValueError("Evolution policy needs a version and explicit mode")
    if policy.get("allowedChanges") != ["add-predictive-rule"]:
        raise ValueError("Only additive predictive contracts are executable by this policy")
    for key in ("minimumIndependentPairs", "minimumDistinctDays", "independenceMinutes",
                "retryMinutes", "maximumAuthoringAttempts", "maximumEvidenceRows", "maximumShadowDays"):
        value = policy.get(key)
        if type(value) is not int or value < 1:
            raise ValueError("Invalid evolution policy field: " + key)
    for key in ("minimumImprovement", "maximumContradictionRate", "minimumWinLowerBound"):
        value = policy.get(key)
        if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid evolution policy field: " + key)
    if not isinstance(policy.get("baselineRuleIds"), list):
        raise ValueError("Evolution baseline rules must be explicitly configured")
    if policy["independenceMinutes"] < 1440:
        raise ValueError("Evolution uses preregistered daily anchors; independence must be at least one day")
    retention = policy.get("experimentEvidenceRetentionDays", 7)
    if type(retention) is not int or not 1 <= retention <= 90:
        raise ValueError("Invalid experiment evidence retention")
    return policy


def create_plan(case, rule, baseline, policy, created_at):
    policy = validate_policy(policy)
    if not baseline.get("deploymentId") or not baseline.get("artifactFingerprint"):
        raise ValueError("An immutable baseline release is required")
    if not timestamp(created_at):
        raise ValueError("A timezone-aware authoring cutoff is required")
    rule_id = str(rule.get("rule_id") or "")
    if not rule_id or not case.account_id or not case.symbol:
        raise ValueError("Evolution requires an account, subject and exact candidate rule")
    plan = {
        "contract": EVOLUTION_CONTRACT,
        "caseId": case.case_id, "accountId": case.account_id, "symbol": case.symbol,
        "changeKind": "add-predictive-rule", "candidateRule": dict(rule),
        "candidateRuleId": rule_id,
        "baseline": dict(baseline), "policy": policy,
        "createdAt": created_at,
        "supportingEvidenceIds": sorted(set(case.supporting_evidence_ids)),
        "counterEvidenceIds": sorted(set(case.counter_evidence_ids)),
        "invalidationConditions": list(case.invalidation_conditions),
        "validationRequirements": list(case.validation_requirements),
        "policyFingerprint": fingerprint(policy),
    }
    if baseline.get("observationRequirements"):
        plan["observationRequirements"] = baseline["observationRequirements"]
    plan["fingerprint"] = fingerprint(plan)
    return plan


def validate_plan(plan):
    if plan.get("contract") != EVOLUTION_CONTRACT:
        raise ValueError("Unsupported evolution plan")
    expected = fingerprint({key: value for key, value in plan.items() if key != "fingerprint"})
    if expected != plan.get("fingerprint"):
        raise ValueError("Evolution plan fingerprint mismatch")
    policy = validate_policy(plan["policy"])
    if fingerprint(policy) != plan.get("policyFingerprint"):
        raise ValueError("Evolution evaluation policy changed")
    return policy


def evaluate_comparison(plan, evidence, *, now, observed_after=""):
    """Only matched point-in-time pairs can qualify a candidate for adoption.

    One pair per non-overlapping subject window is counted. Pending/missing
    results, duplicated polling and old candidate revisions cannot be wins.
    """
    policy = validate_plan(plan)
    if evidence.get("status") != "ok":
        return {"status": "needs-data", "reason": evidence.get("reason") or "comparison-unavailable",
                "independentPairCount": 0, "automaticDeployment": False}
    cutoff, current = timestamp(plan["createdAt"]), timestamp(now)
    if observed_after:
        if not timestamp(observed_after):
            raise ValueError("Invalid monitoring cutoff")
        cutoff = max(cutoff, timestamp(observed_after))
    if current is None:
        raise ValueError("Invalid evaluation clock")
    accepted, excluded, seen, buckets = [], [], set(), set()
    cohort_count = 0
    next_independent_at = None
    rows = sorted(evidence.get("pairs") or [], key=lambda row: str(row.get("observedFromAt") or ""))
    for row in rows:
        start, end = timestamp(row.get("observedFromAt")), timestamp(row.get("observedAt"))
        reason = ""
        if (row.get("accountId") != plan["accountId"] or row.get("symbol") != plan["symbol"]
                or row.get("candidateFingerprint") != plan["fingerprint"]):
            reason = "wrong-scope-or-revision"
        elif not start or start < cutoff or start > current:
            reason = "invalid-observation-window"
        elif not row.get("sourceSnapshotId") or not row.get("id"):
            reason = "incomplete-observation"
        elif not row.get("independenceKey") or row["independenceKey"] in seen:
            reason = "duplicate-event"
        elif int(start.timestamp() // (policy["independenceMinutes"] * 60)) in buckets:
            reason = "duplicate-time-bucket"
        if reason:
            excluded.append({"id": row.get("id"), "reason": reason})
            continue
        seen.add(row["independenceKey"])
        buckets.add(int(start.timestamp() // (policy["independenceMinutes"] * 60)))
        cohort_count += 1
        if cohort_count > policy["minimumIndependentPairs"]:
            break
        if not end or end <= start or end > current:
            reason = "pending-or-invalid-outcome"
        elif next_independent_at and start.timestamp() < next_independent_at:
            reason = "overlapping-observation"
        elif row.get("eligible") is not True:
            reason = "incomplete-observation"
        elif plan.get("observationRequirements") and (not row.get("datasetFingerprint") or row.get("inputState") != "ready"):
            reason = "frozen-experiment-inputs-required"
        elif row.get("candidateOutcome") not in {"corroborated", "contradicted"} or row.get("baselineOutcome") not in {"corroborated", "contradicted"}:
            reason = "inconclusive-pair"
        if end and end > start:
            next_independent_at = max(next_independent_at or 0, end.timestamp())
        if reason:
            excluded.append({"id": row.get("id"), "reason": reason})
            continue
        accepted.append(row)
    # Freeze the first preregistered cohort; repeated polling cannot cherry-pick a later win.
    accepted = accepted[:policy["minimumIndependentPairs"]]
    total = len(accepted)
    wins = sum(row["candidateOutcome"] == "corroborated" for row in accepted)
    baseline_wins = sum(row["baselineOutcome"] == "corroborated" for row in accepted)
    gained = sum(row["candidateOutcome"] == "corroborated" and row["baselineOutcome"] == "contradicted" for row in accepted)
    lost = sum(row["candidateOutcome"] == "contradicted" and row["baselineOutcome"] == "corroborated" for row in accepted)
    discordant = gained + lost
    # Exact paired sign test; identical outcomes are ties, not improvements.
    paired_p = sum(math.comb(discordant, k) for k in range(gained, discordant + 1)) / 2**discordant if discordant else 1.0
    days = len({timestamp(row["observedFromAt"]).date() for row in accepted})
    improvement = (wins - baseline_wins) / total if total else None
    contradiction = (total - wins) / total if total else None
    # Wilson's fixed 95% interval is a statistical definition, not a ranking score.
    z = 1.959963984540054
    p = wins / total if total else 0
    lower = ((p + z*z/(2*total) - z*math.sqrt(p*(1-p)/total + z*z/(4*total*total)))
             / (1 + z*z/total)) if total else None
    enough = total >= policy["minimumIndependentPairs"] and days >= policy["minimumDistinctDays"]
    qualified = bool(enough and improvement >= policy["minimumImprovement"]
                     and contradiction <= policy["maximumContradictionRate"]
                     and lower >= policy["minimumWinLowerBound"]
                     and paired_p <= 0.05 / policy["maximumAuthoringAttempts"])
    return {
        "status": "qualified" if qualified else "not-better" if enough else "needs-data",
        "reason": "paired-holdout-qualified" if qualified else "baseline-not-improved" if enough else "independent-outcomes-required",
        "independentPairCount": total, "distinctDayCount": days,
        "candidateCorroboratedCount": wins, "baselineCorroboratedCount": baseline_wins,
        "improvement": improvement, "contradictionRate": contradiction, "winLowerBound95": lower,
        "pairedGainCount": gained, "pairedLossCount": lost, "pairedTestPValue": paired_p,
        "excludedCount": len(excluded), "exclusions": excluded[:20],
        "evidenceIds": [row["id"] for row in accepted],
        "policyFingerprint": plan["policyFingerprint"], "candidateFingerprint": plan["fingerprint"],
        "evaluatedAt": now, "automaticDeployment": False,
        "dataSummary": dict(evidence.get("dataSummary") or {}),
    }
