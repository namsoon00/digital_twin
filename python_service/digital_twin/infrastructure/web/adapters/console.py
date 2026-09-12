"""Web console boundary."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.adapters.calendar import investment_calendar_payload
from digital_twin.infrastructure.web.adapters.cases import investment_case_api_payload
from digital_twin.infrastructure.web.adapters.flow_lens import flow_lens_read_payload
from digital_twin.infrastructure.web.adapters.research_evidence import research_evidence_payload
from digital_twin.infrastructure.web.cache import cached_api_payload
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.common import safe_int
from digital_twin.modules.read_models.public import ConsoleReadModelService
from typing import Dict
from typing import List


INVESTMENT_READING_CACHE_VERSION = "investment-reading-v1"


DASHBOARD_READ_MODEL = StaleReadModelCache(
    "console-dashboard",
    ttl_seconds=20,
    retry_cooldown_seconds=10,
)


PORTFOLIO_CONSOLE_READ_MODEL = StaleReadModelCache(
    "console-portfolio",
    ttl_seconds=10,
    retry_cooldown_seconds=5,
)


MARKET_INSTRUMENTS_READ_MODEL = StaleReadModelCache(
    "console-market-instruments",
    ttl_seconds=5,
    retry_cooldown_seconds=3,
)


MARKET_EVIDENCE_READ_MODEL = StaleReadModelCache(
    "console-market-evidence",
    ttl_seconds=15,
    retry_cooldown_seconds=5,
)


DECISION_LIST_READ_MODEL = StaleReadModelCache(
    "decision-list",
    ttl_seconds=15,
    retry_cooldown_seconds=5,
)


def console_read_model_service(settings: Dict[str, object] = None) -> ConsoleReadModelService:
    configured_settings = settings or operational_read_settings()
    return ConsoleReadModelService(symbol_repository=stores.symbol_universe_store(configured_settings))


def _console_dashboard_source_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    settings = operational_read_settings()
    account_id = first_query(query, "accountId") or "default"

    def lifecycle_payload():
        return stores.investment_domain_store(settings).latest_portfolio_lifecycle("portfolio:" + account_id)

    def cases_payload():
        return investment_case_api_payload({"accountId": [account_id], "limit": ["100"]})

    def calendar_payload():
        calendar_query = {
            "from": [datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")],
            "limit": ["40"],
        }
        return investment_calendar_payload(calendar_query)

    readers = {
        "snapshot": lambda: flow_lens_read_payload(query),
        "lifecycle": lifecycle_payload,
        "cases": cases_payload,
        "calendar": calendar_payload,
    }
    results = {}
    with ThreadPoolExecutor(max_workers=len(readers), thread_name_prefix="console-dashboard") as executor:
        futures = {key: executor.submit(reader) for key, reader in readers.items()}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except Exception as error:  # noqa: BLE001 - dashboard sections degrade independently.
                results[key] = {"status": "unavailable", "error": str(error)[:240]}
    return console_read_model_service(settings).dashboard_summary(
        results.get("snapshot") or {},
        results.get("lifecycle") or {},
        results.get("cases") or {"items": []},
        results.get("calendar") or {"events": []},
    )


def console_dashboard_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    account_id = first_query(query, "accountId") or "default"
    watchlist = ",".join(sorted(filter(None, first_query(query, "watchlistSymbols").upper().split(","))))
    cache_key = INVESTMENT_READING_CACHE_VERSION + "|" + account_id + "|" + watchlist
    return cached_api_payload(
        DASHBOARD_READ_MODEL,
        cache_key,
        lambda: _console_dashboard_source_payload(query),
        force=request_bool(first_query(query, "refresh"), False),
    )


def console_portfolio_api_payload(query: Dict[str, List[str]], view: str) -> Dict[str, object]:
    account_id = first_query(query, "accountId") or "default"
    portfolio_id = first_query(query, "portfolioId") or "portfolio:" + account_id
    cache_key = "|".join([
        "portfolio-console-v2",
        account_id,
        portfolio_id,
        str(view or "summary"),
    ])

    def load() -> Dict[str, object]:
        settings = operational_read_settings()
        lifecycle = stores.investment_domain_store(settings).latest_portfolio_lifecycle(portfolio_id)
        snapshot = flow_lens_read_payload(query) if view in {"summary", "positions"} else {}
        subject_case = (
            stores.subject_decision_case_store(settings).latest_portfolio(account_id)
            if view in {"summary", "rebalance", "interpretation"}
            else None
        )
        return console_read_model_service(settings).portfolio(
            lifecycle,
            view,
            snapshot=snapshot,
            subject_case=subject_case.to_dict() if subject_case else {},
        )

    payload = cached_api_payload(
        PORTFOLIO_CONSOLE_READ_MODEL,
        cache_key,
        load,
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )
    payload.setdefault("version", "console-read-model-v1")
    payload.setdefault("view", str(view or "summary"))
    payload.setdefault("summary", {})
    if view in {"summary", "positions"}:
        payload.setdefault("positions", [])
    return payload


def console_market_instruments_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    watchlist = ",".join(sorted(filter(None, first_query(query, "watchlistSymbols").upper().split(","))))

    def load() -> Dict[str, object]:
        settings = operational_read_settings()
        snapshot = flow_lens_read_payload(query)
        read_model = snapshot.get("readModel") if isinstance(snapshot.get("readModel"), dict) else {}
        if str(read_model.get("status") or "").lower() == "pending" or not read_model.get("ready", True):
            raise RuntimeError("시장 스냅샷 읽기 모델을 준비하고 있습니다.")
        return console_read_model_service(settings).market_instruments(snapshot)

    payload = cached_api_payload(
        MARKET_INSTRUMENTS_READ_MODEL,
        watchlist or "default",
        load,
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )
    payload.setdefault("version", "console-read-model-v1")
    payload.setdefault("items", [])
    payload.setdefault("summary", {})
    return payload


def console_market_evidence_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    requested_limit = safe_int(first_query(query, "limit"), 12, 1, 100)
    source_query = {key: list(value) for key, value in query.items()}
    source_query["limit"] = [str(min(100, max(16, requested_limit * 2)))]
    cache_key = "|".join([
        str(first_query(query, "symbol") or "all").upper(),
        str(first_query(query, "kind") or "all"),
        str(requested_limit),
    ])

    def load() -> Dict[str, object]:
        payload = research_evidence_payload(source_query)
        return console_read_model_service().market_evidence(payload, requested_limit)

    payload = cached_api_payload(
        MARKET_EVIDENCE_READ_MODEL,
        cache_key,
        load,
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )
    payload.setdefault("version", "console-read-model-v1")
    payload.setdefault("items", [])
    payload.setdefault("totalEligible", 0)
    return payload


def console_decisions_api_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    cache_key = "|".join([
        INVESTMENT_READING_CACHE_VERSION,
        str(first_query(query, "accountId") or first_query(query, "account") or "default"),
        str(first_query(query, "symbol") or "all").upper(),
        str(first_query(query, "limit") or "100"),
        str(first_query(query, "audience") or first_query(query, "includeOperator") or "user"),
    ])

    def load() -> Dict[str, object]:
        settings = operational_read_settings()
        payload = investment_case_api_payload(query)
        return console_read_model_service(settings).decision_heads(payload)

    return cached_api_payload(
        DECISION_LIST_READ_MODEL,
        cache_key,
        load,
        force=request_bool(first_query(query, "refresh"), False),
    )
