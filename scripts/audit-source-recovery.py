"""Read-only aggregate audit of a private research-run JSON snapshot."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python_service"))
from digital_twin.modules.news_intelligence.contracts import source_recovery_summary
from digital_twin.modules.ai_orchestration.domain.documentary_answer import documentary_answer


def audit(runs):
    causes, answers = Counter(), Counter()
    affected, instrumented, extracts, passages = 0, 0, 0, 0
    for run in runs:
        exploration = (run.get("executedPlan") or {}).get("sourceExploration") or {}
        recovery = source_recovery_summary(exploration, run.get("providerStatuses") or [])
        causes.update(recovery["causeCounts"])
        affected += bool(recovery["issues"])
        if recovery["progress"]["measured"]:
            instrumented += 1
            extracts += recovery["progress"]["extractionCount"]
            passages += recovery["progress"]["newPassageCount"]
        for task in run.get("taskIds", []):
            answers[documentary_answer(run, task)["status"]] += 1
    return {"runs": len(runs), "runsWithRecordedIssues": affected,
            "observedCauseCounts": dict(causes), "sourceAnswerStatuses": dict(answers),
            "instrumentedRuns": instrumented,
            "extractionCount": extracts if instrumented else None,
            "distinctPassagesWithinExplorations": passages if instrumented else None,
            "qualification": "operational-audit-only; not research success or hypothesis validation"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()
    values = json.loads(args.input.read_text())
    if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
        parser.error("input must contain a JSON array of research-run receipts")
    if not 1 <= args.limit <= 1000:
        parser.error("limit must be between 1 and 1000")
    print(json.dumps(audit(values[:args.limit]), ensure_ascii=False, indent=2))
