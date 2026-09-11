"""TypeDB static-seed preflight owner; no facade or runtime construction."""

from .preflight_ports import PreflightStore
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_schema import default_tbox_metadata
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from typing import Dict, List


def legacy_static_seed_preflight(
    _store: PreflightStore,
    graph: PortfolioOntology,
    rules_payload: List[Dict[str, object]],
    expected: Dict[str, object],
) -> Dict[str, object]:
    """Upgrade a legacy seed without an ABox-wide or RuleBox-wide scan.

    There is no historical keyed RuleBox fingerprint to trust.  Instead of
    reading every old rule component, verify the TBox and language anchors
    through their unique storage identities and deterministically replace
    the RuleBox.  The replacement produces the first trustworthy manifest.
    """
    expected_counts = dict(expected.get("boxCounts") or {})
    try:
        tbox_properties = _store.seed_static_node_properties(graph, "ontology-box:TBox")
        expected_tbox = default_tbox_metadata()
        tbox_matches = str(
            tbox_properties.get("tboxFingerprint")
            or tbox_properties.get("fingerprint")
            or ""
        ) == str(expected_tbox.get("fingerprint") or "") and str(
            tbox_properties.get("tboxVersion") or tbox_properties.get("version") or ""
        ) == str(
            expected_tbox.get("version") or ""
        )
        language_entity = next(
            (
                item
                for item in graph.entities
                if str(item.kind or "") == "language-registry-version"
            ),
            None,
        )
        language_properties = (
            _store.seed_static_node_properties(
                graph, str(language_entity.entity_id if language_entity else "")
            )
            if language_entity
            else {}
        )
        language_registry_matches = language_entity is None or str(
            language_properties.get("registryVersion") or ""
        ) == str(expected.get("languageRegistryVersion") or "")
    except Exception as error:
        return {
            "ready": False,
            "status": "legacy-probe-error",
            "reason": str(error)[:180],
            "preflightMode": "legacy-static-seed-probe",
            "expectedBoxCounts": expected_counts,
            "actualBoxCounts": {},
            "tboxMatches": False,
            "ruleboxMatches": False,
            "languageRegistryMatches": False,
            "schemaContractMatches": False,
        }
    return {
        "ready": False,
        "status": "legacy-manifest-bootstrap-repair",
        "preflightMode": "legacy-static-seed-anchor",
        "expectedBoxCounts": expected_counts,
        "actualBoxCounts": {
            box: dict(expected_counts.get(box) or {})
            for box in _store.seed_static_box_names()
            if box == "TBox"
            and tbox_matches
            or (box == "LanguageGovernance" and language_registry_matches)
        },
        "tboxMatches": tbox_matches,
        "ruleboxMatches": False,
        "languageRegistryMatches": language_registry_matches,
        "schemaContractMatches": False,
        "staticSeedManifest": {
            "status": "missing",
            "expectedFingerprint": expected.get("staticSeedFingerprint"),
            "bootstrapAction": "replace-rulebox",
        },
    }


def seed_graph_preflight(
    _store: PreflightStore,
    graph: PortfolioOntology,
    rules_payload: List[Dict[str, object]],
) -> Dict[str, object]:
    """Check immutable boxes without scanning the live ABox.

    TypeDB count reductions filtered by ``ontology-box`` can still plan
    over every persisted assertion. A keyed static manifest plus exact
    sentinel probes gives the startup path a bounded, fail-closed check.
    Legacy stores without a manifest validate their keyed TBox/language
    anchors, replace RuleBox deterministically, and receive the manifest
    only after that bounded repair completes.
    """
    expected = _store.seed_static_manifest_metadata(graph, rules_payload)
    expected_counts = dict(expected.get("boxCounts") or {})
    manifest = _store.read_seed_static_manifest()
    stored = dict(manifest.get("metadata") or {})
    if str(manifest.get("status") or "") != "ok":
        if str(manifest.get("status") or "") == "missing":
            return _store.legacy_static_seed_preflight(graph, rules_payload, expected)
        return {
            "ready": False,
            "status": "manifest-" + str(manifest.get("status") or "unavailable"),
            "reason": str(manifest.get("reason") or "Static seed manifest is absent."),
            "preflightMode": "static-seed-manifest",
            "expectedBoxCounts": expected_counts,
            "actualBoxCounts": dict(stored.get("boxCounts") or {}),
            "tboxMatches": False,
            "ruleboxMatches": False,
            "languageRegistryMatches": False,
            "schemaContractMatches": False,
            "staticSeedManifest": {
                "status": str(manifest.get("status") or "unavailable"),
                "expectedFingerprint": expected.get("staticSeedFingerprint"),
            },
        }
    actual_counts = dict(stored.get("boxCounts") or {})
    tbox_matches = (
        str(stored.get("tboxVersion") or "") == str(expected.get("tboxVersion") or "")
        and str(stored.get("tboxFingerprint") or "")
        == str(expected.get("tboxFingerprint") or "")
        and (actual_counts.get("TBox") == expected_counts.get("TBox"))
    )
    rulebox_matches = (
        str(stored.get("ruleboxRulesHash") or "")
        == str(expected.get("ruleboxRulesHash") or "")
        and int(number_or_none(stored.get("ruleboxRuleCount")) or 0)
        == int(number_or_none(expected.get("ruleboxRuleCount")) or 0)
        and (
            int(number_or_none(stored.get("ruleboxConditionCount")) or 0)
            == int(number_or_none(expected.get("ruleboxConditionCount")) or 0)
        )
        and (
            int(number_or_none(stored.get("ruleboxDerivationCount")) or 0)
            == int(number_or_none(expected.get("ruleboxDerivationCount")) or 0)
        )
        and (actual_counts.get("RuleBox") == expected_counts.get("RuleBox"))
    )
    language_registry_matches = str(stored.get("languageRegistryVersion") or "") == str(
        expected.get("languageRegistryVersion") or ""
    ) and actual_counts.get("LanguageGovernance") == expected_counts.get(
        "LanguageGovernance"
    )
    fingerprint_matches = str(stored.get("staticSeedFingerprint") or "") == str(
        expected.get("staticSeedFingerprint") or ""
    )
    schema_contract_matches = str(stored.get("schemaContractVersion") or "") == str(
        expected.get("schemaContractVersion") or ""
    ) and str(stored.get("schemaContractFingerprint") or "") == str(
        expected.get("schemaContractFingerprint") or ""
    )
    sentinels = _store.seed_static_sentinels_present(
        graph, _store.static_seed_generation_ids(stored)
    )
    ready = bool(
        fingerprint_matches
        and tbox_matches
        and rulebox_matches
        and language_registry_matches
        and (str(sentinels.get("status") or "") == "ok")
    )
    return {
        "ready": ready,
        "status": "current" if ready else "stale",
        "reason": str(sentinels.get("reason") or ""),
        "preflightMode": "static-seed-manifest",
        "expectedBoxCounts": expected_counts,
        "actualBoxCounts": actual_counts,
        "tboxMatches": tbox_matches,
        "ruleboxMatches": rulebox_matches,
        "languageRegistryMatches": language_registry_matches,
        "schemaContractMatches": schema_contract_matches,
        "staticSeedManifest": {
            "status": "ok",
            "expectedFingerprint": expected.get("staticSeedFingerprint"),
            "activeFingerprint": stored.get("staticSeedFingerprint"),
            "fingerprintMatches": fingerprint_matches,
            "expectedTboxFingerprint": expected.get("tboxFingerprint"),
            "activeTboxFingerprint": stored.get("tboxFingerprint"),
            "tboxMatches": tbox_matches,
            "expectedRuleboxFingerprint": expected.get("ruleboxRulesHash"),
            "activeRuleboxFingerprint": stored.get("ruleboxRulesHash"),
            "ruleboxMatches": rulebox_matches,
            "sentinelStatus": sentinels.get("status"),
            "missingSentinels": list(sentinels.get("missing") or []),
            "expectedSchemaContractFingerprint": expected.get(
                "schemaContractFingerprint"
            ),
            "activeSchemaContractFingerprint": stored.get("schemaContractFingerprint"),
            "schemaContractMatches": schema_contract_matches,
        },
    }


def seed_relation_repair_eligible(
    _store: PreflightStore, preflight: Dict[str, object]
) -> bool:
    """Return whether a stale seed can be repaired without replacing nodes.

    A TypeDB process can be interrupted after static nodes are committed but
    before every relation batch is written. Replacing all boxes in that
    state is slow and competes with live ABox projection. A relation-only
    repair is safe when every expected static node is already present and
    no box has more relations than the immutable seed expects.
    """
    if not isinstance(preflight, dict) or preflight.get("status") != "stale":
        return False
    if str(preflight.get("preflightMode") or "") in {
        "static-seed-manifest",
        "legacy-static-seed-anchor",
    }:
        return False
    expected = (
        preflight.get("expectedBoxCounts")
        if isinstance(preflight.get("expectedBoxCounts"), dict)
        else {}
    )
    actual = (
        preflight.get("actualBoxCounts")
        if isinstance(preflight.get("actualBoxCounts"), dict)
        else {}
    )
    if not expected or not actual:
        return False
    for box, expected_counts in expected.items():
        if not isinstance(expected_counts, dict):
            return False
        actual_counts = actual.get(box) if isinstance(actual.get(box), dict) else {}
        expected_entities = int(number_or_none(expected_counts.get("entityCount")) or 0)
        expected_relations = int(
            number_or_none(expected_counts.get("relationCount")) or 0
        )
        if (
            int(number_or_none(actual_counts.get("entityCount")) or 0)
            != expected_entities
        ):
            return False
        if (
            int(number_or_none(actual_counts.get("relationCount")) or 0)
            > expected_relations
        ):
            return False
    return True


def seed_static_boxes_requiring_refresh(
    _store: PreflightStore, preflight: Dict[str, object]
) -> List[str]:
    """Identify the smallest safe static seed replacement.

    A RuleBox policy edit must not rewrite the TBox or language registry.
    Conversely, a changed TBox can change the meaning of both, so it is
    deliberately promoted to a complete static refresh.  Incomplete
    preflight metadata is treated conservatively as a full static repair.
    """
    static_boxes = _store.seed_static_box_names()
    if not isinstance(preflight, dict):
        return static_boxes
    expected = preflight.get("expectedBoxCounts")
    actual = preflight.get("actualBoxCounts")
    required_flags = {"tboxMatches", "ruleboxMatches", "languageRegistryMatches"}
    if (
        not isinstance(expected, dict)
        or not isinstance(actual, dict)
        or (not required_flags.issubset(set(preflight)))
    ):
        return static_boxes

    def counts_match(box: str) -> bool:
        expected_counts = expected.get(box)
        actual_counts = actual.get(box)
        if not isinstance(expected_counts, dict) or not isinstance(actual_counts, dict):
            return False
        return int(number_or_none(expected_counts.get("entityCount")) or 0) == int(
            number_or_none(actual_counts.get("entityCount")) or 0
        ) and int(number_or_none(expected_counts.get("relationCount")) or 0) == int(
            number_or_none(actual_counts.get("relationCount")) or 0
        )

    tbox_stale = not bool(preflight.get("tboxMatches")) or not counts_match("TBox")
    if tbox_stale:
        return static_boxes
    stale = []
    if not bool(preflight.get("ruleboxMatches")) or not counts_match("RuleBox"):
        stale.append("RuleBox")
    if not bool(preflight.get("languageRegistryMatches")) or not counts_match(
        "LanguageGovernance"
    ):
        stale.append("LanguageGovernance")
    return stale or static_boxes


def static_seed_schema_prepared(preflight: Dict[str, object]) -> bool:
    """Return whether bounded graph probes already proved the base schema.

    A current manifest is sufficient only when it was written against the
    exact base-schema contract of this build. New, legacy, or upgraded
    databases intentionally return ``False`` and run the bounded schema
    upgrade path before the manifest is republished.
    """
    mode = str((preflight or {}).get("preflightMode") or "")
    status = str((preflight or {}).get("status") or "")
    return (
        mode == "static-seed-manifest"
        and status in {"current", "stale"}
        and bool((preflight or {}).get("schemaContractMatches"))
    )
