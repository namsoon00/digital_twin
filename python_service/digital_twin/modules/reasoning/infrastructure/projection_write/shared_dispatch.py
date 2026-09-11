"""Shared dispatch implementation; facade-independent dependencies."""

from __future__ import annotations
from .shared_dispatch_ports import (
    SharedDispatchPort,
    ScheduleSharedWorldProjectionBindings,
)
from copy import deepcopy
from digital_twin.domain.knowledge_world_projection import build_knowledge_world_graph
from digital_twin.domain.market_world_projection import build_market_world_graph
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_worlds import world_metadata
from typing import Dict


def schedule_market_world_projection(
    _store: SharedDispatchPort,
    portfolio_graph: PortfolioOntology,
    shared_world,
    source_world=None,
) -> Dict[str, object]:
    """Queue the latest account-independent market observation safely."""
    return _store.schedule_shared_world_projection(
        "market",
        portfolio_graph,
        shared_world,
        source_world=source_world,
    )


def schedule_knowledge_world_projection(
    _store: SharedDispatchPort,
    portfolio_graph: PortfolioOntology,
    shared_world,
    source_world=None,
) -> Dict[str, object]:
    """Queue durable real-world relationship topology after verification."""
    # KnowledgeWorld has no legacy in-process writer. It is deliberately
    # durable-only so a process exit cannot quietly lose issuer/security
    # topology while a unit-test or a legacy adapter is exercising the
    # PortfolioWorld path.
    if not _store.world_projection_outbox:
        return {
            **world_metadata(shared_world),
            "projectionKind": "knowledge",
            "status": "deferred-durable-world-projection-outbox-unavailable",
            "preservedActiveGeneration": True,
            "reason": "KnowledgeWorld requires the durable MySQL projection outbox.",
        }
    return _store.schedule_shared_world_projection(
        "knowledge",
        portfolio_graph,
        shared_world,
        source_world=source_world,
    )


def schedule_shared_world_projection(
    _store: SharedDispatchPort,
    projection_kind: str,
    portfolio_graph: PortfolioOntology,
    shared_world,
    source_world=None,
    *,
    _bindings: ScheduleSharedWorldProjectionBindings,
) -> Dict[str, object]:
    """Use the durable outbox whenever production storage is available.

    The in-process coordinator remains only as a compatibility fallback
    for direct unit-test construction and legacy local adapters.  A
    verified PortfolioWorld must never rely on a daemon thread to retain
    the corresponding shared-world projection request.
    """
    kind = str(projection_kind or "market").strip().lower()
    if _store.active_graph_store_key() == "typedb" and _store.world_projection_outbox:
        # Never place a complete account ABox into the durable queue. The
        # source graph can contain hypothesis history and raw research
        # documents; each shared world receives only its bounded semantic
        # projection before the message is serialized to MySQL.
        projection_input = _store.shared_world_projection_input(
            kind,
            portfolio_graph,
            shared_world,
        )
        return _store.world_projection_outbox.enqueue(
            kind,
            shared_world,
            projection_input,
            source_world_id=str(
                getattr(source_world, "world_id", "")
                or (portfolio_graph.worldview or {}).get("worldId")
                or ""
            ),
            source_account_id=str(getattr(source_world, "account_id", "") or ""),
            source_observed_at=str(
                (projection_input.worldview or {}).get("sourceObservedAt")
                or (projection_input.worldview or {}).get("marketObservedAt")
                or (projection_input.worldview or {}).get("asOf")
                or (portfolio_graph.worldview or {}).get("asOf")
                or ""
            ),
        )
    if kind == "knowledge":
        return _store.project_knowledge_world(portfolio_graph, shared_world)
    if (
        _store.active_graph_store_key() != "typedb"
        or not _store.shared_market_world_async_projection_enabled()
    ):
        return _store.project_market_world(portfolio_graph, shared_world)
    return _bindings.SHARED_MARKET_WORLD_PROJECTION_COORDINATOR.enqueue(
        _store,
        portfolio_graph,
        shared_world,
    )


def shared_world_projection_input(
    _store: SharedDispatchPort,
    projection_kind: str,
    portfolio_graph: PortfolioOntology,
    shared_world,
) -> PortfolioOntology:
    """Build the small, shareable ABox payload stored in the outbox."""
    kind = str(projection_kind or "market").strip().lower()
    observed_at = str((portfolio_graph.worldview or {}).get("asOf") or "")
    if kind == "knowledge":
        update = build_knowledge_world_graph(
            portfolio_graph, shared_world, observed_at=observed_at
        )
    else:
        update = build_market_world_graph(
            portfolio_graph, shared_world, observed_at=observed_at
        )
    update.worldview["targetScopedManifestPatch"] = deepcopy(
        dict((portfolio_graph.worldview or {}).get("targetScopedManifestPatch") or {})
    )
    update.worldview["sharedWorldProjection"] = kind
    update.worldview["sourcePortfolioWorldId"] = str(
        (portfolio_graph.worldview or {}).get("worldId") or ""
    )
    return update


def project_market_world(
    _store: SharedDispatchPort, portfolio_graph: PortfolioOntology, shared_world
) -> Dict[str, object]:
    observed_at = str((portfolio_graph.worldview or {}).get("asOf") or "")
    update = build_market_world_graph(
        portfolio_graph, shared_world, observed_at=observed_at
    )
    update.worldview["targetScopedManifestPatch"] = deepcopy(
        dict((portfolio_graph.worldview or {}).get("targetScopedManifestPatch") or {})
    )
    return _store.project_shared_world_update(
        update, shared_world, projection_kind="market"
    )


def project_knowledge_world(
    _store: SharedDispatchPort, portfolio_graph: PortfolioOntology, shared_world
) -> Dict[str, object]:
    observed_at = str((portfolio_graph.worldview or {}).get("asOf") or "")
    update = build_knowledge_world_graph(
        portfolio_graph, shared_world, observed_at=observed_at
    )
    return _store.project_shared_world_update(
        update, shared_world, projection_kind="knowledge"
    )
