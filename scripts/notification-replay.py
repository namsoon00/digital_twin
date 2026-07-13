#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SERVICE = ROOT / "python_service"
if str(PYTHON_SERVICE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SERVICE))

from digital_twin.application.notification_replay_service import NotificationReplayService  # noqa: E402
from digital_twin.infrastructure import operational_store as stores  # noqa: E402
from digital_twin.infrastructure.service_factory import build_notification_queue_runner  # noqa: E402
from digital_twin.infrastructure.settings import runtime_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a notification by job id or tracking number.")
    parser.add_argument("identifier", help="Notification job id or tracking number such as N-99E6BAE5")
    parser.add_argument("--direct", action="store_true", help="Deliver immediately through the notifier instead of queueing.")
    parser.add_argument("--dry-run", action="store_true", help="Render the replay message without queueing or sending.")
    parser.add_argument("--lookup-limit", type=int, default=200, help="Recent jobs to scan when resolving the identifier.")
    args = parser.parse_args()

    settings = runtime_settings()
    service = NotificationReplayService(
        queue=stores.notification_job_store(settings),
        account_repository=stores.account_registry(settings),
        runner_factory=build_notification_queue_runner,
        lookup_limit=args.lookup_limit,
    )
    result = service.replay(args.identifier, direct=args.direct, dry_run=args.dry_run)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status not in {"not-found", "failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

