"""TypeDB static-seed bootstrap owner; no facade or runtime construction."""

from .artifact import ontology_seed_graph
from .bootstrap_ports import BootstrapStore, BootstrapBindings
from .identity import rulebox_runtime_metadata, rulebox_structural_fingerprint
from digital_twin.domain.investment_ubiquitous_language import (
    investment_language_registry,
)
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.infrastructure.graph_store_rulebox import (
    rulebox_rules_from_payload,
    rulebox_rules_to_payload,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import (
    typedb_bool,
)
from typing import Dict


def seed_ontology(
    _store: BootstrapStore,
    payload: Dict[str, object] = None,
    *,
    _bindings: BootstrapBindings
) -> Dict[str, object]:
    payload = payload or {}
    try:
        rules = (
            rulebox_rules_from_payload(payload, strict_governance=True)
            if payload.get("rules") is not None or payload.get("rulesJson")
            else default_graph_inference_rules()
        )
    except ValueError as error:
        return {
            "configured": True,
            "saved": False,
            "seeded": False,
            "status": "invalid-rulebox",
            "graphStore": "typedb",
            "reason": str(error),
        }
    rules = list(rules)
    rules_payload = rulebox_rules_to_payload(rules)
    _store._last_rules = rules
    scoped_write_lease_recovery = {}
    if typedb_bool(payload.get("recoverScopedABoxWriteLease")):
        scoped_write_lease_recovery = (
            _store.recover_scoped_abox_write_lease_after_server_start()
        )

    def complete_seed(result: Dict[str, object]) -> Dict[str, object]:
        completed = dict(result or {})
        if scoped_write_lease_recovery:
            completed["scopedABoxWriteLeaseRecovery"] = dict(
                scoped_write_lease_recovery
            )
        return completed

    seed_graph = ontology_seed_graph(
        rules,
        language_registry=investment_language_registry(_bindings.runtime_settings()),
    )
    preflight = _store.seed_graph_preflight(seed_graph, rules_payload)
    schema_prepared = _store.static_seed_schema_prepared(preflight)
    if preflight.get("ready") and (not typedb_bool(payload.get("forceReseed"))):
        schema_contract_sync = {}
        if str(preflight.get("preflightMode") or "") == "static-seed-manifest" and (
            not bool(preflight.get("schemaContractMatches"))
        ):
            schema_contract_sync = _store.sync_base_schema_contract()
            if not schema_contract_sync.get("saved"):
                return complete_seed(
                    {
                        "configured": True,
                        "saved": False,
                        "seeded": False,
                        "status": "schema-contract-sync-failed",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": True,
                        "seedPreflight": preflight,
                        "schemaContractSync": schema_contract_sync,
                        "reason": str(
                            schema_contract_sync.get("reason")
                            or "TypeDB schema contract sync failed."
                        ),
                    }
                )
            manifest_result = _store.save_seed_static_manifest(
                seed_graph, rules_payload, schema_prepared=True
            )
            schema_contract_sync["manifest"] = manifest_result
            if not manifest_result.get("saved"):
                return complete_seed(
                    {
                        "configured": True,
                        "saved": False,
                        "seeded": False,
                        "status": "schema-contract-manifest-write-failed",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": True,
                        "seedPreflight": preflight,
                        "schemaContractSync": schema_contract_sync,
                        "reason": str(
                            manifest_result.get("reason")
                            or "TypeDB schema contract manifest write failed."
                        ),
                    }
                )
            preflight = _store.seed_graph_preflight(seed_graph, rules_payload)
            if not (preflight.get("ready") and preflight.get("schemaContractMatches")):
                return complete_seed(
                    {
                        "configured": True,
                        "saved": False,
                        "seeded": False,
                        "status": "schema-contract-verification-failed",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": True,
                        "seedPreflight": preflight,
                        "schemaContractSync": schema_contract_sync,
                        "reason": "The TypeDB static manifest did not confirm the active schema contract.",
                    }
                )
        manifest_bootstrap = {}
        if preflight.get("manifestBootstrapRequired"):
            manifest_bootstrap = _store.save_seed_static_manifest(
                seed_graph, rules_payload, schema_prepared=schema_prepared
            )
            if not manifest_bootstrap.get("saved"):
                return complete_seed(
                    {
                        "configured": True,
                        "saved": False,
                        "seeded": False,
                        "status": "static-seed-manifest-write-failed",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": True,
                        "seedPreflight": preflight,
                        "staticSeedManifest": manifest_bootstrap,
                        "reason": str(
                            manifest_bootstrap.get("reason")
                            or "Static seed manifest write failed."
                        ),
                    }
                )
        return complete_seed(
            {
                "configured": True,
                "saved": True,
                "seeded": True,
                "status": "unchanged",
                "graphStore": "typedb",
                "engineVersion": GRAPH_REASONER_VERSION,
                "ruleCount": len(rules),
                "seedSkipped": True,
                "seedPreflight": preflight,
                "ruleBoxReplaceRequested": typedb_bool(payload.get("replaceRuleBox")),
                "ruleBoxAlreadyCurrent": True,
                "ruleBoxHashMatched": True,
                "activeRuleBoxRuleCount": len(rules),
                "expectedRuleBoxRuleCount": len(rules),
                "activeRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)[
                    "ruleboxShortHash"
                ],
                "expectedRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)[
                    "ruleboxShortHash"
                ],
                "staticSeedManifest": manifest_bootstrap,
                "manifestBootstrapped": bool(manifest_bootstrap.get("saved")),
                "schemaContractSync": schema_contract_sync,
            }
        )
    relation_repair = {}
    if not typedb_bool(
        payload.get("forceReseed")
    ) and _store.seed_relation_repair_eligible(preflight):
        relation_repair = _store.repair_seed_relations(seed_graph)
        if relation_repair.get("saved"):
            repaired_preflight = _store.seed_graph_preflight(seed_graph, rules_payload)
            if repaired_preflight.get("ready"):
                return complete_seed(
                    {
                        "configured": True,
                        "saved": True,
                        "seeded": True,
                        "status": "repaired",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": False,
                        "seedPreflight": repaired_preflight,
                        "staticRelationRepair": relation_repair,
                        "ruleBoxReplaceRequested": typedb_bool(
                            payload.get("replaceRuleBox")
                        ),
                        "ruleBoxAlreadyCurrent": True,
                        "ruleBoxHashMatched": True,
                        "activeRuleBoxRuleCount": len(rules),
                        "expectedRuleBoxRuleCount": len(rules),
                        "activeRuleBoxShortHash": rulebox_runtime_metadata(
                            rules_payload
                        )["ruleboxShortHash"],
                        "expectedRuleBoxShortHash": rulebox_runtime_metadata(
                            rules_payload
                        )["ruleboxShortHash"],
                    }
                )
    refresh_boxes = _store.seed_static_boxes_requiring_refresh(preflight)
    result = _store.save_static_seed_boxes(
        seed_graph,
        refresh_boxes,
        rules_payload=rules_payload,
        schema_prepared=schema_prepared,
    )
    result.update(
        {
            "configured": True,
            "seeded": bool(result.get("saved")),
            "engineVersion": GRAPH_REASONER_VERSION,
            "ruleCount": len(rules),
            "graphStore": "typedb",
            "seedSkipped": False,
            "seedPreflight": preflight,
            "staticBoxRefresh": {
                "mode": "targeted-box-replacement",
                "requestedBoxes": refresh_boxes,
                "refreshedBoxes": list(result.get("refreshedBoxes") or refresh_boxes),
            },
        }
    )
    if relation_repair:
        result["staticRelationRepair"] = relation_repair
    if result.get("saved"):
        manifest_result = _store.save_seed_static_manifest(
            seed_graph, rules_payload, schema_prepared=True
        )
        result["staticSeedManifest"] = manifest_result
        if not manifest_result.get("saved"):
            result.update(
                {
                    "saved": False,
                    "seeded": False,
                    "status": "static-seed-manifest-write-failed",
                    "reason": str(
                        manifest_result.get("reason")
                        or "Static seed manifest write failed."
                    ),
                }
            )
    if result.get("saved"):
        _store.clear_rulebox_snapshot_cache()
        post_seed_preflight = _store.seed_graph_preflight(seed_graph, rules_payload)
        result["postSeedPreflight"] = post_seed_preflight
        result["staticBoxRefresh"]["verified"] = bool(post_seed_preflight.get("ready"))
        if not post_seed_preflight.get("ready"):
            result.update(
                {
                    "saved": False,
                    "seeded": False,
                    "status": "static-seed-verification-failed",
                    "reason": "Targeted static seed replacement did not pass the post-write completeness check.",
                }
            )
    if typedb_bool(payload.get("replaceRuleBox")) and result.get("saved"):
        expected_rulebox = rulebox_runtime_metadata(rules_payload)
        _store.clear_rulebox_snapshot_cache()
        rulebox_result = _store.rulebox_snapshot()
        expected_structure = rulebox_structural_fingerprint(rules_payload)
        active_rules_payload = (
            rulebox_result.get("rules")
            if isinstance(rulebox_result.get("rules"), list)
            else []
        )
        active_structure = rulebox_structural_fingerprint(active_rules_payload)
        active_rule_count = int(
            number_or_none(
                rulebox_result.get("ruleCount")
                or rulebox_result.get("ruleboxRuleCount")
            )
            or 0
        )
        active_rule_hash = str(rulebox_result.get("ruleboxRulesHash") or "")
        hash_matched = active_rule_hash == expected_rulebox["ruleboxRulesHash"]
        replace_verified = (
            bool(rulebox_result.get("saved"))
            and str(rulebox_result.get("status") or "") == "ok"
            and (active_rule_count == len(rules_payload))
            and (active_structure == expected_structure)
        )
        result.update(
            {
                "ruleBoxReplaceRequested": True,
                "ruleBoxReplaced": replace_verified,
                "ruleBoxHashMatched": hash_matched,
                "activeRuleBoxRuleCount": active_rule_count,
                "expectedRuleBoxRuleCount": len(rules_payload),
                "activeRuleBoxHash": active_rule_hash,
                "expectedRuleBoxHash": expected_rulebox["ruleboxRulesHash"],
                "activeRuleBoxShortHash": str(
                    rulebox_result.get("ruleboxShortHash") or active_rule_hash[:12]
                ),
                "expectedRuleBoxShortHash": expected_rulebox["ruleboxShortHash"],
                "ruleBoxReplaceResult": {
                    "saved": bool(rulebox_result.get("saved")),
                    "status": rulebox_result.get("status") or "",
                    "reason": rulebox_result.get("reason") or "",
                    "ruleCount": active_rule_count,
                    "conditionCount": int(
                        number_or_none(
                            rulebox_result.get("conditionCount")
                            or rulebox_result.get("ruleboxConditionCount")
                        )
                        or 0
                    ),
                    "derivationCount": int(
                        number_or_none(
                            rulebox_result.get("derivationCount")
                            or rulebox_result.get("ruleboxDerivationCount")
                        )
                        or 0
                    ),
                    "ruleboxRulesHash": active_rule_hash,
                    "ruleboxShortHash": str(
                        rulebox_result.get("ruleboxShortHash") or active_rule_hash[:12]
                    ),
                },
            }
        )
        if replace_verified and typedb_bool(payload.get("clearInference")):
            result["clearInferenceResult"] = _store.clear_inferencebox()
        if not replace_verified:
            result.update(
                {
                    "saved": False,
                    "seeded": False,
                    "status": rulebox_result.get("status") or "rulebox-replace-failed",
                    "reason": (
                        "RuleBox replace requested but active RuleBox did not match the seeded rules. "
                        + str(rulebox_result.get("reason") or "")
                    ).strip(),
                }
            )
    return complete_seed(result)
