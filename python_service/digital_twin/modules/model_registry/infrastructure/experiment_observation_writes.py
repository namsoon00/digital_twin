"""Bounded experimental capture in the prediction/outcome transaction."""

from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.modules.model_registry.domain.experiment_observations import validate_dataset
from digital_twin.modules.model_registry.domain.ontology_evolution import timestamp, validate_plan
from digital_twin.modules.outcomes.contracts import claim_validation_fingerprint
from .experiment_inputs import read_dataset, validate_dataset_size


def capture_prediction(connection, episode, stamp):
    lineage = episode.input_provenance
    plan_id = lineage.get("experimentPlanFingerprint")
    if not plan_id:
        return
    row = connection.execute(
        "SELECT * FROM ontology_experiment_observation_plans WHERE plan_fingerprint = %s FOR UPDATE", (plan_id,),
    ).fetchone() or {}
    plan = _json_loads(row.get("plan_json"), {})
    if not plan:
        return
    validate_plan(plan)
    if row["status"] != "active" or timestamp(stamp) >= timestamp(row["capture_until"]):
        return
    if (episode.account_id != plan["accountId"] or episode.symbol != plan["symbol"]
            or lineage.get("deploymentId") != row["deployment_id"]):
        raise ValueError("experiment-prediction-scope-mismatch")
    phase = row.get("monitoring_from") or plan["createdAt"]
    if timestamp(episode.observed_from_at) < timestamp(phase):
        return
    bucket = str(int(timestamp(episode.observed_from_at).timestamp() // (plan["policy"]["independenceMinutes"] * 60)))
    side = lineage.get("experimentSide")
    expected = plan["baseline"].get("candidateClaim" if side == "candidate" else "comparisonClaim") or {}
    if (side not in {"candidate", "baseline"} or expected.get("claimContractId") != episode.claim_contract_id
            or expected.get("validationFingerprint") != claim_validation_fingerprint(episode.hypothesis.get("claimContract") or {})):
        raise ValueError("experiment-prediction-contract-mismatch")
    existing = connection.execute(
        "SELECT side, episode_json FROM ontology_experiment_dataset_members "
        "WHERE plan_fingerprint = %s AND phase_at = %s AND bucket_key = %s", (plan_id, phase, bucket),
    ).fetchall()
    if any(item["side"] == side for item in existing):
        return
    # The candidate owns the first anchor, even when its inputs or comparator are missing.
    if side == "baseline":
        anchor = next((item for item in existing if item["side"] == "candidate"), None)
        if not anchor or timestamp(_json_loads(anchor["episode_json"], {})["observedFromAt"]) != timestamp(episode.observed_from_at):
            return
    count = connection.execute(
        "SELECT COUNT(*) AS n FROM ontology_experiment_dataset_members "
        "WHERE plan_fingerprint = %s AND phase_at = %s AND side = 'candidate'", (plan_id, phase),
    ).fetchone()["n"]
    if side == "candidate" and count >= plan["policy"]["minimumIndependentPairs"]:
        return
    boundaries = [item for item in lineage.get("sourceBoundaries") or []
                  if item.get("accountId") == plan["accountId"] and item.get("snapshotId")]
    dataset_id, status, reason = "", "unavailable", "source-boundary-unavailable"
    if len(boundaries) == 1:
        try:
            saved = connection.execute(
                "SELECT payload_json FROM ontology_experiment_datasets "
                "WHERE plan_fingerprint = %s AND source_snapshot_id = %s",
                (plan_id, boundaries[0]["snapshotId"]),
            ).fetchone()
            dataset = validate_dataset(_json_loads(saved["payload_json"], {})) if saved else read_dataset(connection, plan, boundaries[0], stamp)
            if timestamp(dataset["asOf"]) != timestamp(episode.observed_from_at):
                raise ValueError("prediction-source-time-mismatch")
            validate_dataset_size(dataset)
            dataset_id = dataset["datasetId"]
            status = "ready" if all(item["state"] == "ready" for item in dataset["coverage"]) else "incomplete"
            reason = "frozen-point-in-time-input" if status == "ready" else "required-observations-missing"
            connection.execute(
                "INSERT IGNORE INTO ontology_experiment_datasets "
                "(plan_fingerprint, dataset_id, source_snapshot_id, payload_json, created_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (plan_id, dataset_id, boundaries[0]["snapshotId"], json_dumps(dataset), stamp, row["expires_at"]),
            )
        except ValueError as error:
            reason = str(error)[:240]
    connection.execute(
        "INSERT IGNORE INTO ontology_experiment_dataset_members "
        "(plan_fingerprint, phase_at, bucket_key, side, episode_id, dataset_id, input_status, input_reason, "
        "episode_json, outcome_json, created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '{}', %s, %s)",
        (plan_id, phase, bucket, side, episode.episode_id, dataset_id, status, reason,
         json_dumps(episode.to_dict()), stamp, row["expires_at"]),
    )


def capture_outcome(connection, episode, outcome, stamp):
    plan_id = getattr(episode, "input_provenance", {}).get("experimentPlanFingerprint")
    if not plan_id:
        return
    row = connection.execute("SELECT plan_json FROM ontology_experiment_observation_plans WHERE plan_fingerprint = %s", (plan_id,)).fetchone()
    if not row:
        return
    plan = _json_loads(row["plan_json"], {})
    if (outcome.payload or {}).get("horizonMinutes") != plan["baseline"]["comparisonHorizonMinutes"]:
        return
    observed = timestamp(outcome.to_dict().get("observedAt"))
    if not observed or not timestamp(stamp) or observed > timestamp(stamp) or observed <= timestamp(episode.observed_from_at):
        raise ValueError("outcome-clock-order-invalid")
    # Needs-data repairs are governed by the existing outcome writer. Only a final result is frozen here.
    if (outcome.payload or {}).get("calibrationEligibility") != "eligible":
        return
    connection.execute(
        "UPDATE ontology_experiment_dataset_members SET outcome_json = %s "
        "WHERE plan_fingerprint = %s AND episode_id = %s AND outcome_json = '{}' AND expires_at > %s",
        (json_dumps(outcome.to_dict()), plan_id, episode.episode_id, stamp),
    )
