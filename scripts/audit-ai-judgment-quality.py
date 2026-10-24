#!/usr/bin/env python3
"""Explicit local read-only quality snapshot; never schedules or forces work."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python_service"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="inclusive ISO time with timezone")
    parser.add_argument("--until", default=datetime.now(timezone.utc).isoformat(), help="exclusive ISO time")
    parser.add_argument("--output", required=True, type=Path, help="private local JSON path outside the repository")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.output.expanduser().resolve()
    if destination == root or root in destination.parents:
        parser.error("keep operational reports outside the repository")
    from digital_twin.infrastructure.settings import load_local_env
    from digital_twin.infrastructure.mysql_monitoring import mysql_settings
    from digital_twin.infrastructure.ai_judgment_audit import collect
    import pymysql
    load_local_env()
    config = mysql_settings()
    if not config.get("unix_socket"):
        config.pop("unix_socket", None)
    connection = pymysql.connect(**config, connect_timeout=5, read_timeout=15, autocommit=False,
                                 cursorclass=pymysql.cursors.DictCursor)
    try:
        report = collect(connection, args.since, args.until)
    finally:
        connection.rollback()
        connection.close()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    destination.chmod(0o600)
    print(json.dumps({"output": str(destination), "since": report["since"], "until": report["until"],
        "calls": sum(r["calls"] for r in report["calls"]), "authored": sum(r["authored"] for r in report["authored"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
