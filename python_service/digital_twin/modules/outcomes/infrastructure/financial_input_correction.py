"""Quarantine mutable tracking/calibration, preserving original decisions."""

import json
from digital_twin.infrastructure.operational_common import json_dumps


def quarantine_financial_input(connection, case, correction, apply=False):
    case_id, candidate_id = case["subject_case_id"], case["candidate_set_id"]
    followups = connection.execute(
        "SELECT f.condition_id, f.payload_json FROM investment_decision_follow_ups f "
        "JOIN investment_ai_insight_episodes e ON e.episode_id = f.episode_id "
        "WHERE e.subject_case_id = %s AND f.status = 'pending'", (case_id,),
    ).fetchall()
    observations = connection.execute(
        "SELECT episode_id, payload_json FROM investment_hypothesis_observation_episodes WHERE candidate_set_id = %s",
        (candidate_id,),
    ).fetchall()
    observations = [row for row in observations if "graph.company." in str(
        json.loads(row["payload_json"]).get("outcomeContract", {}).get("sourceRuleIds") or [])]
    counts = {"followUpsCanceled": len(followups), "targetsExcluded": 0, "outcomesExcluded": 0}
    for row in followups:
        if not apply:
            continue
        payload = json.loads(row["payload_json"])
        payload.update({"status": "canceled", "trackingStatus": "input-corrected", "notificationOnTransition": False,
                        "inputCorrection": correction})
        connection.execute("UPDATE investment_decision_follow_ups SET status = 'canceled', payload_json = %s, updated_at = %s "
                           "WHERE condition_id = %s AND status = 'pending' AND BINARY payload_json = BINARY %s",
                           (json_dumps(payload), correction["correctedAt"], row["condition_id"], row["payload_json"]))
    for observation in observations:
        targets = connection.execute("SELECT target_id, payload_json FROM investment_hypothesis_observation_targets "
                                     "WHERE observation_episode_id = %s AND status = 'pending'", (observation["episode_id"],)).fetchall()
        outcomes = connection.execute("SELECT outcome_id, payload_json FROM investment_hypothesis_observation_outcomes "
                                      "WHERE observation_episode_id = %s", (observation["episode_id"],)).fetchall()
        outcomes = [row for row in outcomes if not json.loads(row["payload_json"]).get("payload", {}).get("inputCorrection")]
        counts["targetsExcluded"] += len(targets)
        counts["outcomesExcluded"] += len(outcomes)
        if not apply:
            continue
        for row in targets:
            payload = json.loads(row["payload_json"])
            payload.update({"status": "excluded", "exclusionReason": correction["reason"], "inputCorrection": correction})
            connection.execute("UPDATE investment_hypothesis_observation_targets SET status = 'excluded', exclusion_reason = %s, "
                               "payload_json = %s, updated_at = %s WHERE target_id = %s AND status = 'pending'",
                               (correction["reason"], json_dumps(payload), correction["correctedAt"], row["target_id"]))
        for row in outcomes:
            payload = json.loads(row["payload_json"])
            detail = payload.setdefault("payload", {})
            detail["inputCorrection"] = {**correction, "previousCalibrationEligibility": detail.get("calibrationEligibility")}
            detail["calibrationEligibility"] = "excluded-financial-input-revalidation"
            connection.execute("UPDATE investment_hypothesis_observation_outcomes SET payload_json = %s WHERE outcome_id = %s "
                               "AND BINARY payload_json = BINARY %s", (json_dumps(payload), row["outcome_id"], row["payload_json"]))
    return counts
