"""Web ontology access boundary."""

from digital_twin.infrastructure.web.common import first_query
from digital_twin.modules.reasoning.domain.ontology_worlds import portfolio_world_id
from typing import Dict
from typing import List


def ontology_world_id_from_values(values: Dict[str, object]) -> str:
    """Resolve an explicit world or an account query to PortfolioWorld.

    API callers can use a stable ``worldId`` once they have it, while existing
    account-oriented UI calls continue to select the same isolated world by
    supplying ``accountId`` and an optional ``tenantId``.
    """
    payload = values if isinstance(values, dict) else {}
    explicit = str(
        payload.get("worldId")
        or payload.get("ontologyWorldId")
        or payload.get("world_id")
        or ""
    ).strip()
    if explicit:
        return explicit
    account_id = str(payload.get("accountId") or payload.get("account_id") or "").strip()
    if not account_id:
        return ""
    tenant_id = str(payload.get("tenantId") or payload.get("tenant_id") or "").strip()
    return portfolio_world_id(account_id, tenant_id)


def ontology_world_id_from_query(query: Dict[str, List[str]]) -> str:
    return ontology_world_id_from_values({
        "worldId": first_query(query, "worldId") or first_query(query, "ontologyWorldId"),
        "accountId": first_query(query, "accountId") or first_query(query, "account"),
        "tenantId": first_query(query, "tenantId") or first_query(query, "tenant"),
    })


def ontology_repository_world_call(repo, method_name: str, *args, world_id: str = "", **kwargs):
    method = getattr(repo, method_name, None)
    if not callable(method):
        raise AttributeError(method_name + " is unavailable")
    if not str(world_id or "").strip():
        return method(*args, **kwargs)
    try:
        return method(*args, world_id=str(world_id), **kwargs)
    except TypeError as error:
        if "unexpected keyword" not in str(error) and "world_id" not in str(error):
            raise
        return method(*args, **kwargs)


def ontology_audit_symbols(query: Dict[str, List[str]]) -> List[str]:
    raw = first_query(query, "symbols") or first_query(query, "symbol")
    return [item.strip().upper() for item in str(raw or "").split(",") if item.strip()]
