"""Run the curated Python suite with storage guards and timing evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _require_test_storage() -> None:
    mysql_database = str(os.environ.get("MYSQL_DATABASE") or "")
    mysql_test_database = str(os.environ.get("MYSQL_TEST_DATABASE") or "")
    typedb_database = str(os.environ.get("TYPEDB_DATABASE") or "")
    if mysql_database != "orbit_alpha_test" or mysql_test_database != "orbit_alpha_test":
        raise RuntimeError("Python tests may only use the managed orbit_alpha_test MySQL schema")
    if typedb_database != "orbit_alpha_ontology_test":
        raise RuntimeError("Python tests may only use the managed TypeDB test database")


class ModuleTimingSuite(unittest.TestSuite):
    def __init__(self, filename: str, tests):
        super().__init__(tests)
        self.filename = filename

    def run(self, result, debug=False):
        started = time.perf_counter()
        try:
            return super().run(result, debug=debug)
        finally:
            result.module_timings.append({
                "module": self.filename,
                "durationMs": round((time.perf_counter() - started) * 1000, 3),
            })


def _load_suite(files: list[str]) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    for index, filename in enumerate(files):
        path = (ROOT / "python_service" / "tests" / filename).resolve()
        if path.parent != (ROOT / "python_service" / "tests").resolve() or not path.is_file():
            raise RuntimeError(f"Invalid test module path: {filename}")
        module_name = f"orbit_alpha_curated_test_{index}_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load test module: {filename}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        suite.addTest(ModuleTimingSuite(filename, loader.loadTestsFromModule(module)))
    return suite


class TimingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings: list[dict[str, object]] = []
        self.module_timings: list[dict[str, object]] = []
        self._started_at = 0.0

    def startTest(self, test):  # noqa: N802 - unittest API
        self._started_at = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):  # noqa: N802 - unittest API
        self.timings.append({
            "test": test.id(),
            "durationMs": round((time.perf_counter() - self._started_at) * 1000, 3),
        })
        super().stopTest(test)


def _write_report(mode: str, result: TimingResult, duration: float) -> Path:
    report_dir = ROOT / "data" / "test-results"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{mode}-latest.json"
    payload = {
        "mode": mode,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "durationSeconds": round(duration, 3),
        "testsRun": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "moduleTimings": sorted(result.module_timings, key=lambda item: item["durationMs"], reverse=True),
        "timings": sorted(result.timings, key=lambda item: item["durationMs"], reverse=True),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    _require_test_storage()
    suite = _load_suite(args.files)
    started = time.perf_counter()
    runner = unittest.TextTestRunner(verbosity=1, resultclass=TimingResult)
    result = runner.run(suite)
    duration = time.perf_counter() - started
    report_path = _write_report(args.mode, result, duration)
    slowest = sorted(result.timings, key=lambda item: item["durationMs"], reverse=True)[:10]
    slowest_modules = sorted(result.module_timings, key=lambda item: item["durationMs"], reverse=True)[:10]
    print(f"\nCurated suite: {result.testsRun} tests in {duration:.2f}s")
    print(f"Timing report: {report_path.relative_to(ROOT)}")
    print("Slowest modules:")
    for item in slowest_modules:
        print(f"  {item['durationMs']:9.3f} ms  {item['module']}")
    print("Slowest tests:")
    for item in slowest:
        print(f"  {item['durationMs']:9.3f} ms  {item['test']}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
