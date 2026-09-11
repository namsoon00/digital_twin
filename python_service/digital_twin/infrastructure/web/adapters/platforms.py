"""Web platforms boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.service_factory import build_ontology_reasoning_queue_probe
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import request_bool
from typing import Dict
from typing import List


def time_series_platform_status_payload() -> Dict[str, object]:
    from digital_twin.infrastructure.time_series_factory import build_time_series_backend_platform

    settings = operational_read_settings()
    return build_time_series_backend_platform(settings).status()


def reasoning_engine_platform_status_payload(
    query: Dict[str, List[str]] = None,
) -> Dict[str, object]:
    from digital_twin.infrastructure.reasoning_engine_factory import build_reasoning_engine_platform

    platform = build_reasoning_engine_platform(operational_read_settings())
    state = platform.initialize()
    include_history = request_bool(first_query(query or {}, "historical"), False)
    return platform.current_status(state, include_history=include_history)


def reasoning_engine_comparisons_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    from digital_twin.infrastructure.reasoning_engine_factory import build_reasoning_engine_platform

    settings = runtime_settings()
    deployment_id = str(first_query(query, "deploymentId") or "ontology-v2-shadow")
    try:
        limit = max(1, min(200, int(first_query(query, "limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    store = stores.reasoning_engine_comparison_store(settings)
    platform = build_reasoning_engine_platform(settings)
    platform.initialize()
    release = platform.release_identity(deployment_id)
    historical = str(first_query(query, "historical") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    release_fingerprint = "" if historical else str(release.get("releaseFingerprint") or "")
    cohort_id = "" if historical else str(release.get("validationCohortId") or "")
    return {
        "release": release,
        "historical": historical,
        "summary": store.summary(
            deployment_id,
            limit=limit,
            candidate_release_fingerprint=release_fingerprint,
            validation_cohort_id=cohort_id,
        ),
        "comparisons": store.latest(
            deployment_id,
            limit=limit,
            candidate_release_fingerprint=release_fingerprint,
            validation_cohort_id=cohort_id,
        ),
    }


def ontology_reasoning_status_payload() -> Dict[str, object]:
    """Expose scheduler-only queue health without running a TypeDB cycle."""
    try:
        # The full runner status evaluates account priority and TypeDB health.
        # The settings screen needs only queue liveness, so use the bounded
        # read probe instead of constructing the operational worker.
        configured = operational_read_settings()
        payload = dict(build_ontology_reasoning_queue_probe(configured)() or {})
        control = stores.reasoning_engine_registry_store(configured).control()
        completion_deployment_id = str(
            getattr(control, "delivery_deployment_id", "")
            or getattr(control, "active_deployment_id", "")
            or configured.get("reasoningEngineV2DeploymentId")
            or ""
        )
        payload["marketObservationReasoningCompletion"] = (
            stores.reasoning_engine_job_store(configured).market_observation_completion_summary(
                completion_deployment_id,
                limit=12,
            )
        )
        return payload
    except Exception as error:  # noqa: BLE001 - diagnostics must remain readable during store recovery.
        return {"enabled": False, "queueHealth": {"status": "error", "reason": str(error)[:240]}}
