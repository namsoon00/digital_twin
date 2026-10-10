"""Business contracts use the existing atomic agenda/event transaction."""
from copy import deepcopy
from datetime import timedelta
import json

from ..domain.business_research import ACTIVE, clock, thesis_case


def record_business(connection, job, result, read, save, now):
    business = result.get("businessResearch") or {}
    receipt = {"created": [], "reviewed": [], "deferred": []}
    for review in business.get("reviews", []):
        case = read(connection, review["thesisId"], job["accountId"], job["symbol"], lock=True)
        if (not case or case.get("kind") != "business-thesis" or case["worldId"] != job["worldId"]
                or case["revision"] != review["expectedRevision"] or case["status"] not in ACTIVE):
            raise ValueError("business contract changed after capture")
        # Evaluations were captured before the author call and independently reviewed.
        memory = next((row for row in result["input"].get("businessThesisMemory", []) if row["caseId"] == case["caseId"]), {})
        case["observations"] = deepcopy(memory.get("observations", case.get("observations", [])))
        case["lastReview"] = {**deepcopy(review), "taskId": job["taskId"], "executionInputId": result["executionInputId"],
                              "at": now, "observations": deepcopy(case["observations"])}
        case.update(status={"retain": "tracking" if case["contract"]["checkpoints"] else "data-needed",
                            "revise": "superseded", "retire": "retired"}[review["disposition"]], reason=review["reason"],
                    nextCheckAt=(clock(now) + timedelta(days=7)).isoformat().replace("+00:00", "Z"))
        save(connection, case, job["taskId"], "business-reviewed", case["lastReview"])
        receipt["reviewed"].append(case["caseId"])
    rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s "
        "AND kind='business-thesis' AND status IN ('tracking','needs-review','data-needed') "
        "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s FOR UPDATE",
        (job["accountId"], job["symbol"], job["worldId"])).fetchall()
    active = [json.loads(row["payload_json"]) for row in rows]
    for thesis in business.get("theses", []):
        case = thesis_case(job, result, thesis, now)
        if read(connection, case["caseId"], job["accountId"], job["symbol"], lock=True):
            continue
        if len(active) >= 2:
            raise ValueError("active business contracts changed after capture")
        case["replaces"] = [row["thesisId"] for row in business.get("reviews", []) if row["disposition"] == "revise"]
        inherited = []
        for replaced_id in case["replaces"]:
            replaced = read(connection, replaced_id, job["accountId"], job["symbol"])
            inherited.extend(deepcopy(replaced.get("origin", {}).get("sourceQuestions", [])))
        sources = {row["caseId"]: row for row in inherited + case["origin"].get("sourceQuestions", [])}
        case["origin"]["sourceQuestions"] = list(sources.values())[:5]
        save(connection, case, job["taskId"], "business-registered", {"contract": deepcopy(thesis), "origin": case["origin"], "replaces": case["replaces"]})
        active.append(case)
        receipt["created"].append(case["caseId"])
    if any(row["disposition"] == "revise" for row in business.get("reviews", [])) and not receipt["created"]:
        raise ValueError("business revision requires a newly persisted replacement")
    return receipt


def business_memories(connection, account, symbol, world, now):
    rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s "
        "AND kind='business-thesis' AND status IN ('tracking','needs-review','data-needed') "
        "AND (%s IS NULL OR JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s) "
        "ORDER BY next_check_at,case_id LIMIT 2", (account, symbol, world, world)).fetchall()
    records = []
    for row in rows:
        case = json.loads(row["payload_json"])
        records.append({**{key: deepcopy(case[key]) for key in ("caseId", "accountId", "symbol", "worldId", "kind", "status",
            "revision", "contract", "createdAt", "expiresAt", "nextCheckAt", "observations")},
            "origin": {key: deepcopy(case["origin"][key]) for key in ("taskId", "executionInputId", "capturedAt", "sourceQuestions") if key in case["origin"]},
            "lastReview": {key: deepcopy(value) for key, value in case.get("lastReview", {}).items() if key != "observations"}, "reviewDue": case["nextCheckAt"] <= now,
            "authority": "historical-context-only", "qualification": "not-empirically-qualified"})
    return records
