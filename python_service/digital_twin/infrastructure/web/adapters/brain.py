"""Web brain boundary."""

from digital_twin.infrastructure.service_factory import build_investment_brain_service
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.cache import cached_api_payload
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.common import safe_int
from typing import Dict
from typing import List


HYPOTHESIS_TEMPLATE_READ_MODEL = StaleReadModelCache(
    "hypothesis-template-list",
    ttl_seconds=120,
    retry_cooldown_seconds=30,
)


HYPOTHESIS_POLICY_VERSION_READ_MODEL = StaleReadModelCache(
    "hypothesis-policy-version-list",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)


INVESTMENT_BRAIN_LIST_READ_MODEL = StaleReadModelCache(
    "investment-brain-list",
    ttl_seconds=15,
    retry_cooldown_seconds=5,
)


HYPOTHESIS_WORKSPACE_READ_MODEL = StaleReadModelCache(
    "hypothesis-workspace",
    ttl_seconds=30,
    retry_cooldown_seconds=10,
)


def hypothesis_templates_api_payload(force: bool = False) -> Dict[str, object]:
    return cached_api_payload(
        HYPOTHESIS_TEMPLATE_READ_MODEL,
        "active",
        lambda: build_investment_brain_service(operational_read_settings()).hypothesis_templates(),
        force=force,
    )


def hypothesis_policy_versions_api_payload(limit: int = 40, force: bool = False) -> Dict[str, object]:
    safe_limit_value = max(1, min(100, int(limit or 40)))
    return cached_api_payload(
        HYPOTHESIS_POLICY_VERSION_READ_MODEL,
        str(safe_limit_value),
        lambda: build_investment_brain_service(operational_read_settings()).hypothesis_policy_versions(
            limit=safe_limit_value,
        ),
        force=force,
    )


def investment_brain_episodes_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = safe_int(first_query(query, "limit"), 50, 1, 500)
    account_id = str(first_query(query, "accountId") or "")
    symbol = str(first_query(query, "symbol") or "").upper()
    view = str(first_query(query, "view") or "summary")
    return cached_api_payload(
        INVESTMENT_BRAIN_LIST_READ_MODEL,
        "episodes|" + "|".join([account_id or "all", symbol or "all", str(limit), view]),
        lambda: build_investment_brain_service().episodes(
            account_id=account_id,
            symbol=symbol,
            limit=limit,
            view=view,
        ),
        force=request_bool(first_query(query, "refresh"), False),
    )


def investment_brain_hypothesis_lifecycles_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = safe_int(first_query(query, "limit"), 100, 1, 500)
    event_limit = safe_int(first_query(query, "eventLimit"), 100, 1, 500)
    account_id = str(first_query(query, "accountId") or "")
    symbol = str(first_query(query, "symbol") or "").upper()
    market_id = str(first_query(query, "marketId") or "")
    scope = str(first_query(query, "scope") or "")
    view = str(first_query(query, "view") or "summary")
    return cached_api_payload(
        INVESTMENT_BRAIN_LIST_READ_MODEL,
        "lifecycles|" + "|".join([
            account_id or "all", symbol or "all", market_id or "all", scope or "all",
            str(limit), str(event_limit), view,
        ]),
        lambda: build_investment_brain_service().hypothesis_lifecycles(
            account_id=account_id,
            symbol=symbol,
            market_id=market_id,
            scope=scope,
            limit=limit,
            event_limit=event_limit,
            view=view,
        ),
        force=request_bool(first_query(query, "refresh"), False),
    )


def investment_brain_hypothesis_workspace_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = safe_int(first_query(query, "limit"), 100, 1, 500)
    event_limit = safe_int(first_query(query, "eventLimit"), 100, 1, 500)
    account_id = str(first_query(query, "accountId") or "")
    symbol = str(first_query(query, "symbol") or "").upper()
    market_id = str(first_query(query, "marketId") or "")
    scope = str(first_query(query, "scope") or "")
    view = str(first_query(query, "view") or "summary")
    cache_key = "|".join([
        account_id or "all", symbol or "all", market_id or "all", scope or "all",
        str(limit), str(event_limit), view,
    ])

    def load() -> Dict[str, object]:
        settings = operational_read_settings()
        return build_investment_brain_service(settings).hypothesis_workspace(
            account_id=account_id,
            symbol=symbol,
            market_id=market_id,
            scope=scope,
            limit=limit,
            event_limit=event_limit,
            view=view,
        )

    return cached_api_payload(
        HYPOTHESIS_WORKSPACE_READ_MODEL,
        cache_key,
        load,
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )


def investment_brain_research_runs_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = safe_int(first_query(query, "limit"), 50, 1, 500)
    account_id = str(first_query(query, "accountId") or "")
    symbol = str(first_query(query, "symbol") or "").upper()
    view = str(first_query(query, "view") or "summary")
    return cached_api_payload(
        INVESTMENT_BRAIN_LIST_READ_MODEL,
        "research-runs|" + "|".join([account_id or "all", symbol or "all", str(limit), view]),
        lambda: build_investment_brain_service().research_runs(
            account_id=account_id,
            symbol=symbol,
            limit=limit,
            view=view,
        ),
        force=request_bool(first_query(query, "refresh"), False),
    )


def investment_brain_question_payload(body: Dict[str, object]) -> Dict[str, object]:
    message = configured(body.get("message") or body.get("question"))
    if not message:
        raise ValueError("투자 질문을 입력하세요.")
    result = build_investment_brain_service().ask(
        message,
        account_id=configured(body.get("accountId")),
        symbol=configured(body.get("symbol")),
    )
    return result
