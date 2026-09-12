"""Explicit V2 reasoning binding composition phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


@dataclass(frozen=True)
class ReasoningBinding:
    candidate_rulebox: Dict[str, Any]
    rulebox_release_preflight: Dict[str, Any]
    rulebox_fingerprint: str
    release_identity: Dict[str, Any]
    release_artifact_persistence: Dict[str, Any]


def bind_v2_release(
    repository, platform, descriptor, candidate_settings, configured, deployment_health
) -> ReasoningBinding:
    from digital_twin.modules.model_registry.domain.investment_ubiquitous_language import investment_language_registry
    from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
    from digital_twin.modules.reasoning.domain.ontology_schema import default_tbox_metadata
    from digital_twin.modules.reasoning.domain.reasoning_engine_versions import reasoning_release_identity
    from digital_twin.modules.reasoning.domain.reasoning_shadow import payload_hash
    from digital_twin.infrastructure.composition.reasoning_release import prepare_v2_rulebox_release
    from digital_twin.infrastructure.graph_store_lifecycle import ontology_release_seed_artifact

    engine_control = platform.registry.control()
    protected_deployment_ids = {
        str(engine_control.active_deployment_id or "").strip(),
        str(engine_control.delivery_deployment_id or "").strip(),
    }
    protected_deployment_ids.discard("")
    frozen_release_recorded = bool(
        str(deployment_health.get("candidateReleaseId") or "").strip()
        and str(deployment_health.get("ruleboxFingerprint") or "").strip()
    )
    read_release_artifact = getattr(platform.registry, "release_artifact", None)
    stored_release_artifact = (
        dict(read_release_artifact(descriptor.deployment_id) or {})
        if callable(read_release_artifact) else {}
    )
    stored_payload = dict(stored_release_artifact.get("artifact") or {})
    if stored_release_artifact and (stored_release_artifact.get("valid") is False or not stored_payload):
        raise RuntimeError("The stored reasoning release artifact is corrupt or empty")
    restored = {}
    if stored_payload:
        if (stored_release_artifact.get("valid") is False
                or stored_payload.get("releaseBundle") != descriptor.release_bundle.to_dict()):
            raise RuntimeError("The stored reasoning release artifact is invalid or belongs to another release")
        if descriptor.deployment_id not in protected_deployment_ids and not frozen_release_recorded:
            read_manifest = getattr(repository, "read_seed_static_manifest", None)
            try:
                manifest = dict(read_manifest() or {}) if callable(read_manifest) else {}
            except Exception:
                manifest = {}
            metadata = dict(manifest.get("metadata") or {})
            if (manifest.get("status") != "ok"
                    or metadata.get("ruleboxRulesHash") != stored_payload.get("ruleboxFingerprint")
                    or metadata.get("tboxFingerprint") != stored_payload.get("tboxFingerprint")):
                restored = dict(repository.seed_release_artifact(stored_payload) or {})
                if not restored.get("saved"):
                    raise RuntimeError("Candidate release artifact restoration failed: " + str(restored.get("status")))
            # The authored artifact, not today's code catalog, defines a candidate.
            frozen_release_recorded = True
            deployment_health = {**deployment_health,
                "ruleboxFingerprint": restored.get("runtimeRuleboxFingerprint") or "",
                "tboxFingerprint": stored_payload.get("tboxFingerprint") or ""}
    candidate_rulebox, rulebox_release_preflight = prepare_v2_rulebox_release(
        repository,
        candidate_settings,
        release_guard={
            "immutable": (
                descriptor.deployment_id in protected_deployment_ids or frozen_release_recorded
            ),
            "ruleboxFingerprint": str(deployment_health.get("ruleboxFingerprint") or ""),
            "tboxFingerprint": str(deployment_health.get("tboxFingerprint") or ""),
            "tboxVersion": str(descriptor.release_bundle.tbox_release_id or "").split("@", 1)[0],
        },
    )
    rulebox_fingerprint = str(
        candidate_rulebox.get("sourceRulesHash")
        or candidate_rulebox.get("rulesHash")
        or candidate_rulebox.get("ruleboxRulesHash")
        or payload_hash(candidate_rulebox.get("rules") or [])
    )
    runtime_tbox_metadata = repository.active_tbox_metadata()
    release_seed_artifact = stored_payload or ontology_release_seed_artifact(
        default_graph_inference_rules(),
        language_registry=investment_language_registry(configured),
        tbox_metadata=default_tbox_metadata(),
        release_bundle=descriptor.release_bundle.to_dict(),
    )
    save_release_artifact = getattr(platform.registry, "save_release_artifact", None)
    runtime_tbox_fingerprint = str(runtime_tbox_metadata.get("fingerprint") or "")
    read_static_manifest = getattr(repository, "read_seed_static_manifest", None)
    runtime_static_manifest = (
        dict(read_static_manifest() or {}) if callable(read_static_manifest) else {}
    )
    runtime_static_metadata = dict(runtime_static_manifest.get("metadata") or {})
    authored_release_matches_runtime = bool(
        str(runtime_static_manifest.get("status") or "") == "ok"
        and str(runtime_static_metadata.get("ruleboxRulesHash") or "")
        == str(release_seed_artifact.get("ruleboxFingerprint") or "")
        and str(runtime_static_metadata.get("tboxFingerprint") or "")
        == str(release_seed_artifact.get("tboxFingerprint") or "")
        and runtime_tbox_fingerprint == str(release_seed_artifact.get("tboxFingerprint") or "")
    )
    if stored_release_artifact:
        stored_payload = dict(stored_release_artifact.get("artifact") or {})
        stored_release_matches_runtime = bool(
            bool(stored_release_artifact.get("valid", True))
            and dict(stored_payload.get("releaseBundle") or {})
            == descriptor.release_bundle.to_dict()
            and str(stored_release_artifact.get("ruleboxFingerprint") or "")
            == str(release_seed_artifact.get("ruleboxFingerprint") or "")
            and str(stored_release_artifact.get("tboxFingerprint") or "")
            == runtime_tbox_fingerprint
            and authored_release_matches_runtime
            and rulebox_fingerprint
        )
        release_artifact_persistence = {
            "status": (
                "unchanged"
                if stored_release_matches_runtime
                else "release-artifact-runtime-mismatch"
            ),
            "artifactFingerprint": str(stored_release_artifact.get("artifactFingerprint") or ""),
            "ruleboxFingerprint": str(stored_release_artifact.get("ruleboxFingerprint") or ""),
            "artifactRuleboxFingerprint": str(
                stored_release_artifact.get("ruleboxFingerprint") or ""
            ),
            "runtimeRuleboxFingerprint": rulebox_fingerprint,
            "tboxFingerprint": str(stored_release_artifact.get("tboxFingerprint") or ""),
        }
    elif callable(save_release_artifact) and authored_release_matches_runtime:
        release_artifact_persistence = dict(
            save_release_artifact(descriptor.deployment_id, release_seed_artifact) or {}
        )
        release_artifact_persistence["artifactRuleboxFingerprint"] = str(
            release_artifact_persistence.get("ruleboxFingerprint") or ""
        )
        release_artifact_persistence["runtimeRuleboxFingerprint"] = rulebox_fingerprint
    elif callable(save_release_artifact):
        # A legacy active release may predate durable artifacts. Never bind its
        # old deployment identity to today's authored TBox/RuleBox.
        release_artifact_persistence = {
            "status": "legacy-release-artifact-missing",
            "artifactFingerprint": "",
            "ruleboxFingerprint": "",
            "artifactRuleboxFingerprint": "",
            "runtimeRuleboxFingerprint": rulebox_fingerprint,
            "tboxFingerprint": runtime_tbox_fingerprint,
        }
    else:
        release_artifact_persistence = {"status": "unsupported"}
    release_identity = reasoning_release_identity(descriptor, rulebox_fingerprint)
    candidate_settings["_reasoningEngineReleaseFingerprint"] = str(
        release_identity.get("releaseFingerprint") or ""
    )
    candidate_settings["_reasoningEngineValidationCohortId"] = str(
        release_identity.get("validationCohortId") or ""
    )
    return ReasoningBinding(
        candidate_rulebox,
        rulebox_release_preflight,
        rulebox_fingerprint,
        release_identity,
        release_artifact_persistence,
    )
