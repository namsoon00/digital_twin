"""Finite-lived experiment evidence, separate from one-day operating caches."""

from datetime import timedelta

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.modules.model_registry.domain.ontology_evolution import validate_plan, timestamp
from digital_twin.modules.model_registry.domain.experiment_observations import validate_dataset, validate_requirements
from digital_twin.modules.portfolio.contracts import utc_now_iso
from .experiment_inputs import capabilities, read_dataset, validate_dataset_size


def active_observation_plans(connection, account_id, symbol, deployment_id, observed_at):
    rows = connection.execute(
        "SELECT plan_fingerprint, plan_json, monitoring_from FROM ontology_experiment_observation_plans WHERE account_id = %s AND symbol = %s "
        "AND deployment_id = %s AND status = 'active' AND capture_until > %s ORDER BY created_at LIMIT 2",
        (account_id, symbol, deployment_id, observed_at),
    ).fetchall()
    result = []
    for row in rows:
        plan = _json_loads(row["plan_json"], {})
        phase = row.get("monitoring_from") or plan["createdAt"]
        if not timestamp(observed_at) or timestamp(observed_at) < timestamp(phase):
            continue
        bucket = str(int(timestamp(observed_at).timestamp() // (plan["policy"]["independenceMinutes"] * 60)))
        member = connection.execute(
            "SELECT episode_json FROM ontology_experiment_dataset_members WHERE plan_fingerprint = %s "
            "AND phase_at = %s AND bucket_key = %s AND side = 'candidate'", (plan["fingerprint"], phase, bucket),
        ).fetchone()
        if member and timestamp(_json_loads(member["episode_json"], {})["observedFromAt"]) != timestamp(observed_at):
            continue
        count = connection.execute("SELECT COUNT(*) AS n FROM ontology_experiment_dataset_members WHERE plan_fingerprint = %s AND phase_at = %s AND side = 'candidate'", (plan["fingerprint"], phase)).fetchone()["n"]
        if member or count < plan["policy"]["minimumIndependentPairs"]:
            result.append(plan)
    return result


class MySQLExperimentObservationStore(MySQLOperationalConnection):
    def collector_cadence_seconds(self):
        configured = getattr(self, "runtime_settings", {}).get("monitorAccountIntervalSeconds") or 180
        return max(30, int(configured))

    def prepare(self, plan):
        validate_plan(plan)
        requirements = validate_requirements(plan["observationRequirements"])
        available = capabilities(self.collector_cadence_seconds())
        unsupported = [{**row, "state": "unsupported", "reason": "collector-or-retention-unsupported"}
                       for row in requirements["inputs"]
                       if row["metric"] not in available or row["lookbackMinutes"] > 1440
                       or (row["minimumSamples"] > 1 and row["cadenceSeconds"] < self.collector_cadence_seconds())
                       or row["minimumSamples"] > min(1000, row["lookbackMinutes"] * 60 // self.collector_cadence_seconds() + 1)]
        if unsupported:
            return {"state": "unsupported", "requirements": unsupported, "automaticCollection": False}
        with self.connect() as connection:
            source = connection.execute(
                "SELECT snapshot_id, account_id, generated_at FROM verified_reasoning_source_snapshots "
                "WHERE account_id = %s AND mode = 'live' ORDER BY generated_at DESC LIMIT 1", (plan["accountId"],),
            ).fetchone()
            if not source:
                return {"state": "future-collection", "reason": "verified-source-required"}
            try:
                dataset = read_dataset(connection, plan, {"snapshotId": source["snapshot_id"], "accountId": source["account_id"]}, utc_now_iso())
            except ValueError as error:
                state = "unsupported" if str(error) == "experiment-input-read-limit" else "future-collection"
                return {"state": state, "reason": str(error)}
        try:
            validate_dataset_size(dataset)
        except ValueError as error:
            return {"state": "unsupported", "reason": str(error), "automaticCollection": False}
        rows = [{key: value for key, value in item.items() if key != "samples"} for item in dataset["coverage"]]
        for row in rows:
            if row["state"] == "historical-unrecoverable":
                # Old samples cannot be invented; a *new* rolling window can still be collected.
                row["state"] = "future-collection"
        return {"state": "ready" if all(item["state"] == "ready" for item in rows) else "future-collection",
                "requirements": rows, "asOf": dataset["asOf"], "futureMeasurements": requirements["outcomes"],
                "collector": "existing-monitor-and-outcome-workers"}

    def register(self, plan, deployment_id):
        validate_plan(plan)
        if not plan.get("observationRequirements"):
            raise ValueError("observation-contract-required")
        end = timestamp(plan["createdAt"]) + timedelta(days=plan["policy"]["maximumShadowDays"] * 2)
        expiry = end + timedelta(days=plan["policy"].get("experimentEvidenceRetentionDays", 7))
        with self.transaction() as connection:
            connection.execute(
                "INSERT IGNORE INTO ontology_experiment_observation_plans "
                "(plan_fingerprint, account_id, symbol, deployment_id, status, plan_json, monitoring_from, "
                "created_at, capture_until, expires_at) VALUES (%s, %s, %s, %s, 'active', %s, '', %s, %s, %s)",
                (plan["fingerprint"], plan["accountId"], plan["symbol"], deployment_id, json_dumps(plan),
                 plan["createdAt"], end.isoformat(), expiry.isoformat()),
            )

    def monitoring(self, plan, adopted_at):
        if not timestamp(adopted_at):
            raise ValueError("monitoring-cutoff-required")
        with self.transaction() as connection:
            connection.execute(
                "UPDATE ontology_experiment_observation_plans SET monitoring_from = %s "
                "WHERE plan_fingerprint = %s AND monitoring_from = ''", (adopted_at, plan["fingerprint"]),
            )

    def close(self, plan):
        expiry = (timestamp(utc_now_iso()) + timedelta(days=plan["policy"].get("experimentEvidenceRetentionDays", 7))).isoformat()
        with self.transaction() as connection:
            connection.execute("UPDATE ontology_experiment_observation_plans SET status = 'closed', expires_at = LEAST(expires_at, %s) WHERE plan_fingerprint = %s", (expiry, plan["fingerprint"]))
            for table in ("ontology_experiment_datasets", "ontology_experiment_dataset_members"):
                connection.execute("UPDATE " + table + " SET expires_at = LEAST(expires_at, %s) WHERE plan_fingerprint = %s", (expiry, plan["fingerprint"]))

    def cleanup(self, now=None):
        stamp = now or utc_now_iso()
        counts = {}
        with self.transaction() as connection:
            for table in ("ontology_experiment_dataset_members", "ontology_experiment_datasets", "ontology_experiment_observation_plans"):
                counts[table] = connection.execute("DELETE FROM " + table + " WHERE expires_at <= %s LIMIT 500", (stamp,)).rowcount
        return counts

    def comparison(self, plan, observed_after=""):
        validate_plan(plan)
        phase = observed_after or plan["createdAt"]
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT m.*, d.payload_json AS dataset_json FROM ontology_experiment_dataset_members m "
                "LEFT JOIN ontology_experiment_datasets d ON d.plan_fingerprint = m.plan_fingerprint AND d.dataset_id = m.dataset_id "
                "WHERE m.plan_fingerprint = %s AND m.phase_at = %s AND m.expires_at > %s ORDER BY m.bucket_key, m.side LIMIT %s",
                (plan["fingerprint"], phase, utc_now_iso(), plan["policy"]["minimumIndependentPairs"] * 2),
            ).fetchall()
        groups, pairs, summary = {}, [], {"capturedInputs": 0, "unavailableInputs": 0, "pendingOutcomes": 0}
        for row in rows:
            groups.setdefault(row["bucket_key"], {})[row["side"]] = row
        for bucket, group in groups.items():
            candidate = group.get("candidate")
            if not candidate:
                continue
            episode = _json_loads(candidate["episode_json"], {})
            pair = {"id": candidate["episode_id"], "accountId": plan["accountId"], "symbol": plan["symbol"],
                    "candidateFingerprint": plan["fingerprint"], "sourceSnapshotId": candidate["dataset_id"] or "missing:" + candidate["episode_id"],
                    "observedFromAt": episode["observedFromAt"], "observedAt": "", "eligible": False,
                    "independenceKey": episode.get("marketIndependenceKey") or bucket,
                    "inputState": candidate["input_status"], "inputReason": candidate["input_reason"]}
            try:
                dataset = validate_dataset(_json_loads(candidate.get("dataset_json"), {}))
                summary["capturedInputs"] += 1
                pair["datasetFingerprint"] = dataset["fingerprint"]
            except ValueError:
                summary["unavailableInputs"] += 1
                pairs.append(pair)
                continue
            other = group.get("baseline") or {}
            outcomes = [_json_loads(item.get("outcome_json"), {}) for item in (candidate, other)]
            payloads = [{**(item.get("payload") or {}), **item} for item in outcomes]
            base_episode = _json_loads(other.get("episode_json"), {})
            valid = bool(other.get("dataset_id") == candidate["dataset_id"] and
                         base_episode.get("observedFromAt") == episode.get("observedFromAt") and
                         all(item.get("input_status") == "ready" for item in (candidate, other)))
            for obs, outcome in zip((episode, base_episode), payloads):
                valid = bool(valid and outcome.get("calibrationEligibility") == "eligible"
                             and outcome.get("contractFingerprint") == (obs.get("outcomeContract") or {}).get("contractFingerprint")
                             and outcome.get("sourceAboxSnapshotId") == obs.get("sourceAboxSnapshotId")
                             and outcome.get("horizonMinutes") == plan["baseline"]["comparisonHorizonMinutes"]
                             and outcome.get("outcomeId"))
            valid = bool(valid and payloads[0].get("targetAt") == payloads[1].get("targetAt")
                         and payloads[0].get("observedAt") == payloads[1].get("observedAt"))
            pair.update({"eligible": valid, "candidateOutcome": payloads[0].get("selectedHypothesisStatus"),
                         "baselineOutcome": payloads[1].get("selectedHypothesisStatus"),
                         "observedAt": payloads[0].get("observedAt") or ""})
            if not valid:
                summary["pendingOutcomes"] += 1
            pairs.append(pair)
        return {"status": "ok", "pairs": pairs, "dataSummary": summary,
                "replayScope": "source-packets-and-observed-claims"}
