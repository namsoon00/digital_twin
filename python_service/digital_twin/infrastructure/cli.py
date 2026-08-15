import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Mapping

from ..application.account_service import AccountApplicationService
from ..application.mysql_minimal_retention_service import MySQLMinimalRetentionService
from ..domain.accounts import AccountConfig, split_symbols
from ..domain.mysql_minimal_retention import mysql_minimal_retention_policy
from ..domain.monitoring import RealtimeMonitor
from ..domain.notification_templates import template_variables, text_context
from ..domain.portfolio import AlertEvent
from ..news_intelligence.application.revalidate_articles import RevalidateNewsIntelligenceService
from .admin_preview import write_admin_preview
from .event_bus import default_event_bus
from . import operational_store as stores
from .mysql_operational_connection import (
    MySQLOperationalConnection,
    mysql_deadlock_retry_count,
    mysql_deadlock_retry_delay_milliseconds,
    mysql_is_connection_lost,
    mysql_is_deadlock,
    mysql_operation_timeout_seconds,
)
from .mysql_retention import (
    apply_mysql_operational_history_retention,
    drop_ephemeral_mysql_databases,
    mysql_operational_compaction_tables,
    mysql_operational_space_reclaim_candidates,
    operational_history_retention_check_interval_seconds,
    optimize_mysql_operational_tables,
    safe_mysql_operational_compaction_tables,
)
from .mysql_minimal_retention import MySQLMinimalRetentionRepository
from .operational_storage_guard import (
    accelerated_mysql_cleanup_settings,
    operational_storage_inventory,
)
from .notifications import queued_notifier_for_account, send_events
from .ontology_graph_store import ontology_repository_from_settings
from .service_factory import (
    build_ai_inference_queue_runner,
    build_investment_calendar_candidate_service,
    build_investment_calendar_discovery_service,
    build_investment_calendar_research_service,
    build_investment_calendar_runner,
    build_investment_calendar_service,
    build_investment_research_queue_runner,
    build_investment_strategy_proposal_service,
    build_kis_realtime_websocket_runner,
    build_market_data_collection_runner,
    build_model_review_runner,
    build_monitor_runner,
    build_news_analysis_enrichment_runner,
    build_news_collection_runner,
    build_notification_queue_runner,
    build_official_calendar_sync_service,
    observe_operational_storage_capacity,
    build_ontology_lab_service,
    build_ontology_inference_detail_runner,
    build_ontology_maintenance_runner,
    build_ontology_rulebox_prewarm_runner,
    build_ontology_reasoning_proof_service,
    build_ontology_reasoning_runner,
    build_ontology_reasoning_queue_probe,
    build_ontology_portfolio_rebuild_runner,
    build_ontology_world_projection_runner,
    build_rule_change_candidate_service,
    build_symbol_universe_service,
    monitor_account_job_store_from_settings,
)
from .schedulers import (
    AIInferenceQueueScheduler,
    InvestmentCalendarScheduler,
    InvestmentResearchScheduler,
    KISRealtimeWebSocketScheduler,
    MIN_REALTIME_INTERVAL_SECONDS,
    MarketDataCollectionScheduler,
    ModelReviewScheduler,
    NewsCollectionScheduler,
    NewsAnalysisEnrichmentScheduler,
    NotificationQueueScheduler,
    IsolatedOntologyReasoningCycle,
    PersistentIsolatedOntologyReasoningCycle,
    OntologyLabScheduler,
    OntologyInferenceDetailScheduler,
    OntologyMaintenanceScheduler,
    OntologyRuleboxPrewarmScheduler,
    OntologyReasoningScheduler,
    OperationalHistoryRetentionScheduler,
    OntologyWorldProjectionScheduler,
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
        inline_projection = str(
            settings.get("ontologyMonitorInlineProjectionEnabled") or "0"
        ).strip().lower() not in {"", "0", "false", "no", "off", "disabled"}
        # Once TypeDB generation is fully delegated to the durable reasoning
        # worker, the monitor only collects and commits source facts.  It can
        # therefore refresh the verified-snapshot boundary more often without
        # competing for TypeDB's single writer transaction.
        minimum_interval = MIN_REALTIME_INTERVAL_SECONDS if inline_projection else 30
        interval = int(
            os.environ.get("PYTHON_REALTIME_INTERVAL_SECONDS")
            or os.environ.get("REALTIME_NOTIFY_INTERVAL_SECONDS")
            or settings.get("monitorAccountIntervalSeconds")
            or minimum_interval
        )
        RealtimeScheduler(
            runner,
            interval,
            minimum_interval_seconds=minimum_interval,
        ).run_forever()
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


def ai_inference_command(args) -> int:
    store = stores.ai_inference_queue_store()
    if args.ai_inference_action == "status":
        print(json.dumps({"aiInferenceQueue": store.summary()}, ensure_ascii=False))
        return 0
    settings = runtime_settings()
    limit = int(args.limit or settings.get("notificationAiQueueBatchSize") or 1)
    worker_id = str(args.worker_id or os.environ.get("NOTIFICATION_AI_WORKER_ID") or "").strip()
    runner = build_ai_inference_queue_runner(worker_id=worker_id)
    if args.ai_inference_action == "once":
        processed = runner.run_once(limit=limit)
        print(json.dumps({
            "aiInferenceRequestsProcessed": processed,
            "workerId": runner.worker_id,
            "details": runner.last_run_details,
        }, ensure_ascii=False))
        return 0
    if args.ai_inference_action == "watch":
        interval = int(
            os.environ.get("NOTIFICATION_AI_QUEUE_INTERVAL_SECONDS")
            or settings.get("notificationAiQueueIntervalSeconds")
            or 5
        )
        AIInferenceQueueScheduler(runner, interval).run_forever(limit=limit)
        return 0
    return 1


def ontology_reasoning_command(args) -> int:
    settings = runtime_settings(fast_operational_read=True)
    if args.ontology_reasoning_action == "profile":
        service = build_ontology_reasoning_proof_service(settings)
        result = service.prove(
            account_id=str(getattr(args, "account_id", "") or ""),
            world_id=str(getattr(args, "world_id", "") or ""),
            symbols=split_symbols(str(getattr(args, "symbols", "") or "")),
            repeats=int(getattr(args, "repeats", 2) or 2),
            production_run_limit=int(getattr(args, "production_runs", 10) or 10),
            rule_ids=list(getattr(args, "rule_id", None) or []),
            use_all_active_rules=bool(getattr(args, "all_active_rules", False)),
            compare_subject_fanout=bool(getattr(args, "compare_subject_fanout", False)),
            subject_parallelism=int(getattr(args, "subject_parallelism", 2) or 2),
            minimum_fanout_reduction_pct=float(
                getattr(args, "minimum_fanout_reduction_pct", 40) or 40
            ),
        )
        if not bool(getattr(args, "full", False)):
            production = dict(result.get("productionEvidence") or {})
            production.pop("runs", None)
            result["productionEvidence"] = production
            replay = dict(result.get("readOnlyReplay") or {})
            compact_samples = []
            for raw_sample in replay.get("samples") or []:
                sample = dict(raw_sample or {})
                compact_samples.append({
                    key: sample.get(key)
                    for key in [
                        "sample", "status", "reason", "validForComparison",
                        "generationUnchanged", "ruleboxUnchanged", "generationFingerprint", "wallClockMs",
                        "stageTimings", "executedRuleCount", "executedRuleWorkCount",
                        "skippedRuleCount", "matchedCount", "readTransactionCount",
                        "readQueryCount", "parallelRuleExecution", "nativeRuleParallelism",
                        "coreEvaluationComplete", "fullEvaluationComplete", "nativeCoverageStatus",
                        "supportingRuleFailureCount", "blockingRuleFailureCount", "graphCounts",
                        "diagnosticWallClockMs", "subjectFanoutComparison",
                    ]
                    if key in sample
                } | {
                    "slowRules": list(sample.get("rules") or [])[:8],
                })
            replay["samples"] = compact_samples
            result["readOnlyReplay"] = replay
            trace = dict(result.get("productionRuleTrace") or {})
            trace["rules"] = list(trace.get("rules") or [])[:8]
            result["productionRuleTrace"] = trace
        print(json.dumps(result, ensure_ascii=False))
        return 0
    limit = int(args.limit or settings.get("ontologyReasoningBatchSize") or 20) if hasattr(args, "limit") else int(settings.get("ontologyReasoningBatchSize") or 20)
    local_lease_recovery = {}
    if args.ontology_reasoning_action in {"once", "watch"}:
        # Do not enumerate all durable TypeDB leases before the worker starts.
        # A large or temporarily stalled control-plane read used to leave every
        # application worker stopped during a routine restart.  The repository
        # now reclaims only an exact, proven-dead local lease when that world
        # attempts to acquire its write lease.
        local_lease_recovery = {
            "status": "deferred",
            "scope": "per-world-acquisition",
            "reason": "Dead local scoped ABox leases are recovered when their world next writes.",
        }
    runner = build_ontology_reasoning_runner(settings)
    if args.ontology_reasoning_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.ontology_reasoning_action == "once":
        result = runner.run_once(limit=limit, force=bool(getattr(args, "force", False)))
        if local_lease_recovery:
            result["localScopedABoxWriteLeaseRecovery"] = local_lease_recovery
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.ontology_reasoning_action == "serve":
        # The parent scheduler owns the timeout and can replace this process
        # at any point. This child deliberately keeps one composed runner
        # alive so its TypeDB driver and immutable RuleBox caches are warm
        # between durable mailbox turns.
        protocol = "ontology-reasoning-worker-v1"
        for raw_line in sys.stdin:
            try:
                request = json.loads(raw_line)
            except (TypeError, ValueError):
                continue
            if not isinstance(request, dict) or str(request.get("protocol") or "") != protocol:
                continue
            request_id = str(request.get("requestId") or "")
            action = str(request.get("action") or "run").strip().lower()
            if action == "stop":
                print(json.dumps({
                    "protocol": protocol,
                    "requestId": request_id,
                    "result": {"status": "stopped", "processedCount": 0, "alertCount": 0},
                }, ensure_ascii=False), flush=True)
                return 0
            if action == "recover-dead-leases":
                recover = getattr(runner, "recover_dead_projection_leases", None)
                try:
                    recovery = dict(recover() or {}) if callable(recover) else {
                        "status": "not-configured",
                        "clearedCount": 0,
                    }
                except Exception as error:  # noqa: BLE001 - parent keeps this child killable and retries safely.
                    recovery = {
                        "status": "error",
                        "clearedCount": 0,
                        "reason": str(error)[:180],
                    }
                print(json.dumps({
                    "protocol": protocol,
                    "requestId": request_id,
                    "result": {
                        "status": "recovered",
                        "processedCount": 0,
                        "alertCount": 0,
                        "typedbDeadLeaseRecovery": recovery,
                    },
                }, ensure_ascii=False), flush=True)
                continue
            if action != "run":
                print(json.dumps({
                    "protocol": protocol,
                    "requestId": request_id,
                    "result": {
                        "status": "error",
                        "processedCount": 0,
                        "alertCount": 0,
                        "deferredReason": "영구 격리 추론 워커의 요청 종류가 올바르지 않습니다.",
                    },
                }, ensure_ascii=False), flush=True)
                continue
            try:
                requested_limit = int(request.get("limit") or limit or 0)
            except (TypeError, ValueError):
                requested_limit = limit
            settings_refresh = {"status": "not-supported", "changedKeys": [], "removedKeys": []}
            refresh_settings = getattr(runner, "refresh_operational_settings", None)
            if callable(refresh_settings):
                try:
                    settings_refresh = dict(
                        refresh_settings(runtime_settings(fast_operational_read=True)) or {}
                    )
                except Exception as error:  # noqa: BLE001 - a settings read must not stall durable work.
                    settings_refresh = {
                        "status": "error",
                        "changedKeys": [],
                        "removedKeys": [],
                        "reason": str(error)[:180],
                    }
            try:
                result = runner.run_once(limit=max(0, requested_limit), force=bool(request.get("force")))
            except Exception as error:  # noqa: BLE001 - the parent retains the durable retry contract.
                result = {
                    "status": "error",
                    "processedCount": 0,
                    "alertCount": 0,
                    "deferredReason": str(error)[:220],
                }
            result = {
                **dict(result or {}),
                "runtimeSettingsRefresh": settings_refresh,
            }
            if local_lease_recovery:
                result = {**dict(result or {}), "localScopedABoxWriteLeaseRecovery": local_lease_recovery}
            print(json.dumps({
                "protocol": protocol,
                "requestId": request_id,
                "result": result,
            }, ensure_ascii=False), flush=True)
        return 0
    if args.ontology_reasoning_action == "watch":
        if local_lease_recovery:
            print("Ontology reasoning local scoped ABox write lease recovery=" + json.dumps(local_lease_recovery, ensure_ascii=False))
        interval = int(
            os.environ.get("ONTOLOGY_REASONING_INTERVAL_SECONDS")
            or settings.get("ontologyReasoningIntervalSeconds")
            or 10
        )
        isolated_cycle = None
        if runner.process_isolation_enabled():
            project_root = Path(__file__).resolve().parents[3]
            persistent_worker_enabled = str(
                settings.get("ontologyReasoningPersistentWorkerEnabled", "1")
            ).strip().lower() not in {"0", "false", "no", "off", "disabled"}
            cycle_class = PersistentIsolatedOntologyReasoningCycle if persistent_worker_enabled else IsolatedOntologyReasoningCycle
            isolated_cycle = cycle_class(
                [
                    sys.executable,
                    "-u",
                    str(project_root / "python_service" / "service.py"),
                    "ontology-reasoning",
                    "serve" if persistent_worker_enabled else "once",
                ],
                working_directory=str(project_root),
            )
        OntologyReasoningScheduler(
            runner,
            interval,
            isolated_cycle=isolated_cycle,
        ).run_forever(limit=limit)
        return 0
    return 1


def ontology_world_projection_command(args) -> int:
    settings = runtime_settings(fast_operational_read=True)
    if args.ontology_world_projection_action == "rebuild-portfolios":
        limit = int(getattr(args, "limit", "") or 0)
        result = build_ontology_portfolio_rebuild_runner(settings).run(limit=limit)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if str(result.get("status") or "") in {"ok", "empty"} else 1
    runner = build_ontology_world_projection_runner(settings)
    limit = int(getattr(args, "limit", "") or settings.get("ontologyWorldProjectionBatchSize") or 6)
    if args.ontology_world_projection_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.ontology_world_projection_action == "retry-failed":
        requeued = runner.outbox.requeue_failed(limit=limit)
        print(json.dumps({"status": "ok", "requeuedFailedCount": requeued, "outbox": runner.outbox.summary()}, ensure_ascii=False))
        return 0
    if args.ontology_world_projection_action == "rebuild":
        result = (
            runner.rebuild_candidate_from_completed(limit=limit)
            if bool(getattr(args, "read_only_source", False))
            else runner.rebuild_after_typedb_reset(limit=limit)
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if str(result.get("status") or "") in {"ok", "empty"} else 1
    if args.ontology_world_projection_action == "once":
        print(json.dumps(runner.run_once(limit=limit), ensure_ascii=False))
        return 0
    if args.ontology_world_projection_action == "watch":
        interval = int(
            os.environ.get("ONTOLOGY_WORLD_PROJECTION_INTERVAL_SECONDS")
            or settings.get("ontologyWorldProjectionIntervalSeconds")
            or 10
        )
        isolated_cycle = None
        isolation_value = str(
            os.environ.get("ONTOLOGY_WORLD_PROJECTION_PROCESS_ISOLATION_ENABLED")
            or settings.get("ontologyWorldProjectionProcessIsolationEnabled")
            or "1"
        ).strip().lower()
        if isolation_value not in {"0", "false", "no", "off", "disabled"}:
            project_root = Path(__file__).resolve().parents[3]
            isolated_cycle = IsolatedOntologyReasoningCycle(
                [
                    sys.executable,
                    "-u",
                    str(project_root / "python_service" / "service.py"),
                    "ontology-world-projection",
                    "once",
                ],
                working_directory=str(project_root),
            )
        OntologyWorldProjectionScheduler(runner, interval, isolated_cycle=isolated_cycle).run_forever(limit=limit)
        return 0
    return 1


def ontology_inference_detail_command(args) -> int:
    settings = runtime_settings(fast_operational_read=True)
    runner = build_ontology_inference_detail_runner(settings)
    limit = int(getattr(args, "limit", "") or settings.get("ontologyInferenceDetailBatchSize") or 1)
    if args.ontology_inference_detail_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.ontology_inference_detail_action == "retry-failed":
        requeued = runner.outbox.requeue_failed(limit=limit)
        print(json.dumps({
            "status": "ok",
            "requeuedFailedCount": requeued,
            "outbox": runner.outbox.summary(),
        }, ensure_ascii=False))
        return 0
    if args.ontology_inference_detail_action == "once":
        print(json.dumps(runner.run_once(limit=limit), ensure_ascii=False))
        return 0
    if args.ontology_inference_detail_action == "watch":
        interval = int(
            os.environ.get("ONTOLOGY_INFERENCE_DETAIL_INTERVAL_SECONDS")
            or settings.get("ontologyInferenceDetailIntervalSeconds")
            or 15
        )
        isolated_cycle = None
        isolation_value = str(
            os.environ.get("ONTOLOGY_INFERENCE_DETAIL_PROCESS_ISOLATION_ENABLED")
            or settings.get("ontologyInferenceDetailProcessIsolationEnabled")
            or "1"
        ).strip().lower()
        if isolation_value not in {"0", "false", "no", "off", "disabled"}:
            project_root = Path(__file__).resolve().parents[3]
            isolated_cycle = IsolatedOntologyReasoningCycle(
                [
                    sys.executable,
                    "-u",
                    str(project_root / "python_service" / "service.py"),
                    "ontology-inference-detail",
                    "once",
                ],
                working_directory=str(project_root),
            )
        OntologyInferenceDetailScheduler(runner, interval, isolated_cycle=isolated_cycle).run_forever(limit=limit)
        return 0
    return 1


def ontology_rulebox_prewarm_command(args) -> int:
    settings = runtime_settings(fast_operational_read=True)
    runner = build_ontology_rulebox_prewarm_runner(settings)
    if args.ontology_rulebox_prewarm_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.ontology_rulebox_prewarm_action == "once":
        print(json.dumps(
            runner.run_once(force=bool(getattr(args, "force", False))),
            ensure_ascii=False,
        ))
        return 0
    if args.ontology_rulebox_prewarm_action == "watch":
        interval = int(
            os.environ.get("ONTOLOGY_RULEBOX_PREWARM_INTERVAL_SECONDS")
            or settings.get("ontologyRuleboxPrewarmIntervalSeconds")
            or runner.interval_seconds()
        )
        isolated_cycle = None
        isolation_value = str(
            os.environ.get("ONTOLOGY_RULEBOX_PREWARM_PROCESS_ISOLATION_ENABLED")
            or settings.get("ontologyRuleboxPrewarmProcessIsolationEnabled")
            or "1"
        ).strip().lower()
        if isolation_value not in {"0", "false", "no", "off", "disabled"}:
            project_root = Path(__file__).resolve().parents[3]
            isolated_cycle = IsolatedOntologyReasoningCycle(
                [
                    sys.executable,
                    "-u",
                    str(project_root / "python_service" / "service.py"),
                    "ontology-rulebox-prewarm",
                    "once",
                ],
                working_directory=str(project_root),
            )
        OntologyRuleboxPrewarmScheduler(
            runner,
            interval,
            isolated_cycle=isolated_cycle,
        ).run_forever()
        return 0
    return 1


def ontology_maintenance_command(args) -> int:
    settings = runtime_settings(fast_operational_read=True)
    runner = build_ontology_maintenance_runner(settings)
    if args.ontology_maintenance_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.ontology_maintenance_action == "once":
        print(json.dumps(runner.run_once(), ensure_ascii=False))
        return 0
    if args.ontology_maintenance_action == "watch":
        interval = int(
            os.environ.get("ONTOLOGY_ABOX_MAINTENANCE_INTERVAL_SECONDS")
            or settings.get("ontologyAboxMaintenanceIntervalSeconds")
            or runner.interval_seconds()
        )
        isolated_cycle = None
        if runner.process_isolation_enabled():
            project_root = Path(__file__).resolve().parents[3]
            isolated_cycle = IsolatedOntologyReasoningCycle(
                [
                    sys.executable,
                    "-u",
                    str(project_root / "python_service" / "service.py"),
                    "ontology-maintenance",
                    "once",
                ],
                working_directory=str(project_root),
            )
        OntologyMaintenanceScheduler(runner, interval, isolated_cycle=isolated_cycle).run_forever()
        return 0
    return 1


def ontology_command(args) -> int:
    settings = runtime_settings()
    repository = ontology_repository_from_settings(settings)
    if args.ontology_action == "seed":
        payload = {
            "replaceRuleBox": bool(args.replace_rulebox),
            "clearInference": bool(args.clear_inference),
            "recoverScopedABoxWriteLease": bool(getattr(args, "recover_scoped_write_lease", False)),
        }
        result = repository.seed_ontology(payload)
        recovery = getattr(repository, "recover_pending_abox_activation", None)
        if callable(recovery):
            try:
                result["pendingAboxActivationRecovery"] = recovery()
            except Exception as error:  # noqa: BLE001 - do not start dependent workers against an unknown ABox generation.
                result["pendingAboxActivationRecovery"] = {"status": "error", "reason": str(error)[:180]}
        print(json.dumps(result, ensure_ascii=False))
        # A current static ontology is a successful seed no-op. Returning a
        # non-zero code here prevents the service manager from starting all
        # dependent collection and reasoning workers after a normal restart.
        recovery_status = str((result.get("pendingAboxActivationRecovery") or {}).get("status") or "skipped")
        return 0 if (
            result.get("status") in {"ok", "unchanged", "disabled"}
            and recovery_status in {"skipped", "disabled", "finalized", "restored", "cleared-stale", "retry-required", "staged"}
        ) else 1
    if args.ontology_action == "recover-scoped-write-lease":
        recovery = getattr(repository, "recover_scoped_abox_write_lease_after_managed_shutdown", None)
        if not callable(recovery):
            result = {"status": "unsupported", "reason": "Graph store has no scoped ABox write lease recovery."}
        else:
            try:
                result = recovery()
            except Exception as error:  # noqa: BLE001 - worker startup must not proceed against an unknown lease owner.
                result = {"status": "error", "reason": str(error)[:180]}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if str(result.get("status") or "") in {
            "cleared", "empty", "disabled", "skipped", "active-owner",
            "foreign-owner", "legacy-owner-unknown", "invalid-owner",
        } else 1
    if args.ontology_action == "recover-abox-activation":
        recovery = getattr(repository, "recover_pending_abox_activation", None)
        if not callable(recovery):
            result = {"status": "skipped", "reason": "Graph store has no pending ABox activation journal."}
        else:
            try:
                result = recovery()
            except Exception as error:  # noqa: BLE001 - expose the blocking TypeDB state to the operator.
                result = {"status": "error", "reason": str(error)[:180]}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if str(result.get("status") or "") in {
            "skipped", "disabled", "finalized", "restored", "cleared-stale", "retry-required", "staged",
        } else 1
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
            account_id=args.account_id,
            tenant_id=args.tenant_id,
        )
        result = service.suggest_from_rule_candidates(candidate_result, {
            "symbols": symbols,
            "activate": bool(args.activate),
            "run": bool(args.run),
            "limit": args.limit,
            "accountId": args.account_id,
            "tenantId": args.tenant_id,
            "worldId": args.world_id,
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
                "accountId": args.account_id,
                "tenantId": args.tenant_id,
                "worldId": args.world_id,
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


def time_series_platform_command(args) -> int:
    from .time_series_factory import (
        build_time_series_adapters,
        build_time_series_backend_platform,
        build_time_series_projection_runner,
        initialize_time_series_registry,
    )
    from ..domain.portfolio_ontology_temporal_concepts import parse_temporal_windows

    configured = runtime_settings()
    adapters = build_time_series_adapters(configured)
    registry = initialize_time_series_registry(configured, adapters)
    runner = build_time_series_projection_runner(configured, worker_id=getattr(args, "worker_id", ""))
    if args.time_series_action == "status":
        health = {}
        for backend_id, adapter in adapters.items():
            health[backend_id] = adapter.health()
            registry.update_health(backend_id, health[backend_id])
        print(json.dumps({
            "control": registry.control(),
            "deployments": registry.list(),
            "health": health,
            "queue": runner.outbox.summary(),
        }, ensure_ascii=False))
        return 0
    if args.time_series_action == "project-once":
        print(json.dumps(runner.run_once(), ensure_ascii=False))
        return 0
    if args.time_series_action == "watch":
        runner.watch()
        return 0
    if args.time_series_action == "backfill":
        source = stores.raw_mysql_market_time_series_store(configured)
        result = runner.enqueue_backfill(
            source,
            args.backend_id,
            args.max_rows,
            args.batch_size,
            args.observed_after,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    platform = build_time_series_backend_platform(configured)
    if args.time_series_action == "candidate":
        print(json.dumps(platform.mark_candidate(args.backend_id), ensure_ascii=False))
        return 0
    if args.time_series_action in {"compare", "promote"}:
        symbols = split_symbols(args.symbols or configured.get("watchlistSymbols") or "")
        if not symbols:
            raise ValueError("At least one comparison symbol is required")
        definitions = parse_temporal_windows(configured.get("temporalWindowPeriods"))
        comparison = platform.compare(
            args.backend_id,
            args.account_id,
            symbols,
            definitions,
            args.as_of,
        )
        if args.time_series_action == "compare":
            print(json.dumps(comparison, ensure_ascii=False))
            return 0 if comparison.get("status") == "equivalent" else 2
        result = platform.promote(args.backend_id, comparison)
        if result.get("status") == "promoted":
            control = dict(result.get("control") or {})
            save_runtime_settings({
                "timeSeriesActiveBackendId": control.get("activeBackendId") or args.backend_id,
                "timeSeriesShadowBackendId": control.get("shadowBackendId") or "",
            })
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "promoted" else 2
    if args.time_series_action == "rollback":
        result = platform.rollback()
        if result.get("status") == "rolled-back":
            control = dict(result.get("control") or {})
            save_runtime_settings({
                "timeSeriesActiveBackendId": control.get("activeBackendId") or "mysql-primary",
                "timeSeriesShadowBackendId": control.get("shadowBackendId") or "",
            })
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "rolled-back" else 2
    return 1


def reasoning_engine_platform_command(args) -> int:
    from .reasoning_engine_factory import build_reasoning_engine_platform

    platform = build_reasoning_engine_platform(runtime_settings())
    state = platform.initialize()
    if args.reasoning_engine_action == "status":
        print(json.dumps(state, ensure_ascii=False))
        return 0
    if args.reasoning_engine_action == "rollback":
        print(json.dumps(platform.rollback(), ensure_ascii=False))
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


def run_mysql_operational_cleanup(
    settings: Dict[str, object],
    optimize: bool = False,
    drop_ephemeral_databases: bool = False,
) -> Dict[str, object]:
    """Run one bounded cleanup pass outside the realtime inference path."""
    settings = dict(settings or {})
    storage_health = operational_storage_inventory(settings)
    storage_capacity_health = observe_operational_storage_capacity(
        settings,
        snapshot=storage_health,
    )
    settings = accelerated_mysql_cleanup_settings(settings, storage_health)
    settings["_skipOperationalHistoryRetention"] = True
    # The service manager bootstraps schema before it starts workers.  This
    # low-priority worker must not repeat table/index DDL checks on every
    # separate process because that work can exceed the normal query timeout.
    settings["_skipOperationalSchemaBootstrap"] = True
    # A completed world-projection row can still carry a multi-megabyte result.
    # Keep this local to the maintenance worker and isolate its pool key so a
    # previously opened 10-second realtime connection cannot be reused here.
    settings["mysqlOperationTimeoutSeconds"] = str(max(60, mysql_operation_timeout_seconds(settings)))
    # A connection can be dropped after the server-side read timeout.  A
    # normal pass is idempotent and uses a fresh pooled connection on retry;
    # explicit compaction is intentionally excluded because it is operator-led.
    retryable = not optimize and not drop_ephemeral_databases
    connection_retries = 0
    deadlock_retries = 0
    while True:
        try:
            store = MySQLOperationalConnection(settings)
            with store.connect() as connection:
                result = apply_mysql_operational_history_retention(
                    connection,
                    settings,
                    use_lock=True,
                )
                minimal_retention = MySQLMinimalRetentionService(
                    MySQLMinimalRetentionRepository(connection),
                    settings,
                ).run_once()
                result["minimalRetention"] = minimal_retention
                result["deleted"] = int(result.get("deleted") or 0) + int(minimal_retention.get("deleted") or 0)
                result["compacted"] = int(minimal_retention.get("compacted") or 0)
                merged_tables = dict(result.get("tables") or {})
                for table, count in dict(minimal_retention.get("tables") or {}).items():
                    merged_tables[str(table)] = int(merged_tables.get(str(table)) or 0) + int(count or 0)
                result["tables"] = merged_tables
                policies = dict(result.get("policies") or {})
                for name, count in dict(minimal_retention.get("policies") or {}).items():
                    policies["minimal:" + str(name)] = int(count or 0)
                result["policies"] = policies
                result["compactionCandidates"] = mysql_operational_compaction_tables(result)
                if optimize:
                    compaction_candidates = mysql_operational_space_reclaim_candidates(
                        connection,
                        minimum_reclaim_mb=int(settings.get("mysqlPhysicalCompactionMinReclaimMb") or 256),
                        maximum_tables=int(settings.get("mysqlPhysicalCompactionMaxTablesPerRun") or 3),
                    )
                    compaction_plan = safe_mysql_operational_compaction_tables(
                        compaction_candidates,
                        free_bytes=int(float(storage_health.get("freeMb") or 0) * 1024 * 1024),
                        reserve_bytes=int(float(storage_health.get("minimumFreeMb") or 0) * 1024 * 1024),
                    )
                    result["compactionPlan"] = {
                        **compaction_plan,
                        "candidates": compaction_candidates,
                    }
                    result["compaction"] = optimize_mysql_operational_tables(
                        connection,
                        compaction_plan["selectedTables"],
                    )
                if drop_ephemeral_databases:
                    result["ephemeralDatabaseCleanup"] = drop_ephemeral_mysql_databases(
                        connection,
                        protected_databases=[str(settings.get("mysqlDatabase") or "")],
                    )
            if connection_retries:
                result["transientConnectionRetryCount"] = connection_retries
            if deadlock_retries:
                result["deadlockRetryCount"] = deadlock_retries
            storage_health = operational_storage_inventory(settings)
            storage_capacity_health = observe_operational_storage_capacity(
                settings,
                snapshot=storage_health,
            )
            result["storageHealth"] = storage_health
            result["storageCapacityHealth"] = storage_capacity_health
            result["cleanupMode"] = str(storage_health.get("cleanupMode") or "normal")
            result["nextIntervalSeconds"] = (
                60
                if result["cleanupMode"] == "emergency"
                else 120
                if result["cleanupMode"] == "accelerated"
                and (
                    int(result.get("deleted") or 0) > 0
                    or not bool(storage_health.get("nonEssentialWritesAllowed", True))
                )
                else operational_history_retention_check_interval_seconds(settings)
            )
            return result
        except Exception as error:
            if not retryable:
                raise
            if mysql_is_deadlock(error) and deadlock_retries < mysql_deadlock_retry_count(settings):
                deadlock_retries += 1
                delay_ms = mysql_deadlock_retry_delay_milliseconds(settings, deadlock_retries)
                time.sleep(delay_ms / 1000.0)
                continue
            if mysql_is_connection_lost(error) and connection_retries < 1:
                connection_retries += 1
                time.sleep(0.25)
                continue
            raise


def run_mysql_minimal_retention(
    settings: Dict[str, object],
    apply: bool = False,
    drain: bool = False,
    drain_max_passes: int = 20,
) -> Dict[str, object]:
    """Run bounded MySQL retention, with an explicit backlog-drain option."""

    configured = dict(settings or {})

    def made_meaningful_progress(result: Mapping[str, object]) -> bool:
        if int((result or {}).get("compacted") or 0) > 0:
            return True
        if int((result or {}).get("archived") or 0) > 0:
            return True
        policies = dict((result or {}).get("policies") or {})
        if policies:
            return any(
                int(count or 0) > 0
                for name, count in policies.items()
                if str(name) != "audit:runs"
            )
        return int((result or {}).get("deleted") or 0) > 0

    if drain and not apply:
        return {
            "status": "invalid",
            "reason": "--drain requires --apply because preview never deletes retained operational data.",
            "deleted": 0,
            "compacted": 0,
            "tables": {},
        }
    if drain:
        # This profile is reserved for an operator-led recovery after a
        # capacity incident. It remains bounded in both rows and bytes per
        # pass and never changes the saved steady-state policy.
        configured.update({
            "_effectiveMysqlMinimalRetentionBatchSize": "1000",
            "_effectiveMysqlMinimalRetentionMaxDeleteBytes": str(256 * 1024 * 1024),
            "_effectiveMysqlMinimalRetentionMaxRunSeconds": "60",
        })
    configured["_skipOperationalHistoryRetention"] = True
    configured["mysqlOperationTimeoutSeconds"] = str(max(60, mysql_operation_timeout_seconds(configured)))
    try:
        requested_passes = int(drain_max_passes or 20)
    except (TypeError, ValueError):
        requested_passes = 20
    passes = max(1, min(50, requested_passes)) if drain else 1
    connection_retries = 0
    deadlock_retries = 0
    while True:
        try:
            store = MySQLOperationalConnection(configured)
            with store.connect() as connection:
                service = MySQLMinimalRetentionService(
                    MySQLMinimalRetentionRepository(connection),
                    configured,
                )
                results = []
                for _index in range(passes):
                    result = service.run_once(
                        force=True,
                        apply=apply,
                        preview=not apply,
                        preview_before_apply=apply,
                    )
                    results.append(result)
                    if not drain or not made_meaningful_progress(result):
                        break
                result = dict(results[-1] or {}) if results else {
                    "status": "ok",
                    "deleted": 0,
                    "compacted": 0,
                    "tables": {},
                }
                if drain:
                    total_deleted = sum(int(item.get("deleted") or 0) for item in results)
                    total_compacted = sum(int(item.get("compacted") or 0) for item in results)
                    total_archived = sum(int(item.get("archived") or 0) for item in results)
                    total_estimated_bytes = sum(int(item.get("estimatedBytes") or 0) for item in results)
                    merged_tables = {}
                    merged_policies = {}
                    for item in results:
                        for table, count in dict(item.get("tables") or {}).items():
                            merged_tables[str(table)] = int(merged_tables.get(str(table)) or 0) + int(count or 0)
                        for name, count in dict(item.get("policies") or {}).items():
                            merged_policies[str(name)] = int(merged_policies.get(str(name)) or 0) + int(count or 0)
                    result["deleted"] = total_deleted
                    result["compacted"] = total_compacted
                    result["archived"] = total_archived
                    result["estimatedBytes"] = total_estimated_bytes
                    result["tables"] = merged_tables
                    result["policies"] = merged_policies
                    result["drain"] = {
                        "enabled": True,
                        "completedPasses": len(results),
                        "maxPasses": passes,
                        "exhausted": bool(results)
                        and not made_meaningful_progress(results[-1]),
                    }
            if connection_retries:
                result["transientConnectionRetryCount"] = connection_retries
            if deadlock_retries:
                result["deadlockRetryCount"] = deadlock_retries
            return result
        except Exception as error:
            if mysql_is_deadlock(error) and deadlock_retries < mysql_deadlock_retry_count(configured):
                deadlock_retries += 1
                delay_ms = mysql_deadlock_retry_delay_milliseconds(configured, deadlock_retries)
                time.sleep(delay_ms / 1000.0)
                continue
            if mysql_is_connection_lost(error) and connection_retries < 1:
                connection_retries += 1
                time.sleep(0.25)
                continue
            raise


def maintenance_command(args) -> int:
    """Run storage maintenance outside the realtime inference path."""
    settings = dict(runtime_settings())
    if args.maintenance_action == "mysql-minimal-retention":
        result = run_mysql_minimal_retention(
            settings,
            apply=bool(args.apply),
            drain=bool(getattr(args, "drain", False)),
            drain_max_passes=getattr(args, "drain_max_passes", 20),
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if str(result.get("status") or "ok") != "error" else 1
    if args.maintenance_action == "mysql-cleanup":
        result = run_mysql_operational_cleanup(
            settings,
            optimize=bool(args.optimize),
            drop_ephemeral_databases=bool(args.drop_ephemeral_databases),
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if str(result.get("status") or "ok") != "error" else 1
    if args.maintenance_action == "watch":
        configured_interval = int(
            getattr(args, "interval", "")
            or os.environ.get("OPERATIONAL_HISTORY_RETENTION_CHECK_INTERVAL_SECONDS")
            or settings.get("operationalHistoryRetentionCheckIntervalSeconds")
            or 300
        )
        minimal_policy = mysql_minimal_retention_policy(settings)
        if minimal_policy.enabled:
            configured_interval = min(configured_interval, minimal_policy.interval_seconds)

        reasoning_queue_probe = build_ontology_reasoning_queue_probe(settings)

        def cleanup_once():
            queue_state = dict(reasoning_queue_probe() or {})
            try:
                pending_count = int(queue_state.get("effectivePendingCount") or 0)
            except (TypeError, ValueError):
                pending_count = 0
            # History retention owns a separate, low-priority worker. Deferring
            # it forever whenever the inference mailbox is non-empty lets
            # superseded high-volume events consume the operational database
            # and eventually blocks the notification outbox itself.
            result = run_mysql_operational_cleanup(settings)
            if pending_count > 0:
                result["status"] = "queue-active-cleanup"
                result["reasoningQueue"] = queue_state
                result["reason"] = "추론 대기열 처리와 별개로 저우선 MySQL 이력 정리를 실행했습니다."
            return result

        OperationalHistoryRetentionScheduler(cleanup_once, configured_interval).run_forever()
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
    if args.news_action == "revalidate":
        result = RevalidateNewsIntelligenceService(stores.research_evidence_store(settings)).revalidate(
            symbol=str(args.symbol or "").upper().strip(),
            limit=max(1, min(5000, int(args.limit or 500))),
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0
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


def news_analysis_command(args) -> int:
    settings = runtime_settings()
    runner = build_news_analysis_enrichment_runner(settings)
    if args.news_analysis_action == "status":
        print(json.dumps(runner.status(), ensure_ascii=False))
        return 0
    if args.news_analysis_action == "once":
        print(json.dumps(runner.run_once(limit=int(args.limit or 0)), ensure_ascii=False))
        return 0
    if args.news_analysis_action == "watch":
        NewsAnalysisEnrichmentScheduler(runner, runner.interval_seconds()).run_forever()
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
    if args.investment_calendar_action == "status":
        print(json.dumps(build_investment_calendar_runner(settings).status(), ensure_ascii=False))
        return 0
    service = build_investment_calendar_service(settings)
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
    if args.investment_calendar_action == "discover":
        discovery_service = build_investment_calendar_discovery_service(settings)
        print(json.dumps(discovery_service.run_once({
            "symbol": args.symbol,
            "limit": args.limit,
            "force": args.force,
        }, force=args.force), ensure_ascii=False))
        return 0
    if args.investment_calendar_action == "approve-candidate":
        candidate_service = build_investment_calendar_candidate_service(settings)
        print(json.dumps(candidate_service.approve_candidate(args.candidate_id, {
            "startsAt": args.starts_at,
            "officialSourceUrl": args.official_source_url,
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

    ai_inference = subparsers.add_parser("ai-inference", help="Run deferred notification AI inference")
    ai_inference_actions = ai_inference.add_subparsers(dest="ai_inference_action", required=True)
    ai_once = ai_inference_actions.add_parser("once")
    ai_once.add_argument("--limit", default="")
    ai_once.add_argument("--worker-id", default="")
    ai_watch = ai_inference_actions.add_parser("watch")
    ai_watch.add_argument("--limit", default="")
    ai_watch.add_argument("--worker-id", default="")
    ai_inference_actions.add_parser("status")
    ai_inference.set_defaults(func=ai_inference_command)

    ontology_reasoning = subparsers.add_parser("ontology-reasoning", help="Run data-update driven ontology reasoning")
    ontology_reasoning_actions = ontology_reasoning.add_subparsers(dest="ontology_reasoning_action", required=True)
    ontology_once = ontology_reasoning_actions.add_parser("once")
    ontology_once.add_argument("--limit", default="")
    ontology_once.add_argument("--force", action="store_true")
    ontology_watch = ontology_reasoning_actions.add_parser("watch")
    ontology_watch.add_argument("--limit", default="")
    ontology_reasoning_actions.add_parser("serve", help=argparse.SUPPRESS)
    ontology_reasoning_actions.add_parser("status")
    ontology_profile = ontology_reasoning_actions.add_parser(
        "profile",
        help="Prove TypeDB reasoning bottlenecks with a read-only same-generation replay",
    )
    ontology_profile.add_argument("--account-id", default="")
    ontology_profile.add_argument("--world-id", default="")
    ontology_profile.add_argument("--symbols", default="")
    ontology_profile.add_argument("--repeats", default="2")
    ontology_profile.add_argument("--production-runs", default="10")
    ontology_profile.add_argument("--rule-id", action="append", default=[])
    ontology_profile.add_argument(
        "--all-active-rules",
        action="store_true",
        help="Replay every enabled RuleBox rule when no production rule trace is available",
    )
    ontology_profile.add_argument(
        "--compare-subject-fanout",
        action="store_true",
        help="Compare combined targets with independent subject reads without writing graph state",
    )
    ontology_profile.add_argument("--subject-parallelism", default="2", choices=["1", "2"])
    ontology_profile.add_argument("--minimum-fanout-reduction-pct", default="40")
    ontology_profile.add_argument(
        "--full",
        action="store_true",
        help="Include full generation metadata, skipped rules, and query diagnostics",
    )
    ontology_reasoning.set_defaults(func=ontology_reasoning_command)

    ontology_world_projection = subparsers.add_parser(
        "ontology-world-projection",
        help="Project verified PortfolioWorld facts into durable shared ontology worlds",
    )
    ontology_world_projection_actions = ontology_world_projection.add_subparsers(
        dest="ontology_world_projection_action",
        required=True,
    )
    ontology_world_projection_once = ontology_world_projection_actions.add_parser("once")
    ontology_world_projection_once.add_argument("--limit", default="")
    ontology_world_projection_watch = ontology_world_projection_actions.add_parser("watch")
    ontology_world_projection_watch.add_argument("--limit", default="")
    ontology_world_projection_retry = ontology_world_projection_actions.add_parser("retry-failed")
    ontology_world_projection_retry.add_argument("--limit", default="")
    ontology_world_projection_rebuild = ontology_world_projection_actions.add_parser("rebuild")
    ontology_world_projection_rebuild.add_argument("--limit", default="")
    ontology_world_projection_rebuild.add_argument(
        "--read-only-source",
        action="store_true",
        help="Replay completed packets into an isolated candidate without mutating the live outbox",
    )
    ontology_world_projection_portfolios = ontology_world_projection_actions.add_parser(
        "rebuild-portfolios",
        help="Rebuild current PortfolioWorlds from durable MySQL monitor snapshots",
    )
    ontology_world_projection_portfolios.add_argument("--limit", default="")
    ontology_world_projection_actions.add_parser("status")
    ontology_world_projection.set_defaults(func=ontology_world_projection_command)

    ontology_inference_detail = subparsers.add_parser(
        "ontology-inference-detail",
        help="Read detailed InferenceBox rows after live reasoning work is idle",
    )
    ontology_inference_detail_actions = ontology_inference_detail.add_subparsers(
        dest="ontology_inference_detail_action",
        required=True,
    )
    ontology_inference_detail_once = ontology_inference_detail_actions.add_parser("once")
    ontology_inference_detail_once.add_argument("--limit", default="")
    ontology_inference_detail_watch = ontology_inference_detail_actions.add_parser("watch")
    ontology_inference_detail_watch.add_argument("--limit", default="")
    ontology_inference_detail_retry = ontology_inference_detail_actions.add_parser("retry-failed")
    ontology_inference_detail_retry.add_argument("--limit", default="")
    ontology_inference_detail_actions.add_parser("status")
    ontology_inference_detail.set_defaults(func=ontology_inference_detail_command)

    ontology_rulebox_prewarm = subparsers.add_parser(
        "ontology-rulebox-prewarm",
        help="Prewarm active RuleBox TypeDB schema functions outside live inference",
    )
    ontology_rulebox_prewarm_actions = ontology_rulebox_prewarm.add_subparsers(
        dest="ontology_rulebox_prewarm_action",
        required=True,
    )
    ontology_rulebox_prewarm_once = ontology_rulebox_prewarm_actions.add_parser("once")
    ontology_rulebox_prewarm_once.add_argument("--force", action="store_true")
    ontology_rulebox_prewarm_actions.add_parser("watch")
    ontology_rulebox_prewarm_actions.add_parser("status")
    ontology_rulebox_prewarm.set_defaults(func=ontology_rulebox_prewarm_command)

    ontology_maintenance = subparsers.add_parser(
        "ontology-maintenance",
        help="Run bounded background retention for inactive scoped ABox manifests",
    )
    ontology_maintenance_actions = ontology_maintenance.add_subparsers(
        dest="ontology_maintenance_action",
        required=True,
    )
    ontology_maintenance_actions.add_parser("once")
    ontology_maintenance_actions.add_parser("watch")
    ontology_maintenance_actions.add_parser("status")
    ontology_maintenance.set_defaults(func=ontology_maintenance_command)

    ontology = subparsers.add_parser("ontology", help="Manage ontology graph projection")
    ontology_actions = ontology.add_subparsers(dest="ontology_action", required=True)
    ontology_seed = ontology_actions.add_parser("seed")
    ontology_seed.add_argument("--replace-rulebox", action="store_true")
    ontology_seed.add_argument("--keep-inference", dest="clear_inference", action="store_false", default=True)
    ontology_seed.add_argument("--recover-scoped-write-lease", action="store_true")
    ontology_actions.add_parser("recover-scoped-write-lease")
    ontology_actions.add_parser("recover-abox-activation")
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
    lab_suggest.add_argument("--account-id", default="")
    lab_suggest.add_argument("--tenant-id", default="")
    lab_suggest.add_argument("--world-id", default="")
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
    lab_apply.add_argument("--account-id", default="")
    lab_apply.add_argument("--tenant-id", default="")
    lab_apply.add_argument("--world-id", default="")
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

    time_series = subparsers.add_parser("time-series-platform", help="Manage replaceable time-series backends")
    time_series_actions = time_series.add_subparsers(dest="time_series_action", required=True)
    time_series_actions.add_parser("status")
    time_series_once = time_series_actions.add_parser("project-once")
    time_series_once.add_argument("--worker-id", default="")
    time_series_watch = time_series_actions.add_parser("watch")
    time_series_watch.add_argument("--worker-id", default="")
    time_series_backfill = time_series_actions.add_parser("backfill")
    time_series_backfill.add_argument("--backend-id", default="questdb-shadow")
    time_series_backfill.add_argument("--batch-size", type=int, default=50)
    time_series_backfill.add_argument("--max-rows", type=int, default=0)
    time_series_backfill.add_argument("--observed-after", default="")
    time_series_candidate = time_series_actions.add_parser("candidate")
    time_series_candidate.add_argument("--backend-id", default="questdb-shadow")
    for action_name in ["compare", "promote"]:
        action = time_series_actions.add_parser(action_name)
        action.add_argument("--backend-id", default="questdb-shadow")
        action.add_argument("--account-id", required=True)
        action.add_argument("--symbols", required=True)
        action.add_argument("--as-of", default="")
    time_series_actions.add_parser("rollback")
    time_series.set_defaults(func=time_series_platform_command)

    reasoning_engine = subparsers.add_parser("reasoning-engine", help="Manage versioned reasoning-engine deployments")
    reasoning_engine_actions = reasoning_engine.add_subparsers(dest="reasoning_engine_action", required=True)
    reasoning_engine_actions.add_parser("status")
    reasoning_engine_actions.add_parser("rollback")
    reasoning_engine.set_defaults(func=reasoning_engine_platform_command)

    app_store = subparsers.add_parser("store", help="Manage app store data")
    app_store_actions = app_store.add_subparsers(dest="store_action", required=True)
    app_store_actions.add_parser("raw-json")
    app_store_actions.add_parser("replace-json")
    app_store.set_defaults(func=app_store_command)

    maintenance = subparsers.add_parser("maintenance", help="Run explicit local storage maintenance")
    maintenance_actions = maintenance.add_subparsers(dest="maintenance_action", required=True)
    mysql_cleanup = maintenance_actions.add_parser("mysql-cleanup")
    mysql_cleanup.add_argument("--optimize", action="store_true")
    mysql_cleanup.add_argument("--drop-ephemeral-databases", action="store_true")
    mysql_minimal_retention = maintenance_actions.add_parser(
        "mysql-minimal-retention",
        help="Preview bounded MySQL data retention, or apply it explicitly with --apply",
    )
    mysql_minimal_retention.add_argument("--apply", action="store_true")
    mysql_minimal_retention.add_argument(
        "--drain",
        action="store_true",
        help="Apply up to a bounded number of accelerated retention passes after a capacity incident",
    )
    mysql_minimal_retention.add_argument("--drain-max-passes", default="20")
    mysql_retention_watch = maintenance_actions.add_parser("watch")
    mysql_retention_watch.add_argument("--interval", default="")
    maintenance.set_defaults(func=maintenance_command)

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
    news_revalidate = news_actions.add_parser("revalidate")
    news_revalidate.add_argument("--symbol", default="")
    news_revalidate.add_argument("--limit", default="500")
    news.set_defaults(func=news_command)

    news_analysis = subparsers.add_parser("news-analysis", help="Enrich stored news with Korean summaries and title translations")
    news_analysis_actions = news_analysis.add_subparsers(dest="news_analysis_action", required=True)
    analysis_once = news_analysis_actions.add_parser("once")
    analysis_once.add_argument("--limit", default="")
    news_analysis_actions.add_parser("watch")
    news_analysis_actions.add_parser("status")
    news_analysis.set_defaults(func=news_analysis_command)

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
    calendar_discover = investment_calendar_actions.add_parser("discover")
    calendar_discover.add_argument("--symbol", default="")
    calendar_discover.add_argument("--limit", default="12")
    calendar_discover.add_argument("--force", action="store_true")
    calendar_candidate_approve = investment_calendar_actions.add_parser("approve-candidate")
    calendar_candidate_approve.add_argument("--candidate-id", required=True)
    calendar_candidate_approve.add_argument("--starts-at", default="")
    calendar_candidate_approve.add_argument("--official-source-url", default="")
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
