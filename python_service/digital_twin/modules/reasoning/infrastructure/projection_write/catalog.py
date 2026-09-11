"""Catalog implementation; facade-independent dependencies."""

from __future__ import annotations
from .catalog_ports import CatalogPort, EnsureRuleboxReadyBindings
from copy import deepcopy
from digital_twin.domain.ontology_rulebox_governance import (
    rulebox_rules_hash as compute_rulebox_rules_hash,
)
from digital_twin.domain.world_partitioned_reasoning import (
    WORLD_PARTITIONED_REASONING_VERSION,
    compile_world_partitioned_rules,
)
from digital_twin.infrastructure.graph_store_rulebox import (
    rulebox_rules_from_payload,
    rulebox_rules_to_payload,
)
from typing import Dict, List
import hashlib


def world_rule_partition(
    _store: CatalogPort, rule_catalog: Dict[str, object]
) -> Dict[str, object]:
    if _store._frozen_rulebox_catalog is not None and isinstance(
        _store._frozen_world_rule_partition, dict
    ):
        return _store.copy_world_rule_partition(_store._frozen_world_rule_partition)
    rows = [
        dict(item)
        for item in (rule_catalog or {}).get("rules") or []
        if isinstance(item, dict)
    ]
    if not rows:
        rows = [
            dict(item)
            for item in _store.rulebox_rules_for_impact()
            if isinstance(item, dict)
        ]
    try:
        parsed = rulebox_rules_from_payload({"rules": rows})
    except ValueError as error:
        return {"status": "invalid", "failures": [{"reason": str(error)}]}
    partition = compile_world_partitioned_rules(parsed)
    if _store._frozen_rulebox_catalog is not None:
        _store._frozen_world_rule_partition = _store.copy_world_rule_partition(
            partition
        )
    return partition


def copy_world_rule_partition(partition: Dict[str, object]) -> Dict[str, object]:
    """Copy mutable containers while sharing frozen compiled rule values."""

    copied = dict(partition or {})
    for key in [
        "sharedRules",
        "overlayRules",
        "sharedRuleIds",
        "overlayRuleIds",
    ]:
        copied[key] = list(copied.get(key) or [])
    copied["failures"] = deepcopy(copied.get("failures") or [])
    copied["rules"] = deepcopy(copied.get("rules") or [])
    return copied


def catalog_for_rules(
    _store: CatalogPort, rule_catalog: Dict[str, object], rules
) -> Dict[str, object]:
    rule_values = list(rules or [])
    cache_key = hashlib.sha256(
        "|".join(
            str(getattr(rule, "rule_id", "") or "") for rule in rule_values
        ).encode("utf-8")
    ).hexdigest()
    if (
        _store._frozen_rulebox_catalog is not None
        and cache_key in _store._frozen_compiled_rule_catalogs
    ):
        return _store.copy_compiled_rule_catalog(
            _store._frozen_compiled_rule_catalogs[cache_key]
        )
    payloads = rulebox_rules_to_payload(rule_values)
    relation_types = sorted(
        {
            str(condition.get("relation_type") or condition.get("relationType") or "")
            .upper()
            .strip()
            for rule in payloads
            for condition in rule.get("conditions") or []
            if isinstance(condition, dict)
            and str(condition.get("kind") or "") == "relation"
            and str(
                condition.get("relation_type") or condition.get("relationType") or ""
            ).strip()
        }
    )
    compiled = {
        **dict(rule_catalog or {}),
        "rules": payloads,
        "inputRelationTypes": relation_types,
        "ruleCount": len(payloads),
        "compiledRuleboxRulesHash": compute_rulebox_rules_hash(payloads),
        "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
    }
    if _store._frozen_rulebox_catalog is not None:
        _store._frozen_compiled_rule_catalogs[cache_key] = (
            _store.copy_compiled_rule_catalog(compiled)
        )
    return compiled


def ensure_rulebox_ready(
    _store: CatalogPort, *, _bindings: EnsureRuleboxReadyBindings
) -> Dict[str, object]:
    if _store._frozen_rulebox_catalog is not None:
        cached_readiness = getattr(_store, "_frozen_rulebox_readiness", None)
        if isinstance(cached_readiness, dict):
            return deepcopy(cached_readiness)

        def freeze_readiness(result: Dict[str, object]) -> Dict[str, object]:
            _store._frozen_rulebox_readiness = deepcopy(result)
            return deepcopy(result)

        snapshot = deepcopy(_store._frozen_rulebox_catalog)
        stored_rules = [
            dict(item) for item in snapshot.get("rules") or [] if isinstance(item, dict)
        ]
        _store._rulebox_impact_rules = stored_rules
        stored_count = int(
            snapshot.get("ruleboxRuleCount")
            or snapshot.get("ruleCount")
            or len(stored_rules)
        )
        stored_hash = str(
            snapshot.get("ruleboxRulesHash")
            or snapshot.get("sourceRulesHash")
            or snapshot.get("rulesHash")
            or ""
        ).strip()
        if not stored_hash and stored_rules:
            stored_hash = compute_rulebox_rules_hash(stored_rules)
        if (
            not snapshot.get("configured")
            or str(snapshot.get("status") or "") != "ok"
            or not stored_rules
            or stored_count <= 0
        ):
            return freeze_readiness(
                {
                    "status": "not-ready",
                    "reason": "The frozen V2 RuleBox release is unavailable or empty.",
                    "ruleCount": stored_count,
                    "runtimeCatalogSource": "frozen-v2-release",
                }
            )
        if _bindings.rulebox_catalog_requires_bootstrap_repair(
            stored_rules
        ) and not bool(snapshot.get("frozenReleaseVerified")):
            return freeze_readiness(
                {
                    "status": "not-ready",
                    "reason": (
                        "The frozen V2 RuleBox requires migration. Register a new "
                        "release after completing RuleBox preflight."
                    ),
                    "ruleCount": stored_count,
                    "ruleboxRulesHash": stored_hash,
                    "runtimeCatalogSource": "frozen-v2-release",
                }
            )
        missing_decision_policy = _bindings.rulebox_rules_missing_decision_stage(
            stored_rules
        )
        if missing_decision_policy:
            return freeze_readiness(
                {
                    "status": "not-ready",
                    "reason": "TypeDB 추론 규칙의 파생 관계에 decisionStage가 없습니다.",
                    "ruleCount": stored_count,
                    "ruleboxRulesHash": stored_hash,
                    "missingDecisionStageRuleIds": missing_decision_policy,
                    "runtimeCatalogSource": "frozen-v2-release",
                }
            )
        return freeze_readiness(
            {
                "status": "ready",
                "ruleCount": stored_count,
                "ruleboxRulesHash": stored_hash,
                "sourceOfTruth": "typedb-frozen-v2-release",
                "ruleCatalogStore": "release-memory",
                "runtimeCatalogSource": "frozen-v2-release",
                "releaseCatalogReused": True,
                "inputRelationTypes": _bindings.rulebox_input_relation_types(
                    stored_rules
                ),
                "bootstrapCatalogChecked": False,
                "ruleCatalogMigration": {
                    "status": "release-preflight-complete",
                    "required": False,
                    "saved": False,
                },
            }
        )
    _store._rulebox_impact_rules = None
    if not hasattr(_store.repository, "rulebox_snapshot"):
        return {}
    try:
        snapshot = _store.repository.rulebox_snapshot()
    except (
        Exception
    ) as error:  # noqa: BLE001 - projection will still expose the persistence error later.
        return {"status": "error", "reason": str(error)[:180]}
    if not isinstance(snapshot, dict):
        return {
            "status": "invalid",
            "reason": "RuleBox snapshot returned non-dict result.",
        }
    stored_rules = [
        dict(item) for item in snapshot.get("rules") or [] if isinstance(item, dict)
    ]
    _store._rulebox_impact_rules = stored_rules
    if not snapshot.get("configured"):
        return {
            "status": "disabled",
            "reason": str(
                snapshot.get("reason") or "Ontology graph storage is not configured."
            ),
        }
    bootstrap = None
    requires_bootstrap_repair = _bindings.rulebox_catalog_requires_bootstrap_repair(
        stored_rules
    )
    if requires_bootstrap_repair:
        bootstrap = _bindings.bootstrap_rule_catalog()
        migration = _store.migrate_typedb_rule_catalog(
            snapshot,
            list(bootstrap.get("rules") or []),
        )
    else:
        # The persisted TypeDB catalog is already structurally compatible
        # with the active raw ABox. Do not spend a realtime cycle
        # rebuilding code defaults merely to compare presentation fields.
        migration = {
            "status": "stored-catalog-ready",
            "required": False,
            "saved": False,
            "bootstrapChecked": False,
        }
    if migration.get("required") and not migration.get("saved"):
        return {
            "status": "not-ready",
            "reason": str(
                migration.get("reason")
                or "TypeDB 추론 규칙 마이그레이션에 실패했습니다."
            ),
            "ruleCount": int(
                snapshot.get("ruleboxRuleCount") or snapshot.get("ruleCount") or 0
            ),
            "ruleCatalogMigration": migration,
        }
    if migration.get("saved"):
        try:
            snapshot = _store.repository.rulebox_snapshot()
        except (
            Exception
        ):  # noqa: BLE001 - successful save metadata still proves the migration ran.
            snapshot = dict(migration.get("result") or snapshot)
        _store._rulebox_impact_rules = [
            dict(item) for item in snapshot.get("rules") or [] if isinstance(item, dict)
        ]
        stored_rules = list(_store._rulebox_impact_rules)
    stored_count = int(
        snapshot.get("ruleboxRuleCount") or snapshot.get("ruleCount") or 0
    )
    stored_hash = str(
        snapshot.get("ruleboxRulesHash") or snapshot.get("rulesHash") or ""
    ).strip()
    if not stored_hash and stored_rules:
        stored_hash = compute_rulebox_rules_hash(stored_rules)
    missing_decision_policy = _bindings.rulebox_rules_missing_decision_stage(
        stored_rules
    )
    if missing_decision_policy:
        return {
            "status": "not-ready",
            "reason": "TypeDB 추론 규칙의 파생 관계에 decisionStage가 없습니다.",
            "ruleCount": stored_count,
            "missingDecisionStageRuleIds": missing_decision_policy,
            "ruleCatalogMigration": migration,
        }
    if stored_count > 0 and str(snapshot.get("status") or "") == "ok":
        result = {
            "status": "ready",
            "ruleCount": stored_count,
            "ruleboxRulesHash": stored_hash,
            "sourceOfTruth": "typedb-direct-typeql-rules",
            "ruleCatalogStore": "typedb",
            "inputRelationTypes": _bindings.rulebox_input_relation_types(stored_rules),
            "bootstrapRuleCount": int(
                (bootstrap or {}).get("ruleCount")
                or snapshot.get("bootstrapRuleCount")
                or 0
            ),
            "bootstrapRulesHash": str((bootstrap or {}).get("ruleboxRulesHash") or ""),
            "bootstrapCatalogChecked": bool(bootstrap),
            "codeDefaultHashMismatch": bool(
                bootstrap
                and stored_hash
                and stored_hash != str(bootstrap.get("ruleboxRulesHash") or "")
            ),
            "ruleCatalogMigration": migration,
        }
        if result["codeDefaultHashMismatch"]:
            result["reason"] = (
                "TypeDB 추론 규칙이 운영 기준입니다. 코드 기본값은 빈 저장소를 시작할 때만 사용하며 "
                "저장된 규칙을 덮어쓰지 않습니다."
            )
        return result
    bootstrap = bootstrap or _bindings.bootstrap_rule_catalog()
    expected_rules = list(bootstrap.get("rules") or [])
    expected_hash = str(bootstrap.get("ruleboxRulesHash") or "")
    expected_count = int(bootstrap.get("ruleCount") or len(expected_rules))
    if str(snapshot.get("status") or "") != "empty":
        return {
            "status": "not-ready",
            "reason": str(
                snapshot.get("reason")
                or snapshot.get("status")
                or "RuleBox is not ready."
            ),
            "ruleCount": stored_count,
            "bootstrapRuleCount": expected_count,
            "ruleboxRulesHash": stored_hash,
            "bootstrapRulesHash": expected_hash,
        }
    if not hasattr(_store.repository, "seed_ontology"):
        return {
            "status": "not-ready",
            "reason": "RuleBox is empty and repository does not support bootstrap seeding.",
            "ruleCount": stored_count,
            "bootstrapRuleCount": expected_count,
            "bootstrapRulesHash": expected_hash,
        }
    try:
        seeded = _store.repository.seed_ontology(
            {
                "replaceRuleBox": False,
                "clearInference": False,
                "changeReason": "자동 RuleBox 부트스트랩: ABox 투영 전에 그래프 추론 규칙을 준비합니다.",
            }
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - projection will report readiness failure instead of crashing.
        return {"status": "error", "reason": str(error)[:180]}
    _store._rulebox_impact_rules = [dict(item) for item in expected_rules]
    return {
        "status": (
            "seeded"
            if bool((seeded or {}).get("seeded"))
            else str((seeded or {}).get("status") or "not-seeded")
        ),
        "ruleCount": int((seeded or {}).get("ruleCount") or 0),
        "sourceOfTruth": "typedb-direct-typeql-rules",
        "ruleCatalogStore": "typedb",
        "inputRelationTypes": _bindings.rulebox_input_relation_types(expected_rules),
        "bootstrapRuleCount": expected_count,
        "bootstrapRulesHash": expected_hash,
        "reason": str((seeded or {}).get("reason") or ""),
    }


def migrate_typedb_rule_catalog(
    _store: CatalogPort,
    snapshot: Dict[str, object],
    bootstrap_rules: List[Dict[str, object]],
) -> Dict[str, object]:
    stored_rules = (
        snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
    )
    if not stored_rules:
        return {"status": "not-inspectable", "required": False, "saved": False}
    migration = migrate_typedb_rule_catalog(stored_rules, bootstrap_rules)
    if not migration.get("changed"):
        return {"status": "ready", "required": False, "saved": False}
    if not hasattr(_store.repository, "save_rulebox"):
        return {**migration, "status": "unsupported", "required": True, "saved": False}
    try:
        result = _store.repository.save_rulebox(
            {
                "rules": migration.get("rules") or [],
                "changeReason": "TypeDB 단일 추론 경로 전환: 원시 ABox 조건·RuleBox 실행 지침으로 마이그레이션",
            }
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - failed migration must stop new inference generations.
        return {
            **migration,
            "status": "error",
            "required": True,
            "saved": False,
            "reason": str(error)[:180],
        }
    saved = (
        bool((result or {}).get("saved"))
        and str((result or {}).get("status") or "") == "ok"
    )
    return {
        **migration,
        "status": (
            "migrated" if saved else str((result or {}).get("status") or "not-saved")
        ),
        "required": True,
        "saved": saved,
        "reason": str((result or {}).get("reason") or "")[:180],
        "result": {
            "status": str((result or {}).get("status") or ""),
            "ruleCount": int(
                (result or {}).get("ruleCount")
                or (result or {}).get("ruleboxRuleCount")
                or 0
            ),
            "reason": str((result or {}).get("reason") or "")[:180],
        },
    }
