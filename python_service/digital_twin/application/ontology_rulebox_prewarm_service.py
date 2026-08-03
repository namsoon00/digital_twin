"""Background preparation of TypeDB-native RuleBox schema functions.

The active RuleBox is an investment-policy contract. Its generated TypeDB
functions are a compiled implementation detail, so compilation belongs to a
separate bounded worker and must never be started by a live alert inference.
Normal deployments fail closed while a RuleBox receipt is cold: raw source
observations can continue, but investment inference waits for a verified
compiled RuleBox. When that safety gate has deferred work and no inference
lease is active, this worker receives a coordinator-protected bootstrap turn
instead of waiting for the mailbox to become empty forever.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
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
        prewarm_state_store=None,
        storage_guard=None,
    ):
        self.ontology_repository = ontology_repository
        self.settings = dict(settings or {})
        self.reasoning_queue_probe = reasoning_queue_probe
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        # Live reasoning needs only a cheap signal that a schema compiler is
        # active. Production wires this to MySQL, never to a TypeDB read.
        self.prewarm_state_store = prewarm_state_store
        self.storage_guard = storage_guard

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
        """Allow a safe compiler recovery for an aged, unleased queue.

        The recovery never overlaps a running reasoning lease.  The shared
        TypeDB projection coordinator still serializes the narrow hand-off if
        a new reasoning worker races this decision.
        """
        value = str(
            self.settings.get("ontologyRuleboxPrewarmBacklogRecoveryEnabled") or "1"
        ).strip().lower()
        return value not in DISABLED_VALUES

    def direct_typeql_fallback_enabled(self) -> bool:
        """Return the explicit compatibility fallback setting.

        The setting is retained for controlled compatibility rollouts. It does
        not bypass the normal readiness gate unless that gate is explicitly
        disabled through ``ontologyRuleboxPrewarmRequireReadyForInference``.
        """
        value = str(
            self.settings.get("typedbNativeRuleDirectQueryFallbackEnabled") or "1"
        ).strip().lower()
        return value not in DISABLED_VALUES

    def require_ready_for_inference(self) -> bool:
        """Require verified functions before a native investment judgement.

        This defaults to enabled so a TypeDB restart or a RuleBox deployment
        cannot silently switch the live path to a slower, differently-shaped
        direct TypeQL execution mode. Operators can still opt out during a
        controlled compatibility migration.
        """
        value = str(
            self.settings.get("ontologyRuleboxPrewarmRequireReadyForInference") or "1"
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
        """Count only leases that can currently execute TypeDB work.

        A ``retrying`` item has no lease and is deliberately held by
        ``not_before_at``.  It is safe to prewarm in that interval because a
        concurrent reasoning claim must acquire the same TypeDB projection
        coordinator before it can begin native inference.
        """
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        mailbox = values.get("mailbox") if isinstance(values.get("mailbox"), dict) else {}
        candidates = [
            values.get("runningEntryCount"),
            mailbox.get("runningEntryCount"),
        ]
        parsed = []
        for candidate in candidates:
            try:
                parsed.append(max(0, int(float(candidate or 0))))
            except (TypeError, ValueError):
                continue
        return max(parsed or [0])

    @staticmethod
    def retrying_reasoning_count(payload: Dict[str, object]) -> int:
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        mailbox = values.get("mailbox") if isinstance(values.get("mailbox"), dict) else {}
        candidates = [
            values.get("retryingEntryCount"),
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
        direct_fallback_enabled = self.direct_typeql_fallback_enabled()
        strict_readiness = self.require_ready_for_inference()
        # A normal production deployment now requires verified functions even
        # if the legacy fallback flag remains configured. Keep the old
        # fallback-only behavior available only when the explicit readiness
        # gate has been disabled.
        enabled = self.backlog_recovery_enabled() and (
            strict_readiness or not direct_fallback_enabled
        )
        active = self.active_reasoning_count(payload)
        eligible = bool(
            enabled
            and waiting >= minimum_pending
            and oldest_age >= age_threshold
        )
        return {
            "enabled": enabled,
            "directTypeqlFallbackEnabled": direct_fallback_enabled,
            "strictReadinessGateEnabled": strict_readiness,
            "waitingEntryCount": waiting,
            "activeEntryCount": active,
            "retryingEntryCount": self.retrying_reasoning_count(payload),
            "oldestPendingAgeSeconds": oldest_age,
            "minimumPendingEntries": minimum_pending,
            "ageThresholdSeconds": age_threshold,
            "eligible": eligible,
            "canRecover": bool(eligible and active == 0),
        }

    def cold_bootstrap_state(self, payload: Dict[str, object]) -> Dict[str, object]:
        """Allow prewarm to make progress after a safe cold-start deferral.

        A strict live worker defers before it acquires a TypeDB inference
        lease. That leaves mailbox entries waiting or retrying, not running.
        Treating every pending entry as active therefore creates a circular
        wait: inference waits for functions while the compiler waits for an
        empty queue. The only safe escape is a compiler pass with no active
        inference lease. The shared TypeDB projection coordinator remains the
        final concurrency guard if a race occurs.
        """
        waiting = self.waiting_reasoning_count(payload)
        active = self.active_reasoning_count(payload)
        queue_status = str(dict(payload or {}).get("status") or "").strip().lower()
        strict_readiness = self.require_ready_for_inference()
        activity = self.prewarm_activity_state()
        last_result = (
            dict(activity.get("lastResult") or {})
            if isinstance(activity.get("lastResult"), dict)
            else {}
        )
        last_functions_ready = bool(last_result.get("functionsReady"))
        activity_status = str(activity.get("status") or "").strip().lower()
        bootstrap_required = (
            activity_status == "bootstrap-required"
            or not last_functions_ready
        )
        return {
            "strictReadinessGateEnabled": strict_readiness,
            "waitingEntryCount": waiting,
            "activeEntryCount": active,
            "queueStatus": queue_status or "unknown",
            "prewarmActivityStatus": activity_status or "unknown",
            "lastFunctionsReady": last_functions_ready,
            "bootstrapRequired": bootstrap_required,
            "eligible": bool(
                strict_readiness
                and waiting > 0
                and active == 0
                and queue_status != "error"
                and bootstrap_required
            ),
            "canBootstrap": bool(
                strict_readiness
                and waiting > 0
                and active == 0
                and queue_status != "error"
                and bootstrap_required
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

    def prewarm_activity_lease_seconds(self) -> int:
        """Keep a stale compiler signal bounded when an isolated child dies."""
        return max(
            60,
            min(
                3600,
                self.execution_timeout_seconds()
                + self.execution_timeout_grace_seconds()
                + 60,
            ),
        )

    def interrupted_compiler_cooldown_seconds(self) -> int:
        """Allow TypeDB to finish a schema commit after a client disconnect."""
        return max(300, min(900, self.execution_timeout_seconds()))

    def activity_now(self) -> datetime:
        current = self.now_provider()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def prewarm_activity_state(self) -> Dict[str, object]:
        """Read the bounded compiler hand-off without opening TypeDB.

        The state is intentionally advisory: an expired or unavailable record
        never prevents the next worker from recovering.  While it is active,
        however, another schema writer would only contend with the TypeDB
        compiler that is already rebuilding its type cache.
        """
        store = self.prewarm_state_store
        reader = getattr(store, "load", None)
        if not callable(reader):
            return {"active": False, "status": "not-configured"}
        try:
            payload = reader()
        except Exception as error:  # noqa: BLE001 - a missing hint must not stall recovery.
            return {
                "active": False,
                "status": "error",
                "reason": str(error)[:180],
            }
        result = dict(payload or {}) if isinstance(payload, dict) else {}
        status = str(result.get("status") or "").strip().lower()
        try:
            expires_at_epoch = float(result.get("expiresAtEpoch") or 0)
        except (TypeError, ValueError):
            expires_at_epoch = 0.0
        if not expires_at_epoch:
            raw_expiry = str(result.get("expiresAt") or "").strip()
            if raw_expiry:
                try:
                    expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    expires_at_epoch = expiry.astimezone(timezone.utc).timestamp()
                except ValueError:
                    expires_at_epoch = 0.0
        remaining = max(0.0, expires_at_epoch - self.activity_now().timestamp())
        raw_active = result.get("active")
        active_flag = (
            raw_active
            if isinstance(raw_active, bool)
            else str(raw_active or "").strip().lower() not in DISABLED_VALUES | {""}
        )
        active = bool(
            active_flag
            and status in {"running", "cooldown", "provisioning", "handoff"}
            and remaining > 0
        )
        return {
            **result,
            "status": status or "unknown",
            "active": active,
            "remainingSeconds": int(remaining + 0.999) if active else 0,
            # Keep polling MySQL instead of leaving a long stale wait when a
            # killed isolated worker never gets to publish its completion.
            "retryAfterSeconds": min(
                max(1, int(remaining + 0.999)) if active else 0,
                self.interval_seconds(),
            ) if active else 0,
        }

    @staticmethod
    def interrupted_compiler_result(payload: Dict[str, object]) -> bool:
        text = " ".join(str(dict(payload or {}).get(key) or "") for key in [
            "reason", "deferredReason", "workerOutput",
        ]).lower()
        return any(token in text for token in [
            "keep-alive timed out",
            "operation timed out",
            "deadline exceeded",
            "transport error",
            "connection reset",
            "connection closed",
        ])

    def activity_cooldown_seconds(self, payload: Dict[str, object]) -> int:
        """Return a no-TypeDB hand-off interval after a prewarm attempt."""
        result = dict(payload or {})
        status = str(result.get("status") or "").strip().lower()
        if status == "timeout" or self.interrupted_compiler_result(result):
            return self.interrupted_compiler_cooldown_seconds()
        if status in {"provisioning", "deferred-projection-coordinator"}:
            try:
                recommended = int(result.get("recommendedRetryAfterSeconds") or 0)
            except (TypeError, ValueError):
                recommended = 0
            return max(3, min(60, recommended or self.interval_seconds()))
        return 0

    @staticmethod
    def activity_result_summary(payload: Dict[str, object]) -> Dict[str, object]:
        result = dict(payload or {})
        return {
            key: result.get(key)
            for key in [
                "status",
                "functionsReady",
                "pendingRuleCount",
                "reasonCode",
                "reason",
                "durationMs",
                "recommendedRetryAfterSeconds",
            ]
            if key in result
        }

    def publish_activity(
        self,
        status: str,
        active_seconds: int = 0,
        result: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Publish compiler activity without letting telemetry block TypeDB work."""
        store = self.prewarm_state_store
        writer = getattr(store, "replace", None) or getattr(store, "save", None)
        if not callable(writer):
            return {}
        now = self.activity_now()
        duration = max(0, int(active_seconds or 0))
        expires_at = now + timedelta(seconds=duration)
        payload = {
            "status": str(status or "idle"),
            "active": bool(duration),
            "updatedAt": now.isoformat().replace("+00:00", "Z"),
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
            "expiresAtEpoch": expires_at.timestamp(),
        }
        if result:
            payload["lastResult"] = self.activity_result_summary(result)
        try:
            writer(payload)
        except Exception:
            return {}
        return payload

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
            "requireReadyForInference": self.require_ready_for_inference(),
            "backlogRecoveryEnabled": self.backlog_recovery_enabled(),
            "backlogRecoveryAgeSeconds": self.backlog_recovery_age_seconds(),
            "backlogRecoveryMinPendingEntries": self.backlog_recovery_min_pending_entries(),
            "backlogRecoveryRetrySeconds": self.backlog_recovery_retry_seconds(),
        }
        activity = self.prewarm_activity_state()
        result["prewarmActivity"] = activity
        if bool(activity.get("active")):
            status = str(activity.get("status") or "running")
            result["prewarm"] = {
                "status": "compiler-" + status,
                "functionsReady": False,
                "reason": "TypeDB RuleBox compiler is active; readiness is deferred without a TypeDB connection.",
            }
            return result
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
        if callable(self.storage_guard):
            try:
                storage = dict(self.storage_guard() or {})
            except Exception as error:  # noqa: BLE001 - an unknown disk state must not start schema compilation.
                storage = {"ready": False, "status": "unavailable", "reason": str(error)[:180]}
            if not bool(storage.get("ready", True)):
                return {
                    "status": "deferred-low-disk",
                    "configured": True,
                    "functionsReady": False,
                    "storage": storage,
                    "reason": str(storage.get("reason") or "TypeDB 저장 여유 공간이 부족합니다."),
                    "durationMs": 0,
                }
        activity = self.prewarm_activity_state()
        if bool(activity.get("active")):
            status = str(activity.get("status") or "running")
            return {
                "status": "deferred-compiler-activity",
                "configured": True,
                "functionsReady": False,
                "pendingRuleCount": None,
                "prewarmActivity": activity,
                "reason": (
                    "TypeDB RuleBox compiler is " + status
                    + "; another schema compilation is deferred without opening TypeDB."
                ),
                "recommendedRetryAfterSeconds": max(
                    1,
                    int(activity.get("retryAfterSeconds") or self.interval_seconds()),
                ),
                "durationMs": 0,
            }
        queue = self.reasoning_queue_state()
        pending = self.pending_reasoning_count(queue)
        recovery = self.backlog_recovery_state(queue)
        bootstrap = self.cold_bootstrap_state(queue)
        recovery_granted = bool(recovery.get("canRecover")) and not force
        bootstrap_granted = bool(bootstrap.get("canBootstrap")) and not force
        compiler_turn_granted = recovery_granted or bootstrap_granted
        # A TypeDB schema commit can keep its compiler busy after a client has
        # timed out or an isolated worker has exited. Preserve the latest-state
        # queue while an inference lease is active. A strict cold start gets a
        # compiler turn as soon as that lease is absent, breaking the circular
        # wait between an empty-queue compiler policy and a prewarm-gated live
        # inference policy.
        if (
            self.defer_when_reasoning_pending()
            and pending
            and not force
            and not compiler_turn_granted
        ):
            return {
                "status": (
                    "deferred-aged-reasoning-backlog-active"
                    if recovery.get("eligible")
                    else "deferred-reasoning-pending"
                ),
                "configured": True,
                "functionsReady": None,
                "pendingRuleCount": None,
                "reasoningPendingCount": pending,
                "reasoningQueue": queue,
                "backlogRecovery": recovery,
                "coldBootstrap": bootstrap,
                "prewarmReadinessDeferred": True,
                "reason": (
                    "Live ontology reasoning is pending; the RuleBox compiler does not open a TypeDB schema "
                    "transaction while an inference lease is active. The compiler will resume as soon as the "
                    "active inference lease ends."
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
        self.publish_activity(
            "running",
            active_seconds=self.prewarm_activity_lease_seconds(),
            result={
                "status": "running",
                "reasoningPendingCount": pending,
                "backlogRecoveryGranted": recovery_granted,
                "bootstrapPriorityGranted": bootstrap_granted,
            },
        )
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
        if bootstrap.get("eligible"):
            result["coldBootstrap"] = bootstrap
            result["bootstrapPriorityGranted"] = bootstrap_granted
            result["reasoningPendingCount"] = pending
            if bootstrap_granted:
                result["recoveryMode"] = "cold-rulebox-bootstrap-no-active-inference-lease"
        if recovery.get("eligible"):
            result["queuePriority"] = True
            result["backlogRecovery"] = recovery
            result["reasoningPendingCount"] = pending
            result["backlogRecoveryGranted"] = recovery_granted
            if recovery_granted:
                result["recoveryMode"] = "aged-backlog-no-active-inference-lease"
            if str(result.get("status") or "") in {
                "provisioning",
                "deferred-projection-coordinator",
            }:
                # A staged RuleBox contains small, durable batches. Polling
                # quickly here is safe and lets it retain priority until the
                # compiled path can release the waiting queue.
                result["recommendedRetryAfterSeconds"] = self.backlog_recovery_retry_seconds()
        elif bootstrap_granted and str(result.get("status") or "") in {
            "provisioning",
            "deferred-projection-coordinator",
        }:
            # Stay responsive while a cold RuleBox is staged, but do not
            # bypass the compiler hand-off state or the TypeDB coordinator.
            result["recommendedRetryAfterSeconds"] = self.backlog_recovery_retry_seconds()
        cooldown_seconds = self.activity_cooldown_seconds(result)
        activity_status = (
            "cooldown"
            if cooldown_seconds
            else "ready" if str(result.get("status") or "") == "ok" else "idle"
        )
        activity = self.publish_activity(
            activity_status,
            active_seconds=cooldown_seconds,
            result=result,
        )
        if activity:
            result["prewarmActivity"] = activity
        return result
