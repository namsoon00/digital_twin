"""Read-side contract for the active investment reasoning model.

The deployment registry, RuleBox, ontology catalog, and experiment ledger stay
authoritative. This projection gives the product one stable model identity
without copying rules into another mutable store.
"""

from __future__ import annotations

from typing import Dict, Mapping

from .investment_product_readiness import investment_product_readiness
from .investment_reasoning.rule_inventory import reasoning_rule_inventory


INVESTMENT_MODEL_VERSION = "investment-model-v1"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _number(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _short(value: object, length: int = 12) -> str:
    current = _text(value)
    return current[:length] if current else ""


def investment_model_projection(
    platform_value: object,
    rulebox_value: object,
    catalog_value: object,
    experiments_value: object,
    settings_value: object,
) -> Dict[str, object]:
    platform = _mapping(platform_value)
    rulebox = _mapping(rulebox_value)
    catalog = _mapping(catalog_value)
    experiments = _mapping(experiments_value)
    settings = _mapping(settings_value)
    control = _mapping(platform.get("control"))
    promotion = _mapping(platform.get("promotionReadiness"))
    promotion_health = _mapping(promotion.get("health"))
    active_id = _text(control.get("active_deployment_id") or control.get("activeDeploymentId"))
    candidate_id = _text(control.get("candidate_deployment_id") or control.get("candidateDeploymentId"))
    deployments = [
        _mapping(item)
        for item in platform.get("deployments") or []
        if isinstance(item, Mapping)
    ]
    active = next((
        item for item in deployments
        if _text(item.get("deploymentId") or item.get("id")) == active_id
    ), {})
    active_health = _mapping(active.get("health"))
    active_release = _mapping(active.get("releaseBundle"))
    active_capabilities = _mapping(active.get("capabilities"))
    counts = _mapping(catalog.get("counts"))
    blockers = [str(item) for item in promotion.get("blockers") or [] if str(item).strip()]
    promotion_ready = bool(promotion.get("ready")) and not blockers
    release_fingerprint = _text(
        active_health.get("releaseFingerprint")
        or active_health.get("candidateReleaseFingerprint")
        or promotion_health.get("releaseFingerprint")
        or promotion_health.get("candidateReleaseFingerprint")
    )
    release_id = _text(
        active_release.get("release_id")
        or active_health.get("candidateReleaseId")
        or promotion_health.get("candidateReleaseId")
    )
    status = "ready" if active_id and promotion_ready else ("review" if active_id else "unavailable")
    inventory = reasoning_rule_inventory([
        item for item in rulebox.get("rules") or [] if isinstance(item, Mapping)
    ])
    comparison = _mapping(promotion.get("comparison")) or _mapping(active_health.get("comparisonSummary"))
    product_readiness = investment_product_readiness(
        operational_promotion_ready=promotion_ready,
        rule_inventory=inventory,
        catalog=catalog,
        experiments=experiments,
        active_health=active_health,
        comparison=comparison,
        settings=settings,
    )
    return {
        "version": INVESTMENT_MODEL_VERSION,
        "status": status,
        "readOnly": True,
        "model": {
            "name": _text(settings.get("modelName")) or "Orbit Alpha 투자 판단 모델",
            "thesis": _text(settings.get("modelHypothesis")) or "관계와 반대 근거를 함께 비교해 현재 투자 의견을 결정합니다.",
            "contract": "facts-relations-hypotheses-inference-decision",
        },
        "activeRelease": {
            "deploymentId": active_id,
            "releaseId": release_id,
            "engineFamily": _text(active.get("engineFamily")),
            "engineVersion": _text(
                active.get("engineVersion")
                or active_health.get("engineVersion")
                or promotion_health.get("engineVersion")
            ),
            "runtimeRevision": _text(
                active_release.get("runtime_revision")
                or active_health.get("candidateRuntimeRevision")
                or promotion_health.get("candidateRuntimeRevision")
            ),
            "releaseFingerprint": release_fingerprint,
            "releaseShortHash": _short(release_fingerprint),
            "ruleboxFingerprint": _text(
                active_health.get("ruleboxFingerprint")
                or promotion_health.get("ruleboxFingerprint")
                or rulebox.get("ruleboxRulesHash")
            ),
            "ruleboxShortHash": _short(
                active_health.get("ruleboxFingerprint")
                or promotion_health.get("ruleboxFingerprint")
                or rulebox.get("ruleboxRulesHash")
            ),
            "status": _text(active.get("status")) or status,
            "updatedAt": _text(active.get("updatedAt") or active_health.get("lastRunAt") or promotion_health.get("lastRunAt")),
            "lastRunAt": _text(active_health.get("lastRunAt") or promotion_health.get("lastRunAt")),
        },
        "bindings": {
            "graphStore": _text(active.get("graphStoreBinding")),
            "timeSeries": _text(active.get("timeSeriesBackendId")),
            "sourceEventsDirect": bool(
                active_capabilities.get("directSourceEvents")
                or active_health.get("directSourceEvents")
                or promotion_health.get("directSourceEvents")
            ),
            "independentExecution": bool(
                active_capabilities.get("independentExecution")
                or active_health.get("independentExecution")
                or promotion_health.get("independentExecution")
            ),
        },
        "inventory": {
            "classes": _number(counts.get("classes")),
            "relations": _number(counts.get("relations")),
            "rules": _number(rulebox.get("ruleCount") or counts.get("executableRules")),
            "conditions": _number(rulebox.get("conditionCount")),
            "derivations": _number(rulebox.get("derivationCount")),
            "hypotheses": _number(counts.get("hypotheses")),
            "experiments": _number(experiments.get("total") or experiments.get("count")),
            "activeExperiments": _number(experiments.get("activeCount")),
        },
        "validation": {
            "state": "pass" if promotion_ready else "blocked",
            "label": "운영 릴리스 통과" if promotion_ready else "운영 승격 점검 필요",
            "promotionReady": promotion_ready,
            "blockers": blockers,
            "cohortId": _text(active_health.get("validationCohortId") or promotion_health.get("validationCohortId")),
            "ruleInventoryReady": bool(active_health.get("ruleInventoryReleaseReady") or promotion_health.get("ruleInventoryReleaseReady")),
        },
        "productReadiness": product_readiness,
        "candidate": {
            "deploymentId": candidate_id,
            "count": sum(1 for item in deployments if _text(item.get("status")) == "candidate"),
        },
        "governance": {
            "automaticPromotion": False,
            "stages": [
                "draft",
                "replay",
                "compare",
                "approval",
                "candidate",
                "promotion",
                "observation",
                "retired",
            ],
        },
    }
