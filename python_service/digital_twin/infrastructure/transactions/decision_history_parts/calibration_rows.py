"""Read calibration history in bounded PK batches, without joining audit blobs.

All phases share a read-only snapshot. A missing compact field falls back to
that exact decision only; query failures never become empty outcome history.
"""

from digital_twin.modules.outcomes.domain.decision_calibration_input import DECISION_CALIBRATION_INPUT_VERSION

BATCH_SIZE = 20
FIELDS = ("ai_judgment_json", "insight_assessment_json", "quality_observation_json", "hypotheses_json")


def read_shadow_calibration_rows(connection, where, params):
    """Select the exact ordered outcomes before reading each episode once.

    One statement previously joined every selected outcome to its full audit
    body. Bounded PK reads avoid a large result-transfer deadline and repeated
    episode blobs. A single read snapshot preserves the original join result.
    """
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    connection.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
    try:
        selected = connection.execute(
            "SELECT /* operation:shadow-history-selection */ outcomes.outcome_id, outcomes.observed_at, "
            "outcomes.observation_episode_id FROM investment_hypothesis_observation_outcomes AS outcomes "
            "JOIN investment_hypothesis_observation_episodes AS episodes "
            "ON episodes.episode_id = outcomes.observation_episode_id" + where
            + " ORDER BY outcomes.observed_at DESC, outcomes.outcome_id DESC LIMIT %s",
            tuple(params),
        ).fetchall()
        outcome_ids = [row["outcome_id"] for row in selected]
        episode_ids = list(dict.fromkeys(row["observation_episode_id"] for row in selected))
        outcomes = {}
        episodes = {}
        for ids, table, identity, columns, destination, operation in (
            (outcome_ids, "investment_hypothesis_observation_outcomes", "outcome_id",
             "outcome_id, observation_episode_id AS episode_id, observed_at, payload_json AS outcome_json",
             outcomes, "shadow-outcomes-batch"),
            (episode_ids, "investment_hypothesis_observation_episodes", "episode_id",
             "episode_id, payload_json AS episode_json", episodes, "shadow-episodes-batch"),
        ):
            for offset in range(0, len(ids), BATCH_SIZE):
                batch = ids[offset:offset + BATCH_SIZE]
                rows = connection.execute(
                    "SELECT /* operation:" + operation + " */ " + columns + " FROM " + table
                    + " FORCE INDEX (PRIMARY) WHERE " + identity + " IN ("
                    + ",".join(["%s"] * len(batch)) + ")", tuple(batch),
                ).fetchall()
                destination.update((row[identity], row) for row in rows)
        return [
            {"episode_id": outcome["episode_id"], "observed_at": outcome["observed_at"],
             "outcome_json": outcome["outcome_json"], "episode_json": episode["episode_json"]}
            for item in selected
            for outcome in [outcomes.get(item["outcome_id"])] if outcome
            for episode in [episodes.get(outcome["episode_id"])] if episode
        ]
    finally:
        import sys
        failing = sys.exc_info()[0] is not None
        try:
            connection.execute("ROLLBACK")
        except Exception:
            if not failing:
                raise


def read_calibration_rows(connection, where, params, cutoff):
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    connection.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
    try:
        selected = connection.execute(
            "SELECT episode_id, MAX(observed_at) AS latest_observed_at "
            "FROM investment_decision_outcomes FORCE INDEX (idx_outcome_calibration_selection)"
            + where + " GROUP BY episode_id ORDER BY latest_observed_at DESC,episode_id LIMIT %s",
            tuple(params),
        ).fetchall()
        ids = [row["episode_id"] for row in selected]
        rows = []
        for offset in range(0, len(ids), BATCH_SIZE):
            batch = ids[offset:offset + BATCH_SIZE]
            placeholders = ",".join(["%s"] * len(batch))
            rows.extend(connection.execute(
                "SELECT outcomes.episode_id,outcomes.outcome_id,outcomes.observed_at,outcomes.payload_json AS outcome_json,"
                "episodes.account_id,episodes.symbol,episodes.subject_name,episodes.action,"
                "episodes.selected_hypothesis_id,episodes.decided_at,"
                "JSON_EXTRACT(calibration.hypotheses_json,'$.aiJudgment') AS ai_judgment_json,"
                "JSON_EXTRACT(calibration.hypotheses_json,'$.insightAssessment') AS insight_assessment_json,"
                "JSON_EXTRACT(calibration.hypotheses_json,'$.assistantQualityObservation') AS quality_observation_json,"
                "JSON_EXTRACT(calibration.hypotheses_json,'$.hypotheses') AS hypotheses_json "
                "FROM investment_decision_episodes AS episodes FORCE INDEX(PRIMARY) "
                "STRAIGHT_JOIN investment_decision_outcomes AS outcomes FORCE INDEX(idx_decision_outcomes_episode_time) "
                "ON outcomes.episode_id=episodes.episode_id "
                "LEFT JOIN investment_decision_calibration_inputs AS calibration ON calibration.episode_id=episodes.episode_id "
                "AND calibration.source_updated_at=episodes.updated_at AND calibration.format_version=%s "
                "WHERE episodes.episode_id IN (" + placeholders + ")"
                + (" AND outcomes.observed_at <= %s" if cutoff else ""),
                (DECISION_CALIBRATION_INPUT_VERSION, *batch, *((cutoff,) if cutoff else ())),
            ).fetchall())

        missing = list(dict.fromkeys(row["episode_id"] for row in rows if any(row.get(key) is None for key in FIELDS)))
        originals = {}
        for offset in range(0, len(missing), BATCH_SIZE):
            batch = missing[offset:offset + BATCH_SIZE]
            raw = connection.execute(
                "SELECT episode_id,JSON_OBJECT('insightAssessment',JSON_EXTRACT(payload_json,'$.factsAtDecision.aiJudgment.insightAssessment'),"
                "'insight_assessment',JSON_EXTRACT(payload_json,'$.factsAtDecision.aiJudgment.insight_assessment')) AS ai_judgment_json,"
                "JSON_EXTRACT(payload_json,'$.insightAssessment') AS insight_assessment_json,"
                "JSON_EXTRACT(payload_json,'$.factsAtDecision.assistantQualityObservation') AS quality_observation_json,"
                "JSON_EXTRACT(payload_json,'$.hypothesisSet.hypotheses') AS hypotheses_json "
                "FROM investment_decision_episodes FORCE INDEX(PRIMARY) WHERE episode_id IN ("
                + ",".join(["%s"] * len(batch)) + ")", tuple(batch),
            ).fetchall()
            originals.update((row["episode_id"], row) for row in raw)
        for row in rows:
            for key in FIELDS:
                if row.get(key) is None:
                    row[key] = originals.get(row["episode_id"], {}).get(key)
        ranks = {value: index for index, value in enumerate(ids)}
        rows.sort(key=lambda row: (ranks[row["episode_id"]], row["observed_at"], row.get("outcome_id", "")))
        return rows
    finally:
        # The connection is owned by this read. Do not return an open snapshot
        # to the pool, or replace the original query failure if it disconnected.
        import sys
        failing = sys.exc_info()[0] is not None
        try:
            connection.execute("ROLLBACK")
        except Exception:
            if not failing:
                raise
