"""Real MySQL rollback rehearsals; no providers, AI calls or message transport."""

import json
import time
import uuid
from unittest.mock import patch

from stabilization_database import (
    StabilizationDatabaseCase,
    fixture_episode,
    fixture_snapshot,
)
from digital_twin.modules.decisions.domain.ai_inference_queue import AIInferenceRequest, AIInferenceResult
from digital_twin.modules.decisions.domain.investment_brain import ObservedOutcome
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.infrastructure.transactions import (
    ai_publication,
    decision_history,
    monitoring,
    portfolio,
)
from digital_twin.modules.portfolio.application.portfolio_lifecycle_service import (
    PortfolioAccountingService,
)
from digital_twin.modules.portfolio.application.investment_domain_service import (
    InvestmentDomainService,
)


class TransactionStabilizationTests(StabilizationDatabaseCase):
    def counts(self, tables):
        return {
            table: self.sql("SELECT COUNT(*) AS n FROM " + table)["n"]
            for table in tables
        }

    def ai_request(
        self, source_event_id="fixture-source", generation="generation:fixture"
    ):
        account = "fixture-" + uuid.uuid4().hex[:12]
        context = {
            "accountId": account,
            "rawSymbol": "AAPL",
            "messageType": "investmentInsight",
            "ontologyRelationContext": {
                "subject": {"symbol": "AAPL"},
                "inferenceGenerationId": generation,
                "sourceAboxSnapshotId": "abox:fixture",
            },
        }
        job = NotificationJob.create(
            "Fixture only",
            account_id=account,
            message_type="investmentInsight",
            source_event_id=source_event_id,
            context=context,
        )
        self.notifications.enqueue(job)
        request = AIInferenceRequest.create(
            job, context, model="fixture", reasoning_effort="high"
        )
        self.ai.enqueue(job, request)
        claimed = self.ai.claim("fixture-ai", 1, 60)[0]
        self.assertEqual(request.request_id, claimed.request_id)
        result = AIInferenceResult.create(
            claimed,
            {
                "action": "HOLD",
                "summary": "Storage contract fixture, not a validated opinion.",
            },
            source="fixture",
            validation_state="ready",
            latency_ms=1,
            prompt_bytes=100,
        )
        return job, claimed, result

    def test_monitor_owner_failure_rolls_back_exact_source_and_event_boundary(self):
        snapshot = fixture_snapshot(account="monitor-" + uuid.uuid4().hex[:12])
        recorder = monitoring.MySQLMonitoringCycleRecorder(self.settings)
        tables = [
            "monitor_snapshots",
            "monitor_snapshot_history",
            "monitor_snapshot_reasoning_inputs",
            "verified_reasoning_source_snapshots",
            "domain_events",
            "reasoning_engine_jobs",
        ]
        before = self.counts(tables)
        with patch.object(
            monitoring.reasoning_writes,
            "upsert_reasoning_snapshot_inputs",
            side_effect=RuntimeError("fixture interruption"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture interruption"):
                recorder.record_cycle([snapshot.account_id], [snapshot], [])
        self.assertEqual(before, self.counts(tables))
        self.assertNotIn(snapshot.account_id, recorder.monitor_store.previous)
        recorder.record_cycle([snapshot.account_id], [snapshot], [])
        self.assertIn(snapshot.account_id, recorder.monitor_store.previous)
        self.assertGreater(
            self.counts(tables)["verified_reasoning_source_snapshots"],
            before["verified_reasoning_source_snapshots"],
        )

    def test_portfolio_checkpoint_failure_rolls_back_ledger_analysis_and_outbox(self):
        snapshot = fixture_snapshot(account="portfolio-" + uuid.uuid4().hex[:12])
        service = PortfolioAccountingService(
            self.portfolio,
            investment_domain_service=InvestmentDomainService(self.portfolio),
            settings=self.settings,
        )
        tables = [
            "portfolio_ledger_entries",
            "portfolio_state_snapshots",
            "portfolio_reconciliations",
            "portfolio_snapshot_checkpoints",
            "portfolio_decision_cycles",
            "notification_jobs",
            "domain_events",
        ]
        before = self.counts(tables)
        with patch.object(
            portfolio.portfolio_writes,
            "advance_observation_checkpoint",
            side_effect=RuntimeError("fixture checkpoint failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture checkpoint failure"):
                service.observe_snapshot(snapshot)
        self.assertEqual(before, self.counts(tables))
        saved = service.observe_snapshot(snapshot)
        self.assertEqual("ready", saved["status"])
        self.assertGreater(
            self.counts(tables)["portfolio_ledger_entries"],
            before["portfolio_ledger_entries"],
        )
        replay_counts = self.counts(tables)
        service.observe_snapshot(snapshot)
        self.assertEqual(replay_counts, self.counts(tables))

    def test_decision_event_failure_rolls_back_current_head_and_outcome_owners(self):
        episode = fixture_episode(
            account="decision-" + uuid.uuid4().hex[:12],
            episode_id="episode:" + uuid.uuid4().hex,
        )
        tables = [
            "investment_decision_episodes",
            "investment_flow_current",
            "investment_flow_heads",
            "investment_decision_follow_ups",
            "investment_decision_outcome_targets",
            "domain_events",
        ]
        before = self.counts(tables)
        with patch.object(
            decision_history,
            "insert_domain_event_with_connection",
            side_effect=RuntimeError("fixture outbox failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture outbox failure"):
                self.decisions.save(episode)
        self.assertEqual(before, self.counts(tables))
        self.decisions.save(episode)
        after = self.counts(tables)
        self.assertEqual(
            before["investment_decision_episodes"] + 1,
            after["investment_decision_episodes"],
        )
        self.assertEqual(
            before["investment_decision_follow_ups"] + 1,
            after["investment_decision_follow_ups"],
        )
        self.decisions.save(episode)
        self.assertEqual(after, self.counts(tables))

    def test_ai_late_wrong_owner_does_not_supersede_the_new_claim(self):
        _job, request, result = self.ai_request()
        self.sql(
            "UPDATE ai_inference_requests SET lease_owner = %s WHERE request_id = %s",
            ("replacement-ai", request.request_id),
        )
        before = self.sql(
            "SELECT * FROM ai_inference_requests WHERE request_id = %s",
            (request.request_id,),
        )
        self.assertFalse(
            self.ai.complete(request, "fixture-ai", result, request.context)
        )
        self.assertEqual(
            before,
            self.sql(
                "SELECT * FROM ai_inference_requests WHERE request_id = %s",
                (request.request_id,),
            ),
        )
        self.assertTrue(
            self.ai.complete(request, "replacement-ai", result, request.context)
        )

    def test_ai_publication_failure_rolls_back_result_audit_and_delivery_release(self):
        job, request, result = self.ai_request()
        tables = [
            "ai_inference_results",
            "ai_inference_execution_audits",
            "domain_events",
        ]
        before = self.counts(tables)
        context = {
            **request.context,
            "notificationAiExecutionAudit": {
                "status": "completed",
                "adoptionState": "executed-not-adopted",
            },
        }
        with patch.object(
            ai_publication,
            "insert_domain_event_with_connection",
            side_effect=RuntimeError("fixture publication failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture publication failure"):
                self.ai.complete(request, "fixture-ai", result, context)
        self.assertEqual(before, self.counts(tables))
        self.assertEqual("processing", self.ai.get(request.request_id).status)
        self.assertEqual("awaiting_ai", self.notifications.get(job.job_id).status)
        self.assertTrue(self.ai.complete(request, "fixture-ai", result, context))
        self.assertEqual("completed", self.ai.get(request.request_id).status)
        self.assertEqual("pending", self.notifications.get(job.job_id).status)

    def test_source_to_publication_receipt_replay_retains_exact_lineage(self):
        # A storage handoff fixture, not a substitute for native TypeDB proof tests.
        claimed = self.claimed_job()
        self.jobs.complete(
            claimed["jobId"],
            {"inferenceGenerationId": "generation:fixture"},
            worker_id="fixture-worker",
            claimed_at=claimed["claimedAt"],
        )
        durable = self.sql(
            "SELECT source_event_id, result_json FROM reasoning_engine_jobs WHERE job_id = %s",
            (claimed["jobId"],),
        )
        generation = json.loads(durable["result_json"])["inferenceGenerationId"]
        job, request, result = self.ai_request(durable["source_event_id"], generation)
        episode = fixture_episode(
            account=job.account_id, episode_id="episode:" + request.request_id
        )
        self.assertTrue(
            self.ai.complete(
                request,
                "fixture-ai",
                result,
                request.context,
                before_complete=lambda connection: self.decisions.save(
                    episode, connection=connection
                ),
            )
        )
        self.assertEqual(
            durable["source_event_id"],
            self.notifications.get(job.job_id).source_event_id,
        )
        self.assertEqual(
            generation, self.ai.get(request.request_id).inference_generation_id
        )
        saved = self.notifications.get(job.job_id)
        attempt = self.notifications.start_delivery_attempt(saved, "fixture", "account")
        self.notifications.complete_delivery_attempt(
            saved, attempt, True, provider="fixture transport receipt"
        )
        self.notifications.mark_done(saved)
        self.assertEqual("done", self.notifications.get(job.job_id).status)
        outcome = ObservedOutcome(
            outcome_id="outcome:" + request.request_id,
            episode_id=episode.episode_id,
            observed_at="2026-09-10T02:00:00Z",
            price=102,
            selected_hypothesis_status="pending",
            payload={"source": "storage-contract-fixture", "calibrationEligible": False},
        )
        self.decisions.save_outcome(episode, outcome)
        self.decisions.save_outcome(episode, outcome)
        observed = self.decisions.outcomes_for_episode(episode.episode_id)
        self.assertEqual([outcome.outcome_id], [item.outcome_id for item in observed])
        before = self.counts(
            [
                "investment_decision_episodes",
                "investment_decision_follow_ups",
                "ai_inference_results",
                "domain_events",
            ]
        )
        self.assertFalse(
            self.ai.complete(request, "fixture-ai", result, request.context)
        )
        self.assertEqual(before, self.counts(list(before)))

    def test_isolated_queue_handoff_has_bounded_queries_and_payload(self):
        from digital_twin.infrastructure.mysql_operational_connection import (
            MySQLOperationalConnection,
        )
        from contextlib import contextmanager

        original = MySQLOperationalConnection.transaction
        queries = []

        class MeasuredConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, statement, params=()):
                queries.append(statement.split()[0])
                return self.connection.execute(statement, params)

        @contextmanager
        def transaction(store):
            with original(store) as connection:
                yield MeasuredConnection(connection)

        claimed = self.claimed_job()
        started = time.perf_counter()
        with patch.object(MySQLOperationalConnection, "transaction", transaction):
            result = self.jobs.complete(
                claimed["jobId"],
                {"inferenceGenerationId": "generation:fixture"},
                worker_id="fixture-worker",
                claimed_at=claimed["claimedAt"],
            )
        elapsed = time.perf_counter() - started
        self.handoff_metrics = {
            "fixtureOnly": True,
            "elapsedMs": round(elapsed * 1000, 2),
            "sqlCount": len(queries),
            "responseBytes": len(json.dumps(result).encode()),
        }
        self.assertLessEqual(len(queries), 20, queries)
        self.assertLess(len(json.dumps(result)), 8192)
        # A generous local stall guard, not a production latency guarantee.
        self.assertLess(elapsed, 10)
