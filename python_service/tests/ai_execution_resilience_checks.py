"""Execution failures, bounded recovery and price-reference regressions."""
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.domain.execution_resilience import (
    AIExecutionDeferred, AIExecutionError, admit_execution, finish_execution,
    process_diagnostic, recovery_wait, execution_health, instant, safe_diagnostic,
)
from digital_twin.modules.ai_orchestration.infrastructure.execution import execute_ai_work
from digital_twin.modules.notifications.domain.observation_price_basis import (
    PRICE_PRESENTATION_VERSION, observation_price_basis,
)

NOW = datetime(2026, 10, 7, 6, tzinfo=timezone.utc)
FAILURE = process_diagnostic(SimpleNamespace(returncode=1, stderr="connection reset", stdout=""))


class ExecutionResilienceChecks(unittest.TestCase):
    def check_error_signals_exclude_secrets_and_successful_model_content(self):
        for message, category in [("You've hit your usage limit", "quota"), ("HTTP 401 unauthorized", "authentication"),
                                  ("rate_limit_exceeded", "rate-limit"), ("connection reset", "network"),
                                  ("service unavailable", "provider-unavailable"), ("invalid schema", "invalid-request")]:
            with self.subTest(category=category):
                d = process_diagnostic(SimpleNamespace(returncode=1, stderr=message + " token=private-secret account=private-owner", stdout=""))
                self.assertEqual(category, d["category"])
                self.assertNotIn("private", json.dumps(d))
                self.assertEqual(64, len(d["diagnosticHash"]))
        self.assertIsNone(process_diagnostic(SimpleNamespace(returncode=0, stderr="", stdout='{"summary":"usage limit"}')))
        self.assertIsNone(process_diagnostic(SimpleNamespace(returncode=0, stderr="", stdout='{"type":[],"summary":"valid model JSON"}')))
        event = '{"type":"turn.failed","error":{"message":"insufficient_quota private-secret"}}'
        self.assertEqual("quota", process_diagnostic(SimpleNamespace(returncode=0, stderr="", stdout=event))["category"])
        recovered = '{"type":"error","message":"stream disconnected; reconnecting"}\n{"type":"turn.completed"}'
        self.assertIsNone(process_diagnostic(SimpleNamespace(returncode=0, stderr="", stdout=recovered)))
        self.assertNotIn("private", json.dumps(safe_diagnostic({"category": "quota", "message": "private-secret",
            "stderr": "private-secret", "diagnosticHash": "private-secret", "signal": "private-secret"})))

    def check_consecutive_failures_open_then_only_one_probe_can_recover(self):
        state = {}
        for i in range(3):
            state, ticket, wait = admit_execution(state, NOW, str(i))
            self.assertIsNone(wait)
            state = finish_execution(state, ticket, NOW, "failure", FAILURE)
        self.assertEqual(NOW + timedelta(seconds=60), instant(state["blockedUntil"]))
        state, ticket, wait = admit_execution(state, NOW, "blocked")
        self.assertIsNone(ticket)
        self.assertIsInstance(wait, AIExecutionDeferred)
        later = NOW + timedelta(seconds=61)
        state, probe, wait = admit_execution(state, later, "only-probe")
        self.assertIsNone(wait)
        _, ticket, wait = admit_execution(state, later, "other-worker")
        self.assertIsNone(ticket)
        self.assertIsNotNone(wait)
        failed = finish_execution(state, probe, later, "failure", FAILURE)
        self.assertEqual(later + timedelta(seconds=120), instant(failed["blockedUntil"]))
        state = finish_execution(state, probe, later, "success")
        self.assertEqual(0, state["failures"])
        self.assertIsNone(recovery_wait(state, later))

    def check_late_success_and_expired_probe_cannot_erase_newer_failure(self):
        state, slow, _ = admit_execution({}, NOW, "slow")
        state, fast, _ = admit_execution(state, NOW, "fast")
        state = finish_execution(state, fast, NOW, "failure", FAILURE)
        self.assertEqual(state, finish_execution(state, slow, NOW, "success"))
        quota = {**FAILURE, "category": "quota"}
        state, ticket, _ = admit_execution(state, NOW, "quota")
        state = finish_execution(state, ticket, NOW, "failure", quota)
        due = instant(state["blockedUntil"]) + timedelta(seconds=1)
        state, abandoned, _ = admit_execution(state, due, "crashed-worker")
        expired = instant(state["probeUntil"]) + timedelta(seconds=1)
        self.assertEqual(state, finish_execution(state, abandoned, expired, "success"))
        state, current, _ = admit_execution(state, expired, "replacement")
        self.assertEqual(state, finish_execution(state, abandoned, expired, "success"))
        state = finish_execution(state, current, expired, "cancelled")
        self.assertEqual(2, state["failures"])
        self.assertIsNone(recovery_wait(state, expired))
        self.assertTrue(state["blockedUntil"])

    def check_open_gate_never_invokes_or_counts_a_model_call(self):
        store, invoke = Mock(), Mock()
        store.acquire_execution.side_effect = AIExecutionDeferred("2026-10-07T06:15:00Z", "quota")
        with patch("digital_twin.modules.ai_orchestration.infrastructure.execution._store", return_value=store):
            with self.assertRaises(AIExecutionDeferred):
                execute_ai_work("news-analysis", "private-prompt", invoke, {})
        invoke.assert_not_called()
        store.begin_call.assert_not_called()

    def check_nonzero_and_failed_turn_are_audited_as_failures(self):
        store = Mock(); store.begin_call.return_value = "call"
        for code, stdout, stderr in [(1, "", "unauthorized private-secret"),
                                     (0, '{"type":"turn.failed","error":{"message":"usage limit"}}', "")]:
            with self.subTest(code=code), patch("digital_twin.modules.ai_orchestration.infrastructure.execution._store", return_value=store):
                with self.assertRaises(AIExecutionError):
                    execute_ai_work("news-analysis", "private-prompt", lambda: SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr), {})
                self.assertTrue(store.finish_call.call_args.args[1].startswith("ai-execution:"))
                self.assertNotIn("private", repr(store.finish_call.call_args))
                self.assertEqual("failure", store.finish_execution.call_args.args[1])

    def check_recovery_wait_defers_before_graph_capture_and_does_not_fail_task(self):
        import test_ai_control as helpers
        service, store, planner = helpers.AIControlTests().runner()
        store.execution_wait.side_effect = AIExecutionDeferred("2026-10-07T06:15:00Z", "quota")
        self.assertEqual("recovery-wait", service.run_once()["status"])
        service.evidence.assert_not_called(); planner.assert_not_called(); store.fail.assert_not_called()
        store.defer_execution.assert_called_once()
        store.execution_wait.side_effect = None
        self.assertEqual("completed", service.run_once()["status"])
        service.evidence.assert_called_once()

    def check_health_separates_active_process_from_success_and_backlog(self):
        failed = [{"workload": "independent-observation", "status": "failed", "count": 155}]
        self.assertEqual("degraded", execution_health(NOW, {}, {}, failed, 0)["status"])
        calls = failed + [{"workload": "independent-observation", "status": "completed", "count": 86}]
        self.assertEqual("recovering", execution_health(NOW, {}, {}, calls, 0)["status"])
        self.assertEqual("delayed", execution_health(NOW, {}, {}, calls, 1)["status"])
        self.assertEqual("paused", execution_health(NOW, {}, {}, calls, 1, False)["status"])
        self.assertEqual("idle", execution_health(NOW, {}, {}, [], 0)["status"])
        successful = [{"workload": "independent-observation", "status": "completed", "count": 2}]
        self.assertEqual("awaiting-result", execution_health(NOW, {}, {}, successful, 0)["status"])
        self.assertEqual("healthy", execution_health(NOW, {}, {"lastObservationCheckAt": NOW.isoformat()}, successful, 0)["status"])

    def check_reference_prices_never_become_current_from_freshness_alone(self):
        quote = {"judgementEvidenceUsable": True}
        assessment = {"status": "fresh", "referenceState": "last-close"}
        self.assertFalse(observation_price_basis(quote, assessment)["currentUseAllowed"])
        self.assertEqual("last-close-reference", observation_price_basis(quote, assessment)["purpose"])
        for change in ({"sourceTimestampState": "queried-at-fallback"}, {"sourceTimestampPresent": False}, {"judgementEvidenceUsable": False}):
            self.assertFalse(observation_price_basis({**quote, **change}, {"status": "fresh"})["currentUseAllowed"])
        self.assertFalse(observation_price_basis(quote, {"status": "stale"})["currentUseAllowed"])
        self.assertFalse(observation_price_basis(quote, {})["currentUseAllowed"])
        self.assertFalse(observation_price_basis({}, {"status": "fresh"})["currentUseAllowed"])

    def check_new_price_presentation_is_explicit_and_legacy_body_is_stable(self):
        from ai_insight_fixtures import observation
        from test_observation_source_clock import aged_packet, CAPTURE
        from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
        result = {**observation(), "input": aged_packet(), "observedAt": CAPTURE}
        original = copy.deepcopy(result)
        legacy = render_ai_observation(result)
        upgraded = {**result, "pricePresentationVersion": PRICE_PRESENTATION_VERSION}
        message = render_ai_observation(upgraded)
        self.assertIn("과거 참고 가격 기준", message)
        self.assertLess(message.index("과거 참고 가격 기준"), message.index(result["summary"]))
        self.assertIn("과거 참고 가격:", message)
        self.assertEqual(original, result)
        self.assertEqual(legacy, render_ai_observation(result))
