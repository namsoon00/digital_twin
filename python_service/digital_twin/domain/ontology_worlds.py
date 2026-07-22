"""Ontology world identity and ownership contracts.

An investment ontology has two different ownership boundaries:

* ``MarketWorld`` contains shareable observations about instruments and the
  market environment.
* ``PortfolioWorld`` contains one tenant/account's positions, policy and
  decision context.

The identifiers in this module are deliberately deterministic.  They are
used by the ABox manifest, TypeDB active pointers, InferenceBox generations
and alert context, so a worker can never select another account's live world
by ordering alone.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, Mapping


ONTOLOGY_WORLD_VERSION = "ontology-world-v1"
DEFAULT_TENANT_ID = "local"
SHARED_MARKET_TENANT_ID = "shared"
MARKET_WORLD_TYPE = "market"
PORTFOLIO_WORLD_TYPE = "portfolio"


def _clean(value: object, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-.")
    return normalized or fallback


def normalize_tenant_id(value: object = "") -> str:
    return _clean(value, DEFAULT_TENANT_ID)


def normalize_account_id(value: object = "") -> str:
    return _clean(value, "default")


def normalize_market_id(value: object = "") -> str:
    return _clean(value, "global")


def portfolio_world_id(account_id: object, tenant_id: object = "") -> str:
    return "portfolio:" + normalize_tenant_id(tenant_id) + ":" + normalize_account_id(account_id)


def market_world_id(market_id: object = "global", tenant_id: object = SHARED_MARKET_TENANT_ID) -> str:
    return "market:" + normalize_tenant_id(tenant_id or SHARED_MARKET_TENANT_ID) + ":" + normalize_market_id(market_id)


def world_type_from_id(world_id: object) -> str:
    value = str(world_id or "").strip().lower()
    if value.startswith("market:"):
        return MARKET_WORLD_TYPE
    return PORTFOLIO_WORLD_TYPE


def world_scope_suffix(world_id: object) -> str:
    """Return a compact deterministic suffix without hiding scope semantics.

    Scope parsing elsewhere depends on ``symbol:<ticker>:<family>`` and
    ``macro:<family>`` prefixes.  Appending this suffix keeps those concepts
    readable while making physical generations impossible to share across
    portfolio worlds accidentally.
    """
    value = str(world_id or "").strip() or "legacy"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def world_scoped_scope_id(scope_id: object, world_id: object) -> str:
    clean_scope = str(scope_id or "").strip()
    clean_world = str(world_id or "").strip()
    if not clean_scope or not clean_world:
        return clean_scope
    marker = ":world:"
    if marker in clean_scope:
        return clean_scope
    return clean_scope + marker + world_scope_suffix(clean_world)


@dataclass(frozen=True)
class OntologyWorld:
    world_id: str
    world_type: str
    tenant_id: str
    account_id: str = ""
    market_id: str = "global"

    def to_dict(self) -> Dict[str, str]:
        return {
            "ontologyWorldVersion": ONTOLOGY_WORLD_VERSION,
            "worldId": self.world_id,
            "worldType": self.world_type,
            "tenantId": self.tenant_id,
            "accountId": self.account_id,
            "marketId": self.market_id,
        }


def portfolio_world(account_id: object, tenant_id: object = "", market_id: object = "global") -> OntologyWorld:
    tenant = normalize_tenant_id(tenant_id)
    account = normalize_account_id(account_id)
    return OntologyWorld(
        world_id=portfolio_world_id(account, tenant),
        world_type=PORTFOLIO_WORLD_TYPE,
        tenant_id=tenant,
        account_id=account,
        market_id=normalize_market_id(market_id),
    )


def market_world(market_id: object = "global", tenant_id: object = SHARED_MARKET_TENANT_ID) -> OntologyWorld:
    tenant = normalize_tenant_id(tenant_id or SHARED_MARKET_TENANT_ID)
    market = normalize_market_id(market_id)
    return OntologyWorld(
        world_id=market_world_id(market, tenant),
        world_type=MARKET_WORLD_TYPE,
        tenant_id=tenant,
        account_id="",
        market_id=market,
    )


def world_from_snapshot(snapshot: object, settings: Mapping[str, object] = None) -> OntologyWorld:
    metadata = getattr(snapshot, "metadata", {}) or {}
    values = dict(settings or {})
    tenant_id = (
        metadata.get("tenantId")
        or metadata.get("tenant_id")
        or values.get("ontologyTenantId")
        or values.get("tenantId")
        or DEFAULT_TENANT_ID
    )
    market_value = (
        metadata.get("marketWorldId")
        or metadata.get("marketId")
        or values.get("ontologyMarketWorldId")
        or "global"
    )
    # Settings may contain a fully qualified shared-world id while a snapshot
    # normally carries just a market key.  Treating ``market:shared:kr`` as a
    # raw key would create the different id ``market:shared:market-shared-kr``
    # and split one physical market across two worlds.
    market_parts = str(market_value or "").strip().split(":", 2)
    if len(market_parts) == 3 and market_parts[0].lower() == "market":
        market_value = market_parts[2]
    return portfolio_world(getattr(snapshot, "account_id", ""), tenant_id, market_value)


def world_metadata(world: OntologyWorld) -> Dict[str, str]:
    return world.to_dict()
