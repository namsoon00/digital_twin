import subprocess
import sys
import unittest
from unittest.mock import patch

from digital_twin.infrastructure.schedulers import (
    IsolatedOntologyReasoningCycle,
    OntologyMaintenanceScheduler,
    OntologyReasoningScheduler,
    OntologyWorldProjectionScheduler,
    PersistentIsolatedOntologyReasoningCycle,
    wait_until_running,
)


class SchedulerWaitTests(unittest.TestCase):
    def test_wait_does_not_pass_negative_duration_after_clock_advances(self):
        sleeps = []
        with patch(
            "digital_twin.infrastructure.schedulers.time.monotonic",
            side_effect=[1.0, 2.0],
        ):
            wait_until_running(lambda: True, 1.5, sleep_fn=sleeps.append)

        self.assertEqual([], sleeps)


class FakeTimeoutProcess:
    pid = 999999
    returncode = None

    def __init__(self):
        self.signals = []
        self.calls = 0

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        self.returncode = -int(value)

    def communicate(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired("ontology-reasoning", timeout, output="partial output")
        return "terminated", ""


class FakeSuccessProcess:
    pid = 999998
    returncode = 0

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        return '{"status":"ok","processedCount":1,"alertCount":0}\n', ""


class FakeStopProcess:
    pid = 999997
    returncode = None

    def __init__(self):
        self.signals = []
        self.calls = 0
        self.cycle = None

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        self.returncode = -int(value)

    def communicate(self, timeout=None):
        self.calls += 1
        if self.calls == 1 and self.cycle:
            self.cycle.stop()
        return "", ""


class FakeRunner:
    def __init__(self):
        self.timeouts = []
        self.orphan_recoveries = 0

    def process_isolation_enabled(self):
        return True

    def execution_timeout_seconds(self):
        return 12

    def execution_timeout_grace_seconds(self):
        return 1

    def record_execution_timeout(self, timeout_seconds, started_at="", output="", worker_id=""):
        self.timeouts.append({"timeoutSeconds": timeout_seconds, "output": output, "workerId": worker_id})
        return {
            "status": "timeout",
            "processedCount": 0,
            "alertCount": 0,
            "retryAfterSeconds": 60,
        }

    def recover_orphaned_mailbox_work(self):
        self.orphan_recoveries += 1
        return {"enabled": True, "recovered": [{"mailboxKey": "key"}]}


class DeferredLowPriorityRunner:
    def __init__(self):
        self.preflight_calls = 0
        self.run_calls = 0

    def reasoning_queue_deferral(self):
        self.preflight_calls += 1
        return {
            "status": "deferred-reasoning-queue",
            "reasoningQueue": {"effectivePendingCount": 2},
        }

    def run_once(self, *_args, **_kwargs):
        self.run_calls += 1
        raise AssertionError("low-priority TypeDB child must not start while reasoning is pending")


class YieldWindowLowPriorityRunner:
    def __init__(self):
        self.preflight_calls = 0

    def reasoning_queue_deferral(self):
        self.preflight_calls += 1
        # A verified inactive-manifest backlog has asked the next reasoning
        # batch to yield. The maintenance scheduler must now attempt its
        # coordinator-protected isolated pass instead of re-deferring itself.
        return {}


class GraceRecoveryRunner(FakeRunner):
    def recover_orphaned_mailbox_work(self):
        self.orphan_recoveries += 1
        return {"enabled": True, "recovered": [], "waitingForGraceCount": 1, "retryAfterSeconds": 12}


class CoordinatorBlockedRunner(FakeRunner):
    def isolated_execution_preflight(self):
        return {
            "ready": False,
            "status": "deferred-projection-coordinator",
            "retryAfterSeconds": 20,
            "reason": "another TypeDB projection is active",
        }


class TimeoutGuardBlockedRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.timeout_guard_calls = 0

    def isolated_timeout_guard_preflight(self):
        self.timeout_guard_calls += 1
        return {
            "ready": False,
            "status": "deferred-timeout-guard",
            "retryAfterSeconds": 240,
            "reason": "a timed-out TypeDB transaction may still be finalising",
            "executionTimeoutGuard": {"status": "open"},
        }


class CountingIsolatedCycle:
    def __init__(self):
        self.calls = 0

    def run_once(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("isolated child must not start while reasoning is pending")


class PersistentCountingCycle:
    persistent_worker = True

    def __init__(self):
        self.calls = 0

    def run_once(self, *_args, **_kwargs):
        self.calls += 1
        return {"status": "ok", "processedCount": 0, "alertCount": 0}


class MaintenanceTimeoutCycle:
    def run_once(self, *_args, **_kwargs):
        return {
            "status": "timeout",
            "timeout": True,
            "durationMs": 30000,
        }


class MaintenancePreemptionCycle:
    def run_once(
        self,
        *_args,
        cancel_requested=None,
        cancel_poll_seconds=0,
        **_kwargs,
    ):
        assert callable(cancel_requested)
        assert cancel_poll_seconds == 1.0
        assert cancel_requested()
        return {
            "status": "preempted-reasoning-queue",
            "preempted": True,
            "durationMs": 1000,
        }


class MaintenanceTimeoutRecoveryRunner:
    def process_isolation_enabled(self):
        return True

    def reasoning_queue_deferral(self):
        return {}

    def recover_dead_projection_leases(self):
        return {
            "status": "cleared",
            "clearedCount": 2,
            "clearedWorldIds": [
                "portfolio:local:default",
                "system:typedb-projection-coordinator",
            ],
        }


class MaintenancePreemptionRunner(MaintenanceTimeoutRecoveryRunner):
    def reasoning_queue_state(self):
        return {"effectivePendingCount": 1}

    @staticmethod
    def queue_pending_count(state):
        return int(state.get("effectivePendingCount") or 0)


class PersistentTimeoutCycle:
    persistent_worker = True

    def __init__(self):
        self.run_calls = 0
        self.recovery_calls = []

    def run_once(self, *_args, **_kwargs):
        self.run_calls += 1
        return {
            "status": "timeout",
            "processedCount": 0,
            "alertCount": 0,
            "timeout": True,
            "timeoutSeconds": 12,
            "durationMs": 12000,
        }

    def recover_dead_leases(self, timeout_seconds, grace_seconds):
        self.recovery_calls.append((timeout_seconds, grace_seconds))
        return {
            "status": "recovered",
            "processedCount": 0,
            "alertCount": 0,
            "typedbDeadLeaseRecovery": {"status": "cleared", "clearedCount": 1},
            "isolatedDurationMs": 31,
        }


class RecoveryAwareRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.dead_lease_recoveries = []

    def record_execution_timeout(
        self,
        timeout_seconds,
        started_at="",
        output="",
        worker_id="",
        dead_lease_recovery=None,
    ):
        self.timeouts.append({"timeoutSeconds": timeout_seconds, "output": output, "workerId": worker_id})
        self.dead_lease_recoveries.append(dict(dead_lease_recovery or {}))
        return {
            "status": "timeout",
            "processedCount": 0,
            "alertCount": 0,
            "retryAfterSeconds": 30,
        }


class OntologyReasoningSchedulerTests(unittest.TestCase):
    def test_isolated_cycle_parses_the_one_shot_json_result(self):
        cycle = IsolatedOntologyReasoningCycle(
            ["python", "service.py", "ontology-reasoning", "once"],
            process_factory=lambda *_args, **_kwargs: FakeSuccessProcess(),
        )

        result = cycle.run_once(limit=3, timeout_seconds=12, grace_seconds=1)

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["processedCount"])
        self.assertTrue(result["isolatedExecution"])

    def test_isolated_cycle_preempts_a_child_when_live_work_arrives(self):
        cycle = IsolatedOntologyReasoningCycle(
            [sys.executable, "-u", "-c", "import time; time.sleep(5)"],
        )

        result = cycle.run_once(
            limit=0,
            timeout_seconds=5,
            grace_seconds=1,
            cancel_requested=lambda: True,
            cancel_poll_seconds=0.05,
        )

        self.assertEqual("preempted-reasoning-queue", result["status"])
        self.assertTrue(result["preempted"])
        self.assertLess(result["durationMs"], 2000)

    def test_persistent_isolated_cycle_reuses_one_warm_child_for_multiple_requests(self):
        worker = """import json
import sys
for raw in sys.stdin:
    request = json.loads(raw)
    result = {'status': 'ok', 'processedCount': request.get('limit', 0), 'alertCount': 0}
    if request.get('action') == 'recover-dead-leases':
        result = {
            'status': 'recovered',
            'processedCount': 0,
            'alertCount': 0,
            'typedbDeadLeaseRecovery': {'status': 'cleared', 'clearedCount': 1},
        }
    print(json.dumps({
        'protocol': 'ontology-reasoning-worker-v1',
        'requestId': request['requestId'],
        'result': result,
    }), flush=True)
"""
        cycle = PersistentIsolatedOntologyReasoningCycle(
            [sys.executable, "-u", "-c", worker],
        )
        try:
            first = cycle.run_once(limit=1, timeout_seconds=5, grace_seconds=1)
            child_pid = cycle.process.pid
            second = cycle.run_once(limit=2, timeout_seconds=5, grace_seconds=1)
            recovery = cycle.recover_dead_leases(timeout_seconds=5, grace_seconds=1)

            self.assertEqual("ok", first["status"])
            self.assertEqual(1, first["processedCount"])
            self.assertEqual("ok", second["status"])
            self.assertEqual(2, second["processedCount"])
            self.assertTrue(first["persistentIsolatedWorker"])
            self.assertEqual("recovered", recovery["status"])
            self.assertEqual(1, recovery["typedbDeadLeaseRecovery"]["clearedCount"])
            self.assertEqual(child_pid, cycle.process.pid)
        finally:
            cycle.close()

    def test_timeout_is_recorded_by_the_parent_and_keeps_work_unacknowledged(self):
        process = FakeTimeoutProcess()
        cycle = IsolatedOntologyReasoningCycle(
            ["python", "service.py", "ontology-reasoning", "once"],
            process_factory=lambda *_args, **_kwargs: process,
        )
        runner = FakeRunner()
        scheduler = OntologyReasoningScheduler(runner, 10, isolated_cycle=cycle)

        result = scheduler.run_once(limit=1)

        self.assertEqual("timeout", result["status"])
        self.assertEqual(60, result["retryAfterSeconds"])
        self.assertEqual(1, len(runner.timeouts))
        self.assertEqual(12, runner.timeouts[0]["timeoutSeconds"])
        self.assertEqual(scheduler.worker_id, runner.timeouts[0]["workerId"])
        self.assertEqual(scheduler.worker_id, cycle.environment["ONTOLOGY_REASONING_WORKER_ID"])
        self.assertEqual(1, runner.orphan_recoveries)
        self.assertEqual(1, len(result["mailboxOrphanLeaseRecovery"]["recovered"]))
        self.assertIn(signal_value("SIGTERM"), process.signals)

    def test_stop_only_signals_the_child_while_communicate_owns_pipe_cleanup(self):
        process = FakeStopProcess()
        cycle = IsolatedOntologyReasoningCycle(
            ["python", "service.py", "ontology-reasoning", "once"],
            process_factory=lambda *_args, **_kwargs: process,
        )
        process.cycle = cycle

        result = cycle.run_once(limit=1, timeout_seconds=12, grace_seconds=1)

        self.assertEqual("stopped", result["status"])
        self.assertTrue(result["stopRequested"])
        self.assertEqual(1, process.calls)
        self.assertIn(signal_value("SIGTERM"), process.signals)

    def test_orphan_lease_grace_defers_before_starting_a_new_typedb_child(self):
        started = []
        cycle = IsolatedOntologyReasoningCycle(
            ["python", "service.py", "ontology-reasoning", "once"],
            process_factory=lambda *_args, **_kwargs: started.append(True) or FakeSuccessProcess(),
        )
        runner = GraceRecoveryRunner()
        scheduler = OntologyReasoningScheduler(runner, 10, isolated_cycle=cycle)

        result = scheduler.run_once(limit=1)

        self.assertEqual("deferred", result["status"])
        self.assertEqual(12, result["retryAfterSeconds"])
        self.assertEqual([], started)
        self.assertEqual(1, runner.orphan_recoveries)

    def test_coordinator_preflight_defers_before_starting_a_new_typedb_child(self):
        child = CountingIsolatedCycle()
        runner = CoordinatorBlockedRunner()
        scheduler = OntologyReasoningScheduler(runner, 10, isolated_cycle=child)

        result = scheduler.run_once(limit=1)

        self.assertEqual("deferred-projection-coordinator", result["status"])
        self.assertEqual(20, result["retryAfterSeconds"])
        self.assertEqual(0, child.calls)

    def test_persistent_cycle_keeps_typedb_preflight_inside_the_killable_child(self):
        child = PersistentCountingCycle()
        runner = CoordinatorBlockedRunner()
        scheduler = OntologyReasoningScheduler(runner, 10, isolated_cycle=child)

        result = scheduler.run_once(limit=1)

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, child.calls)

    def test_timeout_guard_skips_even_a_persistent_child_until_backoff_expires(self):
        child = PersistentCountingCycle()
        runner = TimeoutGuardBlockedRunner()
        scheduler = OntologyReasoningScheduler(runner, 10, isolated_cycle=child)

        result = scheduler.run_once(limit=1)

        self.assertEqual("deferred", result["status"])
        self.assertEqual(240, result["retryAfterSeconds"])
        self.assertEqual(1, runner.timeout_guard_calls)
        self.assertEqual(0, runner.orphan_recoveries)
        self.assertEqual(0, child.calls)
        self.assertEqual("open", result["executionTimeoutGuard"]["status"])

    def test_persistent_timeout_recovers_typedb_leases_in_a_replacement_child(self):
        child = PersistentTimeoutCycle()
        runner = RecoveryAwareRunner()
        scheduler = OntologyReasoningScheduler(runner, 10, isolated_cycle=child)

        result = scheduler.run_once(limit=1)

        self.assertEqual("timeout", result["status"])
        self.assertEqual([(5, 1)], child.recovery_calls)
        self.assertEqual("cleared", runner.dead_lease_recoveries[0]["status"])
        self.assertEqual(1, runner.dead_lease_recoveries[0]["clearedCount"])
        self.assertEqual("cleared", result["timeoutLeaseRecovery"]["status"])

    def test_shared_world_scheduler_skips_isolated_child_when_live_reasoning_is_pending(self):
        runner = DeferredLowPriorityRunner()
        child = CountingIsolatedCycle()
        scheduler = OntologyWorldProjectionScheduler(runner, 10, isolated_cycle=child)

        result = scheduler.run_once(limit=1)

        self.assertEqual("deferred-reasoning-queue", result["status"])
        self.assertEqual(1, runner.preflight_calls)
        self.assertEqual(0, runner.run_calls)
        self.assertEqual(0, child.calls)

    def test_maintenance_scheduler_skips_isolated_child_when_live_reasoning_is_pending(self):
        runner = DeferredLowPriorityRunner()
        child = CountingIsolatedCycle()
        scheduler = OntologyMaintenanceScheduler(runner, 60, isolated_cycle=child)

        result = scheduler.run_once()

        self.assertEqual("deferred-reasoning-queue", result["status"])
        self.assertEqual(1, runner.preflight_calls)
        self.assertEqual(0, runner.run_calls)
        self.assertEqual(0, child.calls)

    def test_maintenance_scheduler_starts_the_bounded_pass_during_a_yield_window(self):
        runner = YieldWindowLowPriorityRunner()
        child = PersistentCountingCycle()
        scheduler = OntologyMaintenanceScheduler(runner, 60, isolated_cycle=child)

        result = scheduler.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, runner.preflight_calls)
        self.assertEqual(1, child.calls)

    def test_maintenance_scheduler_recovers_dead_writer_leases_after_timeout(self):
        scheduler = OntologyMaintenanceScheduler(
            MaintenanceTimeoutRecoveryRunner(),
            60,
            isolated_cycle=MaintenanceTimeoutCycle(),
        )

        result = scheduler.run_once()

        self.assertEqual("timeout", result["status"])
        self.assertEqual("cleared", result["timeoutLeaseRecovery"]["status"])
        self.assertEqual(2, result["timeoutLeaseRecovery"]["clearedCount"])

    def test_maintenance_scheduler_preempts_and_recovers_when_reasoning_arrives(self):
        scheduler = OntologyMaintenanceScheduler(
            MaintenancePreemptionRunner(),
            60,
            isolated_cycle=MaintenancePreemptionCycle(),
        )

        result = scheduler.run_once()

        self.assertEqual("preempted-reasoning-queue", result["status"])
        self.assertEqual("cleared", result["preemptionLeaseRecovery"]["status"])

    def test_maintenance_scheduler_retries_lease_only_deferrals_before_normal_interval(self):
        scheduler = OntologyMaintenanceScheduler(DeferredLowPriorityRunner(), 60)

        self.assertEqual(10.0, scheduler.next_wait_seconds({
            "status": "deferred-reasoning-queue",
            "retryAfterSeconds": 10,
        }))
        self.assertEqual(10.0, scheduler.next_wait_seconds({
            "status": "deferred-pending-abox-activation",
            "retryAfterSeconds": 10,
        }))
        self.assertEqual(60.0, scheduler.next_wait_seconds({"status": "ok"}))

    def test_maintenance_scheduler_reports_reasoning_deferral_with_deduplication(self):
        scheduler = OntologyMaintenanceScheduler(DeferredLowPriorityRunner(), 60)
        result = {
            "status": "deferred-reasoning-queue",
            "reason": "Live reasoning has priority.",
        }

        self.assertTrue(scheduler.should_report(result, 100.0))
        self.assertFalse(scheduler.should_report(result, 200.0))
        self.assertTrue(scheduler.should_report(result, 401.0))


def signal_value(name):
    # Keep the assertion portable across Unix and Windows signal values.
    import signal

    return int(getattr(signal, name))


if __name__ == "__main__":
    unittest.main()
