"""Route an ABox change to the smallest ontology world that owns it.

This module is deliberately not an investment rule evaluator.  It translates
an already-computed scope delta into projection work ownership so a position
change does not enqueue a shared market rewrite, and a fresh quote does not
rewrite durable issuer topology.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Set


WORLD_ROUTING_VERSION = "ontology-world-impact-routing-v1"

# Current observations belong to MarketWorld.  ``macro-*`` facts are shared
# market observations as well, but do not by themselves imply a KnowledgeWorld
# rewrite.
MARKET_FACT_FAMILIES = {
    "market", "flow", "temporal", "evidence", "quality",
    "macro", "macro-market", "macro-fx", "macro-rates", "macro-crypto",
}

# Durable identity, exposure and valuation topology can be shared across
# accounts.  A ``link`` must be accompanied by one of these semantic
# families, otherwise it stays local because a generic account link is not
# reusable knowledge.
KNOWLEDGE_FACT_FAMILIES = {"profile", "exposure", "valuation"}


def _families(values: Iterable[object]) -> Set[str]:
    return {
        str(value or "").strip().lower()
        for value in values or []
        if str(value or "").strip()
    }


def route_world_impact(
    impact_plan: Mapping[str, object] = None,
    *,
    initial_projection: bool = False,
) -> Dict[str, object]:
    """Return bounded world projection work for a verified PortfolioWorld.

    ``build_inference_impact_plan`` remains the authoritative source for the
    changed ABox scopes.  This function only assigns those scopes to their
    physical ownership boundary.  Native investment inference always remains
    in the PortfolioWorld because it includes account positions and policy.
    """
    plan = dict(impact_plan or {})
    changed = _families(plan.get("changedScopeFamilies"))
    routing = _families(plan.get("routingScopeFamilies"))
    requested = _families(plan.get("requestedFactFamilies"))
    effective = changed or routing or requested

    market_required = bool(effective & MARKET_FACT_FAMILIES)
    knowledge_required = bool(effective & KNOWLEDGE_FACT_FAMILIES)
    # A full first projection establishes the reusable shared worlds once.
    if initial_projection:
        market_required = True
        knowledge_required = True

    deferred = sorted(effective - MARKET_FACT_FAMILIES - KNOWLEDGE_FACT_FAMILIES - {"link", "position", "state"})
    reasons = {
        "market": (
            "현재 시세·수급·뉴스·거시 관측 변경이 있어 공유 시장 세계를 갱신합니다."
            if market_required else
            "공유 시장 사실 변경이 없어 MarketWorld 투영을 생략합니다."
        ),
        "knowledge": (
            "기업 정체성·노출·가치 관계 변경이 있어 공유 지식 세계를 갱신합니다."
            if knowledge_required else
            "재사용 가능한 기업·노출 관계 변경이 없어 KnowledgeWorld 투영을 생략합니다."
        ),
    }
    return {
        "version": WORLD_ROUTING_VERSION,
        "portfolio": {
            "required": True,
            "reason": "계좌 보유·정책을 포함한 투자 판단은 PortfolioWorld에서만 확정합니다.",
        },
        "market": {"required": market_required, "reason": reasons["market"]},
        "knowledge": {"required": knowledge_required, "reason": reasons["knowledge"]},
        "effectiveFactFamilies": sorted(effective),
        "deferredFactFamilies": deferred,
        "initialProjection": bool(initial_projection),
    }
