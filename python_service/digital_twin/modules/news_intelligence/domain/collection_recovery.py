"""A previously unsupported collection route may be tried once, not new evidence."""
import hashlib
import json
import re

VERSION = "question-source-route-v1"
DART_ROUTE = "dart-original-document"


def source_route_opportunity(reference, run, enabled_routes):
    if DART_ROUTE not in enabled_routes or not re.fullmatch(r"\d{6}", reference.get("symbol", "")):
        return {}
    if (not reference.get("runId") or not reference.get("taskId")
            or any(run.get(key) != reference.get(key) for key in ("runId", "accountId", "symbol"))
            or run.get("taskIds") != [reference["taskId"]]
            or run.get("status") in {"queued", "processing", "research-cooldown"}):
        return {}
    providers = [row for row in run.get("providerStatuses", [])
                 if row.get("source") == "official-question-research"
                 and row.get("symbol") == reference["symbol"]
                 and (not row.get("taskIds") or row["taskIds"] == [reference["taskId"]])]
    # A changed release alone is not permission to retry. The actual old run
    # must prove this route was unavailable, and must not already have tried it.
    if (not any(row.get("status") == "unsupported-document-source" for row in providers)
            or any(row.get("version") == "dart-question-original-v1" for row in providers)):
        return {}
    contract = reference.get("questionContract") or {}
    if not all(isinstance(contract.get(key), str) and contract[key].strip()
               for key in ("target", "period", "answerCriterion")):
        return {}
    key = hashlib.sha256(json.dumps([VERSION, reference["accountId"], reference["symbol"],
        reference["caseId"], DART_ROUTE], ensure_ascii=False).encode()).hexdigest()
    return {"version": VERSION, "key": key, "route": DART_ROUTE,
            "sourceRunId": reference["runId"], "sourceTaskId": reference["taskId"],
            "previousStatus": "unsupported-document-source", "configurationPresent": True,
            "description": "공식 공시 목록에서 원래 보고 기간을 선택하고 DART 원문을 읽는 경로가 지원됩니다.",
            "remaining": 1, "authority": "collection-retry-only", "sourceAvailability": "unverified"}
