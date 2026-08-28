"""Versioned reasoning-engine release registration and guarded switching."""

import hashlib
import inspect
from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping

from ..domain.reasoning_engine_versions import (
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
    promotion_blockers,
    reasoning_release_identity,
)
from ..domain.time_series_storage import TEMPORAL_FEATURE_SET_VERSION


class ReasoningEnginePlatformService:
    def __init__(
        self,
        registry,
        settings=None,
        comparison_store=None,
        shadow_queue=None,
        independent_job_store=None,
    ):
        self.registry = registry
        self.settings = dict(settings or {})
        self.comparison_store = comparison_store
        self.shadow_queue = shadow_queue
        self.independent_job_store = independent_job_store

    def independent_v2_enabled(self) -> bool:
        return str(
            self.settings.get("reasoningEngineV2IndependentEnabled") or "1"
        ).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def bool_setting(self, key: str, fallback: bool) -> bool:
        value = self.settings.get(key)
        if value is None or str(value).strip() == "":
            return bool(fallback)
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def int_setting(self, key: str, fallback: int, lower: int = 0, upper: int = 100000) -> int:
        try:
            value = int(float(str(self.settings.get(key) or fallback)))
        except (TypeError, ValueError):
            value = fallback
        return max(lower, min(upper, value))

    def float_setting(self, key: str, fallback: float, lower: float = 0.0, upper: float = 100000.0) -> float:
        try:
            value = float(str(self.settings.get(key) or fallback))
        except (TypeError, ValueError):
            value = fallback
        return max(lower, min(upper, value))

    def isolated_candidate_graph_database(
        self,
        deployment_id: str,
        release_id: str,
        protected_bindings: Iterable[str],
    ) -> str:
        protected = {str(item or "").strip() for item in protected_bindings if str(item or "").strip()}
        occupied = set(protected)
        list_deployments = getattr(self.registry, "list", None)
        if callable(list_deployments):
            for row in list_deployments() or []:
                item = dict(row or {})
                if str(item.get("deploymentId") or item.get("deployment_id") or "").strip() == str(
                    deployment_id or ""
                ).strip():
                    continue
                if str(item.get("status") or "").strip().lower() == "retired":
                    continue
                binding = str(
                    item.get("graphStoreBinding")
                    or item.get("graph_store_binding")
                    or ""
                ).strip()
                if binding:
                    occupied.add(binding)
        configured = str(
            self.settings.get("reasoningEngineCandidateTypeDbDatabase")
            or self.settings.get("reasoningEngineShadowTypeDbDatabase")
            or ""
        ).strip()
        if configured and configured not in occupied:
            return configured
        digest = hashlib.sha256(
            (str(deployment_id or "") + "|" + str(release_id or "")).encode("utf-8")
        ).hexdigest()[:16]
        return "orbit_alpha_ontology_candidate_" + digest

    def candidate_graph_isolation(self, control=None) -> Dict[str, object]:
        selected = control or self.registry.control()
        selected_mapping = dict(selected or {}) if isinstance(selected, Mapping) else {}

        def control_value(snake_name: str, camel_name: str) -> str:
            return str(
                selected_mapping.get(snake_name)
                or selected_mapping.get(camel_name)
                or getattr(selected, snake_name, "")
                or ""
            ).strip()

        candidate_id = control_value("candidate_deployment_id", "candidateDeploymentId")
        protected_ids = {
            control_value("active_deployment_id", "activeDeploymentId"),
            control_value("delivery_deployment_id", "deliveryDeploymentId"),
        }
        protected_bindings = set()
        getter = getattr(self.registry, "get", None)
        if not callable(getter):
            return {
                "status": "unavailable",
                "isolated": None,
                "candidateDeploymentId": candidate_id,
                "candidateGraphStoreBinding": "",
                "protectedGraphStoreBindings": [],
                "reasonCode": "graph-store-binding-unavailable",
            }
        for deployment_id in protected_ids:
            if not deployment_id:
                continue
            row = dict(getter(deployment_id) or {})
            binding = str(row.get("graphStoreBinding") or row.get("graph_store_binding") or "").strip()
            if binding:
                protected_bindings.add(binding)
        candidate = dict(getter(candidate_id) or {}) if candidate_id else {}
        candidate_binding = str(
            candidate.get("graphStoreBinding") or candidate.get("graph_store_binding") or ""
        ).strip()
        isolated = (
            bool(candidate_binding not in protected_bindings)
            if candidate_id and candidate_binding and protected_bindings
            else None
            if candidate_id
            else False
        )
        return {
            "status": (
                "isolated" if isolated is True
                else "blocked" if isolated is False and candidate_id
                else "unavailable" if candidate_id
                else "not-configured"
            ),
            "isolated": isolated,
            "candidateDeploymentId": candidate_id,
            "candidateGraphStoreBinding": candidate_binding,
            "protectedGraphStoreBindings": sorted(protected_bindings),
            "reasonCode": (
                "" if isolated is True or not candidate_id
                else "candidate-graph-store-not-isolated" if isolated is False
                else "graph-store-binding-unavailable"
            ),
        }

    def independent_queue_summary(
        self,
        deployment_id: str,
        release: Mapping[str, object],
    ) -> Dict[str, object]:
        lookback = self.int_setting("reasoningEnginePromotionComparisonLookback", 200, 1, 2000)
        parameters = inspect.signature(self.independent_job_store.summary).parameters
        kwargs = {"lookback": lookback}
        if "release_fingerprint" in parameters:
            kwargs["release_fingerprint"] = str(
                release.get("releaseFingerprint") or ""
            )
        if "validation_cohort_id" in parameters:
            kwargs["validation_cohort_id"] = str(
                release.get("validationCohortId") or ""
            )
        if "completed_since" in parameters:
            deployment = dict(self.registry.get(deployment_id) or {})
            health = dict(deployment.get("health") or {})
            kwargs["completed_since"] = str(
                health.get("validationStartedAt") or ""
            )
        return self.independent_job_store.summary(deployment_id, **kwargs)

    def descriptors(self):
        from ..domain.ontology_rulebox_release_manifest import RULEBOX_RELEASE_MANIFEST_VERSION
        from ..domain.ontology_schema import ONTOLOGY_TBOX_VERSION, tbox_fingerprint
        from ..domain.notification_ai_prompt_release import AI_DECISION_PROMPT_VERSION
        from ..infrastructure.typedb_ontology import TYPEDB_NATIVE_RULE_ENGINE_VERSION
        from ..domain.statistical_signals import (
            DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
            DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
            DEFAULT_EVENT_SIGNAL_RELEASE_ID,
            DEFAULT_FLOW_SIGNAL_RELEASE_ID,
            DEFAULT_PRICE_SIGNAL_RELEASE_ID,
            DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
            MODEL_SIGNAL_CONTRACT_VERSION,
        )

        active_backend = str(self.settings.get("timeSeriesActiveBackendId") or "mysql-primary")
        shadow_backend = str(self.settings.get("timeSeriesShadowBackendId") or "questdb-shadow")
        configured_v2_id = str(
            self.settings.get("reasoningEngineV2DeploymentId") or "ontology-v2-shadow"
        )
        configured_v1_id = str(
            self.settings.get("reasoningEngineV1DeploymentId") or "ontology-v1-active"
        )
        control_reader = getattr(self.registry, "control", None)
        try:
            control = control_reader() if callable(control_reader) else None
        except Exception:  # noqa: BLE001 - descriptor fallback remains deterministic without control state.
            control = None
        control_mapping = control if isinstance(control, Mapping) else {}
        active_ids = {
            str(
                getattr(control, "active_deployment_id", "")
                or control_mapping.get("active_deployment_id")
                or control_mapping.get("activeDeploymentId")
                or ""
            ),
            str(
                getattr(control, "delivery_deployment_id", "")
                or control_mapping.get("delivery_deployment_id")
                or control_mapping.get("deliveryDeploymentId")
                or ""
            ),
        }
        configured_v2_backend = str(
            self.settings.get("reasoningEngineV2TimeSeriesBackendId")
            or (active_backend if configured_v2_id in active_ids else shadow_backend)
        )
        v1_graph_database = str(
            self.settings.get("reasoningEngineV1TypeDbDatabase")
            or self.settings.get("typedbDatabase")
            or "orbit_alpha_ontology"
        )
        v2_graph_database = str(
            self.settings.get("reasoningEngineV2TypeDbDatabase")
            or self.settings.get("reasoningEngineShadowTypeDbDatabase")
            or "orbit_alpha_ontology_shadow_v2"
        )
        runtime = dict(self.settings.get("_runtimeIdentity") or {})
        common_bundle_values = dict(
            tbox_release_id=ONTOLOGY_TBOX_VERSION + "@" + tbox_fingerprint(),
            rulebox_release_id=RULEBOX_RELEASE_MANIFEST_VERSION,
            prompt_release_id=AI_DECISION_PROMPT_VERSION,
            feature_set_version=TEMPORAL_FEATURE_SET_VERSION,
            model_signal_release_id="+".join([
                str(
                    self.settings.get("statisticalPriceSignalReleaseId")
                    or DEFAULT_PRICE_SIGNAL_RELEASE_ID
                ),
                str(
                    self.settings.get("statisticalFlowSignalReleaseId")
                    or DEFAULT_FLOW_SIGNAL_RELEASE_ID
                ),
                DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
                DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
                DEFAULT_EVENT_SIGNAL_RELEASE_ID,
                DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
            ]),
            source_contract_versions=(
                "typedb-semantic-storage-v2",
                TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "time-series-storage-contract-v1",
                MODEL_SIGNAL_CONTRACT_VERSION,
            ),
            runtime_revision=str(runtime.get("revision") or "unknown"),
        )
        return [
            ReasoningEngineDescriptor(
                engine_family="ontology-investment-brain",
                engine_version="v1",
                deployment_id=configured_v1_id,
                status="active",
                graph_store_binding=v1_graph_database,
                time_series_backend_id=active_backend,
                release_bundle=EngineReleaseBundle(
                    **common_bundle_values,
                    release_id=str(self.settings.get("reasoningEngineActiveReleaseId") or "ontology-v1-release-r2"),
                ),
                capabilities={
                    "typedbNativeInference": True,
                    "productionDelivery": configured_v1_id in active_ids,
                    "shadowComparison": configured_v1_id not in active_ids,
                    "versionedFeatureSnapshot": True,
                },
            ),
            ReasoningEngineDescriptor(
                engine_family="ontology-investment-brain",
                engine_version="v2",
                deployment_id=configured_v2_id,
                status="provisioning",
                graph_store_binding=v2_graph_database,
                time_series_backend_id=configured_v2_backend,
                release_bundle=EngineReleaseBundle(
                    **common_bundle_values,
                    release_id=str(self.settings.get("reasoningEngineCandidateReleaseId") or "ontology-v2-release-r2"),
                ),
                capabilities={
                    "typedbNativeInference": True,
                    "productionDelivery": configured_v2_id in active_ids,
                    "shadowComparison": configured_v2_id not in active_ids,
                    "versionedFeatureSnapshot": True,
                    "independentExecution": self.independent_v2_enabled(),
                    "directSourceEvents": self.independent_v2_enabled(),
                    "monitorRunnerDependency": not self.independent_v2_enabled(),
                    "aiDecisionHandoff": True,
                },
            ),
        ]

    def graph_database_for(self, deployment_id: str) -> str:
        """Return the immutable TypeDB database binding for one deployment."""
        row = self.registry.get(str(deployment_id or ""))
        if not row:
            raise ValueError("Unknown reasoning engine deployment: " + str(deployment_id or ""))
        database = str(row.get("graphStoreBinding") or "").strip()
        if not database:
            raise RuntimeError(
                "Reasoning engine deployment has no TypeDB database binding: "
                + str(deployment_id or "")
            )
        return database

    def engine_version_for(self, deployment_id: str) -> str:
        row = self.registry.get(str(deployment_id or ""))
        if not row:
            raise ValueError("Unknown reasoning engine deployment: " + str(deployment_id or ""))
        return str(row.get("engineVersion") or "").strip().lower()

    def deployment_descriptor(self, deployment_id: str) -> ReasoningEngineDescriptor:
        """Rehydrate the immutable descriptor stored at release registration."""

        row = dict(self.registry.get(str(deployment_id or "")) or {})
        if not row:
            raise ValueError(
                "Unknown reasoning engine deployment: " + str(deployment_id or "")
            )
        bundle = dict(row.get("releaseBundle") or {})

        def bundle_value(snake: str, camel: str, fallback=""):
            return bundle.get(snake) if snake in bundle else bundle.get(camel, fallback)

        return ReasoningEngineDescriptor(
            engine_family=str(row.get("engineFamily") or row.get("engine_family") or ""),
            engine_version=str(row.get("engineVersion") or row.get("engine_version") or ""),
            deployment_id=str(row.get("deploymentId") or row.get("deployment_id") or ""),
            status=str(row.get("status") or "registered"),
            graph_store_binding=str(
                row.get("graphStoreBinding") or row.get("graph_store_binding") or ""
            ),
            time_series_backend_id=str(
                row.get("timeSeriesBackendId") or row.get("time_series_backend_id") or ""
            ),
            release_bundle=EngineReleaseBundle(
                tbox_release_id=str(bundle_value("tbox_release_id", "tboxReleaseId")),
                rulebox_release_id=str(bundle_value("rulebox_release_id", "ruleboxReleaseId")),
                prompt_release_id=str(bundle_value("prompt_release_id", "promptReleaseId")),
                feature_set_version=str(bundle_value("feature_set_version", "featureSetVersion")),
                model_signal_release_id=str(
                    bundle_value("model_signal_release_id", "modelSignalReleaseId")
                ),
                source_contract_versions=tuple(
                    str(value or "")
                    for value in bundle_value(
                        "source_contract_versions",
                        "sourceContractVersions",
                        [],
                    ) or []
                    if str(value or "")
                ),
                release_id=str(bundle_value("release_id", "releaseId")),
                runtime_contract_version=str(
                    bundle_value(
                        "runtime_contract_version",
                        "runtimeContractVersion",
                        "",
                    )
                ),
                runtime_revision=str(bundle_value("runtime_revision", "runtimeRevision")),
                comparison_contract_version=str(
                    bundle_value(
                        "comparison_contract_version",
                        "comparisonContractVersion",
                        "",
                    )
                ),
            ),
            capabilities=dict(row.get("capabilities") or {}),
        )

    def release_identity(self, deployment_id: str) -> Dict[str, str]:
        row = self.registry.get(deployment_id)
        health = dict(row.get("health") or {})
        bundle = dict(row.get("releaseBundle") or {})
        bundle_release_id = str(bundle.get("release_id") or bundle.get("releaseId") or deployment_id)
        bundle_runtime_revision = str(
            bundle.get("runtime_revision") or bundle.get("runtimeRevision") or "unknown"
        )
        health_matches_bundle = bool(
            str(
                health.get("candidateBaseReleaseId")
                or health.get("baseReleaseId")
                or health.get("candidateReleaseId")
                or ""
            ) == bundle_release_id
            and str(health.get("candidateRuntimeRevision") or "") == bundle_runtime_revision
        )
        if health_matches_bundle and str(
            health.get("candidateReleaseFingerprint") or health.get("releaseFingerprint") or ""
        ):
            return {
                "releaseId": str(
                    health.get("candidateReleaseId")
                    or bundle_release_id
                    or deployment_id
                ),
                "baseReleaseId": str(
                    health.get("candidateBaseReleaseId")
                    or health.get("baseReleaseId")
                    or bundle_release_id
                ),
                "runtimeRevision": str(
                    health.get("candidateRuntimeRevision")
                    or bundle_runtime_revision
                    or "unknown"
                ),
                "ruleboxFingerprint": str(health.get("ruleboxFingerprint") or ""),
                "tboxFingerprint": str(health.get("tboxFingerprint") or ""),
                "tboxReleaseId": str(health.get("tboxReleaseId") or ""),
                "ruleboxReleaseId": str(health.get("ruleboxReleaseId") or ""),
                "promptReleaseId": str(health.get("promptReleaseId") or ""),
                "modelSignalReleaseId": str(health.get("modelSignalReleaseId") or ""),
                "releaseFingerprint": str(
                    health.get("candidateReleaseFingerprint")
                    or health.get("releaseFingerprint")
                    or ""
                ),
                "validationCohortId": str(health.get("validationCohortId") or ""),
            }
        return reasoning_release_identity(row, health.get("ruleboxFingerprint") or "")

    def synchronize_control_capabilities(self, control) -> Dict[str, object]:
        """Make persisted display capabilities agree with control authority."""

        update = getattr(self.registry, "update_capabilities", None)
        if not callable(update):
            return {"status": "unsupported", "updatedDeploymentIds": []}
        control_mapping = control if isinstance(control, Mapping) else {}
        active = str(
            getattr(control, "active_deployment_id", "")
            or control_mapping.get("active_deployment_id")
            or control_mapping.get("activeDeploymentId")
            or ""
        )
        delivery = str(
            getattr(control, "delivery_deployment_id", "")
            or control_mapping.get("delivery_deployment_id")
            or control_mapping.get("deliveryDeploymentId")
            or ""
        )
        candidate = str(
            getattr(control, "candidate_deployment_id", "")
            or control_mapping.get("candidate_deployment_id")
            or control_mapping.get("candidateDeploymentId")
            or ""
        )
        production_ids = {value for value in (active, delivery) if value}
        updated = []
        for deployment_id in dict.fromkeys(
            value for value in (active, delivery, candidate) if value
        ):
            row = dict(self.registry.get(deployment_id) or {})
            if not row:
                continue
            capabilities = dict(row.get("capabilities") or {})
            production_delivery = deployment_id in production_ids
            expected = {
                **capabilities,
                "productionDelivery": production_delivery,
                "shadowComparison": not production_delivery,
            }
            if capabilities == expected:
                continue
            update(deployment_id, expected)
            updated.append(deployment_id)
        return {"status": "synchronized", "updatedDeploymentIds": updated}

    def initialize(self) -> Dict[str, object]:
        descriptors = self.descriptors()
        control = self.registry.control()
        for descriptor in descriptors:
            # A deployment ID is an immutable release identity. Runtime status
            # must not silently rewrite its bundle after a code update; a new
            # bundle is introduced only through register_v2_release().
            if not self.registry.get(descriptor.deployment_id):
                self.registry.upsert(descriptor)
        # A rolling V2 release has two valid descriptors at once: the active
        # historical release and the newly configured candidate.  The active
        # row remains authoritative until promotion switches control, even
        # though it is no longer emitted by ``descriptors()``.
        registered = list(self.registry.list() or [])
        known = {
            str(row.get("deploymentId") or row.get("deployment_id") or "")
            for row in registered
            if str(row.get("deploymentId") or row.get("deployment_id") or "")
        }
        known.update(descriptor.deployment_id for descriptor in descriptors)
        active = control.active_deployment_id
        delivery = control.delivery_deployment_id
        candidate = control.candidate_deployment_id
        if (
            active not in known
            or delivery not in known
            or (candidate and candidate not in known)
        ):
            active = str(self.settings.get("reasoningEngineActiveDeploymentId") or "ontology-v1-active")
            delivery = str(self.settings.get("reasoningEngineDeliveryDeploymentId") or active)
            candidate = str(self.settings.get("reasoningEngineCandidateDeploymentId") or "ontology-v2-shadow")
            if active not in known:
                active = str(self.settings.get("reasoningEngineV1DeploymentId") or "ontology-v1-active")
            if delivery not in known:
                delivery = active
            if candidate not in known or candidate in {active, delivery}:
                configured_v2 = str(self.settings.get("reasoningEngineV2DeploymentId") or "ontology-v2-shadow")
                candidate = configured_v2 if configured_v2 not in {active, delivery} else ""
            control = self.registry.set_control(active, delivery, candidate)
        capability_sync = self.synchronize_control_capabilities(control)
        retirement = {}
        retire = getattr(self.registry, "retire_unselected", None)
        if callable(retire):
            retirement = dict(retire(
                "v2",
                [
                    control.active_deployment_id,
                    control.delivery_deployment_id,
                    control.candidate_deployment_id,
                ],
            ) or {})
        response = {
            "control": control.to_dict(),
            "deployments": self.registry.list(),
            "deploymentRetirement": retirement,
            "controlCapabilitySync": capability_sync,
        }
        candidate_id = str(control.candidate_deployment_id or "")
        configured_v2_id = str(
            self.settings.get("reasoningEngineV2DeploymentId") or ""
        ).strip()
        configured_v2 = dict(self.registry.get(configured_v2_id) or {})
        deployment_by_id = {
            str(item.get("deploymentId") or ""): dict(item)
            for item in response["deployments"]
            if str(item.get("deploymentId") or "")
        }

        def usable_v2(deployment_id):
            descriptor = configured_v2 if deployment_id == configured_v2_id else deployment_by_id.get(deployment_id, {})
            return (
                str(descriptor.get("engineVersion") or "").lower() == "v2"
                and str(descriptor.get("status") or "").lower() != "retired"
            )

        independent_id = next((
            deployment_id
            for deployment_id in (
                configured_v2_id,
                candidate_id,
                str(control.delivery_deployment_id or ""),
                str(control.active_deployment_id or ""),
            )
            if deployment_id and usable_v2(deployment_id)
        ), "")
        independent_descriptor = (
            configured_v2 if independent_id == configured_v2_id
            else deployment_by_id.get(independent_id, {})
        )
        independent_release = self.release_identity(independent_id) if independent_id else {}
        response["independentDeploymentId"] = independent_id
        candidate_release = self.release_identity(candidate_id) if candidate_id else {}
        if self.shadow_queue is not None:
            response["shadowQueue"] = self.shadow_queue.summary(
                candidate_id,
                str(candidate_release.get("releaseId") or ""),
                str(candidate_release.get("runtimeRevision") or ""),
            )
        if self.comparison_store is not None and candidate_id:
            response["comparisonSummary"] = self.comparison_store.summary(
                candidate_id,
                limit=self.int_setting("reasoningEnginePromotionComparisonLookback", 200, 1, 2000),
                candidate_release_fingerprint=str(candidate_release.get("releaseFingerprint") or ""),
                validation_cohort_id=str(candidate_release.get("validationCohortId") or ""),
            )
        if self.independent_job_store is not None and independent_id:
            response["independentQueue"] = self.independent_queue_summary(
                independent_id,
                independent_release,
            )
            completion_summary = getattr(
                self.independent_job_store,
                "market_observation_completion_summary",
                None,
            )
            if callable(completion_summary):
                response["marketObservationReasoningCompletion"] = completion_summary(
                    independent_id,
                    limit=20,
                )
        if independent_id and independent_id not in {
            str(control.active_deployment_id or ""),
            str(control.delivery_deployment_id or ""),
        }:
            response["promotionReadiness"] = self.promotion_readiness(independent_id)
        elif independent_id:
            response["promotionReadiness"] = {
                "ready": True,
                "mode": "active-v2",
                "deploymentId": independent_id,
                "blockers": [],
                "health": dict(independent_descriptor.get("health") or {}),
            }
        return response

    @staticmethod
    def _compact_deployment(row: Mapping[str, object]) -> Dict[str, object]:
        """Return the bounded identity used by current-state diagnostics.

        The registry intentionally retains every immutable deployment, but a
        live status probe must not serialize retired release histories or full
        inference results. Those remain available from the historical view.
        """

        values = dict(row or {})
        bundle = dict(values.get("releaseBundle") or {})
        health = dict(values.get("health") or {})
        last_result = dict(health.get("lastResult") or {})
        rule_execution = dict(health.get("ruleExecutionReadiness") or {})
        runtime_ontology_release = dict(
            health.get("runtimeOntologyRelease") or {}
        )
        return {
            "deploymentId": str(values.get("deploymentId") or ""),
            "engineVersion": str(values.get("engineVersion") or ""),
            "status": str(values.get("status") or ""),
            "graphStoreBinding": str(values.get("graphStoreBinding") or ""),
            "timeSeriesBackendId": str(values.get("timeSeriesBackendId") or ""),
            "releaseId": str(bundle.get("release_id") or bundle.get("releaseId") or ""),
            "releaseFingerprint": str(
                health.get("releaseFingerprint")
                or health.get("candidateReleaseFingerprint")
                or ""
            ),
            "validationCohortId": str(health.get("validationCohortId") or ""),
            "capabilities": dict(values.get("capabilities") or {}),
            "ruleExecutionReadiness": {
                "status": str(rule_execution.get("status") or "ready"),
                "mode": str(rule_execution.get("mode") or "typedb-direct-typeql"),
            },
            "runtimeOntologyRelease": {
                "status": str(runtime_ontology_release.get("status") or ""),
                "catalogSource": str(
                    runtime_ontology_release.get("catalogSource") or ""
                ),
                "ruleCount": int(runtime_ontology_release.get("ruleCount") or 0),
                "sharedRuleCount": int(
                    runtime_ontology_release.get("sharedRuleCount") or 0
                ),
                "overlayRuleCount": int(
                    runtime_ontology_release.get("overlayRuleCount") or 0
                ),
                "tboxSource": str(
                    runtime_ontology_release.get("tboxSource") or ""
                ),
                "warmed": bool(runtime_ontology_release.get("warmed")),
            },
            "graphWriter": dict(health.get("graphWriter") or {}),
            "workerHeartbeats": dict(health.get("workerHeartbeats") or {}),
            "lastRun": {
                "status": str(last_result.get("status") or ""),
                "requestId": str(last_result.get("request_id") or last_result.get("requestId") or ""),
                "completedAt": str(
                    last_result.get("completed_at") or last_result.get("completedAt") or ""
                ),
                "durationMs": int(last_result.get("duration_ms") or last_result.get("durationMs") or 0),
                "traceComplete": bool(
                    last_result.get("trace_complete") or last_result.get("traceComplete")
                ),
                "symbols": list(last_result.get("symbols") or [])[:20],
            },
        }

    def current_status(
        self,
        state: Mapping[str, object] = None,
        include_history: bool = False,
    ) -> Dict[str, object]:
        """Expose one authoritative active-engine status read model.

        ``initialize`` remains the release-management view. This method is the
        operational view consumed by CLI, web and health dashboards so a
        retired V1 incident cannot masquerade as the current engine state.
        """

        platform_state = dict(state or self.initialize())
        control = dict(platform_state.get("control") or {})
        active_id = str(
            control.get("active_deployment_id")
            or control.get("activeDeploymentId")
            or ""
        )
        delivery_id = str(
            control.get("delivery_deployment_id")
            or control.get("deliveryDeploymentId")
            or ""
        )
        candidate_id = str(
            control.get("candidate_deployment_id")
            or control.get("candidateDeploymentId")
            or ""
        )
        deployments = [
            dict(item or {})
            for item in platform_state.get("deployments") or []
            if isinstance(item, Mapping)
        ]
        by_id = {
            str(item.get("deploymentId") or item.get("deployment_id") or ""): item
            for item in deployments
        }
        active_row = by_id.get(active_id) or {}
        active = self._compact_deployment(active_row)
        delivery = self._compact_deployment(by_id.get(delivery_id) or {})
        candidate = self._compact_deployment(by_id.get(candidate_id) or {}) if candidate_id else {}
        candidate_graph_isolation = self.candidate_graph_isolation(control)
        queue = dict(platform_state.get("independentQueue") or {})
        if str(queue.get("deploymentId") or "") != active_id:
            # During an immutable release registration the configured V2
            # worker points at the candidate while delivery remains on the
            # active release. Never turn that mismatch into a false empty
            # queue on the operational status surface.
            queue = {}
            if self.independent_job_store is not None and active_id:
                try:
                    queue = dict(self.independent_queue_summary(
                        active_id,
                        self.release_identity(active_id),
                    ) or {})
                except Exception as error:  # noqa: BLE001 - status remains explicit when storage is unavailable.
                    queue = {
                        "deploymentId": active_id,
                        "status": "unavailable",
                        "reason": str(error)[:180],
                    }
        completion = dict(
            platform_state.get("marketObservationReasoningCompletion") or {}
        )
        if str(completion.get("deploymentId") or "") != active_id:
            completion = {}
            completion_summary = getattr(
                self.independent_job_store,
                "market_observation_completion_summary",
                None,
            )
            if callable(completion_summary) and active_id:
                try:
                    completion = dict(completion_summary(active_id, limit=20) or {})
                except Exception as error:  # noqa: BLE001 - diagnostics stay explicit during storage recovery.
                    completion = {
                        "deploymentId": active_id,
                        "status": "unavailable",
                        "reason": str(error)[:180],
                    }
        queue_ids = list(dict.fromkeys(
            value for value in (active_id, delivery_id, candidate_id) if value
        ))
        queue_by_deployment = {}
        for deployment_id in queue_ids:
            if deployment_id == str(queue.get("deploymentId") or ""):
                queue_by_deployment[deployment_id] = dict(queue)
                continue
            if self.independent_job_store is None:
                continue
            try:
                queue_by_deployment[deployment_id] = dict(self.independent_queue_summary(
                    deployment_id,
                    self.release_identity(deployment_id),
                ) or {})
            except Exception as error:  # noqa: BLE001 - keep every role visible when one probe fails.
                queue_by_deployment[deployment_id] = {
                    "deploymentId": deployment_id,
                    "status": "unavailable",
                    "reason": str(error)[:180],
                }
        pending_count = int(queue.get("pendingCount") or 0)
        failure_count = int(queue.get("failureCount") or 0)
        unresolved_failure_count = int(
            queue.get("unresolvedFailureCount")
            if "unresolvedFailureCount" in queue
            else failure_count
        )
        oldest_pending_age = int(queue.get("oldestPendingAgeSeconds") or 0)
        delivery_heartbeats = dict(delivery.get("workerHeartbeats") or {})
        delivery_heartbeat = dict(delivery_heartbeats.get("delivery") or {})
        delivery_heartbeat_at = self.timestamp(delivery_heartbeat.get("updatedAt"))
        delivery_heartbeat_age = None
        heartbeat_critical_seconds = self.int_setting(
            "reasoningEngineWorkerHeartbeatCriticalSeconds", 90, 30, 3600
        )
        if delivery_heartbeat_at is not None:
            delivery_heartbeat_age = max(
                0,
                int((datetime.now(timezone.utc) - delivery_heartbeat_at).total_seconds()),
            )
        if not active_id or not active_row:
            status = "unavailable"
            reasons = ["active-reasoning-deployment-unavailable"]
        else:
            reasons = []
            if delivery_id != active_id:
                reasons.append("active-delivery-deployment-mismatch")
            if unresolved_failure_count:
                reasons.append("reasoning-failures-present")
            if oldest_pending_age >= self.int_setting(
                "ontologyReasoningQueueCriticalAgeMinutes", 5, 1, 1440
            ) * 60:
                reasons.append("reasoning-queue-critical-age")
            if pending_count and (
                delivery_heartbeat_age is None
                or delivery_heartbeat_age > heartbeat_critical_seconds
            ):
                reasons.append("delivery-reasoning-worker-heartbeat-missing")
            if candidate_id and candidate_graph_isolation.get("isolated") is False:
                reasons.append("candidate-graph-store-not-isolated")
            status = "ready" if not reasons else "degraded"
        result = {
            "status": status,
            "reasons": reasons,
            "control": {
                "activeDeploymentId": active_id,
                "deliveryDeploymentId": delivery_id,
                "candidateDeploymentId": candidate_id,
            },
            "activeDeployment": active,
            "deliveryDeployment": delivery,
            "candidateDeployment": candidate,
            "candidateGraphIsolation": candidate_graph_isolation,
            "writerTopology": {
                "mode": (
                    "single-process"
                    if self.bool_setting("ontologyGraphSingleWriterEnabled", True)
                    else "legacy-multi-process"
                ),
                "graphStoreBinding": str(delivery.get("graphStoreBinding") or ""),
                "owner": dict(delivery.get("graphWriter") or {}),
                "worldProjectionEmbedded": self.bool_setting(
                    "ontologyGraphSingleWriterEnabled", True
                ),
                "maintenanceEmbedded": self.bool_setting(
                    "ontologyGraphSingleWriterEnabled", True
                ),
            },
            "workerLiveness": {
                "delivery": {
                    **delivery_heartbeat,
                    "ageSeconds": delivery_heartbeat_age,
                    "healthy": (
                        delivery_heartbeat_age is not None
                        and delivery_heartbeat_age <= heartbeat_critical_seconds
                    ),
                    "criticalAfterSeconds": heartbeat_critical_seconds,
                },
                "candidate": dict(
                    (candidate.get("workerHeartbeats") or {}).get("candidate") or {}
                ),
            },
            "queue": {
                "deploymentId": str(queue.get("deploymentId") or active_id),
                "status": str(queue.get("status") or "available"),
                "pendingCount": pending_count,
                "awaitingSourceCount": int(queue.get("awaitingSourceCount") or 0),
                "awaitingWorldProjectionCount": int(
                    queue.get("awaitingWorldProjectionCount") or 0
                ),
                "failureCount": failure_count,
                "unresolvedFailureCount": unresolved_failure_count,
                "resolvedFailureCount": int(queue.get("resolvedFailureCount") or 0),
                "recentFailureCount24h": int(queue.get("recentFailureCount24h") or 0),
                "latestUnresolvedFailureAt": str(
                    queue.get("latestUnresolvedFailureAt") or ""
                ),
                "unresolvedFailureReasonCounts": dict(
                    queue.get("unresolvedFailureReasonCounts") or {}
                ),
                "oldestPendingAgeSeconds": oldest_pending_age,
                "uniqueCompletedRunCount": int(
                    queue.get("uniqueCompletedRunCount")
                    or queue.get("successfulRunCount")
                    or 0
                ),
                "successfulRunCount": int(queue.get("successfulRunCount") or 0),
                "traceCompleteRunCount": int(queue.get("traceCompleteRunCount") or 0),
                "durationP95Ms": int(queue.get("durationP95Ms") or 0),
                "queueWaitP95Ms": int(queue.get("queueWaitP95Ms") or 0),
                "endToEndP95Ms": int(queue.get("endToEndP95Ms") or 0),
                "latestCompletedAt": str(queue.get("latestCompletedAt") or ""),
                "jobRowCounts": dict(queue.get("jobRowCounts") or queue.get("counts") or {}),
            },
            "marketObservationReasoningCompletion": completion,
            "queues": {
                role: {
                    "deploymentId": deployment_id,
                    "workerRole": role,
                    "productionDelivery": role in {"active", "delivery"},
                    **self._compact_queue_summary(
                        queue_by_deployment.get(deployment_id) or {}
                    ),
                }
                for role, deployment_id in {
                    "active": active_id,
                    "delivery": delivery_id,
                    "candidate": candidate_id,
                }.items()
                if deployment_id
            },
        }
        if include_history:
            result["deployments"] = deployments
            result["releaseManagement"] = platform_state
        return result

    @staticmethod
    def _compact_queue_summary(summary: Mapping[str, object]) -> Dict[str, object]:
        """Keep the role-level status payload bounded for polling clients."""

        values = dict(summary or {})
        return {
            "status": str(values.get("status") or "available"),
            "pendingCount": int(values.get("pendingCount") or 0),
            "awaitingSourceCount": int(values.get("awaitingSourceCount") or 0),
            "awaitingWorldProjectionCount": int(
                values.get("awaitingWorldProjectionCount") or 0
            ),
            "failureCount": int(values.get("failureCount") or 0),
            "unresolvedFailureCount": int(
                values.get("unresolvedFailureCount")
                if "unresolvedFailureCount" in values
                else values.get("failureCount") or 0
            ),
            "resolvedFailureCount": int(values.get("resolvedFailureCount") or 0),
            "recentFailureCount24h": int(values.get("recentFailureCount24h") or 0),
            "oldestPendingAgeSeconds": int(values.get("oldestPendingAgeSeconds") or 0),
            "uniqueCompletedRunCount": int(
                values.get("uniqueCompletedRunCount")
                or values.get("successfulRunCount")
                or 0
            ),
            "durationP95Ms": int(values.get("durationP95Ms") or 0),
            "queueWaitP95Ms": int(values.get("queueWaitP95Ms") or 0),
            "endToEndP95Ms": int(values.get("endToEndP95Ms") or 0),
            "latestCompletedAt": str(values.get("latestCompletedAt") or ""),
            "jobRowCounts": dict(values.get("jobRowCounts") or values.get("counts") or {}),
            "reason": str(values.get("reason") or ""),
        }

    def register_v2_release(
        self,
        deployment_id: str,
        release_id: str,
        graph_database: str = "",
    ) -> Dict[str, object]:
        """Register a new V2 candidate without disturbing active delivery."""

        clean_deployment_id = str(deployment_id or "").strip()
        clean_release_id = str(release_id or "").strip()
        if not clean_deployment_id or not clean_release_id:
            return {
                "status": "blocked",
                "blockers": ["deployment-and-release-id-required"],
            }

        control = self.registry.control()
        protected = {
            str(control.active_deployment_id or ""),
            str(control.delivery_deployment_id or ""),
        }
        if clean_deployment_id in protected:
            return {
                "status": "blocked",
                "deploymentId": clean_deployment_id,
                "blockers": ["candidate-must-not-rewrite-active-delivery-release"],
            }

        existing = dict(self.registry.get(clean_deployment_id) or {})
        if existing and str(existing.get("status") or "") != "retired":
            existing_bundle = dict(existing.get("releaseBundle") or {})
            existing_release_id = str(
                existing_bundle.get("release_id")
                or existing_bundle.get("releaseId")
                or ""
            )
            if existing_release_id != clean_release_id:
                return {
                    "status": "blocked",
                    "deploymentId": clean_deployment_id,
                    "blockers": ["deployment-id-already-bound-to-another-release"],
                }

        base = next(
            descriptor
            for descriptor in self.descriptors()
            if str(descriptor.engine_version or "").lower() == "v2"
        )
        active_row = dict(self.registry.get(str(control.active_deployment_id or "")) or {})
        active_is_v2 = str(
            active_row.get("engineVersion") or active_row.get("engine_version") or ""
        ).strip().lower() == "v2"
        protected_graph_stores = set()
        for protected_id in protected:
            protected_row = dict(self.registry.get(protected_id) or {})
            protected_binding = str(
                protected_row.get("graphStoreBinding")
                or protected_row.get("graph_store_binding")
                or ""
            ).strip()
            if protected_binding:
                protected_graph_stores.add(protected_binding)
        requested_graph_store = str(graph_database or "").strip()
        if requested_graph_store and requested_graph_store in protected_graph_stores:
            return {
                "status": "blocked",
                "deploymentId": clean_deployment_id,
                "blockers": ["candidate-graph-store-must-be-isolated"],
                "graphStoreBinding": requested_graph_store,
                "protectedGraphStoreBindings": sorted(protected_graph_stores),
            }
        candidate_graph_store = requested_graph_store or self.isolated_candidate_graph_database(
            clean_deployment_id,
            clean_release_id,
            protected_graph_stores,
        )
        inherited_time_series = str(
            active_row.get("timeSeriesBackendId")
            or active_row.get("time_series_backend_id")
            or ""
        ).strip() if active_is_v2 else ""
        bundle = base.release_bundle
        descriptor = ReasoningEngineDescriptor(
            engine_family=base.engine_family,
            engine_version=base.engine_version,
            deployment_id=clean_deployment_id,
            status="provisioning",
            graph_store_binding=candidate_graph_store,
            time_series_backend_id=inherited_time_series or base.time_series_backend_id,
            release_bundle=EngineReleaseBundle(
                tbox_release_id=bundle.tbox_release_id,
                rulebox_release_id=bundle.rulebox_release_id,
                prompt_release_id=bundle.prompt_release_id,
                feature_set_version=bundle.feature_set_version,
                model_signal_release_id=bundle.model_signal_release_id,
                source_contract_versions=bundle.source_contract_versions,
                release_id=clean_release_id,
                runtime_contract_version=bundle.runtime_contract_version,
                runtime_revision=bundle.runtime_revision,
                comparison_contract_version=bundle.comparison_contract_version,
            ),
            capabilities=dict(base.capabilities),
        )
        self.registry.upsert(descriptor)
        update_health = getattr(self.registry, "update_health", None)
        if requested_graph_store and callable(update_health):
            # Supplying a graph database is an explicit assertion that its
            # storage lifecycle is managed outside this release registration.
            # Preserve that bootstrap contract so a provisioning worker does
            # not redefine an already complete TypeDB schema.
            existing_health = dict((self.registry.get(clean_deployment_id) or {}).get("health") or {})
            update_health(clean_deployment_id, {
                **existing_health,
                "graphStoreProvisioning": {
                    "mode": "reuse-existing",
                    "database": candidate_graph_store,
                    "source": "explicit-release-registration",
                },
            })
        if existing and str(existing.get("status") or "") == "retired":
            self.registry.transition(clean_deployment_id, "provisioning")
        next_control = self.registry.set_control(
            control.active_deployment_id,
            control.delivery_deployment_id,
            clean_deployment_id,
            expected_version=control.version,
        )
        capability_sync = self.synchronize_control_capabilities(next_control)
        return {
            "status": "registered",
            "deployment": self.registry.get(clean_deployment_id),
            "control": next_control.to_dict(),
            "controlCapabilitySync": capability_sync,
        }

    @staticmethod
    def timestamp(value: object):
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def promotion_readiness(
        self,
        deployment_id: str,
        allow_recovered_queue_wait: bool = False,
    ) -> Dict[str, object]:
        if self.independent_v2_enabled() and self.independent_job_store is not None:
            return self.independent_promotion_readiness(
                deployment_id,
                allow_recovered_queue_wait=allow_recovered_queue_wait,
            )
        row = self.registry.get(deployment_id)
        release = self.release_identity(deployment_id)
        summary = (
            self.comparison_store.summary(
                deployment_id,
                limit=self.int_setting("reasoningEnginePromotionComparisonLookback", 200, 1, 2000),
                candidate_release_fingerprint=str(release.get("releaseFingerprint") or ""),
                validation_cohort_id=str(release.get("validationCohortId") or ""),
            )
            if self.comparison_store is not None
            else {}
        )
        blockers = []
        if str(row.get("status") or "") not in {"shadow", "candidate"}:
            blockers.append("engine-not-shadow-or-candidate")
        health = dict(row.get("health") or {})
        if str(health.get("status") or "").lower() not in {"ready", "healthy"}:
            blockers.append("engine-unhealthy")
        if int(summary.get("sampleCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumComparisons", 100, 1, 10000
        ):
            blockers.append("insufficient-comparison-samples")
        if int(summary.get("distinctSymbolCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumSymbols", 10, 1, 10000
        ):
            blockers.append("insufficient-symbol-coverage")
        if int(summary.get("nonEmptyNativeInferenceSampleCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumNativeInferenceSamples", 30, 1, 10000
        ):
            blockers.append("insufficient-native-inference-coverage")
        if int(summary.get("nonEmptyDecisionSampleCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumDecisionSamples", 30, 1, 10000
        ):
            blockers.append("insufficient-decision-coverage")
        if int(summary.get("distinctMatchedRuleCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumMatchedRules", 5, 1, 10000
        ):
            blockers.append("insufficient-matched-rule-coverage")
        if int(summary.get("marketClassCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumMarketClasses", 2, 1, 10
        ):
            blockers.append("insufficient-market-coverage")
        if float(summary.get("minimumFactParityPct") or 0.0) < 100.0:
            blockers.append("fact-parity-incomplete")
        if float(summary.get("minimumRuleSlotCoveragePct") or 0.0) < 100.0:
            blockers.append("rule-slot-coverage-incomplete")
        if int(summary.get("unexplainedDecisionDifferenceCount") or 0) > 0:
            blockers.append("unexplained-decision-differences")
        if int(summary.get("shadowDeliveryCount") or 0) > 0:
            blockers.append("shadow-delivery-detected")
        execution_failures = int((summary.get("statusCounts") or {}).get("candidate-failed") or 0)
        if execution_failures > self.int_setting(
            "reasoningEnginePromotionMaximumExecutionFailures", 0, 0, 10000
        ):
            blockers.append("candidate-execution-failures")
        baseline_p95 = int(summary.get("baselineP95DurationMs") or 0)
        candidate_p95 = int(summary.get("candidateP95DurationMs") or 0)
        latency_ratio = round(candidate_p95 / baseline_p95, 3) if baseline_p95 > 0 else 0.0
        max_latency_ratio = self.float_setting(
            "reasoningEnginePromotionMaximumLatencyRatio", 3.0, 1.0, 100.0
        )
        if baseline_p95 > 0 and latency_ratio > max_latency_ratio:
            blockers.append("candidate-latency-regression")
        maximum_candidate_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumCandidateP95Ms", 180000, 1000, 3600000
        )
        if candidate_p95 <= 0 or candidate_p95 > maximum_candidate_p95:
            blockers.append("candidate-absolute-latency-slo-breached")
        queue_wait_p95 = int(summary.get("queueWaitP95Ms") or 0)
        maximum_queue_wait_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumQueueWaitP95Ms", 60000, 1000, 3600000
        )
        if queue_wait_p95 > maximum_queue_wait_p95:
            blockers.append("candidate-queue-wait-slo-breached")
        end_to_end_p95 = int(
            summary.get("candidateEndToEndP95Ms")
            or candidate_p95 + queue_wait_p95
        )
        maximum_end_to_end_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumEndToEndP95Ms",
            maximum_candidate_p95 + maximum_queue_wait_p95,
            1000,
            3600000,
        )
        if end_to_end_p95 <= 0 or end_to_end_p95 > maximum_end_to_end_p95:
            blockers.append("candidate-end-to-end-latency-slo-breached")
        latest = self.timestamp(summary.get("latestComparisonAt"))
        maximum_age = self.int_setting(
            "reasoningEnginePromotionMaximumComparisonAgeSeconds", 3600, 60, 7 * 24 * 60 * 60
        )
        age_seconds = (
            max(0, int((datetime.now(timezone.utc) - latest).total_seconds()))
            if latest
            else None
        )
        if age_seconds is None or age_seconds > maximum_age:
            blockers.append("comparison-window-stale")
        return {
            "ready": not blockers,
            "deploymentId": str(deployment_id or ""),
            "blockers": list(dict.fromkeys(blockers)),
            "comparison": summary,
            "health": health,
            "latencyRatio": latency_ratio,
            "maximumLatencyRatio": max_latency_ratio,
            "release": release,
            "maximumCandidateP95Ms": maximum_candidate_p95,
            "maximumQueueWaitP95Ms": maximum_queue_wait_p95,
            "endToEndP95Ms": end_to_end_p95,
            "maximumEndToEndP95Ms": maximum_end_to_end_p95,
            "latestComparisonAgeSeconds": age_seconds,
            "maximumComparisonAgeSeconds": maximum_age,
        }

    def independent_promotion_readiness(
        self,
        deployment_id: str,
        allow_recovered_queue_wait: bool = False,
    ) -> Dict[str, object]:
        row = self.registry.get(deployment_id)
        health = dict(row.get("health") or {})
        release = self.release_identity(deployment_id)
        summary = self.independent_queue_summary(
            deployment_id,
            release,
        )
        blockers = []
        warnings = []
        if str(row.get("status") or "") not in {"shadow", "candidate"}:
            blockers.append("engine-not-shadow-or-candidate")
        if str(health.get("status") or "").lower() not in {"ready", "healthy"}:
            blockers.append("engine-unhealthy")

        minimum_runs = self.int_setting(
            "reasoningEngineV2PromotionMinimumRuns", 5, 1, 10000
        )
        successful_runs = int(summary.get("successfulRunCount") or 0)
        if successful_runs < minimum_runs:
            blockers.append("insufficient-independent-runs")
        if int(summary.get("distinctSymbolCount") or 0) < self.int_setting(
            "reasoningEngineV2PromotionMinimumSymbols", 3, 1, 10000
        ):
            blockers.append("insufficient-symbol-coverage")
        if int(summary.get("candidateEventRunCount") or 0) < self.int_setting(
            "reasoningEngineV2PromotionMinimumCandidateRuns", 2, 1, 10000
        ):
            blockers.append("insufficient-decision-candidate-coverage")
        if int(summary.get("traceCompleteRunCount") or 0) < successful_runs:
            blockers.append("inference-trace-incomplete")
        if int(summary.get("failureCount") or 0) > self.int_setting(
            "reasoningEngineV2PromotionMaximumFailures", 0, 0, 10000
        ):
            blockers.append("independent-execution-failures")
        if int(summary.get("shadowDeliveryAuthorizedRunCount") or 0) > 0:
            blockers.append("shadow-delivery-detected")

        candidate_p95 = int(summary.get("durationP95Ms") or 0)
        maximum_candidate_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumCandidateP95Ms", 180000, 1000, 3600000
        )
        if candidate_p95 <= 0 or candidate_p95 > maximum_candidate_p95:
            blockers.append("candidate-absolute-latency-slo-breached")
        queue_wait_p95 = int(summary.get("queueWaitP95Ms") or 0)
        maximum_queue_wait_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumQueueWaitP95Ms", 60000, 1000, 3600000
        )
        pending_count = int(summary.get("pendingCount") or 0)
        oldest_pending_age_seconds = int(summary.get("oldestPendingAgeSeconds") or 0)
        recovered_queue_wait = bool(
            allow_recovered_queue_wait
            and queue_wait_p95 > maximum_queue_wait_p95
            and pending_count == 0
            and oldest_pending_age_seconds == 0
        )
        if queue_wait_p95 > maximum_queue_wait_p95 and not recovered_queue_wait:
            blockers.append("candidate-queue-wait-slo-breached")
        elif recovered_queue_wait:
            warnings.append("historical-queue-wait-slo-breached-but-current-queue-drained")
        end_to_end_p95 = int(
            summary.get("endToEndP95Ms")
            or candidate_p95 + queue_wait_p95
        )
        maximum_end_to_end_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumEndToEndP95Ms",
            maximum_candidate_p95 + maximum_queue_wait_p95,
            1000,
            3600000,
        )
        recovered_end_to_end = bool(
            recovered_queue_wait
            and end_to_end_p95 > maximum_end_to_end_p95
            and candidate_p95 <= maximum_candidate_p95
        )
        if end_to_end_p95 <= 0 or (
            end_to_end_p95 > maximum_end_to_end_p95 and not recovered_end_to_end
        ):
            blockers.append("candidate-end-to-end-latency-slo-breached")
        elif recovered_end_to_end:
            warnings.append("historical-end-to-end-slo-breached-but-current-queue-drained")

        latest = self.timestamp(summary.get("latestCompletedAt"))
        maximum_age = self.int_setting(
            "reasoningEnginePromotionMaximumComparisonAgeSeconds", 3600, 60, 7 * 24 * 60 * 60
        )
        age_seconds = (
            max(0, int((datetime.now(timezone.utc) - latest).total_seconds()))
            if latest
            else None
        )
        if age_seconds is None or age_seconds > maximum_age:
            blockers.append("independent-run-window-stale")
        if oldest_pending_age_seconds > max(
            1, maximum_queue_wait_p95 // 1000
        ):
            blockers.append("independent-queue-stale")
        return {
            "ready": not blockers,
            "mode": "independent-v2",
            "deploymentId": str(deployment_id or ""),
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": list(dict.fromkeys(warnings)),
            "independentExecution": summary,
            "health": health,
            "release": release,
            "minimumSuccessfulRuns": minimum_runs,
            "maximumCandidateP95Ms": maximum_candidate_p95,
            "maximumQueueWaitP95Ms": maximum_queue_wait_p95,
            "endToEndP95Ms": end_to_end_p95,
            "maximumEndToEndP95Ms": maximum_end_to_end_p95,
            "latestRunAgeSeconds": age_seconds,
            "maximumRunAgeSeconds": maximum_age,
            "recoveredQueueWaitOverrideApplied": recovered_queue_wait,
        }

    def mark_candidate(
        self,
        deployment_id: str,
        allow_recovered_queue_wait: bool = False,
    ) -> Dict[str, object]:
        row = self.registry.get(deployment_id)
        status = str(row.get("status") or "")
        health = dict(row.get("health") or {})
        health_status = str(health.get("status") or "").lower()
        validation_window_statuses = {"provisioning", "replaying", "shadow", "candidate"}
        if (
            status in validation_window_statuses
            and health_status in {"ready", "healthy"}
            and not str(health.get("validationStartedAt") or "").strip()
        ):
            update_health = getattr(self.registry, "update_health", None)
            if callable(update_health):
                health["validationStartedAt"] = (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                )
                update_health(deployment_id, health)
        if status in {"provisioning", "replaying"} and health_status in {"ready", "healthy"}:
            self.registry.transition(deployment_id, "shadow")
        readiness = self.promotion_readiness(
            deployment_id,
            allow_recovered_queue_wait=allow_recovered_queue_wait,
        )
        if not readiness.get("ready"):
            return {
                "status": "blocked",
                "deploymentId": str(deployment_id or ""),
                "blockers": readiness.get("blockers") or [],
                "promotionReadiness": readiness,
            }
        row = self.registry.get(deployment_id)
        if str(row.get("status") or "") == "shadow":
            row = self.registry.transition(deployment_id, "candidate")
        return {"status": "candidate", "deployment": row, "promotionReadiness": readiness}

    def promote_from_history(
        self,
        deployment_id: str,
        allow_recovered_queue_wait: bool = False,
    ) -> Dict[str, object]:
        readiness = self.promotion_readiness(
            deployment_id,
            allow_recovered_queue_wait=allow_recovered_queue_wait,
        )
        if not readiness.get("ready"):
            return {
                "status": "blocked",
                "deploymentId": str(deployment_id or ""),
                "blockers": readiness.get("blockers") or [],
                "promotionReadiness": readiness,
            }
        row = self.registry.get(deployment_id)
        if str(row.get("status") or "") != "candidate":
            return {
                "status": "blocked",
                "deploymentId": str(deployment_id or ""),
                "blockers": ["engine-not-candidate"],
                "promotionReadiness": readiness,
            }
        if readiness.get("mode") == "independent-v2":
            return self.activate(deployment_id, readiness)
        summary = dict(readiness.get("comparison") or {})
        summary.update({
            "factParityPct": summary.get("minimumFactParityPct"),
            "ruleSlotCoveragePct": summary.get("minimumRuleSlotCoveragePct"),
        })
        return self.promote(deployment_id, readiness.get("health") or {}, summary)

    def activate(self, deployment_id: str, readiness: Mapping[str, object]) -> Dict[str, object]:
        control = self.registry.control()
        previous = control.active_deployment_id
        if previous and previous != deployment_id:
            self.registry.transition(previous, "candidate")
        self.registry.transition(deployment_id, "active")
        next_control = self.registry.set_control(
            deployment_id,
            deployment_id,
            previous,
            expected_version=control.version,
        )
        capability_sync = self.synchronize_control_capabilities(next_control)
        cleanup = self.supersede_inactive_pending_work(previous)
        return {
            "status": "promoted",
            "control": next_control.to_dict(),
            "promotionReadiness": dict(readiness or {}),
            "previousDeploymentQueueCleanup": cleanup,
            "controlCapabilitySync": capability_sync,
        }

    def supersede_inactive_pending_work(self, deployment_id: str) -> Dict[str, object]:
        cleanup = getattr(self.independent_job_store, "supersede_pending_deployment", None)
        if not deployment_id or not callable(cleanup):
            return {"status": "unsupported", "supersededCount": 0}
        return dict(cleanup(
            deployment_id,
            "The reasoning deployment is inactive after a control-plane switch.",
        ) or {})

    def promote(self, deployment_id: str, health: Mapping[str, object], comparison: Mapping[str, object]):
        row = self.registry.get(deployment_id)
        descriptor = next(
            (item for item in self.descriptors() if item.deployment_id == str(deployment_id or "")),
            None,
        )
        if not descriptor:
            raise ValueError("Unknown reasoning engine deployment: " + str(deployment_id or ""))
        descriptor = ReasoningEngineDescriptor(
            engine_family=descriptor.engine_family,
            engine_version=descriptor.engine_version,
            deployment_id=descriptor.deployment_id,
            status=str(row.get("status") or descriptor.status),
            graph_store_binding=descriptor.graph_store_binding,
            time_series_backend_id=descriptor.time_series_backend_id,
            release_bundle=descriptor.release_bundle,
            capabilities=descriptor.capabilities,
        )
        blockers = promotion_blockers(descriptor, health, comparison)
        if blockers:
            return {"status": "blocked", "deploymentId": deployment_id, "blockers": list(blockers)}
        control = self.registry.control()
        previous = control.active_deployment_id
        if previous and previous != deployment_id:
            self.registry.transition(previous, "candidate")
        self.registry.transition(deployment_id, "active")
        next_control = self.registry.set_control(
            deployment_id,
            deployment_id,
            previous,
            expected_version=control.version,
        )
        capability_sync = self.synchronize_control_capabilities(next_control)
        return {
            "status": "promoted",
            "control": next_control.to_dict(),
            "controlCapabilitySync": capability_sync,
        }

    def rollback(self) -> Dict[str, object]:
        control = self.registry.control()
        fallback = control.candidate_deployment_id
        if not fallback:
            raise ValueError("No rollback deployment is registered")
        fallback_row = self.registry.get(fallback)
        if str(fallback_row.get("status") or "") not in {"candidate", "active"}:
            return {
                "status": "blocked",
                "deploymentId": fallback,
                "blockers": ["rollback-deployment-not-previously-active"],
            }
        if control.active_deployment_id and control.active_deployment_id != fallback:
            self.registry.transition(control.active_deployment_id, "candidate")
        self.registry.transition(fallback, "active")
        next_control = self.registry.set_control(
            fallback,
            fallback,
            control.active_deployment_id,
            expected_version=control.version,
        )
        capability_sync = self.synchronize_control_capabilities(next_control)
        cleanup = self.supersede_inactive_pending_work(control.active_deployment_id)
        return {
            "status": "rolled-back",
            "control": next_control.to_dict(),
            "previousDeploymentQueueCleanup": cleanup,
            "controlCapabilitySync": capability_sync,
        }
