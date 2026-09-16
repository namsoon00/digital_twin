"""Point-in-time joins over the original prediction and outcome ledgers."""

from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.modules.outcomes.contracts import claim_validation_fingerprint
from digital_twin.modules.decisions.contracts import canonical_investment_timestamp
from datetime import datetime, timedelta
from digital_twin.modules.model_registry.contracts import parse_timestamp


def read_experiment_outcome_schedule(plan, episode_ids, *, connect, now):
    ids = sorted(set(episode_ids))[:plan["policy"]["minimumIndependentPairs"] * 2]
    if not ids:
        return {}
    with connect() as connection:
        rows = connection.execute(
            "SELECT observation_episode_id, status, target_at, maximum_delay_minutes "
            "FROM investment_hypothesis_observation_targets WHERE account_id = %s AND symbol = %s "
            "AND horizon_minutes = %s AND observation_episode_id IN (" + ",".join(["%s"] * len(ids)) + ")",
            (plan["accountId"], plan["symbol"], plan["baseline"]["comparisonHorizonMinutes"], *ids),
        ).fetchall()
    states = {"missing": len(set(ids) - {row["observation_episode_id"] for row in rows}),
              "notDue": 0, "overdue": 0, "needsData": 0, "excluded": 0, "awaitingCapture": 0}
    for row in rows:
        target = parse_timestamp(row.get("target_at"))
        status = row.get("status")
        if status == "excluded":
            states["excluded"] += 1
        elif status == "needs-data":
            states["needsData"] += 1
        elif status == "pending" and target:
            observed_at = parse_timestamp(now)
            deadline = target + timedelta(minutes=int(row.get("maximum_delay_minutes") or 0))
            state = "notDue" if target > observed_at else "overdue" if deadline < observed_at else "awaitingCapture"
            states[state] += 1
        else:
            states["awaitingCapture"] += 1
    reason = next((reason for key, reason in (
        ("missing", "experiment-outcome-schedule-missing"), ("excluded", "experiment-outcome-excluded"),
        ("needsData", "experiment-outcome-data-gap"), ("overdue", "experiment-outcome-overdue"),
        ("awaitingCapture", "experiment-outcome-capture-pending"), ("notDue", "experiment-outcome-not-due"),
    ) if states[key]), "")
    return {"states": states, "blockingReason": reason}


def read_experiment_inference_coverage(plan, deployment_id, *, connect, observed_after=""):
    """Diagnose capture gaps from bounded, exact-release subject cases, not new predictions."""
    limit = min(128, int(plan["policy"]["maximumEvidenceRows"]))
    phase = observed_after or plan["createdAt"]
    with connect() as connection:
        rows = connection.execute(
            "SELECT subject_case_id, created_at, JSON_EXTRACT(payload_json, '$.candidateSet') AS candidate_json "
            "FROM investment_subject_decision_cases WHERE account_id = %s AND symbol = %s "
            "AND updated_at >= %s AND created_at >= %s AND deployment_id = %s "
            "ORDER BY updated_at DESC LIMIT %s",
            (plan["accountId"], plan["symbol"], phase, phase, deployment_id, limit + 1),
        ).fetchall()
    summary = {"sampledCases": min(len(rows), limit), "sampleTruncated": len(rows) > limit,
               "candidateMatches": 0, "eligibleCandidateMatches": 0, "baselineMatches": 0,
               "latestCaseAt": max((row["created_at"] for row in rows[:limit]), default=""),
               "scope": "recent-exact-deployment-cases"}
    for row in rows[:limit]:
        candidates = _json_loads(row.get("candidate_json"), {})
        eligible = set(candidates.get("eligibleHypothesisIds") or [])
        for hypothesis in candidates.get("hypotheses") or []:
            claim = hypothesis.get("claim_contract") or hypothesis.get("claimContract") or {}
            for side, binding_key in (("candidate", "candidateClaim"), ("baseline", "comparisonClaim")):
                binding = plan["baseline"][binding_key]
                if (claim.get("claimContractId") == binding["claimContractId"]
                        and claim_validation_fingerprint(claim) == binding["validationFingerprint"]):
                    summary[side + "Matches"] += 1
                    if side == "candidate" and (hypothesis.get("hypothesis_id") or hypothesis.get("hypothesisId")) in eligible:
                        summary["eligibleCandidateMatches"] += 1
    summary["blockingReason"] = (
        "experiment-inference-not-observed" if not rows else
        "experiment-condition-not-observed-in-sample" if not summary["candidateMatches"] else
        "experiment-candidate-not-eligible" if not summary["eligibleCandidateMatches"] else
        "experiment-input-capture-missing"
    )
    return summary


def read_comparison(plan, *, connect, observed_after=""):
    baseline = plan["baseline"]["comparisonClaim"]
    candidate = plan["baseline"]["candidateClaim"]
    horizon = plan["baseline"].get("comparisonHorizonMinutes")
    if type(horizon) is not int or horizon <= 0:
        return {"status": "needs-data", "reason": "preregistered-comparison-horizon-required", "pairs": []}
    claim_ids = (candidate["claimContractId"], baseline["claimContractId"])
    cutoff = max(canonical_investment_timestamp(plan["createdAt"]), canonical_investment_timestamp(observed_after))
    with connect() as connection:
        rows = connection.execute(
            "WITH anchors AS (SELECT source_abox_snapshot_id, observed_from_at, "
            "ROW_NUMBER() OVER (PARTITION BY LEFT(observed_from_at, 10) ORDER BY observed_from_at, episode_id) AS anchor_rank "
            "FROM investment_hypothesis_observation_episodes "
            "WHERE account_id = %s AND symbol = %s AND claim_contract_id = %s AND observed_from_at >= %s "
            "AND JSON_EXTRACT(payload_json, '$.readiness.eligible') = true), "
            "cohort AS (SELECT source_abox_snapshot_id, observed_from_at FROM anchors WHERE anchor_rank = 1 "
            "ORDER BY observed_from_at LIMIT %s) "
            "SELECT e.payload_json AS episode_json, o.payload_json AS outcome_json "
            "FROM cohort a JOIN investment_hypothesis_observation_episodes e "
            "ON e.source_abox_snapshot_id = a.source_abox_snapshot_id AND e.observed_from_at = a.observed_from_at "
            "LEFT JOIN investment_hypothesis_observation_outcomes o ON o.observation_episode_id = e.episode_id "
            "AND JSON_EXTRACT(o.payload_json, '$.payload.horizonMinutes') = %s "
            "WHERE e.account_id = %s AND e.symbol = %s AND e.claim_contract_id IN (%s, %s) "
            "ORDER BY e.observed_from_at ASC, o.observed_at ASC, o.outcome_id ASC LIMIT %s",
            (plan["accountId"], plan["symbol"], claim_ids[0], cutoff, plan["policy"]["minimumIndependentPairs"], horizon,
             plan["accountId"], plan["symbol"], *claim_ids, plan["policy"]["maximumEvidenceRows"]),
        ).fetchall()
    if len(rows) >= plan["policy"]["maximumEvidenceRows"]:
        return {"status": "needs-data", "reason": "comparison-read-limit-reached", "pairs": []}
    groups, anchors, rejected = {}, {}, 0
    for row in rows or []:
        episode = _json_loads(row.get("episode_json"), {})
        outcome = _json_loads(row.get("outcome_json"), {})
        outcome = {**(outcome.get("payload") or {}), **outcome}
        actual_claim = (episode.get("hypothesis") or {}).get("claimContract") or {}
        side = "candidate" if episode.get("claimContractId") == claim_ids[0] else "baseline"
        expected = candidate if side == "candidate" else baseline
        anchor = (episode.get("sourceAboxSnapshotId"), episode.get("observedFromAt"))
        if side == "candidate" and all(anchor):
            anchors.setdefault(anchor, episode)
        if (claim_validation_fingerprint(actual_claim) != expected["validationFingerprint"]
                or not episode.get("readiness", {}).get("eligible")
                or outcome.get("calibrationEligibility") != "eligible"
                or not episode.get("sourceAboxSnapshotId")
                or outcome.get("sourceAboxSnapshotId") != episode.get("sourceAboxSnapshotId")
                or outcome.get("contractFingerprint") != (episode.get("outcomeContract") or {}).get("contractFingerprint")
                or not outcome.get("contractFingerprint")
                or not outcome.get("outcomeId") or not outcome.get("targetAt")
                or not isinstance(outcome.get("horizonMinutes"), (int, float)) or outcome["horizonMinutes"] <= 0
                or episode.get("accountId") != plan["accountId"] or episode.get("symbol") != plan["symbol"]):
            rejected += 1
            continue
        key = (episode["sourceAboxSnapshotId"], episode.get("observedFromAt"),
               outcome.get("targetAt"), outcome.get("horizonMinutes"), outcome.get("observedAt"))
        groups.setdefault(key, {}).setdefault(side, (episode, outcome))
    pairs = []
    for anchor, anchor_episode in anchors.items():
        matches = [(key, values) for key, values in groups.items()
                   if key[:2] == anchor and set(values) == {"candidate", "baseline"} and key[3] == horizon]
        if len(matches) != 1:
            # Retain a missing early result in the cohort, never replace it with a later winner.
            expected_end = (datetime.fromisoformat(anchor[1].replace('Z', '+00:00')) + timedelta(minutes=horizon)).isoformat()
            pairs.append({"id": "pending:" + anchor[0], "accountId": plan["accountId"], "symbol": plan["symbol"],
                          "candidateFingerprint": plan["fingerprint"], "sourceSnapshotId": anchor[0],
                          "observedFromAt": anchor[1], "observedAt": expected_end, "eligible": False,
                          "independenceKey": anchor_episode.get("marketIndependenceKey"), "pending": True})
            continue
        key, values = matches[0]
        episode, candidate_outcome = values["candidate"]
        baseline_outcome = values["baseline"][1]
        pairs.append({
            "id": str(candidate_outcome.get("outcomeId")) + ":" + str(baseline_outcome.get("outcomeId")),
            "accountId": plan["accountId"], "symbol": plan["symbol"],
            "candidateFingerprint": plan["fingerprint"], "sourceSnapshotId": key[0],
            "observedFromAt": key[1], "observedAt": key[4],
            "candidateOutcome": candidate_outcome.get("selectedHypothesisStatus"),
            "baselineOutcome": baseline_outcome.get("selectedHypothesisStatus"),
            "eligible": True, "independenceKey": episode.get("marketIndependenceKey"),
        })
    return {"status": "ok", "pairs": pairs, "readRowCount": len(rows),
            "provenanceRejectedCount": rejected, "unpairedAnchorCount": sum(bool(row.get("pending")) for row in pairs)}
