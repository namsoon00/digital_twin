import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

from ..application.account_service import AccountApplicationService
from ..domain.accounts import AccountConfig, split_symbols
from ..domain.monitoring import RealtimeMonitor
from ..domain.notification_templates import template_variables, text_context
from ..domain.portfolio import AlertEvent
from .admin_preview import write_admin_preview
from .event_bus import default_event_bus
from . import operational_store as stores
from .notifications import queued_notifier_for_account, send_events
from .ontology_graph_store import ontology_repository_from_settings
from .service_factory import (
    build_investment_calendar_candidate_service,
    build_investment_calendar_research_service,
    build_investment_calendar_runner,
    build_investment_calendar_service,
    build_investment_research_queue_runner,
    build_investment_strategy_proposal_service,
    build_kis_realtime_websocket_runner,
    build_market_data_collection_runner,
    build_model_review_runner,
    build_monitor_runner,
    build_news_collection_runner,
    build_notification_queue_runner,
    build_official_calendar_sync_service,
    build_ontology_lab_service,
    build_ontology_reasoning_runner,
    build_rule_change_candidate_service,
    build_symbol_universe_service,
    monitor_account_job_store_from_settings,
)
from .schedulers import (
    InvestmentCalendarScheduler,
    InvestmentResearchScheduler,
    KISRealtimeWebSocketScheduler,
    MIN_REALTIME_INTERVAL_SECONDS,
    MarketDataCollectionScheduler,
    ModelReviewScheduler,
    NewsCollectionScheduler,
    NotificationQueueScheduler,
    OntologyLabScheduler,
    OntologyReasoningScheduler,
    RealtimeScheduler,
)
from .settings import (
    SECRET_SETTING_KEYS,
    read_settings_store,
    runtime_settings,
    save_runtime_settings,
    utc_now,
    write_settings_store,
)
from .toss_snapshots import build_snapshot


def account_from_args(args) -> AccountConfig:
    settings = runtime_settings()
    return AccountConfig(
        account_id=args.id,
        label=args.label or args.id,
        provider=args.provider,
        base_url=args.base_url or settings.get("tossApiBaseUrl") or "https://openapi.tossinvest.com",
        client_id=args.client_id or os.environ.get("TOSS_CLIENT_ID", ""),
        client_secret=args.client_secret or os.environ.get("TOSS_CLIENT_SECRET", ""),
        account_seq=args.account_seq or "",
        watchlist_symbols=split_symbols(args.watchlist or settings.get("watchlistSymbols", "")),
        notify_provider=args.notify_provider or settings.get("notifyProvider", ""),
        telegram_bot_token=args.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "") or settings.get("telegramBotToken", ""),
        telegram_chat_id=args.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "") or settings.get("telegramChatId", ""),
        notify_link_url=args.notify_link_url or settings.get("notifyLinkUrl", ""),
        enabled=not args.disabled,
        message_delivery_level=args.message_delivery_level or settings.get("messageDeliveryLevel", "absoluteBeginner"),
    )


def preserve_existing_secrets(registry, payload, account: AccountConfig) -> AccountConfig:
    return AccountApplicationService(registry).preserve_existing_secrets(payload, account)


def collect_message_type_events(accounts: List[AccountConfig], allow_demo: bool = False):
    monitor = RealtimeMonitor(runtime_settings())
    events = []
    skipped = []
    for account in accounts:
        snapshot = build_snapshot(account)
        if snapshot.mode != "live" and not allow_demo:
            skipped.append(account.account_id + ": " + snapshot.status)
            continue
        events.extend(monitor.type_check_events_for_snapshot(snapshot))
    return events, skipped


def event_to_dict(event: AlertEvent):
    return {
        "accountId": event.account_id,
        "rule": event.rule,
        "severity": event.severity,
        "symbol": event.symbol,
        "title": event.title,
        "lines": event.lines,
        "criteria": event.criteria,
        "metadata": dict(getattr(event, "metadata", {}) or {}),
        "generatedAt": getattr(event, "generated_at", ""),
        "message": event.message(),
    }


def print_message_type_report(events: List[AlertEvent], skipped: List[str]) -> None:
    print("messageTypeEvents=" + str(len(events)) + " mode=inspect")
    for event in events:
        print("")
        print("--- " + event.rule + " ---")
        print(event.message())
    for item in skipped:
        print("Skipped " + item)


def monitor_progress_printer(stage: str, payload: Dict[str, object]) -> None:
    print(
        "monitorProgress="
        + str(stage or "")
        + " "
        + json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        flush=True,
    )


def build_handoff_message(summary: str, commit: str = "", validation: str = "", push: str = "", details: str = "") -> str:
    lines = [
        "타입: workHandoff",
        "요약: " + (summary or "작업 완료"),
        "검증: " + (validation or "미기재"),
    ]
    if commit:
        lines.append("커밋: " + commit)
    if push:
        lines.append("푸시: " + push)
    if details:
        lines.append("메모: " + details)
    lines.append("시각: " + utc_now())
    return "작업 완료\n" + "\n".join(["- " + line for line in lines])


def notification_targets(accounts: List[AccountConfig]) -> List[AccountConfig]:
    selected = []
    seen = set()
    for account in accounts:
        key = (
            str(account.notify_provider or "").lower(),
            account.telegram_bot_token or "",
            account.telegram_chat_id or "",
        )
        if not key[1] and not key[2]:
            key = ("account", account.account_id)
        if key in seen:
            continue
        seen.add(key)
        selected.append(account)
    return selected


def accounts_command(args) -> int:
    registry = stores.account_registry()
    service = AccountApplicationService(registry, registry.settings, event_publisher=default_event_bus())
    if args.accounts_action == "list":
        accounts = service.list_masked()
        if args.json:
            print(json.dumps({"accounts": accounts}, ensure_ascii=False))
        else:
            for account in accounts:
                print(account)
        return 0
    if args.accounts_action == "add":
        account = account_from_args(args)
        service.save(account)
        if args.json:
            print(json.dumps({"account": account.masked()}, ensure_ascii=False))
        else:
            print("Saved account: " + account.account_id)
        return 0
    if args.accounts_action == "save-json":
        payload = json.loads(sys.stdin.read() or "{}")
        account = service.save_payload(payload)
        print(json.dumps({"account": account.masked()}, ensure_ascii=False))
        return 0
    if args.accounts_action == "remove":
        removed = service.remove(args.id)
        if args.json:
            print(json.dumps({"removed": removed, "id": args.id}, ensure_ascii=False))
        else:
            print("Removed account: " + args.id if removed else "Account not found: " + args.id)
        return 0 if removed else 1
    return 1


def monitor_command(args) -> int:
    settings = runtime_settings()
    registry = stores.account_registry(settings)
    accounts = registry.load()
    if args.monitor_action == "status":
        store = stores.monitor_store(settings)
        print("Accounts: " + str(len(accounts)))
        for account in accounts:
            previous = store.previous.get(account.account_id)
            print(account.account_id + " · " + account.label + " · previous=" + ("yes" if previous else "no"))
        print("Sent cadence keys: " + str(len([key for key in store.sent.keys() if str(key).startswith("cadence:")])))
        job_store = monitor_account_job_store_from_settings(settings)
        if job_store:
            print("Account monitor jobs: " + json.dumps(job_store.summary(), ensure_ascii=False))
        else:
            print("Account monitor jobs: disabled")
        return 0

    runner = build_monitor_runner(
        accounts,
        progress_callback=monitor_progress_printer if args.monitor_action == "once" else None,
    )
    if args.monitor_action == "once":
        runner.run_once(dry_run=args.dry_run, force=args.force)
        return 0
    if args.monitor_action == "send-types":
        account_map = {account.account_id: account for account in accounts}
        events, skipped = collect_message_type_events(accounts, args.allow_demo)
        if not events:
            print("No message type check events.")
            for item in skipped:
                print("Skipped " + item)
            return 2
        result = send_events(events, dry_run=args.dry_run, accounts=account_map)
        print("messageTypeEvents=" + str(len(events)) + " delivered=" + str(result.delivered) + " provider=" + result.label + (" reason=" + result.reason if result.reason else ""))
        for item in skipped:
            print("Skipped " + item)
        return 0 if args.dry_run or result.delivered else 1
    if args.monitor_action == "message-types":
        account_map = {account.account_id: account for account in accounts}
        events, skipped = collect_message_type_events(accounts, args.allow_demo)
        if not events:
            if args.json:
                print(json.dumps({"messageTypeEvents": [], "skipped": skipped, "send": args.send}, ensure_ascii=False))
            else:
                print("No message type check events.")
                for item in skipped:
                    print("Skipped " + item)
            return 2
        if args.json:
            print(json.dumps({
                "messageTypeEvents": [event_to_dict(event) for event in events],
                "skipped": skipped,
                "send": args.send,
            }, ensure_ascii=False))
        elif args.send:
            result = send_events(events, dry_run=False, accounts=account_map)
            print("messageTypeEvents=" + str(len(events)) + " delivered=" + str(result.delivered) + " provider=" + result.label + (" reason=" + result.reason if result.reason else ""))
        else:
            print_message_type_report(events, skipped)
        if args.send and "result" in locals() and not result.delivered:
            return 1
        return 0
    if args.monitor_action == "watch":
        interval = int(os.environ.get("PYTHON_REALTIME_INTERVAL_SECONDS") or os.environ.get("REALTIME_NOTIFY_INTERVAL_SECONDS") or MIN_REALTIME_INTERVAL_SECONDS)
        RealtimeScheduler(runner, interval).run_forever()
        return 0
    return 1


def model_review_command(args) -> int:
    store = stores.model_review_job_store()
    if args.model_review_action == "status":
        summary = store.summary()
        print(json.dumps({"jobs": summary}, ensure_ascii=False))
        return 0
    settings = runtime_settings()
    limit = int(args.limit or settings.get("modelReviewBatchSize") or 1)
    runner = build_model_review_runner(dry_run=args.dry_run)
    if args.model_review_action == "once":
        runner.run_once(limit=limit)
        return 0
    if args.model_review_action == "watch":
        interval = int(os.environ.get("MODEL_REVIEW_INTERVAL_SECONDS") or settings.get("modelReviewIntervalSeconds") or 300)
        ModelReviewScheduler(runner, interval).run_forever(limit=limit)
        return 0
    return 1


def notifications_command(args) -> int:
    store = stores.notification_job_store()
    if args.notifications_action == "status":
        print(json.dumps({"jobs": store.summary()}, ensure_ascii=False))
        return 0
    settings = runtime_settings()
    limit = int(args.limit or settings.get("notificationQueueBatchSize") or 10)
    runner = build_notification_queue_runner(dry_run=args.dry_run)
    if args.notifications_action == "once":
        processed = runner.run_once(limit=limit)
        print("notificationJobsProcessed=" + str(processed))
        return 0
    if args.notifications_action == "watch":
        interval = int(
            os.environ.get("NOTIFICATION_QUEUE_INTERVAL_SECONDS")
            or settings.get("notificationQueueIntervalSeconds")
            or 30
        )
        NotificationQueueScheduler(runner, interval).run_forever(limit=limit)
        return 0
    return 1


def ontology_reasoning_command(args) -> int:
    settings = runtime_settings()
    limit = int(args.limit or settings.get("ontologyReasoningBatchSize") or 20) if hasattr(args, "limit") else int(settings.get("ontologyReasoningBatchSize") or 20)
    runner = build_ontology_reasoning_runner(settings)
    if args.ontology_reasoning_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.ontology_reasoning_action == "once":
        print(json.dumps(runner.run_once(limit=limit, force=True), ensure_ascii=False))
        return 0
    if args.ontology_reasoning_action == "watch":
        interval = int(
            os.environ.get("ONTOLOGY_REASONING_INTERVAL_SECONDS")
            or settings.get("ontologyReasoningIntervalSeconds")
            or 10
        )
        OntologyReasoningScheduler(runner, interval).run_forever(limit=limit)
        return 0
    return 1


def ontology_command(args) -> int:
    settings = runtime_settings()
    repository = ontology_repository_from_settings(settings)
    if args.ontology_action == "seed":
        payload = {
            "replaceRuleBox": bool(args.replace_rulebox),
            "clearInference": bool(args.clear_inference),
        }
        result = repository.seed_ontology(payload)
        print(json.dumps(result, ensure_ascii=False))
        # A current static ontology is a successful seed no-op. Returning a
        # non-zero code here prevents the service manager from starting all
        # dependent collection and reasoning workers after a normal restart.
        return 0 if result.get("status") in {"ok", "unchanged", "disabled"} else 1
    return 1


def ontology_lab_command(args) -> int:
    settings = runtime_settings()
    service = build_ontology_lab_service(settings)
    if args.ontology_lab_action == "list":
        print(json.dumps(service.list(), ensure_ascii=False))
        return 0
    if args.ontology_lab_action == "status":
        print(json.dumps(service.status(), ensure_ascii=False))
        return 0
    if args.ontology_lab_action == "create":
        payload = read_json_payload(args.payload_file)
        if args.title:
            payload["title"] = args.title
        if args.hypothesis:
            payload["hypothesis"] = args.hypothesis
        if args.symbols:
            payload["symbols"] = [item.strip() for item in args.symbols.split(",") if item.strip()]
        result = service.create(payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.ontology_lab_action == "suggest":
        symbols = split_symbols(args.symbols or "")
        candidate_result = build_rule_change_candidate_service(settings).propose(
            symbols=symbols,
            trigger=args.trigger or "ontology-lab-suggest",
        )
        result = service.suggest_from_rule_candidates(candidate_result, {
            "symbols": symbols,
            "activate": bool(args.activate),
            "run": bool(args.run),
            "limit": args.limit,
        })
        result["candidateResult"] = {
            "status": candidate_result.get("status"),
            "candidateCount": candidate_result.get("candidateCount"),
            "savedCount": candidate_result.get("savedCount"),
            "contextSummary": candidate_result.get("contextSummary") or {},
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") not in {"disabled", "error"} else 1
    if args.ontology_lab_action == "activate":
        result = service.activate(args.id)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") != "not-found" else 1
    if args.ontology_lab_action == "pause":
        result = service.pause(args.id)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") != "not-found" else 1
    if args.ontology_lab_action == "run":
        result = service.run(args.id, read_json_payload(args.payload_file) if args.payload_file else {})
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") != "not-found" else 1
    if args.ontology_lab_action == "apply":
        result = service.apply_recommendations(
            args.id,
            {
                "runRulebox": not bool(args.skip_run_rulebox),
                "reviewApproved": bool(args.approve_needs_review),
                "reviewedBy": args.reviewed_by,
                "reviewReason": args.review_reason,
                "recommendationIds": [item.strip() for item in str(args.recommendation_ids or "").split(",") if item.strip()],
            },
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") not in {"not-found", "no-result", "not-ready", "disabled", "pending", "error"} else 1
    if args.ontology_lab_action == "auto-suggest":
        result = service.auto_suggest(
            symbols=split_symbols(args.symbols or ""),
            trigger=args.trigger or "ontology-lab-cli-auto-suggest",
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") not in {"disabled", "error"} else 1
    if args.ontology_lab_action == "once":
        result = service.run_once(limit=int(args.limit or settings.get("ontologyLabBatchSize") or 0), force=args.force)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.ontology_lab_action == "watch":
        interval = int(os.environ.get("ONTOLOGY_LAB_INTERVAL_SECONDS") or settings.get("ontologyLabIntervalSeconds") or 300)
        OntologyLabScheduler(service, interval).run_forever(
            limit=int(args.limit or settings.get("ontologyLabBatchSize") or 0),
            force=args.force,
        )
        return 0
    if args.ontology_lab_action == "report":
        result = service.report(args.id)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") != "not-found" else 1
    return 1


def investment_strategy_proposals_command(args) -> int:
    service = build_investment_strategy_proposal_service(runtime_settings())
    action = args.strategy_proposals_action
    if action == "list":
        print(json.dumps(service.list(), ensure_ascii=False))
        return 0
    if action == "status":
        print(json.dumps(service.status(), ensure_ascii=False))
        return 0
    if action == "get":
        result = service.get(args.id)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") != "not-found" else 1
    if action == "validate":
        result = service.validate_materialization(args.id, read_json_payload(args.payload_file) if args.payload_file else {})
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") not in {"not-found", "error", "invalid-rulebox"} else 1
    if action == "approve":
        result = service.approve(args.id, {
            "reviewedBy": args.reviewed_by,
            "reviewReason": args.review_reason,
            "forceApproved": bool(args.force),
        })
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "approved" else 1
    if action == "performance":
        result = service.performance(args.id)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") != "not-found" else 1
    if action == "record-performance":
        payload = read_json_payload(args.payload_file) if args.payload_file else {}
        for key in [
            "observedAt",
            "portfolioReturnPct",
            "benchmarkReturnPct",
            "maxDrawdownPct",
            "signalCount",
            "falsePositiveCount",
            "notes",
            "source",
        ]:
            value = getattr(args, snake_arg(key), "")
            if value not in (None, ""):
                payload[key] = value
        result = service.record_performance_sample(args.id, payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "recorded" else 1
    return 1


def read_json_payload(path: str = "") -> Dict[str, object]:
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.loads(handle.read() or "{}")
    elif not sys.stdin.isatty():
        payload = json.loads(sys.stdin.read() or "{}")
    else:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def snake_arg(name: str) -> str:
    result = []
    for char in str(name or ""):
        if char.isupper():
            result.append("_")
            result.append(char.lower())
        else:
            result.append(char)
    return "".join(result).lstrip("_")


MASKED_RUNTIME_SETTING_KEYS = set(SECRET_SETTING_KEYS) | {
    "tossAccountSeq",
    "telegramChatId",
    "operationsTelegramChatId",
}


def public_settings_payload(settings):
    public = {}
    configured = {}
    for key, value in settings.items():
        if key in MASKED_RUNTIME_SETTING_KEYS:
            public[key] = ""
            configured[key] = bool(value)
        else:
            public[key] = value
    return {"settings": public, "configured": configured}


def settings_command(args) -> int:
    if args.settings_action == "raw-json":
        print(json.dumps({"settings": read_settings_store()}, ensure_ascii=False))
        return 0
    if args.settings_action == "save-json":
        payload = json.loads(sys.stdin.read() or "{}")
        settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
        saved = save_runtime_settings(settings if isinstance(settings, dict) else {})
        print(json.dumps(public_settings_payload(saved), ensure_ascii=False))
        return 0
    if args.settings_action == "replace-json":
        payload = json.loads(sys.stdin.read() or "{}")
        settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
        write_settings_store(settings if isinstance(settings, dict) else {})
        print(json.dumps({"ok": True}, ensure_ascii=False))
        return 0
    return 1


def app_store_command(args) -> int:
    store = stores.app_store()
    if args.store_action == "raw-json":
        print(json.dumps({"store": store.load()}, ensure_ascii=False))
        return 0
    if args.store_action == "replace-json":
        payload = json.loads(sys.stdin.read() or "{}")
        next_store = payload.get("store") if isinstance(payload.get("store"), dict) else payload
        store.replace(next_store if isinstance(next_store, dict) else {})
        print(json.dumps({"store": store.load()}, ensure_ascii=False))
        return 0
    return 1


def templates_command(args) -> int:
    store = stores.notification_template_store()
    if args.templates_action == "list":
        payload = {
            "templates": [item.to_dict() for item in store.list()],
            "variables": template_variables(),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.templates_action == "save":
        payload = json.loads(sys.stdin.read() or "{}")
        message_type = str(payload.get("messageType") or payload.get("message_type") or "").strip()
        template = str(payload.get("template") or "")
        description = str(payload.get("description") or "")
        enabled = payload.get("enabled")
        saved = store.upsert(message_type, template, description, enabled is not False)
        print(json.dumps({"template": saved.to_dict()}, ensure_ascii=False))
        return 0
    if args.templates_action == "reset":
        saved = store.reset(args.message_type)
        print(json.dumps({"template": saved.to_dict()}, ensure_ascii=False))
        return 0
    if args.templates_action == "preview":
        context = text_context(args.body, args.message_type)
        print(store.render(args.message_type, context))
        return 0
    return 1


def symbols_command(args) -> int:
    service = build_symbol_universe_service()
    if args.symbols_action == "status":
        print(json.dumps({"summary": service.summary()}, ensure_ascii=False))
        return 0
    if args.symbols_action == "search":
        print(json.dumps(service.search(query=args.query, market=args.market, limit=int(args.limit or 80)), ensure_ascii=False))
        return 0
    if args.symbols_action == "refresh":
        markets = [item.strip().upper() for item in str(args.markets or "").split(",") if item.strip()]
        print(json.dumps(service.refresh(markets or None), ensure_ascii=False))
        return 0
    return 1


def market_data_command(args) -> int:
    settings = runtime_settings()
    runner = build_market_data_collection_runner(settings)
    if args.market_data_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.market_data_action == "once":
        print(json.dumps(runner.run_once(force=args.force), ensure_ascii=False))
        return 0
    if args.market_data_action == "watch":
        interval = int(os.environ.get("MARKET_DATA_COLLECTION_INTERVAL_SECONDS") or settings.get("marketDataCollectionIntervalSeconds") or 180)
        MarketDataCollectionScheduler(runner, interval).run_forever()
        return 0
    return 1


def kis_realtime_command(args) -> int:
    settings = runtime_settings()
    runner = build_kis_realtime_websocket_runner(settings)
    if args.kis_realtime_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.kis_realtime_action == "once":
        print(json.dumps(runner.run_once(duration_seconds=int(args.seconds or 0), force=args.force), ensure_ascii=False))
        return 0
    if args.kis_realtime_action == "watch":
        KISRealtimeWebSocketScheduler(runner, runner.reconnect_delay_seconds()).run_forever()
        return 0
    return 1


def news_command(args) -> int:
    settings = runtime_settings()
    runner = build_news_collection_runner(settings)
    if args.news_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.news_action == "once":
        print(json.dumps(runner.run_once(force=args.force), ensure_ascii=False))
        return 0
    if args.news_action == "watch":
        interval = int(os.environ.get("NEWS_COLLECTION_INTERVAL_SECONDS") or settings.get("newsCollectionIntervalSeconds") or 60)
        NewsCollectionScheduler(runner, interval).run_forever()
        return 0
    return 1


def investment_research_command(args) -> int:
    settings = runtime_settings()
    runner = build_investment_research_queue_runner(settings)
    if args.investment_research_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.investment_research_action == "once":
        print(json.dumps(runner.run_once(limit=int(args.limit or 3)), ensure_ascii=False))
        return 0
    if args.investment_research_action == "watch":
        InvestmentResearchScheduler(
            runner,
            int(settings.get("investmentBrainResearchWorkerIntervalSeconds") or 15),
            int(settings.get("investmentBrainResearchWorkerBatchSize") or 3),
        ).run_forever()
        return 0
    return 1


def investment_calendar_command(args) -> int:
    settings = runtime_settings()
    service = build_investment_calendar_service(settings)
    if args.investment_calendar_action == "status":
        print(json.dumps(service.status(), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "list":
        print(json.dumps(service.list_events({"limit": args.limit}), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "save-json":
        print(json.dumps(service.save_event(read_json_payload()), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "delete":
        print(json.dumps(service.delete_event(args.event_id), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "candidates":
        candidate_service = build_investment_calendar_candidate_service(settings)
        print(json.dumps(candidate_service.list_candidates({
            "status": args.status,
            "limit": args.limit,
            "page": args.page,
            "pageSize": args.page_size,
        }), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "research-candidates":
        research_service = build_investment_calendar_research_service(settings)
        print(json.dumps(research_service.recommend({
            "symbol": args.symbol,
            "kind": args.kind,
            "limit": args.limit,
            "runCollection": args.run_collection,
            "force": args.force,
        }), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "approve-candidate":
        candidate_service = build_investment_calendar_candidate_service(settings)
        print(json.dumps(candidate_service.approve_candidate(args.candidate_id, {
            "startsAt": args.starts_at,
            "reviewNote": args.note,
        }), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "reject-candidate":
        candidate_service = build_investment_calendar_candidate_service(settings)
        print(json.dumps(candidate_service.reject_candidate(args.candidate_id, {"reviewNote": args.note}), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "sync-official":
        print(json.dumps(build_official_calendar_sync_service(settings).run_once(force=True), ensure_ascii=False))
        return 0
    runner = build_investment_calendar_runner(settings)
    if args.investment_calendar_action == "once":
        print(json.dumps(runner.run_once(), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "watch":
        interval = int(os.environ.get("INVESTMENT_CALENDAR_INTERVAL_SECONDS") or settings.get("investmentCalendarIntervalSeconds") or 60)
        InvestmentCalendarScheduler(runner, interval).run_forever()
        return 0
    return 1


def handoff_command(args) -> int:
    if args.handoff_action != "notify":
        return 1
    registry = stores.account_registry()
    accounts = notification_targets(registry.load())
    message = build_handoff_message(args.summary, args.commit, args.validation, args.push, args.details)
    if args.dry_run:
        print(message)
        return 0
    results = []
    targets = accounts or [None]
    for account in targets:
        result = queued_notifier_for_account(account, message_type="workHandoff").send(message)
        results.append(result)
    queued = sum(result.queued for result in results if result.delivered)
    failed = len([result for result in results if not result.delivered])
    reason = next((result.reason for result in results if not result.delivered and result.reason), "")
    print(
        "handoffNotifications="
        + str(len(results))
        + " queued="
        + str(queued)
        + " failed="
        + str(failed)
        + (" reason=" + reason if reason else "")
    )
    return 0 if failed == 0 else 1


def admin_preview_command(args) -> int:
    payload = write_admin_preview(Path(args.output))
    print(json.dumps({"output": args.output, "buildId": payload.get("buildId")}, ensure_ascii=False))
    return 0


def web_command(args) -> int:
    from .web_server import serve

    serve(args.host, int(args.port))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Orbit Alpha Python service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    accounts = subparsers.add_parser("accounts", help="Manage service accounts")
    account_actions = accounts.add_subparsers(dest="accounts_action", required=True)
    list_accounts = account_actions.add_parser("list")
    list_accounts.add_argument("--json", action="store_true")
    add = account_actions.add_parser("add")
    add.add_argument("--id", required=True)
    add.add_argument("--label", default="")
    add.add_argument("--provider", default="toss")
    add.add_argument("--base-url", default="")
    add.add_argument("--client-id", default="")
    add.add_argument("--client-secret", default="")
    add.add_argument("--account-seq", default="")
    add.add_argument("--watchlist", default="")
    add.add_argument("--notify-provider", default="")
    add.add_argument("--telegram-bot-token", default="")
    add.add_argument("--telegram-chat-id", default="")
    add.add_argument("--notify-link-url", default="")
    add.add_argument("--message-delivery-level", default="absoluteBeginner", choices=["absoluteBeginner", "beginner", "intermediate", "advanced"])
    add.add_argument("--disabled", action="store_true")
    add.add_argument("--json", action="store_true")
    account_actions.add_parser("save-json")
    remove = account_actions.add_parser("remove")
    remove.add_argument("--id", required=True)
    remove.add_argument("--json", action="store_true")
    accounts.set_defaults(func=accounts_command)

    monitor = subparsers.add_parser("monitor", help="Run realtime monitoring")
    monitor_actions = monitor.add_subparsers(dest="monitor_action", required=True)
    once = monitor_actions.add_parser("once")
    once.add_argument("--dry-run", action="store_true")
    once.add_argument("--force", action="store_true")
    send_types = monitor_actions.add_parser("send-types")
    send_types.add_argument("--dry-run", action="store_true")
    send_types.add_argument("--allow-demo", action="store_true")
    message_types = monitor_actions.add_parser("message-types")
    message_types.add_argument("--send", action="store_true")
    message_types.add_argument("--json", action="store_true")
    message_types.add_argument("--allow-demo", action="store_true")
    monitor_actions.add_parser("watch")
    monitor_actions.add_parser("status")
    monitor.set_defaults(func=monitor_command)

    model_review = subparsers.add_parser("model-review", help="Run async model review worker")
    model_review_actions = model_review.add_subparsers(dest="model_review_action", required=True)
    review_once = model_review_actions.add_parser("once")
    review_once.add_argument("--dry-run", action="store_true")
    review_once.add_argument("--limit", default="")
    review_watch = model_review_actions.add_parser("watch")
    review_watch.add_argument("--dry-run", action="store_true")
    review_watch.add_argument("--limit", default="")
    model_review_actions.add_parser("status")
    model_review.set_defaults(func=model_review_command)

    notifications = subparsers.add_parser("notifications", help="Run queued notification delivery")
    notification_actions = notifications.add_subparsers(dest="notifications_action", required=True)
    notify_once = notification_actions.add_parser("once")
    notify_once.add_argument("--dry-run", action="store_true")
    notify_once.add_argument("--limit", default="")
    notify_watch = notification_actions.add_parser("watch")
    notify_watch.add_argument("--dry-run", action="store_true")
    notify_watch.add_argument("--limit", default="")
    notification_actions.add_parser("status")
    notifications.set_defaults(func=notifications_command)

    ontology_reasoning = subparsers.add_parser("ontology-reasoning", help="Run data-update driven ontology reasoning")
    ontology_reasoning_actions = ontology_reasoning.add_subparsers(dest="ontology_reasoning_action", required=True)
    ontology_once = ontology_reasoning_actions.add_parser("once")
    ontology_once.add_argument("--limit", default="")
    ontology_watch = ontology_reasoning_actions.add_parser("watch")
    ontology_watch.add_argument("--limit", default="")
    ontology_reasoning_actions.add_parser("status")
    ontology_reasoning.set_defaults(func=ontology_reasoning_command)

    ontology = subparsers.add_parser("ontology", help="Manage ontology graph projection")
    ontology_actions = ontology.add_subparsers(dest="ontology_action", required=True)
    ontology_seed = ontology_actions.add_parser("seed")
    ontology_seed.add_argument("--replace-rulebox", action="store_true")
    ontology_seed.add_argument("--keep-inference", dest="clear_inference", action="store_false", default=True)
    ontology.set_defaults(func=ontology_command)

    ontology_lab = subparsers.add_parser("ontology-lab", help="Run local ontology experiments")
    ontology_lab_actions = ontology_lab.add_subparsers(dest="ontology_lab_action", required=True)
    ontology_lab_actions.add_parser("list")
    ontology_lab_actions.add_parser("status")
    lab_create = ontology_lab_actions.add_parser("create")
    lab_create.add_argument("--payload-file", default="")
    lab_create.add_argument("--title", default="")
    lab_create.add_argument("--hypothesis", default="")
    lab_create.add_argument("--symbols", default="")
    lab_suggest = ontology_lab_actions.add_parser("suggest")
    lab_suggest.add_argument("--symbols", default="")
    lab_suggest.add_argument("--trigger", default="ontology-lab-suggest")
    lab_suggest.add_argument("--limit", default="")
    lab_suggest.add_argument("--activate", action="store_true")
    lab_suggest.add_argument("--run", action="store_true")
    lab_activate = ontology_lab_actions.add_parser("activate")
    lab_activate.add_argument("--id", required=True)
    lab_pause = ontology_lab_actions.add_parser("pause")
    lab_pause.add_argument("--id", required=True)
    lab_run = ontology_lab_actions.add_parser("run")
    lab_run.add_argument("--id", required=True)
    lab_run.add_argument("--payload-file", default="")
    lab_apply = ontology_lab_actions.add_parser("apply")
    lab_apply.add_argument("--id", required=True)
    lab_apply.add_argument("--skip-run-rulebox", action="store_true")
    lab_apply.add_argument("--approve-needs-review", action="store_true")
    lab_apply.add_argument("--reviewed-by", default="cli-user")
    lab_apply.add_argument("--review-reason", default="")
    lab_apply.add_argument("--recommendation-ids", default="")
    lab_auto_suggest = ontology_lab_actions.add_parser("auto-suggest")
    lab_auto_suggest.add_argument("--symbols", default="")
    lab_auto_suggest.add_argument("--trigger", default="ontology-lab-cli-auto-suggest")
    lab_once = ontology_lab_actions.add_parser("once")
    lab_once.add_argument("--limit", default="")
    lab_once.add_argument("--force", action="store_true")
    lab_watch = ontology_lab_actions.add_parser("watch")
    lab_watch.add_argument("--limit", default="")
    lab_watch.add_argument("--force", action="store_true")
    lab_report = ontology_lab_actions.add_parser("report")
    lab_report.add_argument("--id", required=True)
    ontology_lab.set_defaults(func=ontology_lab_command)

    strategy_proposals = subparsers.add_parser("strategy-proposals", help="Review investment strategy proposals")
    strategy_proposals_actions = strategy_proposals.add_subparsers(dest="strategy_proposals_action", required=True)
    strategy_proposals_actions.add_parser("list")
    strategy_proposals_actions.add_parser("status")
    strategy_get = strategy_proposals_actions.add_parser("get")
    strategy_get.add_argument("--id", required=True)
    strategy_validate = strategy_proposals_actions.add_parser("validate")
    strategy_validate.add_argument("--id", required=True)
    strategy_validate.add_argument("--payload-file", default="")
    strategy_approve = strategy_proposals_actions.add_parser("approve")
    strategy_approve.add_argument("--id", required=True)
    strategy_approve.add_argument("--reviewed-by", default="cli-user")
    strategy_approve.add_argument("--review-reason", default="")
    strategy_approve.add_argument("--force", action="store_true")
    strategy_performance = strategy_proposals_actions.add_parser("performance")
    strategy_performance.add_argument("--id", required=True)
    strategy_record_performance = strategy_proposals_actions.add_parser("record-performance")
    strategy_record_performance.add_argument("--id", required=True)
    strategy_record_performance.add_argument("--payload-file", default="")
    strategy_record_performance.add_argument("--observed-at", default="")
    strategy_record_performance.add_argument("--portfolio-return-pct", default="")
    strategy_record_performance.add_argument("--benchmark-return-pct", default="")
    strategy_record_performance.add_argument("--max-drawdown-pct", default="")
    strategy_record_performance.add_argument("--signal-count", default="")
    strategy_record_performance.add_argument("--false-positive-count", default="")
    strategy_record_performance.add_argument("--notes", default="")
    strategy_record_performance.add_argument("--source", default="")
    strategy_proposals.set_defaults(func=investment_strategy_proposals_command)

    settings = subparsers.add_parser("settings", help="Manage runtime settings")
    settings_actions = settings.add_subparsers(dest="settings_action", required=True)
    settings_actions.add_parser("raw-json")
    settings_actions.add_parser("save-json")
    settings_actions.add_parser("replace-json")
    settings.set_defaults(func=settings_command)

    app_store = subparsers.add_parser("store", help="Manage app store data")
    app_store_actions = app_store.add_subparsers(dest="store_action", required=True)
    app_store_actions.add_parser("raw-json")
    app_store_actions.add_parser("replace-json")
    app_store.set_defaults(func=app_store_command)

    templates = subparsers.add_parser("templates", help="Manage notification message templates")
    templates_actions = templates.add_subparsers(dest="templates_action", required=True)
    templates_actions.add_parser("list")
    templates_actions.add_parser("save")
    reset_template = templates_actions.add_parser("reset")
    reset_template.add_argument("--message-type", required=True)
    preview_template = templates_actions.add_parser("preview")
    preview_template.add_argument("--message-type", required=True)
    preview_template.add_argument("--body", default="샘플 알림")
    templates.set_defaults(func=templates_command)

    symbols = subparsers.add_parser("symbols", help="Manage listed symbol universe")
    symbol_actions = symbols.add_subparsers(dest="symbols_action", required=True)
    symbol_actions.add_parser("status")
    symbol_search = symbol_actions.add_parser("search")
    symbol_search.add_argument("--query", default="")
    symbol_search.add_argument("--market", default="")
    symbol_search.add_argument("--limit", default="80")
    symbol_refresh = symbol_actions.add_parser("refresh")
    symbol_refresh.add_argument("--markets", default="")
    symbols.set_defaults(func=symbols_command)

    market_data = subparsers.add_parser("market-data", help="Collect market data for recommendation features")
    market_data_actions = market_data.add_subparsers(dest="market_data_action", required=True)
    market_once = market_data_actions.add_parser("once")
    market_once.add_argument("--force", action="store_true")
    market_data_actions.add_parser("watch")
    market_data_actions.add_parser("status")
    market_data.set_defaults(func=market_data_command)

    kis_realtime = subparsers.add_parser("kis-realtime", help="Collect KIS realtime price and orderbook over WebSocket")
    kis_realtime_actions = kis_realtime.add_subparsers(dest="kis_realtime_action", required=True)
    kis_once = kis_realtime_actions.add_parser("once")
    kis_once.add_argument("--seconds", default="")
    kis_once.add_argument("--force", action="store_true")
    kis_realtime_actions.add_parser("watch")
    kis_realtime_actions.add_parser("status")
    kis_realtime.set_defaults(func=kis_realtime_command)

    news = subparsers.add_parser("news", help="Collect domestic and overseas news evidence")
    news_actions = news.add_subparsers(dest="news_action", required=True)
    news_once = news_actions.add_parser("once")
    news_once.add_argument("--force", action="store_true")
    news_actions.add_parser("watch")
    news_actions.add_parser("status")
    news.set_defaults(func=news_command)

    investment_research = subparsers.add_parser("investment-research", help="Process queued hypothesis research runs")
    investment_research_actions = investment_research.add_subparsers(dest="investment_research_action", required=True)
    investment_research_once = investment_research_actions.add_parser("once")
    investment_research_once.add_argument("--limit", default="3")
    investment_research_actions.add_parser("watch")
    investment_research_actions.add_parser("status")
    investment_research.set_defaults(func=investment_research_command)

    investment_calendar = subparsers.add_parser("investment-calendar", help="Manage investment calendar events and reminders")
    investment_calendar_actions = investment_calendar.add_subparsers(dest="investment_calendar_action", required=True)
    investment_calendar_actions.add_parser("status")
    calendar_list = investment_calendar_actions.add_parser("list")
    calendar_list.add_argument("--limit", default="80")
    investment_calendar_actions.add_parser("save-json")
    calendar_delete = investment_calendar_actions.add_parser("delete")
    calendar_delete.add_argument("--event-id", required=True)
    calendar_candidates = investment_calendar_actions.add_parser("candidates")
    calendar_candidates.add_argument("--status", default="pending")
    calendar_candidates.add_argument("--limit", default="100")
    calendar_candidates.add_argument("--page", default="0")
    calendar_candidates.add_argument("--page-size", default="20")
    calendar_research = investment_calendar_actions.add_parser("research-candidates")
    calendar_research.add_argument("--symbol", default="")
    calendar_research.add_argument("--kind", default="")
    calendar_research.add_argument("--limit", default="120")
    calendar_research.add_argument("--run-collection", action=argparse.BooleanOptionalAction, default=True)
    calendar_research.add_argument("--force", action="store_true")
    calendar_candidate_approve = investment_calendar_actions.add_parser("approve-candidate")
    calendar_candidate_approve.add_argument("--candidate-id", required=True)
    calendar_candidate_approve.add_argument("--starts-at", default="")
    calendar_candidate_approve.add_argument("--note", default="")
    calendar_candidate_reject = investment_calendar_actions.add_parser("reject-candidate")
    calendar_candidate_reject.add_argument("--candidate-id", required=True)
    calendar_candidate_reject.add_argument("--note", default="")
    investment_calendar_actions.add_parser("once")
    investment_calendar_actions.add_parser("sync-official")
    investment_calendar_actions.add_parser("watch")
    investment_calendar.set_defaults(func=investment_calendar_command)

    handoff = subparsers.add_parser("handoff", help="Send development handoff notifications")
    handoff_actions = handoff.add_subparsers(dest="handoff_action", required=True)
    notify = handoff_actions.add_parser("notify")
    notify.add_argument("--summary", required=True)
    notify.add_argument("--commit", default="")
    notify.add_argument("--validation", default="")
    notify.add_argument("--push", default="")
    notify.add_argument("--details", default="")
    notify.add_argument("--dry-run", action="store_true")
    handoff.set_defaults(func=handoff_command)

    admin_preview = subparsers.add_parser("admin-preview", help="Generate GitHub Pages admin preview")
    admin_preview.add_argument("--output", default="public/admin")
    admin_preview.set_defaults(func=admin_preview_command)

    web = subparsers.add_parser("web", help="Run local Python web server")
    web.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    web.add_argument("--port", default=os.environ.get("PORT", "3000"))
    web.set_defaults(func=web_command)
    return parser


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)
