"""Web instruments boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.operational_error_reporting import operational_error_reporter
from digital_twin.infrastructure.operational_error_reporting import report_runtime_error
from digital_twin.infrastructure.service_factory import build_account_watchlist_service
from digital_twin.infrastructure.service_factory import build_market_data_collection_runner
from digital_twin.infrastructure.service_factory import build_monitor_runner
from digital_twin.infrastructure.service_factory import build_symbol_universe_service
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import new_id
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.events import RealtimeEventBridge
from digital_twin.infrastructure.web.events import new_domain_event
from digital_twin.modules.instruments.contracts import symbol_search_symbol_candidates
from digital_twin.modules.instruments.domain.event_types import SYMBOL_UNIVERSE_REFRESHED
from digital_twin.modules.instruments.domain.event_types import SYMBOL_UNIVERSE_REFRESH_FAILED
from digital_twin.modules.instruments.domain.event_types import SYMBOL_UNIVERSE_REFRESH_REQUESTED
from digital_twin.modules.instruments.public import AccountWatchlistService
from digital_twin.modules.instruments.public import DEFAULT_SYMBOL_SEEDS
from digital_twin.modules.instruments.public import SUPPORTED_MARKETS
from digital_twin.modules.instruments.public import seed_symbol
from typing import Dict
from typing import List
import threading


WATCHLIST_REFRESH_LOCK = threading.Lock()


WATCHLIST_REFRESH_STATE: Dict[str, object] = {
    "running": False,
    "pending": False,
    "accountIds": set(),
    "symbols": set(),
    "lastStatus": "idle",
    "lastError": "",
    "lastFinishedAt": "",
}


SYMBOL_UNIVERSE_REFRESH_LOCK = threading.Lock()


SYMBOL_UNIVERSE_REFRESH_STATE: Dict[str, object] = {
    "jobId": "",
    "running": False,
    "status": "idle",
    "markets": set(),
    "pendingMarkets": set(),
    "completedMarkets": set(),
    "results": [],
    "summary": {},
    "requestedAt": "",
    "startedAt": "",
    "finishedAt": "",
    "lastError": "",
    "stage": "idle",
    "currentMarket": "",
    "stageItemCount": 0,
    "updatedAt": "",
}


def watchlist_refresh_status() -> Dict[str, object]:
    with WATCHLIST_REFRESH_LOCK:
        return {
            "status": "queued" if WATCHLIST_REFRESH_STATE["running"] else str(WATCHLIST_REFRESH_STATE["lastStatus"]),
            "running": bool(WATCHLIST_REFRESH_STATE["running"]),
            "pending": bool(WATCHLIST_REFRESH_STATE["pending"]),
            "accountIds": sorted(WATCHLIST_REFRESH_STATE["accountIds"]),
            "symbols": sorted(WATCHLIST_REFRESH_STATE["symbols"]),
            "lastError": str(WATCHLIST_REFRESH_STATE["lastError"]),
            "lastFinishedAt": str(WATCHLIST_REFRESH_STATE["lastFinishedAt"]),
        }


def run_watchlist_refresh_pipeline() -> None:
    while True:
        with WATCHLIST_REFRESH_LOCK:
            account_ids = set(WATCHLIST_REFRESH_STATE["accountIds"])
            symbols = set(WATCHLIST_REFRESH_STATE["symbols"])
            WATCHLIST_REFRESH_STATE["accountIds"] = set()
            WATCHLIST_REFRESH_STATE["symbols"] = set()
            WATCHLIST_REFRESH_STATE["pending"] = False
            WATCHLIST_REFRESH_STATE["lastStatus"] = "running"
            WATCHLIST_REFRESH_STATE["lastError"] = ""
        try:
            settings = runtime_settings()
            build_market_data_collection_runner(settings=settings).run_once(force=True)
            registry = stores.account_reader(settings)
            accounts = [account for account in registry.load() if not account_ids or account.account_id in account_ids]
            if accounts:
                build_monitor_runner(accounts, settings=settings).run_once(
                    force=False,
                    symbol_filter=symbols,
                    holdings_snapshot_requested=False,
                )
            with WATCHLIST_REFRESH_LOCK:
                WATCHLIST_REFRESH_STATE["lastStatus"] = "completed"
        except Exception as error:  # noqa: BLE001 - the saved watchlist must remain usable when a vendor is unavailable.
            report_runtime_error(operational_error_reporter(), "Watchlist refresh", error, "watchlist refresh pipeline")
            with WATCHLIST_REFRESH_LOCK:
                WATCHLIST_REFRESH_STATE["lastStatus"] = "failed"
                WATCHLIST_REFRESH_STATE["lastError"] = str(error)[:300]
        with WATCHLIST_REFRESH_LOCK:
            WATCHLIST_REFRESH_STATE["lastFinishedAt"] = now()
            if WATCHLIST_REFRESH_STATE["pending"]:
                continue
            WATCHLIST_REFRESH_STATE["running"] = False
            return


def request_watchlist_refresh(account_id: str, symbol: str, _action: str) -> Dict[str, object]:
    should_start = False
    with WATCHLIST_REFRESH_LOCK:
        WATCHLIST_REFRESH_STATE["accountIds"].add(str(account_id or ""))
        if symbol:
            WATCHLIST_REFRESH_STATE["symbols"].add(str(symbol).upper())
        WATCHLIST_REFRESH_STATE["pending"] = True
        if not WATCHLIST_REFRESH_STATE["running"]:
            WATCHLIST_REFRESH_STATE["running"] = True
            should_start = True
    if should_start:
        threading.Thread(target=run_watchlist_refresh_pipeline, name="watchlist-refresh", daemon=True).start()
    return watchlist_refresh_status()


def account_watchlist_service() -> AccountWatchlistService:
    return build_account_watchlist_service(
        event_publisher=RealtimeEventBridge(),
        refresh_requester=request_watchlist_refresh,
    )


def symbol_universe_service():
    return build_symbol_universe_service()


def symbol_universe_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    search = first_query(query, "query") or first_query(query, "q")
    market = first_query(query, "market")
    limit = int(first_query(query, "limit") or 16)
    offset = int(first_query(query, "offset") or 0)
    try:
        return symbol_universe_service().search(
            query=search,
            market=market,
            limit=limit,
            offset=offset,
        )
    except Exception as error:  # noqa: BLE001 - seed universe keeps search usable without optional MySQL.
        items = fallback_symbol_universe_items(search, market)
        return {
            "items": items[offset: offset + limit],
            "summary": fallback_symbol_universe_summary(str(error)[:240]),
            "query": search or "",
            "market": market or "",
            "limit": limit,
            "offset": offset,
            "total": len(items),
            "storeWarning": str(error)[:240],
        }


def symbol_universe_suggest_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    search = first_query(query, "query") or first_query(query, "q")
    market = first_query(query, "market")
    limit = int(first_query(query, "limit") or 8)
    try:
        return symbol_universe_service().suggest(
            query=search,
            market=market,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - autocomplete can fall back to local seed symbols.
        return {
            "items": fallback_symbol_universe_items(search, market)[:limit],
            "query": search or "",
            "market": market or "",
            "limit": limit,
            "storeWarning": str(error)[:240],
        }


def fallback_symbol_universe_items(search: str = "", market: str = "") -> List[Dict[str, object]]:
    needle = configured(search).lower()
    market_filter = configured(market).upper()
    candidate_symbol_list = symbol_search_symbol_candidates(search)
    candidate_symbols = set(candidate_symbol_list)
    seed_symbols = list(DEFAULT_SYMBOL_SEEDS)
    for symbol in reversed(candidate_symbol_list):
        if symbol in seed_symbols:
            seed_symbols.remove(symbol)
        seed_symbols.insert(0, symbol)
    items = [seed_symbol(symbol).to_dict(24) for symbol in seed_symbols]
    if market_filter:
        items = [item for item in items if str(item.get("market") or "").upper() == market_filter]
    if needle:
        def matches(item):
            if str(item.get("symbol") or "").upper() in candidate_symbols:
                return True
            haystack = " ".join([
                str(item.get("symbol") or ""),
                str(item.get("name") or ""),
                str(item.get("market") or ""),
                str(item.get("sector") or ""),
            ]).lower()
            return needle in haystack

        items = [item for item in items if matches(item)]
    return items


def fallback_symbol_universe_summary(warning: str = "") -> Dict[str, object]:
    items = [seed_symbol(symbol).to_dict(24) for symbol in DEFAULT_SYMBOL_SEEDS]
    markets = []
    for market in sorted({str(item.get("market") or "") for item in items if item.get("market")}):
        markets.append({
            "market": market,
            "count": len([item for item in items if item.get("market") == market]),
            "lastSeenAt": "",
            "stale": True,
            "source": "Orbit Alpha seed",
            "sourceUrl": "local-default",
        })
    return {
        "markets": markets,
        "sources": [],
        "maxAgeHours": 24,
        "total": len(items),
        "storeWarning": warning,
    }


def requested_symbol_universe_markets(payload: Dict[str, object]) -> List[str]:
    raw_markets = payload.get("markets") if isinstance(payload, dict) else None
    if isinstance(raw_markets, str):
        requested = [item.strip().upper() for item in raw_markets.split(",") if item.strip()]
    elif isinstance(raw_markets, list):
        requested = [str(item or "").strip().upper() for item in raw_markets if str(item or "").strip()]
    else:
        requested = list(SUPPORTED_MARKETS)
    supported = [str(market or "").upper() for market in SUPPORTED_MARKETS]
    selected = [market for market in supported if market in requested]
    return selected or supported


def _symbol_universe_refresh_status_locked() -> Dict[str, object]:
    markets = [market for market in SUPPORTED_MARKETS if market in SYMBOL_UNIVERSE_REFRESH_STATE["markets"]]
    completed = [market for market in markets if market in SYMBOL_UNIVERSE_REFRESH_STATE["completedMarkets"]]
    total = len(markets)
    finished = len(completed)
    status = str(SYMBOL_UNIVERSE_REFRESH_STATE["status"] or "idle")
    stage = str(SYMBOL_UNIVERSE_REFRESH_STATE.get("stage") or "idle")
    stage_progress = {
        "queued": 3,
        "connecting": 10,
        "fetching": 35,
        "saving": 72,
        "verifying": 88,
        "summarizing": 94,
    }.get(stage, 0)
    progress = (
        100
        if status in {"completed", "partial", "failed"} and total
        else round(((finished * 100) + stage_progress) / total)
        if total
        else 0
    )
    return {
        "jobId": str(SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] or ""),
        "status": status,
        "running": bool(SYMBOL_UNIVERSE_REFRESH_STATE["running"]),
        "pending": bool(SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"]),
        "markets": markets,
        "completedMarkets": completed,
        "completedCount": finished,
        "totalCount": total,
        "progressPercent": progress,
        "results": [dict(item) for item in SYMBOL_UNIVERSE_REFRESH_STATE["results"]],
        "summary": dict(SYMBOL_UNIVERSE_REFRESH_STATE["summary"]),
        "requestedAt": str(SYMBOL_UNIVERSE_REFRESH_STATE["requestedAt"] or ""),
        "startedAt": str(SYMBOL_UNIVERSE_REFRESH_STATE["startedAt"] or ""),
        "finishedAt": str(SYMBOL_UNIVERSE_REFRESH_STATE["finishedAt"] or ""),
        "lastError": str(SYMBOL_UNIVERSE_REFRESH_STATE["lastError"] or ""),
        "stage": stage,
        "currentMarket": str(SYMBOL_UNIVERSE_REFRESH_STATE.get("currentMarket") or ""),
        "stageItemCount": int(SYMBOL_UNIVERSE_REFRESH_STATE.get("stageItemCount") or 0),
        "updatedAt": str(SYMBOL_UNIVERSE_REFRESH_STATE.get("updatedAt") or ""),
    }


def symbol_universe_refresh_status(job_id: str = "") -> Dict[str, object]:
    with SYMBOL_UNIVERSE_REFRESH_LOCK:
        status = _symbol_universe_refresh_status_locked()
    requested_job_id = configured(job_id)
    if requested_job_id and status["jobId"] and requested_job_id != status["jobId"]:
        status["requestedJobId"] = requested_job_id
        status["superseded"] = True
        return status
    if requested_job_id and not status["jobId"]:
        return {
            "jobId": requested_job_id,
            "status": "unknown",
            "running": False,
            "pending": False,
            "markets": [],
            "completedMarkets": [],
            "completedCount": 0,
            "totalCount": 0,
            "progressPercent": 0,
            "results": [],
            "summary": {},
            "requestedAt": "",
            "startedAt": "",
            "finishedAt": "",
            "lastError": "갱신 작업 상태를 찾을 수 없습니다. 서버가 재시작되었을 수 있습니다.",
            "latestJobId": "",
            "stage": "unknown",
            "currentMarket": "",
            "stageItemCount": 0,
            "updatedAt": "",
        }
    return status


def _replace_symbol_universe_market_result(result: Dict[str, object]) -> None:
    market = str(result.get("market") or "").upper()
    rows = [
        dict(item)
        for item in SYMBOL_UNIVERSE_REFRESH_STATE["results"]
        if str(item.get("market") or "").upper() != market
    ]
    rows.append(dict(result))
    order = {market_name: index for index, market_name in enumerate(SUPPORTED_MARKETS)}
    SYMBOL_UNIVERSE_REFRESH_STATE["results"] = sorted(
        rows,
        key=lambda item: order.get(str(item.get("market") or "").upper(), len(order)),
    )


def run_symbol_universe_refresh_pipeline(job_id: str) -> None:
    try:
        new_domain_event(
            SYMBOL_UNIVERSE_REFRESH_REQUESTED,
            job_id,
            {"jobId": job_id, "status": "running", "markets": symbol_universe_refresh_status(job_id)["markets"]},
        )
    except Exception as error:  # noqa: BLE001 - event transport cannot cancel the accepted refresh.
        report_runtime_error(operational_error_reporter(), "Symbol universe refresh", error, "refresh requested event")

    service = None
    while True:
        with SYMBOL_UNIVERSE_REFRESH_LOCK:
            if SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] != job_id:
                return
            batch = [
                market
                for market in SUPPORTED_MARKETS
                if market in SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"]
                and market not in SYMBOL_UNIVERSE_REFRESH_STATE["completedMarkets"]
            ]
            SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"].difference_update(batch)
            SYMBOL_UNIVERSE_REFRESH_STATE["status"] = "running"
            SYMBOL_UNIVERSE_REFRESH_STATE["startedAt"] = SYMBOL_UNIVERSE_REFRESH_STATE["startedAt"] or now()
            SYMBOL_UNIVERSE_REFRESH_STATE["stage"] = "connecting"
            SYMBOL_UNIVERSE_REFRESH_STATE["currentMarket"] = batch[0] if batch else ""
            SYMBOL_UNIVERSE_REFRESH_STATE["stageItemCount"] = 0
            SYMBOL_UNIVERSE_REFRESH_STATE["updatedAt"] = now()

        for market in batch:
            try:
                if service is None:
                    service = symbol_universe_service()

                def update_progress(progress: Dict[str, object]) -> None:
                    with SYMBOL_UNIVERSE_REFRESH_LOCK:
                        if SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] != job_id:
                            return
                        SYMBOL_UNIVERSE_REFRESH_STATE["stage"] = str(progress.get("stage") or "running")
                        SYMBOL_UNIVERSE_REFRESH_STATE["currentMarket"] = str(progress.get("market") or market)
                        SYMBOL_UNIVERSE_REFRESH_STATE["stageItemCount"] = int(progress.get("count") or 0)
                        SYMBOL_UNIVERSE_REFRESH_STATE["updatedAt"] = now()

                payload = service.refresh([market], on_progress=update_progress)
                rows = [dict(item) for item in (payload.get("results") or []) if isinstance(item, dict)]
                result = next((item for item in rows if str(item.get("market") or "").upper() == market), None)
                result = result or {"market": market, "status": "error", "count": 0, "error": "갱신 결과가 없습니다."}
                summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            except Exception as error:  # noqa: BLE001 - one market must not stop the remaining catalog refresh.
                result = {"market": market, "status": "error", "count": 0, "error": str(error)[:300]}
                summary = {}
                report_runtime_error(operational_error_reporter(), "Symbol universe refresh", error, market)
            with SYMBOL_UNIVERSE_REFRESH_LOCK:
                if SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] != job_id:
                    return
                _replace_symbol_universe_market_result(result)
                SYMBOL_UNIVERSE_REFRESH_STATE["completedMarkets"].add(market)
                SYMBOL_UNIVERSE_REFRESH_STATE["stage"] = "market_completed"
                SYMBOL_UNIVERSE_REFRESH_STATE["currentMarket"] = market
                SYMBOL_UNIVERSE_REFRESH_STATE["stageItemCount"] = int(result.get("count") or 0)
                SYMBOL_UNIVERSE_REFRESH_STATE["updatedAt"] = now()
                if summary:
                    SYMBOL_UNIVERSE_REFRESH_STATE["summary"] = dict(summary)

        with SYMBOL_UNIVERSE_REFRESH_LOCK:
            if SYMBOL_UNIVERSE_REFRESH_STATE["jobId"] != job_id:
                return
            remaining = set(SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"]).difference(
                SYMBOL_UNIVERSE_REFRESH_STATE["completedMarkets"]
            )
            if remaining:
                continue
            results = [dict(item) for item in SYMBOL_UNIVERSE_REFRESH_STATE["results"]]
            errors = [str(item.get("error") or item.get("status") or "error") for item in results if item.get("status") != "ok"]
            success_count = len([item for item in results if item.get("status") == "ok"])
            if errors and not success_count:
                final_status = "failed"
            elif errors:
                final_status = "partial"
            else:
                final_status = "completed"
            SYMBOL_UNIVERSE_REFRESH_STATE["status"] = final_status
            SYMBOL_UNIVERSE_REFRESH_STATE["running"] = False
            SYMBOL_UNIVERSE_REFRESH_STATE["finishedAt"] = now()
            SYMBOL_UNIVERSE_REFRESH_STATE["lastError"] = "; ".join(errors)[:500]
            SYMBOL_UNIVERSE_REFRESH_STATE["stage"] = final_status
            SYMBOL_UNIVERSE_REFRESH_STATE["currentMarket"] = ""
            SYMBOL_UNIVERSE_REFRESH_STATE["updatedAt"] = now()
            final_payload = _symbol_universe_refresh_status_locked()
            break

    event_name = SYMBOL_UNIVERSE_REFRESH_FAILED if final_payload["status"] == "failed" else SYMBOL_UNIVERSE_REFRESHED
    try:
        new_domain_event(event_name, job_id, final_payload)
    except Exception as error:  # noqa: BLE001 - status polling remains available when event delivery fails.
        report_runtime_error(operational_error_reporter(), "Symbol universe refresh", error, "refresh completion event")


def request_symbol_universe_refresh(payload: Dict[str, object]) -> Dict[str, object]:
    markets = requested_symbol_universe_markets(payload)
    should_start = False
    coalesced = False
    with SYMBOL_UNIVERSE_REFRESH_LOCK:
        if SYMBOL_UNIVERSE_REFRESH_STATE["running"]:
            coalesced = True
        else:
            SYMBOL_UNIVERSE_REFRESH_STATE.update({
                "jobId": new_id("symbol-refresh"),
                "running": True,
                "status": "queued",
                "markets": set(),
                "pendingMarkets": set(),
                "completedMarkets": set(),
                "results": [],
                "summary": {},
                "requestedAt": now(),
                "startedAt": "",
                "finishedAt": "",
                "lastError": "",
                "stage": "queued",
                "currentMarket": "",
                "stageItemCount": 0,
                "updatedAt": now(),
            })
            should_start = True
        SYMBOL_UNIVERSE_REFRESH_STATE["markets"].update(markets)
        SYMBOL_UNIVERSE_REFRESH_STATE["pendingMarkets"].update(markets)
        job_id = str(SYMBOL_UNIVERSE_REFRESH_STATE["jobId"])
        status = _symbol_universe_refresh_status_locked()
    if should_start:
        threading.Thread(
            target=run_symbol_universe_refresh_pipeline,
            args=(job_id,),
            name="symbol-universe-refresh",
            daemon=True,
        ).start()
    return {**status, "accepted": True, "coalesced": coalesced}


def refresh_symbol_universe_payload(payload: Dict[str, object]) -> Dict[str, object]:
    markets = requested_symbol_universe_markets(payload)
    result = symbol_universe_service().refresh(markets)
    new_domain_event(
        SYMBOL_UNIVERSE_REFRESHED,
        ",".join(markets or []) or "all",
        {"status": "completed", "summary": result.get("summary") or {}, "markets": markets or []},
    )
    return result


def account_watchlist_payload(account_id: str) -> Dict[str, object]:
    return account_watchlist_service().list_payload(account_id)


def add_account_watchlist_payload(account_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    return account_watchlist_service().add(account_id, (payload or {}).get("symbol"))


def replace_account_watchlist_payload(account_id: str, payload: Dict[str, object]) -> Dict[str, object]:
    symbols = (payload or {}).get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("symbols 배열이 필요합니다.")
    return account_watchlist_service().replace(account_id, symbols)


def remove_account_watchlist_payload(account_id: str, symbol: str) -> Dict[str, object]:
    return account_watchlist_service().remove(account_id, symbol)
