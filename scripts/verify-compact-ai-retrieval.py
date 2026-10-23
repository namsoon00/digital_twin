"""No-send comparison of privately captured retrieval inputs (JSON or gzip)."""
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python_service"))
from digital_twin.modules.ai_orchestration.domain import compact_retrieval, purpose_retrieval
from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input


def bytes_of(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


def verify(path):
    data = Path(path).read_bytes()
    rows = json.loads(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)
    comparisons = []
    for row in rows:
        old = row["envelope"]
        validate_execution_input(old)
        if old["promptVersion"] not in purpose_retrieval.SUPPORTED_VERSIONS:
            continue
        new = compact_retrieval.freeze(old["current"], old["retrievalContext"], old["promptBudgetBytes"])
        validate_execution_input(new)
        if new["current"] != old["current"] or any(new["retrievalContext"].get(key) != value
                for key, value in old["retrievalContext"].items()):
            raise ValueError("immutable source or read history changed")
        comparisons.append({"oldPromptBytes": len(old["prompt"].encode()),
                            "newPromptBytes": len(new["prompt"].encode()),
                            "oldSchemaBytes": bytes_of(old["outputSchema"]),
                            "newSchemaBytes": bytes_of(new["outputSchema"])})
    totals = {key: sum(row[key] for row in comparisons) for key in
              ("oldPromptBytes", "newPromptBytes", "oldSchemaBytes", "newSchemaBytes")}
    old_total = totals["oldPromptBytes"] + totals["oldSchemaBytes"]
    new_total = totals["newPromptBytes"] + totals["newSchemaBytes"]
    return {"samples": len(comparisons), **totals,
            "promptReductionPct": round(100 * (1-totals["newPromptBytes"]/totals["oldPromptBytes"]), 2) if comparisons else None,
            "promptAndSchemaReductionPct": round(100*(1-new_total/old_total), 2) if comparisons else None,
            "sourceAndHistoryPreserved": True, "newReportedTokens": None,
            "modelCalls": 0, "databaseWrites": 0, "notificationSends": 0, "comparisons": comparisons}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify-compact-ai-retrieval.py /private/captured-inputs.json.gz")
    print(json.dumps(verify(sys.argv[1]), ensure_ascii=False, indent=2))
