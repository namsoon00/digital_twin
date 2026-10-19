"""Retain bounded, independently reviewed answers after their agenda case closes."""
import json


def market_memory(store, account, symbol, world):
    records = store.memory(account, symbol, world, max_cases=12)
    with store.connect() as connection:
        ids = [row["case_id"] for row in connection.execute("SELECT case_id FROM ai_brain_cases FORCE INDEX (brain_subject) "
            "WHERE account_id=%s AND symbol=%s AND kind='question' AND status IN ('answered','dismissed') "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s "
            "ORDER BY updated_at DESC,case_id DESC LIMIT 4", (account, symbol, world)).fetchall()]
        if ids:
            rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE case_id IN ("
                + ",".join(["%s"] * len(ids)) + ") AND account_id=%s AND symbol=%s", (*ids, account, symbol)).fetchall()
            cases = {case["caseId"]: case for row in rows for case in [json.loads(row["payload_json"])]}
            for key in ids:
                case = cases.get(key, {})
                if (case.get("questionAssessment") or {}).get("researchReviewInputId") and case.get("worldId") == world:
                    records.append({**case, "kind": "brain-case", "reviewDue": False, "authority": "historical-work-status-only"})
    return records
