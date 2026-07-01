import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

from .admin_preview import write_admin_preview
from .application.account_service import AccountApplicationService
from .application.model_review_service import ModelReviewScheduler
from .application.scheduler import MIN_REALTIME_INTERVAL_SECONDS, RealtimeScheduler
from .domain.accounts import AccountConfig, split_symbols
from .infrastructure.event_bus import default_event_bus
from .infrastructure.json_monitor_state import MonitorStore
from .infrastructure.model_review_queue import ModelReviewJobStore
from .infrastructure.service_factory import build_model_review_runner, build_monitor_runner
from .infrastructure.settings import runtime_settings
from .infrastructure.sqlite_accounts import AccountRegistry


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
        store = MonitorStore()
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
    if args.monitor_action == "watch":
        interval = int(os.environ.get("PYTHON_REALTIME_INTERVAL_SECONDS") or os.environ.get("REALTIME_NOTIFY_INTERVAL_SECONDS") or MIN_REALTIME_INTERVAL_SECONDS)
        RealtimeScheduler(runner, interval).run_forever()
        return 0
    return 1


def model_review_command(args) -> int:
    store = ModelReviewJobStore()
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

    admin_preview = subparsers.add_parser("admin-preview", help="Generate GitHub Pages admin preview")
    admin_preview.add_argument("--output", default="public/admin")
    admin_preview.set_defaults(func=admin_preview_command)
    return parser


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)
