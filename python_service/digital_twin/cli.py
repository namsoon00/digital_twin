import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

from .admin_preview import write_admin_preview
from .application.account_service import AccountApplicationService
from .application.model_review_service import ModelReviewScheduler
from .application.notification_service import NotificationQueueScheduler
from .application.scheduler import MIN_REALTIME_INTERVAL_SECONDS, RealtimeScheduler
from .domain.accounts import AccountConfig, split_symbols
from .domain.monitoring import RealtimeMonitor
from .domain.portfolio import AlertEvent
from .infrastructure.event_bus import default_event_bus
from .infrastructure.sqlite_operational import SQLiteAppStore, SQLiteModelReviewJobStore, SQLiteMonitorStore, SQLiteNotificationJobStore
from .infrastructure.notifications import queued_notifier_for_account, send_events
from .infrastructure.service_factory import build_model_review_runner, build_monitor_runner, build_notification_queue_runner
from .infrastructure.settings import (
    SECRET_SETTING_KEYS,
    read_settings_store,
    runtime_settings,
    save_runtime_settings,
    utc_now,
    write_settings_store,
)
from .infrastructure.sqlite_accounts import AccountRegistry
from .infrastructure.toss_snapshots import build_snapshot


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
    )


def preserve_existing_secrets(registry: AccountRegistry, payload, account: AccountConfig) -> AccountConfig:
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
    registry = AccountRegistry()
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
    registry = AccountRegistry()
    accounts = registry.load()
    if args.monitor_action == "status":
        store = SQLiteMonitorStore()
        print("Accounts: " + str(len(accounts)))
        for account in accounts:
            previous = store.previous.get(account.account_id)
            print(account.account_id + " · " + account.label + " · previous=" + ("yes" if previous else "no"))
        print("Sent cadence keys: " + str(len([key for key in store.sent.keys() if str(key).startswith("cadence:")])))
        return 0

    runner = build_monitor_runner(accounts)
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
    store = SQLiteModelReviewJobStore()
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
    store = SQLiteNotificationJobStore()
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


MASKED_RUNTIME_SETTING_KEYS = set(SECRET_SETTING_KEYS) | {"tossAccountSeq", "telegramChatId"}


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
    store = SQLiteAppStore()
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


def handoff_command(args) -> int:
    if args.handoff_action != "notify":
        return 1
    registry = AccountRegistry()
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Digiter Twin Python service")
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
    return parser


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)
