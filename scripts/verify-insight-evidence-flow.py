"""Replay captured inputs without DB writes, model calls or notifications."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python_service"))

from digital_twin.modules.decisions.domain.notification_ai_decision_brief import build_notification_ai_prompt_bundle


def verify(rows, budget):
    counts, failures, sizes = Counter(), [], []
    for index, row in enumerate(rows):
        brief = row.get("decisionBrief") or {}
        if not brief:
            failures.append({"index": index, "reason": "missing-frozen-decision-brief"})
            continue
        try:
            bundle = build_notification_ai_prompt_bundle({}, decision_brief=brief, max_prompt_bytes=budget)
            core = bundle["decisionCore"]
            current = brief.get("currentSituation") or {}
            source = (current.get("reasoningDeliveryTrigger") or {}).get("facts") or {}
            expected = source.get("confirmedSignalTransitions") or []
            if expected:
                counts["transitionCases"] += 1
                actual = ((core.get("reasoningTrigger") or {}).get("facts") or {}).get("confirmedSignalTransitions")
                if actual != expected:
                    raise ValueError("changed-transition-values")
                counts["transitionsPreserved"] += 1
            facts = current.get("relationFacts") or {}
            if facts.get("btcPrice", 0) > 0:
                counts["btcCases"] += 1
                for key in ("btcPrice", "btcChange24h", "btcChange7d"):
                    if key in facts and (core.get("facts") or {}).get(key) != facts[key]:
                        raise ValueError("missing-crypto-fact:" + key)
                counts["btcFactsPreserved"] += 1
            for evidence in (brief.get("inference") or {}).get("evidenceAssertions") or []:
                if evidence.get("kind") == "model-signal" and not evidence.get("featureSummary"):
                    counts["legacyModelFeaturesUnavailable"] += 1
            sizes.append(len(bundle["prompt"].encode("utf-8")))
            counts["replayed"] += 1
        except (ValueError, TypeError, KeyError) as error:
            failures.append({"index": index, "reason": str(error)})
    return {"mode": "frozen-input-replay", "notificationsSent": 0, "databaseWrites": 0,
        "nativeTypeDBReexecuted": False, "newAIResponsesGenerated": False,
        "inputs": len(rows), "counts": dict(counts), "maxPromptBytes": max(sizes or [0]),
        "budgetBytes": budget, "failures": failures,
        "status": "passed" if rows and not failures else "failed",
        "limitation": "Missing historical source snapshots remain unavailable; this replay does not establish investment performance."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--budget-bytes", type=int, default=48 * 1024)
    args = parser.parse_args()
    raw = args.capture.read_bytes()
    rows = json.loads(gzip.decompress(raw) if args.capture.suffix == ".gz" else raw)
    if not isinstance(rows, list) or len(rows) > 500:
        raise SystemExit("Expected at most 500 captured audit objects")
    report = verify(rows, args.budget_bytes)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "passed" else 1)
