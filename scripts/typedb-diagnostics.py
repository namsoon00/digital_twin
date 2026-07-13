#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SERVICE = ROOT / "python_service"
if str(PYTHON_SERVICE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SERVICE))

from digital_twin.application.ontology_diagnostics_service import OntologyDiagnosticsService  # noqa: E402
from digital_twin.infrastructure import operational_store as stores  # noqa: E402
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings  # noqa: E402
from digital_twin.infrastructure.settings import runtime_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Print TypeDB ontology and event-to-notification diagnostics.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols to filter InferenceBox rows.")
    parser.add_argument("--limit", type=int, default=80, help="Maximum InferenceBox rows per collection.")
    args = parser.parse_args()

    settings = runtime_settings()
    symbols = [item.strip() for item in str(args.symbols or "").split(",") if item.strip()]
    service = OntologyDiagnosticsService(
        ontology_repository=ontology_repository_from_settings(settings),
        settings=settings,
        event_log=stores.event_log(settings),
        notification_queue=stores.notification_job_store(settings),
    )
    print(json.dumps(service.status(symbols=symbols, limit=args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

