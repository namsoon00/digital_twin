"""Question lineage commits with the thesis/experiment handoff, never separately."""
from copy import deepcopy
import json
from ..domain.business_research import thesis_case
from ..domain.planning import stamp


def defer_resolution(case, job, result, now):
    row = next((row for row in result.get("questionResolutions", []) if row["caseId"] == case["caseId"]), None)
    if row is None:
        return
    old = case.get("hypothesisResolution", {})
    case["hypothesisResolution"] = {**deepcopy(row), "disposition": "defer",
        "proposedDisposition": row["disposition"], "state": "quality-blocked", "at": now,
        "taskId": job["taskId"], "executionInputId": result["executionInputId"],
        "reason": "관찰 설명 검증을 통과하지 못해 가설 연결을 보류했습니다. " + row["reason"],
        **{key: old[key] for key in ("requestId", "thesisId") if old.get(key)}}


def link_resolution(connection, case, job, result, read, now):
    resolution = next((row for row in result.get("questionResolutions", []) if row["caseId"] == case["caseId"]), None)
    if resolution is None:
        return
    entry = {**deepcopy(resolution), "taskId": job["taskId"], "executionInputId": result["executionInputId"], "at": now}
    disposition = resolution["disposition"]
    if disposition == "business-thesis":
        thesis = result["businessResearch"]["theses"][resolution["targetIndex"]]
        target = thesis_case(job, result, thesis, now)["caseId"]
        persisted = read(connection, target, job["accountId"], job["symbol"])
        if not persisted or persisted["worldId"] != job["worldId"] or persisted.get("kind") != "business-thesis":
            raise ValueError("question thesis link requires a persisted scoped target")
        entry.update(thesisId=target, state="tracking", qualification="not-empirically-qualified")
    elif disposition == "experiment":
        receipt = result.get("development", {})
        if not receipt.get("requestId"):
            raise ValueError("question experiment link requires a persisted request")
        if case["caseId"] not in receipt.get("sourceQuestionIds", []):
            entry.update(state="deferred-daily-budget", qualification="not-requested")
            case.update(status="waiting", reason="같은 날 먼저 등록된 다른 질문의 실험이 있어 다음 검토에서 개발 여부를 다시 판단합니다.")
        else:
            entry.update(requestId=receipt["requestId"], requestSourceTaskId=receipt.get("sourceTaskId", ""),
                         reused=receipt.get("reused", False), state="pending", qualification="proposal-only")
            case.update(status="waiting", reason="원래 질문에 연결된 가설 개발 결과를 기다립니다.")
            case["development"] = deepcopy(receipt)
    else:
        entry["state"] = disposition
        old = case.get("hypothesisResolution", {})
        entry.update({key: old[key] for key in ("requestId", "thesisId") if old.get(key)})
        if disposition == "defer" and old.get("requestId"):
            case["status"] = "waiting"
    case["hypothesisResolution"] = entry


def refresh_development(store, subjects, reader):
    """Poll owned references, then commit changed state with revision fencing."""
    changed = 0
    for subject in subjects:
        account, symbol, world = (subject[key] for key in ("accountId", "symbol", "worldId"))
        with store.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s "
                "AND kind='question' AND status IN ('open','waiting','review-needed','blocked') "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s ORDER BY next_check_at,case_id LIMIT 12",
                (account, symbol, world)).fetchall()
        for row in rows:
            captured = json.loads(row["payload_json"])
            request_id = (captured.get("development") or {}).get("requestId")
            if not request_id:
                continue
            progress = reader(request_id, account, symbol, world)
            if progress.get("status") in {"pending", "processing"} and not progress.get("cases"):
                continue  # Scheduling alone is not a new answer to review.
            if progress == captured.get("developmentProgress"):
                continue
            with store.transaction() as connection:
                case = store.read(connection, captured["caseId"], account, symbol, lock=True)
                if not case or case["revision"] != captured["revision"]:
                    continue
                now = stamp()
                case.update(developmentProgress=deepcopy(progress), status="review-needed", nextCheckAt=now,
                            reason="연결된 가설 개발 상태가 바뀌어 원래 질문을 다시 검토합니다.")
                store.save(connection, case, request_id, "development-returned", progress)
                store.wake_observation(connection, account, symbol, world, now)
                changed += 1
    return changed
