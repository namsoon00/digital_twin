"""World-scoped adapter calls, preserving empty-world callback contracts."""

from typing import Dict


def typedb_world_kwargs(world_id: str = "") -> Dict[str, str]:
    """Return a world argument only when an explicit ontology world exists.

    Legacy TypeDB repositories and compatibility doubles deliberately expose
    their pre-world method signatures.  Omitting an empty keyword preserves
    that contract, while every explicit PortfolioWorld or MarketWorld remains
    mandatory and query-scoped.
    """
    clean_world_id = str(world_id or "").strip()
    return {"world_id": clean_world_id} if clean_world_id else {}


def typedb_call_for_world(callback, *args, world_id: str = "", **kwargs):
    """Invoke a world-aware repository method without mutating legacy calls."""
    return callback(*args, **kwargs, **typedb_world_kwargs(world_id))
