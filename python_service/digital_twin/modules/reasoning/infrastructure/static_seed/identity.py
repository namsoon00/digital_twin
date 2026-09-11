"""TypeDB static-seed identity owner; no facade or runtime construction."""

from .artifact import graph_box_entity_counts, graph_box_relation_counts
from .identity_ports import IdentityStore
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.model_registry.contracts import RULE_EXECUTION_POLICY_VERSION, rule_execution_profile
from digital_twin.modules.model_registry.contracts import rule_dependency_reverse_index
from digital_twin.modules.model_registry.contracts import GRAPH_REASONER_VERSION
from digital_twin.modules.model_registry.contracts import rulebox_rules_hash
from digital_twin.modules.reasoning.domain.ontology_schema import default_tbox_metadata, normalize_tbox_metadata
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_storage_id,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    typedb_rule_is_enabled,
)
from typing import Dict, List, Tuple
import hashlib
import json


def rulebox_runtime_metadata(
    rules_payload: List[Dict[str, object]]
) -> Dict[str, object]:
    rules_payload = [item for item in rules_payload or [] if isinstance(item, dict)]
    active_rules_payload = [
        item for item in rules_payload if typedb_rule_is_enabled(item)
    ]
    rules_hash = rulebox_rules_hash(rules_payload)
    execution_profiles = [rule_execution_profile(item) for item in active_rules_payload]
    all_execution_profiles = [rule_execution_profile(item) for item in rules_payload]
    stage_counts = {
        stage: len(
            [
                item
                for item in execution_profiles
                if str(item.get("executionStage") or "") == stage
            ]
        )
        for stage in ["critical", "core", "supporting"]
    }
    dependency_index = rule_dependency_reverse_index(rules_payload)
    return {
        "ruleboxRulesHash": rules_hash,
        "ruleboxShortHash": rules_hash[:12],
        "ruleboxRuleCount": len(rules_payload),
        "ruleboxActiveRuleCount": len(active_rules_payload),
        "ruleboxDisabledRuleCount": len(rules_payload) - len(active_rules_payload),
        "ruleboxConditionCount": sum(
            (len(item.get("conditions") or []) for item in rules_payload)
        ),
        "ruleboxDerivationCount": sum(
            (len(item.get("derivations") or []) for item in rules_payload)
        ),
        "ruleboxEngineVersion": GRAPH_REASONER_VERSION,
        "ruleExecutionPolicyVersion": RULE_EXECUTION_POLICY_VERSION,
        "ruleExecutionStageCounts": stage_counts,
        "ruleExecutionStageCountsAll": {
            stage: len(
                [
                    item
                    for item in all_execution_profiles
                    if str(item.get("executionStage") or "") == stage
                ]
            )
            for stage in ["critical", "core", "supporting"]
        },
        "ruleDependencyIndexVersion": str(dependency_index.get("version") or ""),
        "ruleDependencyIndexFingerprint": str(
            dependency_index.get("fingerprint") or ""
        ),
        "ruleDependencyIndexRuleCount": int(dependency_index.get("ruleCount") or 0),
    }


def rulebox_structural_fingerprint(
    rules_payload: List[Dict[str, object]]
) -> Dict[str, Tuple[int, int]]:
    fingerprint: Dict[str, Tuple[int, int]] = {}
    for rule in rules_payload or []:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
        if not rule_id:
            continue
        fingerprint[rule_id] = (
            len(rule.get("conditions") or []),
            len(rule.get("derivations") or []),
        )
    return fingerprint


def seed_static_manifest_entity_id() -> str:
    return "ontology-seed-manifest:typedb-static-v1"


def seed_static_box_names() -> List[str]:
    return ["TBox", "RuleBox", "LanguageGovernance"]


def static_seed_generation_ids(metadata: Dict[str, object] = None) -> Dict[str, str]:
    """Resolve active immutable static generations from one manifest."""
    values = dict(metadata or {})
    mappings = {
        "TBox": str(values.get("tboxSnapshotId") or "").strip(),
        "RuleBox": str(values.get("ruleboxSnapshotId") or "").strip(),
        "LanguageGovernance": str(values.get("languageSnapshotId") or "").strip(),
    }
    return {box: generation for (box, generation) in mappings.items() if generation}


def seed_static_manifest_metadata(
    _store: IdentityStore,
    graph: PortfolioOntology,
    rules_payload: List[Dict[str, object]],
    tbox_metadata: Dict[str, object] = None,
) -> Dict[str, object]:
    """Return the durable identity of the immutable ontology seed.

    The manifest deliberately excludes mutable ABox/InferenceBox data. It
    lets startup compare a small, keyed record instead of reducing every
    row in a multi-gigabyte TypeDB graph merely to prove static boxes have
    not changed.
    """
    expected_entities = graph_box_entity_counts(graph)
    expected_relations = graph_box_relation_counts(graph)
    expected_boxes = _store.seed_static_box_names()
    counts = {
        box: {
            "entityCount": int(expected_entities.get(box, 0)),
            "relationCount": int(expected_relations.get(box, 0)),
        }
        for box in expected_boxes
    }
    expected_rulebox = rulebox_runtime_metadata(rules_payload)
    expected_tbox = normalize_tbox_metadata(
        dict(tbox_metadata or default_tbox_metadata())
    )
    language_registry = next(
        (
            item
            for item in graph.entities
            if str(item.kind or "") == "language-registry-version"
        ),
        None,
    )
    metadata = {
        "manifestVersion": "typedb-static-seed-manifest-v1",
        "engineVersion": GRAPH_REASONER_VERSION,
        "tboxVersion": str(expected_tbox.get("version") or ""),
        "tboxFingerprint": str(expected_tbox.get("fingerprint") or ""),
        "ruleboxRulesHash": str(expected_rulebox.get("ruleboxRulesHash") or ""),
        "ruleboxRuleCount": int(expected_rulebox.get("ruleboxRuleCount") or 0),
        "ruleboxConditionCount": int(
            expected_rulebox.get("ruleboxConditionCount") or 0
        ),
        "ruleboxDerivationCount": int(
            expected_rulebox.get("ruleboxDerivationCount") or 0
        ),
        "languageRegistryVersion": str(
            (language_registry.properties if language_registry else {}).get(
                "registryVersion"
            )
            or ""
        ),
        "boxCounts": counts,
    }
    canonical = json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    fingerprint = (
        "typedb-static-seed:"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    )
    tbox_fingerprint = str(expected_tbox.get("fingerprint") or "")
    rulebox_fingerprint = hashlib.sha256(
        (
            tbox_fingerprint + ":" + str(expected_rulebox.get("ruleboxRulesHash") or "")
        ).encode("utf-8")
    ).hexdigest()[:24]
    language_fingerprint = hashlib.sha256(
        (
            tbox_fingerprint + ":" + str(metadata.get("languageRegistryVersion") or "")
        ).encode("utf-8")
    ).hexdigest()[:24]
    schema_contract = _store.base_schema_contract_metadata()
    return {
        **metadata,
        "staticSeedFingerprint": fingerprint,
        **schema_contract,
        "tboxSnapshotId": "static-tbox:" + tbox_fingerprint,
        "ruleboxSnapshotId": "static-rulebox:" + rulebox_fingerprint,
        "languageSnapshotId": "static-language:" + language_fingerprint,
    }


def seed_static_manifest_storage_id(_store: IdentityStore) -> str:
    return ontology_storage_id(
        {"ontologyBox": "TBox"}, _store.seed_static_manifest_entity_id(), "node"
    )
