"""Rulebox commands implementation; facade-independent dependencies."""

from __future__ import annotations
from .rulebox_commands_ports import (
    RuleboxCommandsPort,
    SaveRuleboxBindings,
    EnsureRuleboxVersionBaselineBindings,
)
from digital_twin.modules.model_registry.contracts import rulebox_version_payload
from digital_twin.infrastructure.graph_store_rulebox import (
    rulebox_rules_from_payload,
    rulebox_rules_to_payload,
)
from typing import Dict
import json


def save_rulebox(
    _store: RuleboxCommandsPort,
    payload: Dict[str, object] = None,
    *,
    _bindings: SaveRuleboxBindings,
) -> Dict[str, object]:
    try:
        rules = rulebox_rules_from_payload(payload or {}, strict_governance=True)
    except ValueError as error:
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-rulebox",
            "graphStore": "typedb",
            "reason": str(error),
        }
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    version = rulebox_version_payload(
        rules,
        _bindings.utc_now(),
        str(source.get("changeReason") or ""),
        str(source.get("author") or "local-admin"),
        str(source.get("status") or "saved"),
    )
    baseline_result = {}
    # The first governed save must retain the previous active RuleBox as a
    # restoration point.  Later writes already have immutable versions.
    try:
        previous_snapshot = _store.rulebox_snapshot()
        previous_rows = (
            previous_snapshot.get("rules")
            if isinstance(previous_snapshot.get("rules"), list)
            else []
        )
        previous_versions = (
            previous_snapshot.get("versions")
            if isinstance(previous_snapshot.get("versions"), list)
            else []
        )
        if previous_rows and not previous_versions:
            previous_rules = rulebox_rules_from_payload({"rules": previous_rows})
            baseline = rulebox_version_payload(
                previous_rules,
                _bindings.utc_now(),
                "정책 변경 전 자동 기준선",
                "system-baseline",
                "baseline",
            )
            baseline_result = _store.append_rulebox_version(baseline)
    except (
        Exception
    ) as error:  # noqa: BLE001 - a failed audit append must not hide a valid active RuleBox.
        baseline_result = {
            "saved": False,
            "status": "error",
            "reason": str(error)[:220],
        }
    previous_rules = _store._last_rules
    _store.clear_rulebox_snapshot_cache()
    # RuleBox is an immutable static generation. Route an admin edit
    # through the same seed/manifest boundary used at startup so a policy
    # save never broad-deletes the durable graph before its replacement is
    # available for TypeDB-native inference.
    save_result = {}
    try:
        save_result = _store.seed_ontology(
            {
                "rules": rulebox_rules_to_payload(rules),
                "replaceRuleBox": True,
                "clearInference": False,
            }
        )
    finally:
        if not bool(save_result.get("saved")):
            _store._last_rules = previous_rules
            _store.clear_rulebox_snapshot_cache()
    version_result = {}
    if bool(save_result.get("saved")):
        version_result = _store.append_rulebox_version(version)
    _store.clear_rulebox_snapshot_cache()
    snapshot = _store.rulebox_snapshot()
    snapshot.update(
        {
            "saved": bool(save_result.get("saved")),
            "status": save_result.get("status") or snapshot.get("status"),
            "reason": save_result.get("reason") or snapshot.get("reason") or "",
            "saveResult": save_result,
            "savedVersion": (
                {
                    key: version.get(key)
                    for key in [
                        "id",
                        "versionLabel",
                        "rulesHash",
                        "shortHash",
                        "ruleCount",
                        "conditionCount",
                        "derivationCount",
                        "createdAt",
                        "changeReason",
                        "author",
                        "status",
                    ]
                }
                if bool(version_result.get("saved"))
                else {}
            ),
            "versionAudit": version_result
            or {
                "saved": False,
                "status": "skipped",
                "reason": "RuleBox 저장이 완료되지 않아 버전 기록을 만들지 않았습니다.",
            },
            "preSaveBaselineAudit": baseline_result,
        }
    )
    return snapshot


def restore_rulebox_version(
    _store: RuleboxCommandsPort,
    version_id: str,
    change_reason: str = "",
    author: str = "",
) -> Dict[str, object]:
    target = str(version_id or "").strip()
    if not target:
        return {
            "configured": bool(_store.address),
            "saved": False,
            "status": "invalid-version",
            "graphStore": "typedb",
            "reason": "RuleBox version ID is required.",
        }
    snapshot = _store.rulebox_snapshot()
    version = next(
        (
            item
            for item in snapshot.get("versions") or []
            if str(item.get("id") or "").strip() == target
        ),
        None,
    )
    if not isinstance(version, dict):
        return {
            "configured": bool(_store.address),
            "saved": False,
            "status": "version-not-found",
            "graphStore": "typedb",
            "reason": "RuleBox version was not found: " + target,
        }
    try:
        rules = json.loads(str(version.get("rulesJson") or "[]"))
    except json.JSONDecodeError as error:
        return {
            "configured": bool(_store.address),
            "saved": False,
            "status": "invalid-version",
            "graphStore": "typedb",
            "reason": "Stored RuleBox version is not valid JSON: " + str(error),
        }
    result = _store.save_rulebox(
        {
            "rules": rules,
            "changeReason": str(change_reason or "").strip()
            or ("RuleBox 버전 복원: " + target),
            "author": str(author or "local-admin").strip() or "local-admin",
            "status": "restored",
            "source": "rulebox-version-restore",
        }
    )
    result["restoredVersionId"] = target
    return result


def ensure_rulebox_version_baseline(
    _store: RuleboxCommandsPort,
    author: str = "",
    *,
    _bindings: EnsureRuleboxVersionBaselineBindings,
) -> Dict[str, object]:
    """Record the active RuleBox once without changing its executable rows."""
    snapshot = _store.rulebox_snapshot()
    if str(snapshot.get("status") or "") != "ok":
        return {
            "configured": bool(_store.address),
            "saved": False,
            "status": str(snapshot.get("status") or "unavailable"),
            "graphStore": "typedb",
            "reason": str(snapshot.get("reason") or "현재 RuleBox를 읽지 못했습니다."),
        }
    existing = (
        snapshot.get("versions") if isinstance(snapshot.get("versions"), list) else []
    )
    if existing:
        return {
            "configured": True,
            "saved": False,
            "status": "unchanged",
            "graphStore": "typedb",
            "versionCount": len(existing),
            "reason": "기존 RuleBox 버전이 이미 있습니다.",
        }
    try:
        rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
    except ValueError as error:
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-rulebox",
            "graphStore": "typedb",
            "reason": str(error),
        }
    version = rulebox_version_payload(
        rules,
        _bindings.utc_now(),
        "기존 활성 RuleBox 기준선 기록",
        str(author or "system-baseline").strip() or "system-baseline",
        "baseline",
    )
    result = _store.append_rulebox_version(version)
    _store.clear_rulebox_snapshot_cache()
    result["baselineVersion"] = {
        key: version.get(key)
        for key in [
            "id",
            "versionLabel",
            "shortHash",
            "rulesHash",
            "createdAt",
            "changeReason",
            "author",
            "status",
        ]
    }
    return result
