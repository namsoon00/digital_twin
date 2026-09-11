import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.modules.reasoning.application.job_transitions import (
    ClaimedJobTransitions,
)
from digital_twin.modules.reasoning.domain.job_claim import ReasoningJobLeaseLost
from stabilization_database import StabilizationDatabaseCase


class ReasoningJobFenceTests(StabilizationDatabaseCase):
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
