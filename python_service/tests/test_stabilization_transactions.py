"""Real MySQL rollback rehearsals; no providers, AI calls or message transport."""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
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
from digital_twin.modules.outcomes.infrastructure.transaction_writes import upsert_decision_outcome_target
from digital_twin.infrastructure.transactions.decision_history_parts.target_queries import PendingTargetRead, pending_outcome_targets
from digital_twin.modules.model_registry.domain.hypothesis_development import AUTHORING_SCHEDULE_VERSION, HypothesisDevelopmentCase
from digital_twin.modules.model_registry.infrastructure.mysql_hypothesis_development import MySQLHypothesisDevelopmentStore


class TransactionStabilizationTests(StabilizationDatabaseCase):
    def assert_legacy_authoring_selection_and_ready_age_match_after_recovery(self):
        store = MySQLHypothesisDevelopmentStore(self.settings)
        self.addCleanup(self.sql, "DELETE FROM hypothesis_development_cases WHERE case_id IN (%s,%s,%s,%s,%s,%s)",
                        ("legacy", "blocked", "exhausted", "enrolled", "running", "closed"))
        for name, status, retry, evolution in [
            ("legacy", "needs-revision", {}, {}),
            ("blocked", "blocked", {"authoringAttempts": 2}, {}),
            ("exhausted", "blocked", {"authoringAttempts": 3}, {}),
            ("enrolled", "needs-revision", {"authoringScheduleVersion": AUTHORING_SCHEDULE_VERSION}, {}),
            ("running", "blocked", {}, {"plan": {"planId": "active"}}),
            ("closed", "rejected", {}, {}),
        ]:
            store.save(HypothesisDevelopmentCase(
                case_id=name, fingerprint=name, account_id="fixture", symbol="MSTR",
                title="Fixture", claim="Fixture", status=status, retry=retry, evolution=evolution,
            ))
        candidates = store.authoring_recovery_candidates(AUTHORING_SCHEDULE_VERSION, 3)
        self.assertEqual({"legacy", "blocked"}, {case.case_id for case in candidates})
        self.assertEqual(1, len(store.authoring_recovery_candidates(AUTHORING_SCHEDULE_VERSION, 3, limit=1)))
        self.assertEqual([], store.pending())
        case = store.get("legacy")
        due = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
        case.retry.update({"state": "authoring-retry", "authoringScheduleVersion": AUTHORING_SCHEDULE_VERSION,
                           "nextCheckAt": due})
        store.save(case)
        restarted = MySQLHypothesisDevelopmentStore(self.settings)
        self.assertEqual(["legacy"], [case.case_id for case in restarted.pending()])
        self.assertEqual(due, restarted.oldest_ready_at())
        self.assertEqual(["blocked"], [case.case_id for case in restarted.authoring_recovery_candidates(AUTHORING_SCHEDULE_VERSION, 3)])
        self.sql("DELETE FROM hypothesis_development_cases WHERE case_id IN (%s,%s,%s,%s,%s,%s)",
                 ("legacy", "blocked", "exhausted", "enrolled", "running", "closed"))

    def test_ready_hypothesis_selection_survives_restart_and_skips_future_backlog(self):
        self.assert_legacy_authoring_selection_and_ready_age_match_after_recovery()
        store = MySQLHypothesisDevelopmentStore(self.settings)
        prefix = "schedule:" + uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        future = (now + timedelta(hours=3)).isoformat()
        due = (now - timedelta(hours=1)).isoformat()
        for index in range(60):
            case = HypothesisDevelopmentCase(case_id=prefix + str(index), fingerprint=prefix + str(index),
                                             account_id="fixture", symbol="MSTR", title="Fixture", claim="Fixture only", status="needs-data",
                                             retry={"nextCheckAt": future, "lastCheckedAt": now.isoformat()})
            store.save(case)
        ready = HypothesisDevelopmentCase(case_id=prefix + "ready", fingerprint=prefix + "ready",
                                          account_id="fixture", symbol="MSTR", title="Fixture", claim="Fixture only", status="needs-data",
                                          retry={"nextCheckAt": due, "lastCheckedAt": now.isoformat()})
        store.save(ready)
        restarted = MySQLHypothesisDevelopmentStore(self.settings)
        self.assertEqual([ready.case_id], [case.case_id for case in restarted.pending(limit=1)])
        self.assertEqual(due.replace("+00:00", "Z"), restarted.oldest_ready_at())
        row = self.sql("SELECT next_check_at, last_checked_at FROM hypothesis_development_cases WHERE case_id = %s", (ready.case_id,))
        self.assertEqual(due.replace("+00:00", "Z"), row["next_check_at"])
        self.assertEqual(now.isoformat().replace("+00:00", "Z"), row["last_checked_at"])
        with store.processing_lock(ready.case_id) as first:
            with restarted.processing_lock(ready.case_id) as second:
                self.assertTrue(first)
                self.assertFalse(second)
        ready.status = "needs-revision"
        store.save(ready)
        self.assertEqual([], restarted.pending(limit=1))
        self.assertEqual("", restarted.oldest_ready_at())

    def outcome_target(self, episode, target_at="2099-01-01T01:00:00Z"):
        target_id = "target:" + episode.episode_id
        payload = {"requestId": target_id, "episodeId": episode.episode_id, "symbol": episode.symbol,
                   "horizonMinutes": 60, "baselineAt": episode.decided_at, "targetAt": target_at}
        with self.decisions.transaction() as connection:
            upsert_decision_outcome_target(connection, target_id, episode, 60, target_at, 120,
                                           "contract:fixture", "pending", "", payload, episode.decided_at)
        return target_id

    def test_compact_baseline_is_write_once_account_scoped_and_survives_reschedule(self):
        episode = fixture_episode(account="baseline:" + uuid.uuid4().hex, episode_id="episode:" + uuid.uuid4().hex)
        self.decisions.save(episode)
        target_id = self.outcome_target(episode)
        def read_targets(include_future):
            return pending_outcome_targets(PendingTargetRead(episode.account_id, "2026-09-12T00:00:00Z", 200, include_future), _connect=self.decisions.connect)
        self.assertEqual([], read_targets(False))
        targets = read_targets(True)
        self.assertIn(target_id, [item["requestId"] for item in targets])
        record = {"requestId": target_id, "kind": "benchmark", "observation": {"currentPrice": 200, "sourceAsOf": episode.decided_at}}
        self.assertEqual(0, self.decisions.record_outcome_baselines("different-account", [record]))
        self.assertEqual(1, self.decisions.record_outcome_baselines(episode.account_id, [record]))
        record["observation"]["currentPrice"] = 999
        self.assertEqual(0, self.decisions.record_outcome_baselines(episode.account_id, [record]))
        self.outcome_target(episode)
        target = next(item for item in read_targets(True) if item["requestId"] == target_id)
        self.assertEqual(200, target["baselineObservations"]["benchmark"]["currentPrice"])

    def test_gap_outcome_repair_is_atomic_and_completed_observation_is_immutable(self):
        episode = fixture_episode(account="repair:" + uuid.uuid4().hex, episode_id="episode:" + uuid.uuid4().hex)
        self.decisions.save(episode)
        target_id = self.outcome_target(episode, "2026-09-10T02:00:00Z")
        outcome = ObservedOutcome(outcome_id="outcome:" + episode.episode_id, episode_id=episode.episode_id,
                                  observed_at="2026-09-10T02:00:00Z", price=110,
                                  payload={"calibrationEligibility": "excluded-criterion-data-gap", "decisionPrice": 100,
                                           "contractFingerprint": "contract:fixture", "horizonMinutes": 60})
        self.decisions.save_outcome(episode, outcome)
        self.sql("UPDATE investment_decision_outcome_targets SET updated_at = %s WHERE target_id = %s", ("2026-09-10T02:01:00Z", target_id))
        target = self.decisions.pending_outcome_targets(episode.account_id, "2026-09-12T00:00:00Z")[0]
        self.assertEqual(110, target["previousOutcome"]["price"])
        invalid = ObservedOutcome.from_dict(outcome.to_dict())
        invalid.price = 999
        with self.assertRaises(ValueError):
            self.decisions.save_outcome(episode, invalid)
        self.assertEqual("needs-data", self.sql("SELECT status FROM investment_decision_outcome_targets WHERE target_id = %s", (target_id,))["status"])
        repaired = ObservedOutcome.from_dict(outcome.to_dict())
        repaired.payload.update({"calibrationEligibility": "eligible", "benchmarkReturnPct": 2})
        self.decisions.save_outcome(episode, repaired)
        self.assertEqual("observed", self.sql("SELECT status FROM investment_decision_outcome_targets WHERE target_id = %s", (target_id,))["status"])
        self.assertEqual([], self.decisions.pending_outcome_targets(episode.account_id, "2026-09-12T00:00:00Z"))
        returned = self.decisions.save_outcome(episode, invalid)
        self.assertEqual(110, returned.price)
        stored = self.decisions.outcomes_for_episode(episode.episode_id)[0]
        self.assertEqual("eligible", stored.payload["calibrationEligibility"])
        self.assertEqual(1, len(stored.payload["evaluationHistory"]))

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
