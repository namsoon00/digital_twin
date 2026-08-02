"""Low-priority, lease-safe retention for immutable TypeDB ABox manifests."""

from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from ..domain.ontology_runtime_operations import (
    bounded_background_work_fairness,
    scoped_abox_maintenance_health,
    scoped_abox_maintenance_policy,
    scoped_abox_maintenance_yield_backlog,
    scoped_abox_maintenance_yield_status,
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

    state_contract = "ontology-maintenance-state-v5"

    def __init__(
        self,
        ontology_repository,
        state_store=None,
        settings: Dict[str, object] = None,
        reasoning_queue_probe=None,
        capacity_guard=None,
    ):
        self.ontology_repository = ontology_repository
        self.state_store = state_store
        self.settings = dict(settings or {})
        self.reasoning_queue_probe = reasoning_queue_probe
        self.capacity_guard = capacity_guard
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
            120,
        )))

    def fairness_cooldown_seconds(self) -> int:
        return max(10, min(60 * 60, integer(
            self.settings.get("ontologyBackgroundWorkFairnessCooldownSeconds"),
            300,
        )))

    def busy_retry_seconds(self) -> int:
        """Retry a no-write availability probe often enough to catch an idle gap."""
        return max(5, min(60, integer(
            self.settings.get("ontologyAboxMaintenanceBusyRetrySeconds"),
            10,
        )))

    def maintenance_yield_status(self, state: Dict[str, object] = None) -> Dict[str, object]:
        return scoped_abox_maintenance_yield_status(
            self.state() if state is None else state,
            self.settings,
        )

    def request_maintenance_yield(
        self,
        state: Dict[str, object],
        fairness: Dict[str, object],
    ) -> Dict[str, object]:
        """Persist one bounded writer hand-off for a verified ABox backlog.

        The request is created only while a known live reasoning lease keeps
        maintenance out of TypeDB.  The reasoning worker consumes the durable
        request before it starts its next batch, so this method never reaches
        into a running TypeDB transaction or cancels investment work.
        """

        current_state = dict(state or {})
        status = self.maintenance_yield_status(current_state)
        policy = dict(status.get("policy") or {})
        if bool(status.get("active")):
            return status
        if not bool(policy.get("enabled")):
            return status
        if text((fairness or {}).get("reasonCode")) != "active-reasoning-lease":
            return {
                **status,
                "status": "not-required",
                "reason": "실행 중인 라이브 추론 lease가 확인된 경우에만 ABox 정리 창을 요청합니다.",
            }
        background_wait = max(0, integer((fairness or {}).get("backgroundWaitSeconds")))
        if background_wait < max(1, integer(policy.get("afterSeconds"), 120)):
            return {
                **status,
                "status": "within-deferral-budget",
                "reason": "ABox 정리 대기 시간이 제한된 추론 양보 기준보다 짧습니다.",
            }
        if integer(status.get("cooldownRemainingSeconds")) > 0:
            return {
                **status,
                "status": "cooldown",
                "reason": "직전 ABox 정리 창 뒤 최소 간격이 남아 있습니다.",
            }
        backlog = scoped_abox_maintenance_yield_backlog(current_state, self.settings)
        if not bool(backlog.get("eligible")):
            return {
                **status,
                "status": str(backlog.get("status") or "no-priority-backlog"),
                "backlog": backlog,
                "reason": "최근 직접 확인된 우선 ABox 적체가 없어 라이브 추론을 유예하지 않습니다.",
            }
        now = datetime.now(timezone.utc)
        requested_at = now.isoformat().replace("+00:00", "Z")
        expires_at = (now + timedelta(seconds=max(
            integer(policy.get("windowSeconds"), 30),
            integer(policy.get("requestTtlSeconds"), 420),
        ))).isoformat().replace("+00:00", "Z")
        request = {
            "version": str(policy.get("version") or "typedb-scoped-abox-maintenance-yield-v1"),
            "requestedAt": requested_at,
            "expiresAt": expires_at,
            "worldId": text(backlog.get("worldId")),
            "inactiveManifestCount": max(0, integer(backlog.get("inactiveManifestCount"))),
            "inventoryObservedAt": text(backlog.get("inventoryObservedAt")),
            "backgroundWaitSeconds": background_wait,
            "reasonCode": "active-reasoning-lease-priority-backlog",
        }
        self.save_state({
            **current_state,
            "maintenanceYieldRequest": request,
            "maintenanceYieldLastRequestedAt": requested_at,
        })
        return self.maintenance_yield_status({
            **current_state,
            "maintenanceYieldRequest": request,
            "maintenanceYieldLastRequestedAt": requested_at,
        })

    def reasoning_queue_state(self) -> Dict[str, object]:
        if not callable(self.reasoning_queue_probe):
            return {"status": "not-configured", "effectivePendingCount": 0}
        try:
            value = self.reasoning_queue_probe()
        except Exception as error:  # noqa: BLE001 - uncertainty must not block low-priority cleanup forever.
            return {"status": "error", "effectivePendingCount": 0, "reason": str(error)[:180]}
        return dict(value or {}) if isinstance(value, dict) else {"status": "invalid", "effectivePendingCount": 0}

    def capacity_guard_state(self) -> Dict[str, object]:
        """Return the role-specific TypeDB capacity policy for retention."""
        if not callable(self.capacity_guard):
            return {"ready": True, "status": "not-configured", "mode": "normal"}
        try:
            value = self.capacity_guard()
        except Exception as error:  # noqa: BLE001 - no deletion on an unknown TypeDB capacity state.
            return {
                "ready": False,
                "status": "error",
                "mode": "unavailable",
                "reason": "TypeDB 용량 상태 확인 실패: " + str(error)[:180],
            }
        state = dict(value or {}) if isinstance(value, dict) else {}
        state["ready"] = bool(state.get("ready"))
        state.setdefault("status", "ready" if state["ready"] else "blocked")
        state.setdefault("mode", state["status"])
        return state

    def capacity_maintenance_budget(
        self,
        policy: Dict[str, object],
        adaptive_drain: Dict[str, object],
        capacity: Dict[str, object],
    ) -> Dict[str, int]:
        """Increase one cleanup turn only while TypeDB is below rotation level."""
        base_manifests = max(1, integer(policy.get("maxManifestsPerRun"), 1))
        base_batches = max(1, integer(adaptive_drain.get("effectiveMaxDeleteBatches"), 1))
        base_batch_size = max(10, integer(policy.get("deleteBatchSize"), 50))
        if not bool(capacity.get("capacityPriority")):
            return {
                "maxInactiveManifests": base_manifests,
                "maxAboxDeleteBatches": base_batches,
                "aboxDeleteBatchSize": base_batch_size,
                "capacityPriority": 0,
            }
        return {
            "maxInactiveManifests": max(
                base_manifests,
                min(20, integer(self.settings.get("typedbCapacityMaintenanceMaxManifests"), 10)),
            ),
            "maxAboxDeleteBatches": max(
                base_batches,
                min(50, integer(self.settings.get("typedbCapacityMaintenanceMaxDeleteBatches"), 12)),
            ),
            "aboxDeleteBatchSize": max(
                base_batch_size,
                min(500, integer(self.settings.get("typedbCapacityMaintenanceDeleteBatchSize"), 250)),
            ),
            "capacityPriority": 1,
        }

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
            # Before state-v5, this field was recorded before the coordinator
            # was acquired. Only a completed lease-owning pass is allowed to
            # start the fairness cooldown.
            last_fairness_at=state.get("lastFairnessCompletedAt"),
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
        # A fairness grant only permits an attempt; it does not prove this
        # worker received the TypeDB writer. Persisting the cooldown here
        # would turn a rejected coordinator lease into a five-minute cleanup
        # delay. ``run_once`` records the grant only after a lease-owning
        # maintenance pass returns successfully.
        del commit_fairness
        self.last_background_fairness = dict(decision)
        return decision

    def reasoning_queue_deferral(
        self,
        policy: Dict[str, object] = None,
        commit_fairness: bool = False,
    ) -> Dict[str, object]:
        """Return a no-write preflight result while investment reasoning runs."""
        capacity = self.capacity_guard_state()
        if not bool(capacity.get("ready")):
            return {
                "status": "deferred-capacity",
                "contract": self.state_contract,
                "policy": dict(policy or self.policy()),
                "reason": str(capacity.get("reason") or "TypeDB 용량 보호 중 ABox 정리를 보류합니다."),
                "capacityGuard": capacity,
                "retryAfterSeconds": self.interval_seconds(),
            }
        if bool(capacity.get("bypassReasoningDeferral")):
            self.clear_reasoning_deferral_state(self.state())
            self.last_background_fairness = {}
            return {}
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
        maintenance_yield = self.request_maintenance_yield(self.state(), fairness)
        # The request is useful only when this worker is allowed to attempt
        # the shared TypeDB coordinator. A still-running reasoning batch may
        # reject that attempt, but its next batch will observe the same
        # durable request and yield. Returning a normal queue deferral here
        # would leave the request active without ever giving retention the
        # writer opportunity it was created for.
        if bool(maintenance_yield.get("active")):
            self.last_background_fairness = {
                **fairness,
                "maintenanceYieldRequested": True,
            }
            return {}
        retry_after = self.interval_seconds()
        if text(fairness.get("reasonCode")) in {
            "active-reasoning-lease",
            "active-lease-unknown",
        }:
            retry_after = self.busy_retry_seconds()
        return {
            "status": "deferred-reasoning-queue",
            "contract": self.state_contract,
            "policy": dict(policy or self.policy()),
            "reason": "활성 추론 요청이 남아 있어 ABox 정리를 유휴 시간으로 미룹니다.",
            "reasoningQueue": queue_state,
            "backgroundFairness": fairness,
            "maintenanceYield": maintenance_yield,
            "retryAfterSeconds": retry_after,
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
    def manifest_inventory_available(inventory: Dict[str, object]) -> bool:
        values = dict(inventory or {}) if isinstance(inventory, dict) else {}
        if "inactiveManifestCount" not in values:
            return False
        return text(values.get("status") or "ok").lower() not in {
            "error", "disabled", "driver-missing", "unavailable",
        }

    def manifest_inventories(self, worlds: Iterable[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
        """Read the cheap manifest-only inventory before taking a writer lease.

        Full ABox diagnostics count every physical row and are intentionally
        not suitable for a one-minute maintenance scheduler.  The TypeDB
        adapter exposes this lighter control-plane read so the runner can
        select the world where retention is actually needed.
        """
        reader = getattr(self.ontology_repository, "scoped_abox_manifest_inventory", None)
        if not callable(reader):
            return {}
        results: Dict[str, Dict[str, object]] = {}
        for world in worlds or []:
            world_id = text(dict(world or {}).get("worldId"))
            if not world_id:
                continue
            try:
                raw = reader(world_id)
            except Exception as error:  # noqa: BLE001 - fall back to the persisted cursor.
                raw = {"status": "error", "reason": str(error)[:180]}
            value = dict(raw or {}) if isinstance(raw, dict) else {"status": "invalid"}
            results[world_id] = {
                "status": text(value.get("status") or "unknown"),
                "inactiveManifestCount": max(0, integer(value.get("inactiveManifestCount"))),
                "storedManifestCount": max(0, integer(value.get("storedManifestCount"))),
                "available": self.manifest_inventory_available(value),
                "reason": text(value.get("reason"))[:180],
            }
        return results

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

    def select_world_for_maintenance(
        self,
        worlds: Iterable[Dict[str, object]],
        state: Dict[str, object],
        manifest_inventories: Dict[str, Dict[str, object]],
        policy: Dict[str, object],
    ) -> tuple:
        """Prefer a verified inactive-manifest backlog, otherwise round robin.

        The rotation cursor remains the tie breaker.  That preserves fairness
        between worlds with equally large retention backlogs while preventing
        an active portfolio from waiting behind two empty shared worlds.
        """
        rows = list(worlds or [])
        fallback, fallback_next = self.next_world(rows, state)
        threshold = max(1, integer((policy or {}).get("priorityInactiveManifestCount"), 8))
        if not rows:
            return {}, "", {
                "mode": "none",
                "priorityInactiveManifestCount": threshold,
                "observedInactiveManifestCounts": {},
            }
        next_id = text((state or {}).get("nextWorldId"))
        cursor = next((idx for idx, item in enumerate(rows) if item.get("worldId") == next_id), 0)
        candidates = []
        observed_counts = {}
        for index, item in enumerate(rows):
            world_id = text(item.get("worldId"))
            inventory = dict((manifest_inventories or {}).get(world_id) or {})
            if not bool(inventory.get("available")):
                continue
            inactive_count = max(0, integer(inventory.get("inactiveManifestCount")))
            observed_counts[world_id] = inactive_count
            if inactive_count >= threshold:
                candidates.append((inactive_count, (index - cursor) % len(rows), index, item))
        if not candidates:
            return fallback, fallback_next, {
                "mode": "round-robin",
                "priorityInactiveManifestCount": threshold,
                "observedInactiveManifestCounts": observed_counts,
            }
        _, _, selected_index, selected = sorted(
            candidates,
            key=lambda item: (-item[0], item[1], text(item[3].get("worldId"))),
        )[0]
        following = rows[(selected_index + 1) % len(rows)]
        return selected, text(following.get("worldId")), {
            "mode": "inactive-manifest-priority",
            "priorityInactiveManifestCount": threshold,
            "observedInactiveManifestCounts": observed_counts,
        }

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

    def reconciled_observed_backlog(
        self,
        previous: Dict[str, object],
        manifest_inventories: Dict[str, Dict[str, object]],
        valid_world_ids: Iterable[str],
        policy: Dict[str, object],
    ) -> Dict[str, Dict[str, object]]:
        """Replace stale maintenance estimates with direct manifest evidence."""
        rows = self.backlog_by_world(previous)
        allowed = {text(item) for item in valid_world_ids if text(item)}
        rows = {key: value for key, value in rows.items() if key in allowed}
        observed_at = utc_now_iso()
        for world_id, inventory in dict(manifest_inventories or {}).items():
            if world_id not in allowed or not bool(dict(inventory or {}).get("available")):
                continue
            current = dict(rows.get(world_id) or {})
            inactive_count = max(0, integer(dict(inventory or {}).get("inactiveManifestCount")))
            health = scoped_abox_maintenance_health({
                "status": str(dict(inventory or {}).get("status") or "ok"),
                "inactiveManifestCount": inactive_count,
            }, policy)
            current.update({
                "inventoryAvailable": True,
                "lastInactiveManifestCount": inactive_count,
                "lastObservedInactiveManifestCount": inactive_count,
                "lastStoredManifestCount": max(0, integer(dict(inventory or {}).get("storedManifestCount"))),
                "lastInventoryObservedAt": observed_at,
                "lastInventoryStatus": text(dict(inventory or {}).get("status") or "ok"),
            })
            if text(health.get("state")) != "critical":
                current["criticalDrainRuns"] = 0
            rows[world_id] = current
        return rows

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
            current["lastMaintenanceInventoryAvailable"] = False
            # A lightweight manifest read may already have corrected the
            # previous run's stale estimate. Keep that evidence visible even
            # when this turn lost the writer lease before full retention ran.
            current["inventoryAvailable"] = bool(current.get("lastInventoryObservedAt"))
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
            **current,
            "criticalDrainRuns": critical_runs,
            "lastStatus": result_status,
            "inventoryAvailable": True,
            "lastMaintenanceInventoryAvailable": True,
            "lastInactiveManifestCount": inactive_remaining,
            "lastObservedInactiveManifestCount": inactive_remaining,
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
            "busyRetrySeconds": self.busy_retry_seconds(),
            "processIsolationEnabled": self.process_isolation_enabled(),
            "executionTimeoutSeconds": self.execution_timeout_seconds(),
            "deferWhenReasoningPending": self.defer_while_reasoning_pending(),
            "reasoningQueue": self.reasoning_queue_state(),
            "capacityGuard": self.capacity_guard_state(),
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
        capacity = self.capacity_guard_state()
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
        manifest_inventories = self.manifest_inventories(worlds)
        observed_backlog = self.reconciled_observed_backlog(
            previous,
            manifest_inventories,
            [item.get("worldId") for item in worlds],
            policy,
        )
        selection_state = {
            **previous,
            "backlogByWorld": observed_backlog,
        }
        selected, next_world_id, world_selection = self.select_world_for_maintenance(
            worlds,
            selection_state,
            manifest_inventories,
            policy,
        )
        world_id = text(selected.get("worldId"))
        selected_inventory = dict(manifest_inventories.get(world_id) or {})
        adaptive_drain = self.adaptive_drain(policy, selection_state, world_id)
        capacity_budget = self.capacity_maintenance_budget(policy, adaptive_drain, capacity)
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
                "manifestInventory": selected_inventory,
                "worldSelection": world_selection,
            }
            self.save_state({
                **selection_state,
                "lastRunAt": utc_now_iso(),
                "lastResult": result,
            })
            return result
        maintenance_yield = self.maintenance_yield_status(selection_state)
        try:
            result = dict(runner({
                "worldId": world_id,
                "maxInactiveManifests": capacity_budget["maxInactiveManifests"],
                "maxAboxDeleteBatches": capacity_budget["maxAboxDeleteBatches"],
                "aboxDeleteBatchSize": capacity_budget["aboxDeleteBatchSize"],
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
        abox_status = text(abox.get("status") or result_status).lower()
        # ``run_deferred_maintenance`` keeps a pending ABox activation intact
        # until native inference completes. That adapter result is a valid
        # no-delete outcome, but it has no retention counts. Treating its
        # truthy payload as an empty inventory used to overwrite the direct
        # Manifest backlog with zero and delayed the next real cleanup pass.
        inventory_available = bool(abox) and (
            "completedInactiveManifestCount" in abox
            or "remainingInactiveManifestCount" in abox
        )
        if abox_status == "skipped" and not inventory_available:
            result_status = "deferred-pending-abox-activation"
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
                "status": "ok" if result_status in {
                    "deferred-write-lease",
                    "deferred-pending-abox-activation",
                } else "warning",
                "state": "deferred" if result_status in {
                    "deferred-write-lease",
                    "deferred-pending-abox-activation",
                } else "unavailable",
                "inactiveManifestCount": None,
                "warningInactiveManifestCount": integer(policy.get("warningInactiveManifestCount")),
                "criticalInactiveManifestCount": integer(policy.get("criticalInactiveManifestCount")),
                "drainRequired": None,
                "recommendedMaxManifests": 0,
                "reason": (
                    "Scoped ABox activation is pending native inference; the verified Manifest backlog remains queued."
                    if result_status == "deferred-pending-abox-activation"
                    else "Scoped ABox inventory was not read because a live writer lease has priority."
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
            "capacityGuard": capacity,
            "capacityBudget": capacity_budget,
            "manifestInventory": selected_inventory,
            "worldSelection": world_selection,
            "reason": text(abox.get("reason") or result.get("reason"))[:220],
        }
        if result_status == "deferred-pending-abox-activation":
            compact["retryAfterSeconds"] = self.busy_retry_seconds()
        backlog_by_world = self.updated_backlog_by_world(
            selection_state,
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
        next_state = {
            **selection_state,
            "contract": self.state_contract,
            "lastRunAt": utc_now_iso(),
            "nextWorldId": next_world_id,
            "lastResult": compact,
            "backlogByWorld": backlog_by_world,
        }
        if bool(self.last_background_fairness.get("fairnessGranted")) and result_status in {"ok", "partial"}:
            next_state.update({
                "lastFairnessAttemptAt": text(self.last_background_fairness.get("checkedAt")),
                "lastFairnessCompletedAt": text(self.last_background_fairness.get("checkedAt")),
                "lastFairness": {
                    key: self.last_background_fairness.get(key)
                    for key in [
                        "version", "checkedAt", "reasonCode", "backgroundWaitSeconds",
                        "maxDeferralSeconds", "fairnessCooldownSeconds",
                    ]
                },
            })
        if bool(maintenance_yield.get("active")) and result_status in {"ok", "partial"}:
            granted_at = utc_now_iso()
            next_state.update({
                "maintenanceYieldRequest": {},
                "maintenanceYieldLastGrantedAt": granted_at,
                "maintenanceYieldLastWorldId": world_id,
            })
            compact["maintenanceYield"] = {
                **maintenance_yield,
                "status": "consumed",
                "active": False,
                "grantedAt": granted_at,
                "worldId": world_id,
            }
        elif bool(maintenance_yield.get("active")):
            compact["maintenanceYield"] = maintenance_yield
        self.save_state(next_state)
        response = {
            "contract": self.state_contract,
            "status": result_status,
            "worldId": world_id,
            "worldType": text(selected.get("worldType")),
            "policy": policy,
            "maintenance": compact,
            "repository": result,
        }
        if compact.get("retryAfterSeconds"):
            response["retryAfterSeconds"] = compact["retryAfterSeconds"]
        if bool(self.last_background_fairness.get("fairnessGranted")):
            response["backgroundFairness"] = dict(self.last_background_fairness)
        return response
