import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.modules.reasoning.application.job_transitions import (
    ClaimedJobTransitions,
)
from digital_twin.modules.reasoning.domain.job_claim import ReasoningJobLeaseLost
from stabilization_database import StabilizationDatabaseCase


class ReasoningJobFenceTests(StabilizationDatabaseCase):
    def test_claim_looks_past_incompatible_boundary_without_leasing_or_changing_it(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        boundary = now.isoformat().replace("+00:00", "Z")
        other_boundary = (now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        for index, (symbol, at) in enumerate((("AAPL", boundary), ("NVDA", other_boundary), ("AAPL", boundary))):
            event = self.source(symbol, stamp=at)
            event.payload.update(workClass="GOVERNANCE", importanceGate="non-fungible")
            self.events.handle(event)
            self.sql("UPDATE reasoning_engine_jobs SET created_at=%s WHERE deployment_id=%s AND source_event_id=%s",
                     ((now + timedelta(milliseconds=index)).isoformat().replace("+00:00", "Z"), self.deployment, event.event_id))
        untouched = self.sql("SELECT * FROM reasoning_engine_jobs WHERE deployment_id=%s AND source_snapshot_at=%s",
                             (self.deployment, other_boundary))
        selected = self.jobs.claim(self.deployment, "bounded-worker", 2, 60)
        self.assertEqual(2, len(selected))
        self.assertTrue(all(row["sourceSnapshotAt"] == boundary for row in selected))
        self.assertEqual(untouched, self.sql("SELECT * FROM reasoning_engine_jobs WHERE job_id=%s", (untouched["job_id"],)))

    def test_claim_keeps_explicit_critical_priority_ahead_of_an_aged_job(self):
        from datetime import datetime, timezone, timedelta
        for symbol in ("AAPL", "NVDA"):
            self.events.handle(self.source(symbol))
        rows = self.sql("SELECT job_id FROM reasoning_engine_jobs WHERE deployment_id=%s ORDER BY created_at",
                        (self.deployment,), all_rows=True)
        old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        self.sql("UPDATE reasoning_engine_jobs SET created_at=%s, priority=1 WHERE job_id=%s", (old, rows[0]["job_id"]))
        self.sql("UPDATE reasoning_engine_jobs SET priority=100 WHERE job_id=%s", (rows[1]["job_id"],))
        selected = self.jobs.claim(self.deployment, "fair-worker", 1, 60)
        self.assertEqual(rows[1]["job_id"], selected[0]["jobId"])

    def test_latest_state_turns_keep_durable_history_fairness_sources_and_atomic_claims(self):
        from datetime import datetime, timezone, timedelta
        from digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime import MySQLReasoningEngineJobStore
        from digital_twin.modules.reasoning.infrastructure import mysql_engine_runtime as runtime
        now = datetime.now(timezone.utc)
        stamp = lambda seconds: (now - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
        events = [self.source(symbol, stamp=stamp(age)) for symbol, age in (("AAPL",600),("NVDA",2),("MSFT",1))]
        for event in events:
            self.events.handle(event)
        self.sql("UPDATE reasoning_engine_jobs SET created_at=%s WHERE deployment_id=%s AND source_event_id=%s",
                 (stamp(300), self.deployment, events[0].event_id))
        original = self.sql("SELECT job_id,request_json,source_snapshot_at,source_snapshot_id,source_boundary_json,source_payload_hash,created_at "
                            "FROM reasoning_engine_jobs WHERE deployment_id=%s ORDER BY job_id", (self.deployment,), all_rows=True)
        foreign = self.deployment + ":other"
        self.sql("INSERT INTO reasoning_engine_claim_turns VALUES (%s,2,%s)", (foreign, stamp(0)))
        first = self.jobs.claim(self.deployment, "first-worker", 1, 60)[0]
        self.assertEqual(["MSFT"], first["request"]["symbols"])
        # A worker restart must not reset the fairness debt.
        replacement = MySQLReasoningEngineJobStore(self.settings)
        second = replacement.claim(self.deployment, "replacement-worker", 1, 60)[0]
        self.assertEqual(["NVDA"], second["request"]["symbols"])
        third = replacement.claim(self.deployment, "replacement-worker", 1, 60)[0]
        self.assertEqual(["AAPL"], third["request"]["symbols"])
        self.assertEqual(original, self.sql(
            "SELECT job_id,request_json,source_snapshot_at,source_snapshot_id,source_boundary_json,source_payload_hash,created_at "
            "FROM reasoning_engine_jobs WHERE deployment_id=%s ORDER BY job_id", (self.deployment,), all_rows=True))
        self.assertEqual(2, self.sql("SELECT live_streak FROM reasoning_engine_claim_turns WHERE deployment_id=%s", (foreign,))["live_streak"])
        before = self.sql("SELECT * FROM reasoning_engine_claim_turns WHERE deployment_id=%s", (self.deployment,))
        self.assertEqual([], replacement.claim(self.deployment, "idle-worker", 1, 60))
        self.assertEqual(before, self.sql("SELECT * FROM reasoning_engine_claim_turns WHERE deployment_id=%s", (self.deployment,)))
        self.events.handle(self.source("GOOG", stamp=stamp(0)))
        pending = self.sql("SELECT * FROM reasoning_engine_jobs WHERE deployment_id=%s AND job_status='queued'", (self.deployment,))
        finish = runtime.finish_claim_turn

        def fail_after_cursor(*args):
            finish(*args)
            raise RuntimeError("claim cursor rollback")

        with patch.object(runtime, "finish_claim_turn", side_effect=fail_after_cursor):
            with self.assertRaisesRegex(RuntimeError, "claim cursor rollback"):
                replacement.claim(self.deployment, "failed-worker", 1, 60)
        self.assertEqual(pending, self.sql("SELECT * FROM reasoning_engine_jobs WHERE job_id=%s", (pending["job_id"],)))
        self.assertEqual(before, self.sql("SELECT * FROM reasoning_engine_claim_turns WHERE deployment_id=%s", (self.deployment,)))

        # Mixed/durable and latest-state slots for a shared market subject
        # cannot run in reverse source order, even across different accounts.
        older = self.source("AMZN", stamp=stamp(10))
        older.payload.update(workClass="GOVERNANCE", importanceGate="non-fungible")
        newer = self.source("AMZN", account="another-account", stamp=stamp(1))
        for event in (older, newer):
            self.events.handle(event)
            self.sql("UPDATE reasoning_engine_jobs SET priority=101 WHERE deployment_id=%s AND source_event_id=%s",
                     (self.deployment, event.event_id))
        oldest = replacement.claim(self.deployment, "ordered-worker", 1, 60)[0]
        self.assertEqual(older.event_id, oldest["sourceEventId"])
        self.assertEqual("queued", self.sql("SELECT job_status FROM reasoning_engine_jobs WHERE deployment_id=%s AND source_event_id=%s",
                                          (self.deployment, newer.event_id))["job_status"])
        other = replacement.claim(self.deployment, "second-worker", 1, 60)
        self.assertTrue(all(row["sourceEventId"] != newer.event_id for row in other))
        replacement.retry(oldest["jobId"], "retry after source check", 60,
                          worker_id="ordered-worker", claimed_at=oldest["claimedAt"])
        self.assertEqual([], replacement.claim(self.deployment, "waiting-worker", 1, 60))
        self.sql("UPDATE reasoning_engine_jobs SET available_at='' WHERE job_id=%s", (oldest["jobId"],))
        retried = replacement.claim(self.deployment, "ordered-worker", 1, 60)[0]
        replacement.complete(retried["jobId"], {}, worker_id="ordered-worker", claimed_at=retried["claimedAt"])
        self.assertEqual(newer.event_id, replacement.claim(self.deployment, "latest-worker", 1, 60)[0]["sourceEventId"])

    def test_fenced_job_rejects_every_late_transition_without_changing_new_owner(self):
        job = self.claimed_job("old-worker")
        job_id = job["jobId"]
        self.sql(
            "UPDATE reasoning_engine_jobs SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE job_id = %s",
            (job_id,),
        )
        replacement = self.jobs.claim(self.deployment, "new-worker", 1, 60)[0]
        self.assertNotEqual(job["claimedAt"], replacement["claimedAt"])
        before = self.sql(
            "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job_id,)
        )
        calls = {
            "complete": ({},),
            "retry": ("timeout",),
            "fail": ({}, "failed"),
            "exclude": ({}, "excluded"),
            "supersede": ("newer",),
            "defer": ("wait",),
            "await_world_projection": ({}, "pending"),
            "await_target_scope_repair": ({}, "repair"),
        }
        for name, args in calls.items():
            with (
                self.subTest(transition=name),
                self.assertRaises(ReasoningJobLeaseLost),
            ):
                getattr(self.jobs, name)(
                    job_id, *args, worker_id="old-worker", claimed_at=job["claimedAt"]
                )
            self.assertEqual(
                before,
                self.sql(
                    "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job_id,)
                ),
            )

    def test_fenced_job_attempt_token_protects_same_worker_reclaim(self):
        first = self.claimed_job()
        job_id = first["jobId"]
        self.sql(
            "UPDATE reasoning_engine_jobs SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE job_id = %s",
            (job_id,),
        )
        second = self.jobs.claim(self.deployment, "fixture-worker", 1, 60)[0]
        with self.assertRaisesRegex(ReasoningJobLeaseLost, "different-attempt"):
            self.jobs.complete(
                job_id, {}, worker_id="fixture-worker", claimed_at=first["claimedAt"]
            )
        self.jobs.complete(
            job_id, {}, worker_id="fixture-worker", claimed_at=second["claimedAt"]
        )
        self.assertEqual("completed", self.jobs.get(job_id)["status"])

    def test_fenced_job_terminal_state_cannot_be_retried_after_publication_error(self):
        job = self.claimed_job()
        transitions = ClaimedJobTransitions(self.jobs, "fixture-worker")
        transitions.remember([job])
        transitions.call("complete", job["jobId"], {"durationMs": 12})
        self.assertEqual(
            "lease-lost",
            transitions.retry(job["jobId"], "health update failed", 3)["status"],
        )
        with self.assertRaises(ReasoningJobLeaseLost):
            self.jobs.retry(job["jobId"], "stale callback")
        self.assertEqual("completed", self.jobs.get(job["jobId"])["status"])
        self.assertEqual(0, self.jobs.get(job["jobId"])["attemptCount"])

    def test_fenced_job_heartbeat_cannot_revive_expired_or_previous_attempt(self):
        job = self.claimed_job()
        tokens = {job["jobId"]: job["claimedAt"]}
        self.assertTrue(
            self.jobs.heartbeat(
                [job["jobId"]], "fixture-worker", 60, claimed_tokens=tokens
            )
        )
        self.sql(
            "UPDATE reasoning_engine_jobs SET lease_expires_at = '2000-01-01T00:00:00Z' WHERE job_id = %s",
            (job["jobId"],),
        )
        self.assertFalse(
            self.jobs.heartbeat(
                [job["jobId"]], "fixture-worker", 60, claimed_tokens=tokens
            )
        )
        self.jobs.claim(self.deployment, "fixture-worker", 1, 60)
        self.assertFalse(
            self.jobs.heartbeat(
                [job["jobId"]], "fixture-worker", 60, claimed_tokens=tokens
            )
        )

    def test_fenced_job_binding_and_exhaustion_keep_original_source(self):
        job = self.claimed_job()
        with self.assertRaises(ReasoningJobLeaseLost):
            self.jobs.bind_release(
                [job["jobId"]],
                {"releaseFingerprint": "incorrect"},
                "REALTIME",
                worker_id="other",
            )
        transitions = ClaimedJobTransitions(self.jobs, "fixture-worker")
        transitions.remember([job])
        transitions.bind_release(
            [job["jobId"]], {"releaseFingerprint": "f" * 64}, "REALTIME"
        )
        outcome = transitions.call(
            "await_target_scope_repair",
            job["jobId"],
            {},
            "unrepairable fixture",
            max_attempts=1,
        )
        self.assertTrue(outcome["terminal"])
        saved = self.jobs.get(job["jobId"])
        self.assertEqual("failed", saved["status"])
        self.assertEqual(job["sourceEventId"], saved["sourceEventId"])
        self.assertEqual(job["sourceSnapshotAt"], saved["sourceSnapshotAt"])
        self.assertEqual("f" * 64, saved["releaseFingerprint"])

    def test_fenced_job_coalescing_preserves_transitive_account_source_lineage(self):
        sources = [
            self.source(
                suffix="coalesce-" + str(i), stamp="2026-09-10T01:0" + str(i) + ":00Z"
            )
            for i in range(3)
        ]
        for event in sources:
            self.events.handle(event)
            self.jobs.ingress_event(event)
        job = self.jobs.claim(self.deployment, "fixture-worker", 1, 60)[0]
        lineage = self.sql(
            "SELECT source_event_id, account_id, symbol FROM reasoning_engine_job_sources WHERE survivor_job_id = %s",
            (job["jobId"],),
            True,
        )
        self.assertTrue(
            {e.event_id for e in sources}.issubset(
                {r["source_event_id"] for r in lineage}
            )
        )
        self.assertEqual({"stabilization"}, {r["account_id"] for r in lineage})
        self.assertEqual({"AAPL"}, {r["symbol"] for r in lineage})
        self.jobs.complete(job["jobId"], {}, worker_id="fixture-worker", claimed_at=job["claimedAt"])
        other = self.claimed_job("other-worker", account="another-account")
        self.assertNotEqual(other["scopeKey"], job["scopeKey"])

    def test_fenced_transition_adapter_never_reinvokes_internal_type_error(self):
        queue = SimpleNamespace(retry=Mock(side_effect=TypeError("mutation started")))
        transitions = ClaimedJobTransitions(queue, "worker")
        with self.assertRaisesRegex(TypeError, "mutation started"):
            transitions.call("retry", "job", "error")
        queue.retry.assert_called_once()

    def test_fenced_completion_lost_ack_is_idempotent_at_durable_boundary(self):
        job = self.claimed_job()
        self.jobs.complete(
            job["jobId"],
            {"durationMs": 10},
            worker_id="fixture-worker",
            claimed_at=job["claimedAt"],
        )
        before = self.sql(
            "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],)
        )
        with self.assertRaises(ReasoningJobLeaseLost):
            self.jobs.complete(
                job["jobId"],
                {"durationMs": 999},
                worker_id="fixture-worker",
                claimed_at=job["claimedAt"],
            )
        self.assertEqual(
            before,
            self.sql(
                "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],)
            ),
        )

    def test_completed_receipt_repair_uses_stored_result_without_changing_job(self):
        from digital_twin.infrastructure.transactions.monitoring import (
            MySQLMarketObservationReasoningAnchorStore,
        )

        account = self.deployment
        job = self.claimed_job(account=account)
        values = {
            "accountIds": [account],
            "evaluatedSymbols": ["AAPL"],
            "projectionResults": {
                account: {
                    "sourceAboxSnapshotId": "abox:fixture",
                    "inferenceGenerationId": "generation:fixture",
                },
            },
        }
        self.assertEqual(
            "not-completed", self.jobs.repair_completed_receipts(job["jobId"])["status"]
        )
        self.jobs.complete(
            job["jobId"],
            values,
            worker_id="fixture-worker",
            claimed_at=job["claimedAt"],
        )
        before = self.sql(
            "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],)
        )
        anchors = MySQLMarketObservationReasoningAnchorStore(self.settings)
        with anchors.transaction() as connection:
            anchors.mark_pending_with_connection(
                connection,
                account,
                job["sourceEventId"],
                [
                    {
                        "symbol": "AAPL",
                        "marketObservation": {
                            "currentPrice": 101,
                            "baselinePrice": 100,
                        },
                    },
                ],
                job["sourceSnapshotAt"],
            )
        repair = anchors.repair_completed_reasoning_receipts([job["jobId"]])
        self.assertEqual([], repair["errors"])
        self.assertEqual([job["sourceEventId"]], repair["eventIds"])
        self.assertEqual(
            before,
            self.sql(
                "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],)
            ),
        )
        receipt = self.sql(
            "SELECT * FROM market_observation_reasoning_receipts WHERE source_event_id = %s AND account_id = %s",
            (job["sourceEventId"], account),
        )
        self.assertEqual("generation:fixture", receipt["inference_generation_id"])
        self.assertEqual("abox:fixture", receipt["source_abox_snapshot_id"])
        self.assertEqual(
            0, self.jobs.repair_completed_receipts(job["jobId"])["completedCount"]
        )

    def test_completion_receipt_failure_does_not_advance_terminal_state(self):
        from digital_twin.modules.reasoning.infrastructure import mysql_engine_runtime

        job = self.claimed_job()
        before = self.sql(
            "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],)
        )
        with patch.object(
            mysql_engine_runtime,
            "publish_job_receipts",
            side_effect=RuntimeError("fixture receipt failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture receipt failure"):
                self.jobs.complete(
                    job["jobId"],
                    {},
                    worker_id="fixture-worker",
                    claimed_at=job["claimedAt"],
                )
        self.assertEqual(
            before,
            self.sql(
                "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],)
            ),
        )
