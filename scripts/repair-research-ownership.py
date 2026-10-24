"""Inspect or repair explicitly selected central source executions; default is rollback."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python_service"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    ids = list(dict.fromkeys(args.task_id))
    if len(ids) > 50 or any(len(key) != 64 or any(c not in "0123456789abcdef" for c in key) for key in ids):
        parser.error("select 1–50 explicit task IDs")
    import pymysql
    from digital_twin.infrastructure.settings import load_local_env
    from digital_twin.infrastructure.mysql_monitoring import mysql_settings
    from digital_twin.modules.ai_orchestration.infrastructure.research_execution_repair import repair_source_execution
    from digital_twin.modules.news_intelligence.infrastructure.mysql_investment_research import MySQLInvestmentResearchStore
    load_local_env()
    config = mysql_settings()
    if not config.get("unix_socket"):
        config.pop("unix_socket", None)
    db = pymysql.connect(**config, connect_timeout=5, read_timeout=20, autocommit=False,
                         cursorclass=pymysql.cursors.DictCursor)

    class Bound:
        def execute(self, statement, params=()):
            cursor = db.cursor()
            cursor.execute(statement, params)
            return cursor

    results = []
    try:
        for task_id in ids:
            try:
                db.begin()
                result = repair_source_execution(Bound(), task_id,
                    MySQLInvestmentResearchStore.read_run_for_repair, apply=args.apply)
                db.commit() if args.apply else db.rollback()
                results.append(result)
            except Exception:
                db.rollback()
                raise
    finally:
        db.close()
    print(json.dumps({"applied": args.apply, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
