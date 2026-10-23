"""Native MySQL parity for indexed recovery, including legacy observations."""

from contextlib import contextmanager
import json

from stabilization_database import StabilizationDatabaseCase
from digital_twin.infrastructure.transactions.decision_history_parts.target_queries import (
    PendingTargetRead, pending_outcome_targets,
)

NOW = "2026-10-23T06:30:00Z"
OLD = "2026-10-23T06:00:00Z"
GAP = "excluded-contract-data-gap"


class OutcomeRecoveryReadTests(StabilizationDatabaseCase):
    def target(self, label, *, shadow=True, state="pending", eligibility=GAP,
               recovery=None, target_at=OLD, updated_at=OLD, account=None,
               with_outcome=True, orphan=False):
        account = account or self.deployment
        identity = self.deployment + ":" + label
        episode_id = identity + ":episode"
        outcome_id = identity + ":outcome" if with_outcome else ""
        target_table, outcome_table, episode_column = (
            ("investment_hypothesis_observation_targets", "investment_hypothesis_observation_outcomes", "observation_episode_id")
            if shadow else ("investment_decision_outcome_targets", "investment_decision_outcomes", "episode_id")
        )
        if not shadow and not orphan:
            payload = {"factsAtDecision": {"currentPrice": 123.5, "sourceAsOf": OLD,
                                          "factDelta": {"source_observed_at": OLD}}}
            self.sql("INSERT INTO investment_decision_episodes "
                     "(episode_id,account_id,symbol,decided_at,payload_json,created_at,updated_at) "
                     "VALUES (%s,%s,'AAA',%s,%s,%s,%s)",
                     (episode_id, account, OLD, json.dumps(payload), OLD, OLD))
        if with_outcome:
            payload = {"observedAt": OLD, "price": 125, "payload": {"calibrationEligibility": eligibility}}
            if recovery is not None:
                payload["payload"]["evaluationRecovery"] = recovery
            self.sql("INSERT INTO " + outcome_table + " (outcome_id," + episode_column + ",account_id,symbol,"
                     "observed_at,payload_json,created_at) VALUES (%s,%s,%s,'AAA',%s,%s,%s)",
                     (outcome_id, episode_id, account, OLD, json.dumps(payload), OLD))
        payload = {"requestId": identity, "targetAt": target_at, "symbol": "AAA"}
        self.sql("INSERT INTO " + target_table + " (target_id," + episode_column + ",account_id,symbol,"
                 "target_at,status,outcome_id,payload_json,created_at,updated_at) VALUES (%s,%s,%s,'AAA',%s,%s,%s,%s,%s,%s)",
                 (identity, episode_id, account, target_at, state, outcome_id, json.dumps(payload), OLD, updated_at))
        return identity, outcome_table, outcome_id

    def read(self, *, future=False, limit=200, legacy=False):
        request = PendingTargetRead(self.deployment, NOW, limit, future)
        connect = self.decisions.connect
        if legacy:
            # The pre-optimization SQL is an independent selection oracle.
            @contextmanager
            def connect():
                with self.decisions.connect() as connection:
                    class LegacyReader:
                        def execute(_self, sql, _params):
                            shadow = "STRAIGHT_JOIN investment_hypothesis_observation_targets" in sql
                            target = "investment_hypothesis_observation_targets" if shadow else "investment_decision_outcome_targets"
                            outcome = "investment_hypothesis_observation_outcomes" if shadow else "investment_decision_outcomes"
                            episode_select = "" if shadow else ", episodes.payload_json AS episode_json"
                            episode_join = "" if shadow else " JOIN investment_decision_episodes AS episodes ON episodes.episode_id=targets.episode_id"
                            time_clause = "" if future else " AND targets.target_at <= %s"
                            return connection.execute(
                                "SELECT targets.payload_json, outcomes.payload_json AS outcome_json" + episode_select
                                + " FROM " + target + " AS targets" + episode_join
                                + " LEFT JOIN " + outcome + " AS outcomes ON outcomes.outcome_id=targets.outcome_id "
                                "WHERE targets.account_id=%s AND (targets.status='pending' OR ("
                                "targets.status IN ('needs-data','observed') AND targets.updated_at <= %s "
                                "AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(outcomes.payload_json,'$.payload.evaluationRecovery.automaticRetryStopped')),'false') <> 'true' "
                                "AND COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(outcomes.payload_json,'$.payload.evaluationRecovery.nextRetryAt')),''),targets.updated_at) <= %s "
                                "AND JSON_UNQUOTE(JSON_EXTRACT(outcomes.payload_json,'$.payload.calibrationEligibility')) "
                                "IN ('excluded-contract-data-gap','excluded-criterion-data-gap')))"
                                + time_clause + " ORDER BY targets.target_at,targets.target_id LIMIT %s",
                                (request.account_id, "2026-10-23T06:15:00Z", NOW, *(() if future else (NOW,)), limit),
                            )
                    yield LegacyReader()
        return pending_outcome_targets(request, _connect=connect)

    def test_indexed_recovery_matches_original_rows_cutoffs_and_order(self):
        for shadow in (False, True):
            prefix = str(shadow)
            self.target(prefix + "-pending", shadow=shadow, with_outcome=False)
            self.target(prefix + "-legacy", shadow=shadow, state="observed")
            self.target(prefix + "-gap", shadow=shadow, state="needs-data", eligibility="excluded-criterion-data-gap")
            self.target(prefix + "-stopped", shadow=shadow, state="needs-data", recovery={"automaticRetryStopped": True})
            self.target(prefix + "-backoff", shadow=shadow, state="needs-data", recovery={"nextRetryAt": "2026-10-23T07:00:00Z"})
            self.target(prefix + "-recent", shadow=shadow, state="observed", updated_at="2026-10-23T06:29:00Z")
            self.target(prefix + "-complete", shadow=shadow, state="observed", eligibility="eligible")
            self.target(prefix + "-future", shadow=shadow, target_at="2026-10-24T00:00:00Z")
            self.target(prefix + "-other", shadow=shadow, account="another-account")
        for future in (False, True):
            for limit in (1, 3, 200):
                with self.subTest(future=future, limit=limit):
                    self.assertEqual(self.read(future=future, limit=limit, legacy=True),
                                     self.read(future=future, limit=limit))
        rows = self.read()
        self.assertEqual(6, len(rows))
        self.assertEqual(123.5, next(r for r in rows if r["episodeKind"] == "decision")["decisionPrice"])

    def test_generated_index_tracks_repair_and_rollback_atomically(self):
        for shadow in (False, True):
            identity, table, outcome = self.target(str(shadow), shadow=shadow, state="observed")
            self.assertIn(identity, [r["requestId"] for r in self.read()])
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with self.decisions.transaction() as connection:
                    connection.execute("UPDATE " + table + " SET payload_json=JSON_SET(payload_json,"
                                       "'$.payload.evaluationRecovery',JSON_OBJECT('automaticRetryStopped',JSON_EXTRACT('true','$'))) WHERE outcome_id=%s", (outcome,))
                    self.assertEqual(0, connection.execute("SELECT recovery_candidate FROM " + table + " WHERE outcome_id=%s", (outcome,)).fetchone()["recovery_candidate"])
                    raise RuntimeError("rollback")
            self.assertEqual(1, self.sql("SELECT recovery_candidate FROM " + table + " WHERE outcome_id=%s", (outcome,))["recovery_candidate"])
            self.sql("UPDATE " + table + " SET payload_json=JSON_SET(payload_json,'$.payload.calibrationEligibility','eligible') WHERE outcome_id=%s", (outcome,))
            self.assertNotIn(identity, [r["requestId"] for r in self.read()])

    def test_pending_payload_and_orphan_semantics_survive_branch_limits(self):
        self.target("0-orphan", shadow=False, orphan=True)
        identity, _, _ = self.target("1-pending", shadow=False, recovery={"automaticRetryStopped": True})
        self.target("2-legacy", shadow=False, state="observed", recovery={"nextRetryAt": ""})
        self.assertEqual(self.read(limit=1, legacy=True), self.read(limit=1))
        row = self.read(limit=1)[0]
        self.assertEqual(identity, row["requestId"])
        self.assertTrue(row["previousOutcome"]["payload"]["evaluationRecovery"]["automaticRetryStopped"])

    def test_sparse_recovery_selection_uses_index_and_returns_same_limit(self):
        from digital_twin.infrastructure.transactions.decision_history_parts.target_queries import pending_target_selection
        for index in range(80):
            self.target("done-%03d" % index, state="observed", eligibility="eligible")
        self.target("recover", state="observed")
        self.assertEqual(self.read(legacy=True), self.read())
        selection, params = pending_target_selection(PendingTargetRead(self.deployment, NOW, 20, False), "2026-10-23T06:15:00Z", shadow=True)
        with self.decisions.connect() as connection:
            rows = connection.execute("EXPLAIN FORMAT=TRADITIONAL " + selection, params).fetchall()
        self.assertTrue(any(r.get("key") == "idx_outcome_recovery_candidate" for r in rows), rows)
        self.assertTrue(any(r.get("key") == "idx_outcome_target_recovery" for r in rows), rows)
