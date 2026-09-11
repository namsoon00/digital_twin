"""Reasoning Health runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import Dict


def typedb_projection_recovery_health(ontology_repository, world_id: str) -> Dict[str, object]:
    """Check durable TypeDB availability before retrying a failed projection.

    The circuit guards the worker after an infrastructure failure. It must not
    require the pending event's future InferenceBox generation to already
    exist, because the normal projection path is responsible for producing it
    once this health probe succeeds.
    """
    clean_world_id = str(world_id or "").strip()
    active = ontology_repository.active_abox_metadata(world_id=clean_world_id)
    active_status = str((active or {}).get("status") or "").strip().lower()
    active_abox = str((active or {}).get("aboxSnapshotId") or "").strip()
    marker_reader = getattr(ontology_repository, "inferencebox_recovery_metadata", None)
    if callable(marker_reader):
        try:
            inference = marker_reader(world_id=clean_world_id)
        except Exception as error:  # noqa: BLE001 - active ABox availability is the recovery gate.
            inference = {
                "status": "error",
                "reason": "TypeDB active InferenceBox marker 조회 실패: " + str(error)[:180],
            }
    else:
        inference = {
            "status": "not-supported",
            "reason": "현재 저장소는 경량 InferenceBox 복구 표식을 제공하지 않습니다.",
        }
    inference = dict(inference or {}) if isinstance(inference, dict) else {"status": "invalid"}
    source_abox = str(inference.get("sourceAboxSnapshotId") or "").strip()
    aligned = bool(source_abox and source_abox == active_abox)
    ready = bool(active_status == "ok" and active_abox)
    return {
        "ready": ready,
        "recoveryMode": "active-abox-health-probe",
        "worldId": clean_world_id,
        "activeAboxSnapshotId": active_abox,
        "activeAboxStatus": active_status,
        "inferenceStatus": str(inference.get("status") or ""),
        "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
        "sourceAboxSnapshotId": source_abox,
        "inferenceGenerationAligned": aligned,
        "inferenceTargetSymbols": list(inference.get("targetSymbols") or []),
        "inferenceDiagnostic": str(inference.get("reason") or "")[:180],
        "requiresFreshProjection": not aligned,
        "reason": (
            "현재 활성 ABox를 읽을 수 있어 보류된 이벤트의 새 TypeDB 추론을 다시 시도합니다."
            if ready
            else str((active or {}).get("reason") or "현재 활성 ABox를 검증하지 못했습니다.")[:180]
        ),
    }


def active_versioned_reasoning_queue_state(
    registry,
    job_store,
    configured_v2_deployment_id: str = "",
) -> Dict[str, object]:
    """Read every protected V2 deployment's live TypeDB-writer backlog.

    Active and candidate releases can briefly coexist during an immutable
    release switch. Low-priority TypeDB writers must yield to either queue;
    observing only the active release lets maintenance or schema compilation
    race a candidate projection that is already processing.
    """
    try:
        control = registry.control()
        active_deployment_id = str(
            getattr(control, "active_deployment_id", "") or ""
        ).strip()
        requested_ids = list(dict.fromkeys(
            str(getattr(control, field, "") or "").strip()
            for field in ("active_deployment_id", "delivery_deployment_id")
            if str(getattr(control, field, "") or "").strip()
        ))
        candidate_id = str(
            getattr(control, "candidate_deployment_id", "") or ""
        ).strip()
        if candidate_id and candidate_id == str(configured_v2_deployment_id or "").strip():
            requested_ids.append(candidate_id)
        v2_ids = []
        for deployment_id in requested_ids:
            deployment = dict(registry.get(deployment_id) or {})
            engine_version = str(
                deployment.get("engineVersion")
                or deployment.get("engine_version")
                or ""
            ).strip().lower()
            if engine_version == "v2":
                v2_ids.append(deployment_id)
        if not v2_ids:
            return {
                "status": "not-active-v2",
                "deploymentId": active_deployment_id,
                "deploymentIds": [],
                "effectivePendingCount": 0,
            }
        states = {
            deployment_id: dict(job_store.live_queue_state(deployment_id) or {})
            for deployment_id in v2_ids
        }
        effective_pending = sum(
            max(0, int(state.get("effectivePendingCount") or 0))
            for state in states.values()
        )
        processing = sum(
            max(0, int(state.get("processingCount") or 0))
            for state in states.values()
        )
        queued = sum(
            max(0, int(state.get("queuedCount") or 0))
            for state in states.values()
        )
        primary = dict(states.get(active_deployment_id) or states[v2_ids[0]])
        return {
            **primary,
            "status": "active" if effective_pending else "idle",
            "deploymentId": active_deployment_id or v2_ids[0],
            "deploymentIds": v2_ids,
            "effectivePendingCount": effective_pending,
            "processingCount": processing,
            "queuedCount": queued,
            "queuesByDeployment": states,
        }
    except Exception as error:  # Fail closed so background writes cannot race an unknown V2 state.
        return {
            "status": "error",
            "effectivePendingCount": 1,
            "pendingCount": 1,
            "reason": str(error)[:180],
        }


def build_ontology_reasoning_queue_probe(settings=None):
    """Build a low-cost read-only signal for lower-priority workers.

    The full reasoning status is an operator diagnostic; it evaluates account
    priority and TypeDB health.  That is intentionally not a dependency of
    shared-world projection, ABox retention, or notification dispatch.
    """
    import time
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.reasoning.public import lightweight_ontology_reasoning_queue_state

    configured_settings = settings or runtime_settings()
    store_settings = dict(configured_settings)
    # This probe is called by isolated low-priority workers.  Do not let its
    # construction run schema DDL or operational-history retention before it
    # can yield.
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    event_reader = stores.event_log(store_settings)
    cursor_store = stores.ontology_reasoning_cursor_store(store_settings)
    mailbox_store = stores.ontology_reasoning_mailbox_store(store_settings)
    reasoning_registry = stores.reasoning_engine_registry_store(store_settings)
    versioned_job_store = stores.reasoning_engine_job_store(store_settings)
    try:
        cache_seconds = float(str(configured_settings.get("ontologyReasoningQueueProbeCacheSeconds") or "5").strip())
    except ValueError:
        cache_seconds = 5.0
    cache_seconds = max(0.0, min(60.0, cache_seconds))
    cache = {"at": 0.0, "value": None}

    def probe():
        now = time.monotonic()
        cached = cache.get("value")
        age = now - float(cache.get("at") or 0.0)
        if cached is not None and age >= 0 and age < cache_seconds:
            value = dict(cached or {})
            value["probeCached"] = True
            value["probeCacheAgeMs"] = int(age * 1000)
            return value
        try:
            legacy_value = lightweight_ontology_reasoning_queue_state(
                event_reader,
                cursor_store,
                mailbox_store=mailbox_store,
                settings=configured_settings,
            )
            versioned_value = active_versioned_reasoning_queue_state(
                reasoning_registry,
                versioned_job_store,
                configured_v2_deployment_id=str(
                    configured_settings.get("reasoningEngineV2DeploymentId") or ""
                ),
            )
            if str(versioned_value.get("status") or "") != "not-active-v2":
                value = {
                    **dict(versioned_value or {}),
                    "probeMode": "active-v2-authoritative-queue",
                    "queueMode": str(versioned_value.get("status") or "unknown"),
                    "activeReasoningEngineQueue": versioned_value,
                    "compatibilityQueueIncluded": True,
                    "legacyReasoningQueue": legacy_value,
                }
            else:
                value = dict(legacy_value or {})
            cache["at"] = time.monotonic()
            cache["value"] = dict(value or {})
            return value
        except Exception as error:  # noqa: BLE001 - the global TypeDB lease remains the final safety boundary.
            return {
                "status": "error",
                "effectivePendingCount": 0,
                "probeHealth": {"status": "degraded", "reason": str(error)[:180]},
                "queueHealth": {"status": "degraded", "reason": str(error)[:180], "scope": "probe-connectivity"},
            }

    return probe
