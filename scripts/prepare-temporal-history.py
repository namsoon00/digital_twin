#!/usr/bin/env python3
"""Prepare bounded monitor history outside the live read path; source bytes stay intact."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python_service"))

from digital_twin.infrastructure.settings import load_local_env
from digital_twin.infrastructure.transactions.monitoring import MySQLOntologyReasoningMonitorStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--limit", type=int, default=96)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error("limit must be between 1 and 500")
    if not args.apply:
        print(json.dumps({"status": "preview", "limit": args.limit,
            "operation": "Prepare retained history only; preserve raw/replay payloads and timestamps. Use --apply to run."}))
        return
    load_local_env()
    store = MySQLOntologyReasoningMonitorStore({"_skipOperationalHistoryRetention": True})
    print(json.dumps({"status": "prepared", **store.backfill_temporal_history(args.account, args.limit)}))


if __name__ == "__main__":
    main()
