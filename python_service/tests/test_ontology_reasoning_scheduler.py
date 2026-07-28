import subprocess
import unittest

from digital_twin.infrastructure.schedulers import (
    IsolatedOntologyReasoningCycle,
    OntologyMaintenanceScheduler,
    OntologyReasoningScheduler,
    OntologyWorldProjectionScheduler,
)


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


class CountingIsolatedCycle:
    def __init__(self):
        self.calls = 0

    def run_once(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("isolated child must not start while reasoning is pending")


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


def signal_value(name):
    # Keep the assertion portable across Unix and Windows signal values.
    import signal

    return int(getattr(signal, name))


if __name__ == "__main__":
    unittest.main()
