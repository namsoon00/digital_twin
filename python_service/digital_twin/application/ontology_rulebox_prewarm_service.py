"""Background preparation of TypeDB-native RuleBox schema functions.

The active RuleBox is an investment-policy contract. Its generated TypeDB
functions are a compiled implementation detail, so compilation belongs to a
separate bounded worker and must never be started by a live alert inference.
When a receipt is cold, the live path can use its bounded direct-TypeQL
fallback.  Schema commits are deliberately kept out of a live queue: TypeDB
can continue compiling a commit after a client deadline, so forcing a compiler
turn into an aged backlog makes the queue worse rather than recovering it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Dict


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def _integer_setting(
    settings: Dict[str, object],
    key: str,
    fallback: int,
    lower: int,
    upper: int,
) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


class OntologyRuleboxPrewarmRunner:
    """Run one bounded full-RuleBox schema preparation pass at a time."""

    def __init__(
        self,
        ontology_repository,
        settings: Dict[str, object] = None,
        reasoning_queue_probe=None,
        now_provider: Callable[[], datetime] = None,
    ):
        self.ontology_repository = ontology_repository
        self.settings = dict(settings or {})
        self.reasoning_queue_probe = reasoning_queue_probe
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def enabled(self) -> bool:
        # A live inference must never be the process that discovers a cold
        # RuleBox function.  The dedicated worker owns that compilation and
        # the reasoning runner waits for its durable receipt instead.
        value = str(self.settings.get("ontologyRuleboxPrewarmEnabled") or "1").strip().lower()
        return value not in DISABLED_VALUES

    def defer_when_reasoning_pending(self) -> bool:
        value = str(
            self.settings.get("ontologyRuleboxPrewarmDeferWhenReasoningPending") or "1"
        ).strip().lower()
        return value not in DISABLED_VALUES

    def backlog_recovery_enabled(self) -> bool:
        """Expose aged-backlog compiler risk in diagnostics.

        This flag no longer authorizes a schema write while live work is
        pending.  It remains a compatibility setting so operators can see
        when a cold RuleBox coincides with an old durable queue.
        """
        value = str(
            self.settings.get("ontologyRuleboxPrewarmBacklogRecoveryEnabled") or "0"
        ).strip().lower()
        return value not in DISABLED_VALUES

    def backlog_recovery_age_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmBacklogRecoveryAgeSeconds",
            90,
            15,
            24 * 60 * 60,
        )

    def backlog_recovery_min_pending_entries(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmBacklogRecoveryMinPendingEntries",
            2,
            1,
            100000,
        )

    def backlog_recovery_retry_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmBacklogRecoveryRetrySeconds",
            5,
            3,
            60,
        )

    def reasoning_queue_state(self) -> Dict[str, object]:
        probe = self.reasoning_queue_probe
        if not callable(probe):
            return {"status": "not-supported", "effectivePendingCount": 0}
        try:
            payload = probe()
            return dict(payload or {}) if isinstance(payload, dict) else {
                "status": "invalid",
                "effectivePendingCount": 0,
            }
        except Exception as error:  # noqa: BLE001 - an unsafe compiler pass must not block alerts.
            return {
                "status": "error",
                "effectivePendingCount": 0,
                "reason": str(error)[:180],
            }

    @staticmethod
    def pending_reasoning_count(payload: Dict[str, object]) -> int:
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        candidates = [
            values.get("effectivePendingCount"),
            values.get("mailboxPendingEntryCount"),
            values.get("pendingEntryCount"),
            # A claimed or retrying entry is still live alert work.  The
            # compact queue state deliberately stores those counters apart
            # from ``pendingEntryCount``; treating either as an empty queue
            # can start a global TypeDB schema compilation halfway through a
            # recovery pass.
            values.get("runningEntryCount"),
            values.get("retryingEntryCount"),
        ]
        parsed = []
        for candidate in candidates:
            try:
                parsed.append(max(0, int(float(candidate or 0))))
            except (TypeError, ValueError):
                continue
        return max(parsed or [0])

    @staticmethod
    def waiting_reasoning_count(payload: Dict[str, object]) -> int:
        """Count work waiting behind the current projection, not its lease."""
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        mailbox = values.get("mailbox") if isinstance(values.get("mailbox"), dict) else {}
        candidates = [
            values.get("effectivePendingCount"),
            values.get("mailboxPendingEntryCount"),
            values.get("pendingEntryCount"),
            mailbox.get("pendingEntryCount"),
        ]
        parsed = []
        for candidate in candidates:
            try:
                parsed.append(max(0, int(float(candidate or 0))))
            except (TypeError, ValueError):
                continue
        return max(parsed or [0])

    @staticmethod
    def active_reasoning_count(payload: Dict[str, object]) -> int:
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        mailbox = values.get("mailbox") if isinstance(values.get("mailbox"), dict) else {}
        candidates = [
            values.get("runningEntryCount"),
            values.get("retryingEntryCount"),
            mailbox.get("runningEntryCount"),
            mailbox.get("retryingEntryCount"),
        ]
        parsed = []
        for candidate in candidates:
            try:
                parsed.append(max(0, int(float(candidate or 0))))
            except (TypeError, ValueError):
                continue
        return max(parsed or [0])

    def oldest_pending_age_seconds(self, payload: Dict[str, object]) -> int:
        """Read the durable queue age without assuming a specific probe shape."""
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        mailbox = values.get("mailbox") if isinstance(values.get("mailbox"), dict) else {}
        dispatch = values.get("queueDispatch") if isinstance(values.get("queueDispatch"), dict) else {}
        direct_ages = [
            values.get("oldestWaitSeconds"),
            values.get("oldestPendingAgeSeconds"),
            dispatch.get("oldestWaitSeconds"),
            dispatch.get("oldestPendingAgeSeconds"),
            mailbox.get("oldestPendingAgeSeconds"),
        ]
        parsed_ages = []
        for candidate in direct_ages:
            try:
                parsed_ages.append(max(0, int(float(candidate or 0))))
            except (TypeError, ValueError):
                continue
        stamps = [
            values.get("oldestRequestAt"),
            values.get("oldestPendingAt"),
            dispatch.get("oldestRequestAt"),
            mailbox.get("oldestPendingAt"),
        ]
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        for stamp in stamps:
            raw = str(stamp or "").strip()
            if not raw:
                continue
            try:
                observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            parsed_ages.append(max(
                0,
                int((now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()),
            ))
        return max(parsed_ages or [0])

    def backlog_recovery_state(self, payload: Dict[str, object]) -> Dict[str, object]:
        waiting = self.waiting_reasoning_count(payload)
        oldest_age = self.oldest_pending_age_seconds(payload)
        minimum_pending = self.backlog_recovery_min_pending_entries()
        age_threshold = self.backlog_recovery_age_seconds()
        enabled = self.backlog_recovery_enabled()
        return {
            "enabled": enabled,
            "waitingEntryCount": waiting,
            "activeEntryCount": self.active_reasoning_count(payload),
            "oldestPendingAgeSeconds": oldest_age,
            "minimumPendingEntries": minimum_pending,
            "ageThresholdSeconds": age_threshold,
            "eligible": bool(
                enabled
                and waiting >= minimum_pending
                and oldest_age >= age_threshold
            ),
        }

    def interval_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmIntervalSeconds",
            15,
            5,
            3600,
        )

    def idle_quiet_seconds(self) -> int:
        """Require a sustained empty queue before opening a schema writer.

        TypeDB schema function commits can take minutes and are not reliably
        cancelled by disconnecting their client.  A momentarily empty mailbox
        is not enough evidence that it is safe to begin one; the scheduler
        owns the durable quiet-period check before it starts an isolated
        compiler child.
        """
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmIdleQuietSeconds",
            300,
            30,
            24 * 60 * 60,
        )

    def execution_timeout_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmExecutionTimeoutSeconds",
            1500,
            30,
            1800,
        )

    def execution_timeout_grace_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmExecutionTimeoutGraceSeconds",
            10,
            1,
            120,
        )

    def process_isolation_enabled(self) -> bool:
        value = str(
            self.settings.get("ontologyRuleboxPrewarmProcessIsolationEnabled") or "1"
        ).strip().lower()
        return value not in DISABLED_VALUES

    def status(self) -> Dict[str, object]:
        result = {
            "enabled": self.enabled(),
            "intervalSeconds": self.interval_seconds(),
            "idleQuietSeconds": self.idle_quiet_seconds(),
            "executionTimeoutSeconds": self.execution_timeout_seconds(),
            "executionTimeoutGraceSeconds": self.execution_timeout_grace_seconds(),
            "processIsolationEnabled": self.process_isolation_enabled(),
            "deferWhenReasoningPending": self.defer_when_reasoning_pending(),
            "backlogRecoveryEnabled": self.backlog_recovery_enabled(),
            "backlogRecoveryAgeSeconds": self.backlog_recovery_age_seconds(),
            "backlogRecoveryMinPendingEntries": self.backlog_recovery_min_pending_entries(),
            "backlogRecoveryRetrySeconds": self.backlog_recovery_retry_seconds(),
        }
        reader = getattr(self.ontology_repository, "schema_function_prewarm_status", None)
        if not callable(reader):
            result["prewarm"] = {
                "status": "unsupported",
                "functionsReady": False,
                "reason": "Ontology repository has no TypeDB schema-function prewarm capability.",
            }
            return result
        try:
            result["prewarm"] = dict(reader() or {})
        except Exception as error:  # noqa: BLE001 - status must not stop the worker process.
            result["prewarm"] = {
                "status": "error",
                "functionsReady": False,
                "reason": str(error)[:220],
            }
        return result

    def prewarm_readiness(self) -> Dict[str, object]:
        """Read the durable prewarm receipt without beginning compilation."""
        reader = getattr(self.ontology_repository, "schema_function_prewarm_status", None)
        if not callable(reader):
            return {
                "status": "unsupported",
                "functionsReady": False,
                "reason": "Ontology repository has no TypeDB schema-function prewarm capability.",
            }
        try:
            payload = reader()
            return dict(payload or {}) if isinstance(payload, dict) else {
                "status": "error",
                "functionsReady": False,
                "reason": "TypeDB schema-function prewarm returned an invalid readiness payload.",
            }
        except Exception as error:  # noqa: BLE001 - the bounded worker can retry its own read.
            return {
                "status": "error",
                "functionsReady": False,
                "reason": str(error)[:220],
            }

    def run_once(self, force: bool = False) -> Dict[str, object]:
        if not self.enabled():
            return {
                "status": "disabled",
                "configured": True,
                "functionsReady": False,
                "reason": "RuleBox schema-function prewarm worker is disabled.",
                "durationMs": 0,
            }
        queue = self.reasoning_queue_state()
        pending = self.pending_reasoning_count(queue)
        recovery = self.backlog_recovery_state(queue)
        # A TypeDB schema commit can keep its compiler busy after a client has
        # timed out or an isolated worker has exited.  Starting one while an
        # alert queue is waiting therefore turns a bounded recovery task into
        # a minutes-long server-wide stall.  Preserve the latest-state queue,
        # let live inference use the direct TypeQL path, and run compilation
        # only during a genuinely idle interval (or an explicit force pass).
        if (
            self.defer_when_reasoning_pending()
            and pending
            and not force
        ):
            # Do not even read TypeDB deployment receipts here. A readiness
            # check opens RuleBox and receipt reads for both namespaces and
            # therefore competes with the exact workload this branch is
            # protecting. The compact queue probe is sufficient to yield;
            # readiness will be checked only after the queue is quiet.
            return {
                "status": (
                    "deferred-aged-reasoning-backlog"
                    if recovery.get("eligible")
                    else "deferred-reasoning-pending"
                ),
                "configured": True,
                "functionsReady": None,
                "pendingRuleCount": None,
                "reasoningPendingCount": pending,
                "reasoningQueue": queue,
                "backlogRecovery": recovery,
                "prewarmReadinessDeferred": True,
                "reason": (
                    "Live ontology reasoning is pending; the RuleBox compiler does not open a TypeDB schema "
                    "transaction. Any cold function receipt uses the bounded direct TypeQL fallback until the "
                    "durable queue is empty."
                ),
                "recommendedRetryAfterSeconds": self.interval_seconds(),
                "durationMs": 0,
            }
        if self.defer_when_reasoning_pending() and str(queue.get("status") or "") == "error" and not force:
            return {
                "status": "deferred-reasoning-queue-probe",
                "configured": True,
                "functionsReady": False,
                "pendingRuleCount": 0,
                "reasoningQueue": queue,
                "reason": (
                    "Live reasoning queue state could not be confirmed; RuleBox schema compilation is deferred "
                    "to avoid competing with an alert."
                ),
                "recommendedRetryAfterSeconds": self.interval_seconds(),
                "durationMs": 0,
            }
        prewarm = getattr(self.ontology_repository, "prewarm_typedb_native_rule_functions", None)
        if not callable(prewarm):
            return {
                "status": "unsupported",
                "configured": False,
                "functionsReady": False,
                "reason": "Ontology repository has no TypeDB schema-function prewarm capability.",
                "durationMs": 0,
            }
        started_at = time.perf_counter()
        try:
            result = dict(prewarm(force=bool(force)) or {})
        except Exception as error:  # noqa: BLE001 - the next bounded pass can retry safely.
            result = {
                "status": "error",
                "configured": True,
                "functionsReady": False,
                "reason": str(error)[:220],
            }
        result.setdefault("durationMs", int((time.perf_counter() - started_at) * 1000))
        result.setdefault("background", True)
        result.setdefault("liveInferenceDeploymentAvoided", True)
        if recovery.get("eligible"):
            result["queuePriority"] = True
            result["backlogRecovery"] = recovery
            result["reasoningPendingCount"] = pending
            if str(result.get("status") or "") in {
                "provisioning",
                "deferred-projection-coordinator",
            }:
                # A staged RuleBox contains small, durable batches. Polling
                # quickly here is safe and lets it retain priority until the
                # compiled path can release the waiting queue.
                result["recommendedRetryAfterSeconds"] = self.backlog_recovery_retry_seconds()
        return result
