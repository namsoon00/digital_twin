"""Explicit V2 reasoning release health composition phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


def record_v2_release_health(
    registry_store,
    descriptor,
    candidate_rulebox,
    rulebox_fingerprint,
    release_identity,
    rulebox_release_preflight,
    release_artifact_persistence,
    runtime_rulebox_catalog,
    compiled_ontology_release,
    runtime_world_partition,
) -> None:
    from digital_twin.domain.investment_reasoning import reasoning_rule_inventory

    existing_health = dict((registry_store.get(descriptor.deployment_id) or {}).get("health") or {})
    frozen_rulebox_fingerprint = str(existing_health.get("ruleboxFingerprint") or "")
    if (
        frozen_rulebox_fingerprint
        and str(existing_health.get("candidateReleaseId") or "")
        and frozen_rulebox_fingerprint != rulebox_fingerprint
    ):
        raise RuntimeError(
            "The independent V2 RuleBox changed after its release was frozen; "
            "register a new V2 deployment before starting the worker."
        )
    rule_inventory = reasoning_rule_inventory(candidate_rulebox.get("rules") or [])
    existing_health.update(
        {
            "candidateReleaseId": release_identity.get("releaseId"),
            "candidateBaseReleaseId": release_identity.get("baseReleaseId"),
            "candidateRuntimeRevision": release_identity.get("runtimeRevision"),
            "candidateReleaseFingerprint": release_identity.get("releaseFingerprint"),
            "releaseFingerprint": release_identity.get("releaseFingerprint"),
            "validationCohortId": release_identity.get("validationCohortId"),
            "ruleboxFingerprint": release_identity.get("ruleboxFingerprint"),
            "tboxFingerprint": release_identity.get("tboxFingerprint"),
            "tboxReleaseId": release_identity.get("tboxReleaseId"),
            "ruleboxReleaseId": release_identity.get("ruleboxReleaseId"),
            "promptReleaseId": release_identity.get("promptReleaseId"),
            "modelSignalReleaseId": release_identity.get("modelSignalReleaseId"),
            "independentExecution": True,
            "directSourceEvents": True,
            "monitorRunnerUsed": False,
            "ruleboxOwnership": "v2-release-frozen",
            "ruleInventory": rule_inventory,
            "ruleInventoryReleaseReady": bool(rule_inventory.get("releaseReady")),
            "ruleboxReleasePreflight": {
                "status": str(rulebox_release_preflight.get("status") or ""),
                "ruleCount": int(rulebox_release_preflight.get("ruleCount") or 0),
                "ruleboxRulesHash": str(rulebox_release_preflight.get("ruleboxRulesHash") or ""),
                "migrationStatus": str(
                    (rulebox_release_preflight.get("ruleCatalogMigration") or {}).get("status")
                    or ""
                ),
            },
            "releaseSeedArtifact": {
                "status": str(release_artifact_persistence.get("status") or ""),
                "artifactFingerprint": str(
                    release_artifact_persistence.get("artifactFingerprint") or ""
                ),
                "ruleboxFingerprint": str(
                    release_artifact_persistence.get("ruleboxFingerprint") or ""
                ),
                "artifactRuleboxFingerprint": str(
                    release_artifact_persistence.get("artifactRuleboxFingerprint")
                    or release_artifact_persistence.get("ruleboxFingerprint")
                    or ""
                ),
                "runtimeRuleboxFingerprint": str(
                    release_artifact_persistence.get("runtimeRuleboxFingerprint")
                    or rulebox_fingerprint
                    or ""
                ),
                "tboxFingerprint": str(release_artifact_persistence.get("tboxFingerprint") or ""),
                "reconstructable": str(release_artifact_persistence.get("status") or "")
                in {"saved", "unchanged"},
            },
            "runtimeOntologyRelease": {
                "status": "ready",
                "catalogSource": str(runtime_rulebox_catalog.get("runtimeCatalogSource") or ""),
                "ruleCount": int(runtime_rulebox_catalog.get("ruleCount") or 0),
                "sharedRuleCount": int(runtime_world_partition.get("sharedRuleCount") or 0),
                "overlayRuleCount": int(runtime_world_partition.get("overlayRuleCount") or 0),
                "tboxSource": "frozen-v2-release",
                "warmed": True,
                "compilerVersion": str(compiled_ontology_release.get("version") or ""),
                "compilerIrFingerprint": str(compiled_ontology_release.get("irFingerprint") or ""),
                "compilerStatus": str(compiled_ontology_release.get("status") or ""),
                "predictiveRuleCount": int(
                    compiled_ontology_release.get("predictiveRuleCount") or 0
                ),
            },
            "ruleExecutionReadiness": {
                "status": "ready",
                "mode": "typedb-direct-typeql",
                "realtimeProjectionMode": "incremental-current-state-one-pass-v1",
                "aboxPersistenceMode": "current-state-copy-on-write-v2",
                "sharedPremiseCriticalPath": False,
            },
        }
    )
    registry_store.update_health(descriptor.deployment_id, existing_health)
