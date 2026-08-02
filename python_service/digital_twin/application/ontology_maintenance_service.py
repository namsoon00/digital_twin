"""Low-priority, lease-safe retention for immutable TypeDB ABox manifests."""

from typing import Dict, Iterable, List

from ..domain.ontology_runtime_operations import (
    bounded_background_work_fairness,
    scoped_abox_maintenance_health,
    scoped_abox_maintenance_policy,
)
from ..domain.portfolio import utc_now_iso


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def text(value: object) -> str:
    return str(value or "").strip()


def integer(value: object, fallback: int = 0) -> int:
    if value in (None, ""):
        return fallback
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return fallback


class OntologyMaintenanceRunner:
    """Drain one TypeDB ontology world at a time outside investment latency.

    The repository owns the writer lease and performs reference-aware cleanup.
    This runner only schedules a bounded pass and stores compact operational
    state in MySQL so restarts continue the round-robin world order.
    """

    state_contract = "ontology-maintenance-state-v3"

    def __init__(
        self,
        ontology_repository,
        state_store=None,
        settings: Dict[str, object] = None,
        reasoning_queue_probe=None,
    ):
        self.ontology_repository = ontology_repository
        self.state_store = state_store
        self.settings = dict(settings or {})
        self.reasoning_queue_probe = reasoning_queue_probe
        self.last_background_fairness: Dict[str, object] = {}

    def policy(self) -> Dict[str, object]:
        return scoped_abox_maintenance_policy(self.settings)

    def enabled(self) -> bool:
        value = text(self.settings.get("ontologyAboxMaintenanceEnabled"))
        return value.lower() not in DISABLED_VALUES

    def interval_seconds(self) -> int:
        return max(15, integer(self.policy().get("intervalSeconds"), 60))

    def execution_timeout_seconds(self) -> int:
        return max(30, min(1800, integer(
            self.settings.get("ontologyAboxMaintenanceExecutionTimeoutSeconds"),
            180,
        )))

    def execution_timeout_grace_seconds(self) -> int:
        return max(1, min(120, integer(
            self.settings.get("ontologyAboxMaintenanceExecutionTimeoutGraceSeconds"),
            10,
        )))

    def process_isolation_enabled(self) -> bool:
        value = text(self.settings.get("ontologyAboxMaintenanceProcessIsolationEnabled"))
        return value.lower() not in DISABLED_VALUES

    def configured_world_types(self) -> set:
        raw = text(self.settings.get("ontologyAboxMaintenanceWorldTypes") or "portfolio,market,knowledge")
        return {
            item.strip().lower()
            for item in raw.split(",")
            if item.strip()
        }

    def defer_while_reasoning_pending(self) -> bool:
        value = text(self.settings.get("ontologyAboxMaintenanceDeferWhenReasoningPending") or "1").lower()
        return value not in DISABLED_VALUES

    def background_fairness_enabled(self) -> bool:
        value = text(self.settings.get("ontologyBackgroundWorkFairnessEnabled") or "1").lower()
        return value not in DISABLED_VALUES

    def max_reasoning_deferral_seconds(self) -> int:
        return max(30, min(24 * 60 * 60, integer(
            self.settings.get("ontologyAboxMaintenanceMaxReasoningDeferralSeconds"),
            300,
        )))

    def fairness_cooldown_seconds(self) -> int:
        return max(10, min(60 * 60, integer(
            self.settings.get("ontologyBackgroundWorkFairnessCooldownSeconds"),
            300,
        )))

    def reasoning_queue_state(self) -> Dict[str, object]:
        if not callable(self.reasoning_queue_probe):
            return {"status": "not-configured", "effectivePendingCount": 0}
        try:
            value = self.reasoning_queue_probe()
        except Exception as error:  # noqa: BLE001 - uncertainty must not block low-priority cleanup forever.
            return {"status": "error", "effectivePendingCount": 0, "reason": str(error)[:180]}
        return dict(value or {}) if isinstance(value, dict) else {"status": "invalid", "effectivePendingCount": 0}

    @staticmethod
    def queue_pending_count(state: Dict[str, object]) -> int:
        for key in ["effectivePendingCount", "pendingEntryCount", "pendingCount"]:
            value = integer((state or {}).get(key), -1)
            if value >= 0:
                return value
        return 0

    @staticmethod
    def active_reasoning_count(state: Dict[str, object]):
        values = dict(state or {}) if isinstance(state, dict) else {}
        mailbox = values.get("mailbox") if isinstance(values.get("mailbox"), dict) else {}
        counts = []
        for source in (values, mailbox):
            if "runningEntryCount" not in source:
                continue
            try:
                counts.append(max(0, int(float(source.get("runningEntryCount") or 0))))
            except (TypeError, ValueError):
                continue
        return max(counts) if counts else None

    def clear_reasoning_deferral_state(self, state: Dict[str, object]) -> None:
        if not text((state or {}).get("reasoningQueueDeferredSinceAt")):
            return
        self.save_state({
            **dict(state or {}),
            "reasoningQueueDeferredSinceAt": "",
        })

    def background_fairness_decision(
        self,
        policy: Dict[str, object] = None,
        reasoning_queue: Dict[str, object] = None,
        commit_fairness: bool = False,
        record_deferral: bool = False,
    ) -> Dict[str, object]:
        del policy  # The fairness boundary is independent from delete budgets.
        queue = dict(reasoning_queue or self.reasoning_queue_state())
        pending = self.queue_pending_count(queue)
        state = self.state()
        deferred_since = text(state.get("reasoningQueueDeferredSinceAt"))
        if pending > 0 and not deferred_since and record_deferral:
            deferred_since = utc_now_iso()
            state = {
                **state,
                "reasoningQueueDeferredSinceAt": deferred_since,
            }
            self.save_state(state)
        decision = bounded_background_work_fairness(
            reasoning_pending_count=pending,
            active_reasoning_count=self.active_reasoning_count(queue),
            background_work_pending=bool(pending),
            oldest_background_work_at=deferred_since,
            last_fairness_at=state.get("lastFairnessAttemptAt"),
            max_deferral_seconds=self.max_reasoning_deferral_seconds(),
            fairness_cooldown_seconds=self.fairness_cooldown_seconds(),
        )
        fairness_enabled = self.background_fairness_enabled()
        if not fairness_enabled and pending > 0:
            decision.update({
                "deferred": True,
                "fairnessGranted": False,
                "reasonCode": "fairness-disabled",
                "reason": "공정 실행이 비활성화되어 라이브 추론 우선 정책을 유지합니다.",
            })
        decision.update({
            "worker": "ontology-abox-maintenance",
            "enabled": fairness_enabled,
        })
        if bool(decision.get("fairnessGranted")) and commit_fairness:
            self.save_state({
                **state,
                "lastFairnessAttemptAt": text(decision.get("checkedAt")),
                "lastFairness": {
                    key: decision.get(key)
                    for key in [
                        "version", "checkedAt", "reasonCode", "backgroundWaitSeconds",
                        "maxDeferralSeconds", "fairnessCooldownSeconds",
                    ]
                },
            })
        self.last_background_fairness = dict(decision)
        return decision

    def reasoning_queue_deferral(
        self,
        policy: Dict[str, object] = None,
        commit_fairness: bool = False,
    ) -> Dict[str, object]:
        """Return a no-write preflight result while investment reasoning runs."""
        queue_state = self.reasoning_queue_state()
        pending = self.queue_pending_count(queue_state)
        if not self.defer_while_reasoning_pending() or pending <= 0:
            self.clear_reasoning_deferral_state(self.state())
            self.last_background_fairness = {}
            return {}
        fairness = self.background_fairness_decision(
            policy,
            queue_state,
            commit_fairness=commit_fairness,
            record_deferral=True,
        )
        if bool(fairness.get("fairnessGranted")):
            return {}
        return {
            "status": "deferred-reasoning-queue",
            "contract": self.state_contract,
            "policy": dict(policy or self.policy()),
            "reason": "활성 추론 요청이 남아 있어 ABox 정리를 유휴 시간으로 미룹니다.",
            "reasoningQueue": queue_state,
            "backgroundFairness": fairness,
            "retryAfterSeconds": self.interval_seconds(),
        }

    def projection_coordinator_summary(self, lease: Dict[str, object]) -> Dict[str, object]:
        allowed = {
            "acquired", "status", "coordinator", "coordinatorVersion",
            "requestedWorldId", "leaseOwner", "leaseRemainingSeconds",
            "recommendedRetryAfterSeconds", "reason",
        }
        return {
            key: value
            for key, value in dict(lease or {}).items()
            if key in allowed and value not in (None, "", [], {})
        }

    def acquire_projection_coordinator_lease(self, world_id: str) -> Dict[str, object]:
        acquire = getattr(self.ontology_repository, "acquire_projection_coordinator_lease", None)
        if not callable(acquire):
            return {"acquired": True, "status": "unsupported"}
        try:
            return dict(acquire("maintenance", world_id=world_id) or {})
        except Exception as error:  # noqa: BLE001 - maintenance remains deferrable.
            return {
                "acquired": False,
                "status": "error",
                "recommendedRetryAfterSeconds": 10,
                "reason": str(error)[:180],
            }

    def release_projection_coordinator_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        if not bool((lease or {}).get("acquired")):
            return {"status": "not-owner"}
        release = getattr(self.ontology_repository, "release_projection_coordinator_lease", None)
        if not callable(release):
            return {"status": "unsupported"}
        try:
            return dict(release(lease) or {})
        except Exception as error:  # noqa: BLE001 - expiry is the final fallback.
            return {"status": "error", "reason": str(error)[:180]}

    def state(self) -> Dict[str, object]:
        if not self.state_store or not hasattr(self.state_store, "load"):
            return {}
        try:
            value = self.state_store.load()
            return dict(value or {}) if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001 - a missing operational audit store must not block cleanup.
            return {}

    def save_state(self, payload: Dict[str, object]) -> None:
        if not self.state_store:
            return
        writer = getattr(self.state_store, "replace", None)
        if not callable(writer):
            writer = getattr(self.state_store, "save", None)
        if not callable(writer):
            return
        writer(dict(payload or {}))

    def worlds(self) -> List[Dict[str, object]]:
        reader = getattr(self.ontology_repository, "list_ontology_worlds", None)
        if not callable(reader):
            return []
        try:
            raw = list(reader() or [])
        except Exception:  # noqa: BLE001 - the caller reports the repository failure in its result.
            return []
        allowed_types = self.configured_world_types()
        rows = []
        for item in raw:
            value = dict(item or {}) if isinstance(item, dict) else {}
            world_id = text(value.get("worldId"))
            world_type = text(value.get("worldType") or world_id.split(":", 1)[0]).lower()
            if not world_id or (allowed_types and world_type not in allowed_types):
                continue
            rows.append({"worldId": world_id, "worldType": world_type})
        return sorted(rows, key=lambda item: (item["worldType"], item["worldId"]))

    @staticmethod
    def next_world(worlds: Iterable[Dict[str, object]], state: Dict[str, object]) -> tuple:
        rows = list(worlds or [])
        if not rows:
            return {}, ""
        next_id = text((state or {}).get("nextWorldId"))
        index = next((idx for idx, item in enumerate(rows) if item.get("worldId") == next_id), 0)
        selected = rows[index]
        following = rows[(index + 1) % len(rows)]
        return selected, text(following.get("worldId"))

    @staticmethod
    def backlog_by_world(state: Dict[str, object]) -> Dict[str, Dict[str, object]]:
        raw = state.get("backlogByWorld") if isinstance(state, dict) else {}
        if not isinstance(raw, dict):
            return {}
        return {
            text(world_id): dict(value or {})
            for world_id, value in raw.items()
            if text(world_id) and isinstance(value, dict)
        }

    def adaptive_drain(self, policy: Dict[str, object], state: Dict[str, object], world_id: str) -> Dict[str, object]:
        """Select a bounded physical-delete budget from prior safe passes.

        We deliberately use only persisted results from the same world.  A
        live writer lease, timeout, or error never raises the next budget.
        This prevents retention from competing with active ABox projection.
        """
        base_batches = max(1, integer(policy.get("maxDeleteBatchesPerRun"), 1))
        maximum_batches = max(
            base_batches,
            integer(policy.get("adaptiveDrainMaxDeleteBatchesPerRun"), base_batches),
        )
        required_runs = max(1, integer(policy.get("adaptiveDrainCriticalRunsBeforeIncrease"), 2))
        world_state = self.backlog_by_world(state).get(world_id, {})
        critical_runs = max(0, integer(world_state.get("criticalDrainRuns")))
        enabled = bool(policy.get("adaptiveDrainEnabled"))
        increase_steps = 0
        if enabled and critical_runs >= required_runs:
            increase_steps = 1 + ((critical_runs - required_runs) // required_runs)
        effective_batches = min(maximum_batches, base_batches + increase_steps)
        return {
            "enabled": enabled,
            "baseMaxDeleteBatches": base_batches,
            "effectiveMaxDeleteBatches": effective_batches,
            "maximumMaxDeleteBatches": maximum_batches,
            "criticalRunsBeforeIncrease": required_runs,
            "criticalDrainRunsBefore": critical_runs,
            "mode": "adaptive-drain" if effective_batches > base_batches else "bounded-base",
        }

    def updated_backlog_by_world(
        self,
        previous: Dict[str, object],
        world_id: str,
        result_status: str,
        inventory_available: bool,
        health: Dict[str, object],
        inactive_before: int,
        inactive_remaining: int,
        removed_manifest_count: int,
        deleted_batch_count: int,
        valid_world_ids: Iterable[str],
    ) -> Dict[str, Dict[str, object]]:
        rows = self.backlog_by_world(previous)
        allowed = {text(item) for item in valid_world_ids if text(item)}
        rows = {key: value for key, value in rows.items() if key in allowed}
        current = dict(rows.get(world_id) or {})
        if not inventory_available:
            current["lastStatus"] = result_status
            current["inventoryAvailable"] = False
            rows[world_id] = current
            return rows

        critical = text(health.get("state")) == "critical"
        safe_pass = result_status in {"ok", "partial"}
        progress = bool(
            deleted_batch_count
            or removed_manifest_count
            or inactive_remaining < inactive_before
        )
        if critical and safe_pass:
            critical_runs = max(0, integer(current.get("criticalDrainRuns"))) + 1
        elif result_status in {"error", "timeout"}:
            critical_runs = 0
        else:
            critical_runs = 0
        rows[world_id] = {
            "criticalDrainRuns": critical_runs,
            "lastStatus": result_status,
            "inventoryAvailable": True,
            "lastInactiveManifestCount": inactive_remaining,
            "lastProgress": progress,
            "lastDeletedBatchCount": deleted_batch_count,
        }
        return rows

    def status(self) -> Dict[str, object]:
        policy = self.policy()
        state = self.state()
        worlds = self.worlds()
        last_result = state.get("lastResult") if isinstance(state.get("lastResult"), dict) else {}
        return {
            "contract": self.state_contract,
            "enabled": self.enabled(),
            "worldTypes": sorted(self.configured_world_types()),
            "worldCount": len(worlds),
            "intervalSeconds": self.interval_seconds(),
            "processIsolationEnabled": self.process_isolation_enabled(),
            "executionTimeoutSeconds": self.execution_timeout_seconds(),
            "deferWhenReasoningPending": self.defer_while_reasoning_pending(),
            "reasoningQueue": self.reasoning_queue_state(),
            "backgroundFairness": self.background_fairness_decision(policy),
            "policy": policy,
            "lastRunAt": text(state.get("lastRunAt")),
            "lastResult": dict(last_result),
            "nextWorldId": text(state.get("nextWorldId")),
            "backlogByWorld": self.backlog_by_world(state),
        }

    def run_once(self) -> Dict[str, object]:
        policy = self.policy()
        if not self.enabled():
            return {"status": "disabled", "contract": self.state_contract, "policy": policy}
        runner = getattr(self.ontology_repository, "run_deferred_maintenance", None)
        if not callable(runner):
            return {
                "status": "not-supported",
                "contract": self.state_contract,
                "policy": policy,
                "reason": "Graph store has no scoped ABox maintenance adapter.",
            }
        deferred = self.reasoning_queue_deferral(policy, commit_fairness=True)
        if deferred:
            result = dict(deferred)
            previous = self.state()
            self.save_state({
                **previous,
                "lastRunAt": utc_now_iso(),
                "lastResult": result,
            })
            return result
        worlds = self.worlds()
        if not worlds:
            return {
                "status": "idle",
                "contract": self.state_contract,
                "policy": policy,
                "reason": "No active ontology world matches the maintenance scope.",
            }
        previous = self.state()
        selected, next_world_id = self.next_world(worlds, previous)
        world_id = text(selected.get("worldId"))
        adaptive_drain = self.adaptive_drain(policy, previous, world_id)
        coordinator_lease = self.acquire_projection_coordinator_lease(world_id)
        if not bool(coordinator_lease.get("acquired")):
            result = {
                "status": "deferred-projection-coordinator",
                "contract": self.state_contract,
                "policy": policy,
                "worldId": world_id,
                "reason": str(
                    coordinator_lease.get("reason")
                    or "다른 TypeDB World 투영이 데이터베이스 쓰기 경계를 사용 중입니다."
                )[:220],
                "retryAfterSeconds": int(coordinator_lease.get("recommendedRetryAfterSeconds") or 10),
                "projectionCoordinator": self.projection_coordinator_summary(coordinator_lease),
            }
            self.save_state({
                **previous,
                "lastRunAt": utc_now_iso(),
                "lastResult": result,
            })
            return result
        try:
            result = dict(runner({
                "worldId": world_id,
                "maxInactiveManifests": integer(policy.get("maxManifestsPerRun"), 1),
                "maxAboxDeleteBatches": integer(adaptive_drain.get("effectiveMaxDeleteBatches"), 1),
                "aboxDeleteBatchSize": integer(policy.get("deleteBatchSize"), 50),
                "keepInactiveManifests": integer(policy.get("keepInactiveManifestCount"), 0),
            }) or {})
        except Exception as error:  # noqa: BLE001 - a maintenance fault must not affect native inference.
            result = {"status": "error", "reason": str(error)[:220]}
        finally:
            coordinator_release = self.release_projection_coordinator_lease(coordinator_lease)
        result["projectionCoordinator"] = self.projection_coordinator_summary(coordinator_lease)
        result["projectionCoordinatorRelease"] = coordinator_release
        result_status = text(result.get("status") or "unknown")
        abox = result.get("abox") if isinstance(result.get("abox"), dict) else {}
        inventory_available = bool(abox)
        inactive_before = max(0, integer(abox.get("completedInactiveManifestCount")))
        inactive_remaining = max(
            0,
            integer(abox.get("remainingInactiveManifestCount"), inactive_before),
        )
        removed_manifest_count = len([
            item for item in abox.get("removedManifestIds") or [] if text(item)
        ])
        health = (
            scoped_abox_maintenance_health({
                "status": "ok" if result_status not in {"error", "disabled"} else result_status,
                "inactiveManifestCount": inactive_remaining,
            }, policy)
            if inventory_available
            else {
                "status": "ok" if result_status == "deferred-write-lease" else "warning",
                "state": "deferred" if result_status == "deferred-write-lease" else "unavailable",
                "inactiveManifestCount": None,
                "warningInactiveManifestCount": integer(policy.get("warningInactiveManifestCount")),
                "criticalInactiveManifestCount": integer(policy.get("criticalInactiveManifestCount")),
                "drainRequired": None,
                "recommendedMaxManifests": 0,
                "reason": (
                    "Scoped ABox inventory was not read because a live writer lease has priority."
                    if result_status == "deferred-write-lease"
                    else "Scoped ABox retention inventory is unavailable for this maintenance result."
                ),
            }
        )
        compact = {
            "status": result_status,
            "worldId": world_id,
            "worldType": text(selected.get("worldType")),
            "inventoryAvailable": inventory_available,
            "inactiveManifestCountBefore": inactive_before,
            "inactiveManifestCountRemaining": inactive_remaining,
            "removedManifestCount": removed_manifest_count,
            "deletedBatchCount": max(0, integer(abox.get("deletedBatchCount"))),
            "maxDeleteBatches": max(
                0,
                integer(abox.get("maxDeleteBatches"), integer(adaptive_drain.get("effectiveMaxDeleteBatches"), 0)),
            ),
            "deleteBatchSize": max(0, integer(abox.get("deleteBatchSize"), integer(policy.get("deleteBatchSize"), 50))),
            "health": health,
            "adaptiveDrain": adaptive_drain,
            "reason": text(result.get("reason"))[:220],
        }
        backlog_by_world = self.updated_backlog_by_world(
            previous,
            world_id,
            result_status,
            inventory_available,
            health,
            inactive_before,
            inactive_remaining,
            removed_manifest_count,
            compact["deletedBatchCount"],
            [item.get("worldId") for item in worlds],
        )
        current_backlog = dict(backlog_by_world.get(world_id) or {})
        compact["adaptiveDrain"] = {
            **adaptive_drain,
            "criticalDrainRuns": integer(current_backlog.get("criticalDrainRuns")),
            "lastProgress": bool(current_backlog.get("lastProgress")),
        }
        self.save_state({
            **previous,
            "contract": self.state_contract,
            "lastRunAt": utc_now_iso(),
            "nextWorldId": next_world_id,
            "lastResult": compact,
            "backlogByWorld": backlog_by_world,
        })
        response = {
            "contract": self.state_contract,
            "status": result_status,
            "worldId": world_id,
            "worldType": text(selected.get("worldType")),
            "policy": policy,
            "maintenance": compact,
            "repository": result,
        }
        if bool(self.last_background_fairness.get("fairnessGranted")):
            response["backgroundFairness"] = dict(self.last_background_fairness)
        return response
