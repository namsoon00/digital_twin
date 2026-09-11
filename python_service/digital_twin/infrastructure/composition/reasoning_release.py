"""Reasoning Release runtime composition, loaded only when requested."""

from __future__ import annotations


def v2_model_signal_release_contract(rulebox_snapshot, settings=None):
    """Resolve model releases required by enabled rules and runtime scorers."""
    from digital_twin.modules.model_registry.domain.statistical_signals import CAPITAL_FLOW_SHADOW_RELEASE_ID, DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID, DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID, DEFAULT_EVENT_SIGNAL_RELEASE_ID, DEFAULT_FLOW_SIGNAL_RELEASE_ID, DEFAULT_PRICE_SIGNAL_RELEASE_ID, DEFAULT_VALUATION_SIGNAL_RELEASE_ID

    required = set()
    for rule in (rulebox_snapshot or {}).get("rules") or []:
        if not bool(rule.get("enabled", True)):
            continue
        for condition in rule.get("conditions") or []:
            relation_type = str(
                condition.get("relation_type")
                or condition.get("relationType")
                or ""
            ).strip()
            if relation_type != "HAS_MODEL_SIGNAL":
                continue
            filters = (
                condition.get("target_property_filters")
                or condition.get("targetPropertyFilters")
                or {}
            )
            release_id = str(
                filters.get("releaseId") or filters.get("release_id") or ""
            ).strip()
            if release_id:
                required.add(release_id)
    configured = dict(settings or {})
    available = {
        str(
            configured.get("statisticalPriceSignalReleaseId")
            or DEFAULT_PRICE_SIGNAL_RELEASE_ID
        ).strip(),
        str(
            configured.get("statisticalFlowSignalReleaseId")
            or DEFAULT_FLOW_SIGNAL_RELEASE_ID
        ).strip(),
        DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
        DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
        DEFAULT_EVENT_SIGNAL_RELEASE_ID,
        DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
        CAPITAL_FLOW_SHADOW_RELEASE_ID,
    }
    available.discard("")
    missing = sorted(required - available)
    return {
        "status": "matched" if not missing else "mismatch",
        "requiredReleaseIds": sorted(required),
        "availableReleaseIds": sorted(available),
        "missingReleaseIds": missing,
    }


def prepare_v2_rulebox_release(repository, settings=None, release_guard=None):
    """Read or migrate one V2 ontology release before freezing it in memory.

    A provisioning deployment may repair its isolated RuleBox before its first
    release fingerprint is recorded. An active, delivery-authorized, or
    already-frozen candidate is immutable: startup verifies its persisted
    fingerprints and must never rewrite that graph store in place.
    """
    from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import rulebox_rules_hash
    from digital_twin.modules.reasoning.domain.ontology_schema import default_tbox_metadata
    from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder

    guard = dict(release_guard or {})
    immutable_release = bool(guard.get("immutable"))
    expected_rulebox_fingerprint = str(
        guard.get("ruleboxFingerprint")
        or guard.get("rulebox_fingerprint")
        or ""
    ).strip()
    expected_tbox_fingerprint = str(
        guard.get("tboxFingerprint")
        or guard.get("tbox_fingerprint")
        or ""
    ).strip()
    expected_tbox_version = str(
        guard.get("tboxVersion")
        or guard.get("tbox_version")
        or ""
    ).strip()

    if immutable_release:
        try:
            snapshot = dict(repository.rulebox_snapshot() or {})
        except Exception as error:
            raise RuntimeError(
                "The immutable V2 RuleBox release is unavailable: "
                + str(error)[:220]
            ) from error
        if str(snapshot.get("status") or "") != "ok" or not snapshot.get("rules"):
            raise RuntimeError("The immutable V2 RuleBox release is unavailable or empty")
        actual_rulebox_fingerprint = str(
            snapshot.get("sourceRulesHash")
            or snapshot.get("rulesHash")
            or snapshot.get("ruleboxRulesHash")
            or rulebox_rules_hash(snapshot.get("rules") or [])
        ).strip()
        if (
            not expected_rulebox_fingerprint
            or actual_rulebox_fingerprint != expected_rulebox_fingerprint
        ):
            raise RuntimeError(
                "The immutable V2 RuleBox release fingerprint does not match its "
                "deployment record: "
                + (actual_rulebox_fingerprint or "unknown")
                + " != "
                + (expected_rulebox_fingerprint or "missing")
                + ". Register a new V2 deployment or restore the frozen release."
            )
        try:
            deployed_tbox = dict(repository.active_tbox_metadata() or {})
        except Exception as error:
            raise RuntimeError(
                "The immutable V2 TBox release cannot be verified: "
                + str(error)[:220]
            ) from error
        actual_tbox_fingerprint = str(deployed_tbox.get("fingerprint") or "").strip()
        actual_tbox_version = str(deployed_tbox.get("version") or "").strip()
        if (
            str(deployed_tbox.get("status") or "") != "ok"
            or not expected_tbox_fingerprint
            or actual_tbox_fingerprint != expected_tbox_fingerprint
            or (expected_tbox_version and actual_tbox_version != expected_tbox_version)
        ):
            raise RuntimeError(
                "The immutable V2 TBox release fingerprint does not match its "
                "deployment record: "
                + (actual_tbox_version or "unknown")
                + "/"
                + (actual_tbox_fingerprint or "unknown")
                + " != "
                + (expected_tbox_version or actual_tbox_version or "unknown")
                + "/"
                + (expected_tbox_fingerprint or "missing")
                + ". Register a new V2 deployment or restore the frozen release."
            )
        model_signal_contract = v2_model_signal_release_contract(snapshot, settings)
        if model_signal_contract["status"] != "matched":
            raise RuntimeError(
                "The immutable V2 RuleBox requires model-signal releases that the "
                "runtime scorer does not produce: "
                + ", ".join(model_signal_contract["missingReleaseIds"])
            )
        snapshot["frozenReleaseVerified"] = True
        return snapshot, {
            "status": "ready",
            "ruleCount": int(snapshot.get("ruleCount") or len(snapshot.get("rules") or [])),
            "ruleboxRulesHash": actual_rulebox_fingerprint,
            "sourceOfTruth": "typedb-immutable-v2-release",
            "ruleCatalogStore": "typedb",
            "modelSignalReleasePreflight": model_signal_contract,
            "tboxReleasePreflight": {
                "status": "matched",
                "version": actual_tbox_version,
                "fingerprint": actual_tbox_fingerprint,
                "source": str(deployed_tbox.get("source") or ""),
            },
            "ruleCatalogMigration": {
                "status": "immutable-release-reused",
                "required": False,
                "saved": False,
            },
        }

    schema_release_preflight = {}
    synchronize_schema = getattr(repository, "sync_base_schema_contract", None)
    if callable(synchronize_schema):
        try:
            schema_release_preflight = dict(synchronize_schema() or {})
        except Exception as error:
            raise RuntimeError(
                "The independent V2 TypeDB storage schema preflight failed: "
                + str(error)[:220]
            ) from error
        if not bool(schema_release_preflight.get("saved")):
            raise RuntimeError(
                "The independent V2 TypeDB storage schema preflight failed: "
                + str(
                    schema_release_preflight.get("reason")
                    or schema_release_preflight.get("status")
                    or "unknown"
                )[:220]
            )

    projection_recorder = PortfolioOntologyProjectionRecorder(
        repository,
        settings=dict(settings or {}),
        source="reasoning-engine-v2-release-preflight",
    )
    readiness = projection_recorder.ensure_rulebox_ready()
    readiness_reason = str(readiness.get("reason") or "")
    missing_base_schema = (
        str(readiness.get("status") or "") == "error"
        and "type label 'ontology-node' not found" in readiness_reason.lower()
    )
    if missing_base_schema and hasattr(repository, "seed_ontology"):
        try:
            seed_result = dict(repository.seed_ontology({
                "replaceRuleBox": True,
                "clearInference": False,
                "changeReason": "Bootstrap isolated V2 reasoning release",
                "author": "reasoning-engine-v2-release-preflight",
            }) or {})
        except Exception as error:
            raise RuntimeError(
                "The independent V2 TypeDB release bootstrap failed: "
                + str(error)[:220]
            ) from error
        if not bool(seed_result.get("saved") or seed_result.get("seeded")):
            raise RuntimeError(
                "The independent V2 TypeDB release bootstrap failed: "
                + str(
                    seed_result.get("reason")
                    or seed_result.get("status")
                    or "unknown"
                )[:220]
            )
        readiness = projection_recorder.ensure_rulebox_ready()
        readiness = {
            **dict(readiness or {}),
            "releaseBootstrap": {
                "status": str(seed_result.get("status") or "seeded"),
                "saved": True,
                "seeded": bool(seed_result.get("seeded", True)),
            },
        }
    if str(readiness.get("status") or "") not in {"ready", "seeded"}:
        raise RuntimeError(
            "The independent V2 RuleBox release preflight failed: "
            + str(readiness.get("reason") or readiness.get("status") or "unknown")
        )
    try:
        snapshot = dict(repository.rulebox_snapshot() or {})
    except Exception as error:
        raise RuntimeError(
            "The independent V2 RuleBox release is unavailable after preflight: "
            + str(error)[:220]
        ) from error
    if str(snapshot.get("status") or "") != "ok" or not snapshot.get("rules"):
        raise RuntimeError("The independent V2 RuleBox release is unavailable or empty")
    model_signal_contract = v2_model_signal_release_contract(snapshot, settings)
    if model_signal_contract["status"] != "matched":
        raise RuntimeError(
            "The independent V2 RuleBox requires model-signal releases that the "
            "runtime scorer does not produce: "
            + ", ".join(model_signal_contract["missingReleaseIds"])
        )
    expected_tbox = default_tbox_metadata()
    try:
        deployed_tbox = dict(repository.active_tbox_metadata() or {})
    except Exception as error:
        raise RuntimeError(
            "The independent V2 TBox release cannot be verified: " + str(error)[:220]
        ) from error
    if (
        str(deployed_tbox.get("status") or "") != "ok"
        or str(deployed_tbox.get("version") or "") != str(expected_tbox.get("version") or "")
        or str(deployed_tbox.get("fingerprint") or "") != str(expected_tbox.get("fingerprint") or "")
    ):
        raise RuntimeError(
            "The independent V2 TypeDB TBox does not match the source release: "
            + str(deployed_tbox.get("version") or "unknown")
            + "/"
            + str(deployed_tbox.get("fingerprint") or "unknown")
            + " != "
            + str(expected_tbox.get("version") or "unknown")
            + "/"
            + str(expected_tbox.get("fingerprint") or "unknown")
        )
    readiness = {
        **dict(readiness or {}),
        "storageSchemaPreflight": schema_release_preflight,
        "modelSignalReleasePreflight": model_signal_contract,
        "tboxReleasePreflight": {
            "status": "matched",
            "version": str(expected_tbox.get("version") or ""),
            "fingerprint": str(expected_tbox.get("fingerprint") or ""),
            "source": str(deployed_tbox.get("source") or ""),
        },
    }
    return snapshot, readiness
