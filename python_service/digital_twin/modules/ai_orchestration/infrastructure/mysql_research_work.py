"""Research plans commit through the central task lease and agenda transaction."""
from copy import deepcopy
import gzip
import json

from ..domain.planning import identity, stamp
from ..domain.brain_management import case_identity, later
from ..domain import research_work as work
from ..domain import research_repeat as repeat
from ..domain.research_request import validate_research_request


def record_plan(store, connection, job, result, children):
    if result.get("status") in {"research-plan-obsolete", "research-plan-suppressed", "retired"}:
        return {"status": "obsolete"}
    row = connection.execute("SELECT i.artifact_gzip FROM ai_control_inputs i JOIN ai_control_input_calls l ON l.input_id=i.input_id "
        "JOIN ai_control_calls c ON c.call_id=l.call_id AND c.task_id=i.task_id AND c.prompt_hash=i.prompt_hash "
        "WHERE i.input_id=%s AND i.task_id=%s AND i.attempt=%s AND c.status='completed' LIMIT 1",
        (result.get("executionInputId", ""), job["taskId"], job["attempts"])).fetchone()
    if not row:
        raise ValueError("research plan requires completed frozen model input")
    envelope = json.loads(gzip.decompress(row["artifact_gzip"]))
    work.validate_input(envelope)
    if any(envelope["current"][key] != job[key] for key in ("accountId", "symbol", "worldId", "taskId")):
        raise ValueError("research plan completion scope mismatch")
    plan = work.validate_plan(work.raw_plan(result["researchPlan"]), envelope)
    if plan != result["researchPlan"]:
        raise ValueError("research plan changed after capture")
    receipt = {"status": "recorded", "caseIds": [], "deferred": []}
    now = stamp()
    for review in plan["reviews"]:
        case = store.read(connection, review["caseId"], job["accountId"], job["symbol"], lock=True)
        if (not case or case["worldId"] != job["worldId"] or case["revision"] != review["expectedRevision"]
                or not work.needs_research_plan(case)):
            receipt["deferred"].append({"caseId": review["caseId"], "reason": "case-changed-after-capture"})
            continue
        repeat.initialize(case)
        from ..domain.question_recovery import gap_opportunity
        recovery_key = gap_opportunity(case, "planned")
        if repeat.admission(case, "planned"):
            receipt["deferred"].append({"caseId": case["caseId"], "reason": "unchanged-research-material"})
            continue
        repeat.remember(case, "planned")
        if review["action"] == "merge":
            target = store.read(connection, review["duplicateOf"], job["accountId"], job["symbol"], lock=True)
            if (not target or target["worldId"] != job["worldId"] or not work.needs_research_plan(target)
                    or target.get("questionContract") != case.get("questionContract")):
                receipt["deferred"].append({"caseId": case["caseId"], "reason": "merge-target-changed"})
                case.update(nextCheckAt=later(now, 360), reason="연결할 질문의 상태가 바뀌어 다음 조사 계획에서 다시 확인합니다.")
                store.save(connection, case, job["taskId"], "research-plan-deferred", {"reason": "merge-target-changed"})
                continue
            pending = connection.execute("SELECT status FROM ai_control_tasks WHERE task_id=%s",
                                         (case.get("lastResearch", {}).get("taskId", ""),)).fetchone()
            if pending and pending["status"] in {"pending", "processing"}:
                receipt["deferred"].append({"caseId": case["caseId"], "reason": "research-in-progress"})
                case.update(nextCheckAt=later(now, 360), reason="현재 진행 중인 조사의 답변을 받은 뒤 질문 연결 여부를 확인합니다.")
                store.save(connection, case, job["taskId"], "research-plan-deferred", {"reason": "research-in-progress"})
                continue
            case.update(status="dismissed", mergedInto=target["caseId"])
            target.setdefault("mergedQuestions", []).append({"caseId": case["caseId"], "question": case["question"],
                "answerResolution": deepcopy(case.get("answerResolution", {}))})
            store.save(connection, target, job["taskId"], "question-linked", {"sourceCaseId": case["caseId"], "authority": "research-planning-only"})
        else:
            case["status"] = "blocked" if review["action"] == "blocked" else "waiting"
        if review["action"] in {"wait", "blocked"}:
            case["researchRepeat"]["waitReason"] = "planner-" + review["action"]
        previous = case.get("researchPlan") or {}
        state = identity(review["action"], review["researchRequest"], case.get("answerResolution", {}))
        repeats = int(previous.get("unchangedPlans", 0)) + 1 if previous.get("stateKey") == state else 0
        minutes = max(review["nextCheckMinutes"], min(10080, 1440 * 2 ** min(repeats, 3)))
        case.update(questionContract=deepcopy(review["contract"]), reason=review["reason"], nextCheckAt=later(now, minutes))
        case["researchPlan"] = {"version": work.VERSION, "action": review["action"], "reason": review["reason"],
            "resumeWhen": review["resumeWhen"], "missingRequirements": list(dict.fromkeys(
                (case.get("answerResolution") or {}).get("missingRequirements", [])
                + (case.get("evidenceProgress") or {}).get("missingRequirements", [])))[:12],
            "executionInputId": result["executionInputId"], "taskId": job["taskId"], "at": now,
            "stateKey": state, "unchangedPlans": repeats, "authority": "research-planning-only"}
        if recovery_key:
            case["researchPlan"]["gapRecoveryKey"] = recovery_key
        if review["action"] == "research":
            case["researchRequest"] = deepcopy(review["researchRequest"])
            basis = identity(case["caseId"], work.contract_key(review["contract"]),
                             case.get("answerResolution", {}).get("assessmentFingerprint", ""))
            store.schedule_research(connection, case, job, {"researchBasis": basis}, children, now)
            scheduled = next((child for child in children if child.get("brainCaseId") == case["caseId"]), None)
            if scheduled:
                case["researchPlan"]["scheduledTaskId"] = scheduled["taskId"]
        store.save(connection, case, job["taskId"], "research-planned", case["researchPlan"])
        receipt["caseIds"].append(case["caseId"])
    for item in plan["questions"]:
        rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s "
            "AND kind='question' AND status IN ('open','waiting','review-needed','blocked') "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s ORDER BY created_at LIMIT 12 FOR UPDATE",
            (job["accountId"], job["symbol"], job["worldId"])).fetchall()
        active = [json.loads(row["payload_json"]) for row in rows]
        key = case_identity(job["accountId"], job["symbol"], job["worldId"], "research", item["question"])
        duplicate = next((row for row in active if row["caseId"] == key or (row.get("questionContract")
                         and work.contract_key(row["questionContract"]) == work.contract_key(item["contract"]))), None)
        if duplicate or store.read(connection, key, job["accountId"], job["symbol"], lock=True):
            receipt["deferred"].append({"question": item["question"], "reason": "existing-question", "caseId": duplicate["caseId"] if duplicate else key})
            continue
        stalled = repeat.stalled_question(active, item["research"], item["contract"])
        if stalled:
            receipt["deferred"].append({"question": item["question"], "reason": "existing-source-work-stalled", "caseId": stalled})
            continue
        if len(active) >= 12:
            receipt["deferred"].append({"question": item["question"], "reason": "active-case-limit"})
            continue
        case = {**{key: job[key] for key in ("accountId", "symbol", "worldId")}, "caseId": key, "kind": "question",
            "question": item["question"], "questionContract": deepcopy(item["contract"]), "capability": "research",
            "revision": 0, "status": "open", "createdAt": now, "nextCheckAt": now, "researchAttempts": 0,
            "completionCriterion": item["contract"]["answerCriterion"], "authority": "research-only",
            "researchRequest": validate_research_request(item["research"], True, job["accountId"]),
            "origin": {"taskId": job["taskId"], "executionInputId": result["executionInputId"], "capturedAt": now,
                "summary": "부족한 자료를 확인하기 위한 조사 질문입니다.", "hypothesis": "", "counterEvidence": "",
                "quality": "research-planning-only", "sourceSnapshots": {}, "evidence": [], "researchGaps": envelope["gaps"],
                "authority": "research-planning-only"}}
        store.schedule_research(connection, case, job, {"researchBasis": identity(key, work.contract_key(item["contract"]))}, children, now)
        store.save(connection, case, job["taskId"], "created", {"executionInputId": result["executionInputId"], "authority": "research-planning-only"})
        receipt["caseIds"].append(key)
    return receipt


def reconcile_and_schedule(store, connection, subjects):
    """Idempotently reconcile existing receipts and enqueue bounded source work."""
    from .mysql_control import MySQLAIControlStore
    from .mysql_business_research import expire_business
    now = stamp()
    counts = {"answersReconciled": 0, "plansScheduled": 0, "thesesExpired": 0, "repeatPlansSuppressed": 0}
    for subject in subjects:
        scope = tuple(subject[key] for key in ("accountId", "symbol", "worldId"))
        counts["thesesExpired"] += expire_business(store, connection, *scope, now)
        rows = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s "
            "AND kind='question' AND status IN ('open','waiting','review-needed','blocked') "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s ORDER BY next_check_at,case_id LIMIT 12 FOR UPDATE", scope).fetchall()
        due = []
        for row in rows:
            case = json.loads(row["payload_json"])
            old_answer = case.get("lastResearch", {}).get("result", {}).get("documentaryAnswer")
            if old_answer and not case.get("answerResolution"):
                task_id = case["lastResearch"]["taskId"]
                receipt = connection.execute("SELECT payload_json,result_json,status FROM ai_control_tasks WHERE task_id=%s "
                    "AND account_id=%s AND symbol=%s AND capability='research'", (task_id, *scope[:2])).fetchone()
                if receipt and receipt["status"] == "completed":
                    job, result = json.loads(receipt["payload_json"]), json.loads(receipt["result_json"])
                    if job.get("brainCaseId") == case["caseId"] and job.get("worldId") == scope[2] and result.get("documentaryAnswer") == old_answer:
                        case["answerResolution"] = work.answer_resolution(result, task_id)
                        case["latestSourceAnswer"] = deepcopy(old_answer)
                        case.update(nextCheckAt=now, reason="저장된 원문 답변을 질문에 연결했습니다. 가설 판단과 구분해 확인합니다.")
                        store.save(connection, case, task_id, "answer-reconciled", case["answerResolution"])
                        counts["answersReconciled"] += 1
            from ..domain.question_recovery import answer_review_key, gap_opportunity
            review_key = answer_review_key(case)
            if review_key and (case.get("answerReviewWake") or {}).get("key") != review_key:
                updated = store.wake_observation(connection, *scope, now)
                if not updated:
                    updated = connection.execute("SELECT task_id FROM ai_control_tasks WHERE account_id=%s AND symbol=%s "
                        "AND capability='observe' AND status='pending' AND attempts=0 AND available_at<=%s "
                        "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s LIMIT 1", (*scope[:2], now, scope[2])).fetchone()
                if updated:
                    case["answerReviewWake"] = {"key": review_key, "at": now, "authority": "review-scheduling-only"}
                    store.save(connection, case, "answer-review-wake", "answer-review-scheduled", case["answerReviewWake"])
            if work.needs_research_plan(case) and (case.get("nextCheckAt", "") <= now
                    or (gap_opportunity(case, "planned") and not repeat.admission(case, "planned"))):
                before = deepcopy(case)
                store.refresh_research_material(case)
                reason = repeat.admission(case, "planned")
                if reason:
                    case["researchRepeat"]["waitReason"] = reason
                    case.update(nextCheckAt=later(now, repeat.WAIT_MINUTES), reason=repeat.WAIT_REASON)
                    store.save(connection, case, case.get("researchPlan", {}).get("taskId", "repeat-guard"),
                        "research-repeat-suppressed", {"reason": reason, "modelCalled": False})
                    counts["repeatPlansSuppressed"] += 1
                elif repeat.planning_due(case, now):
                    # Persist the exact source-content admission state before a
                    # planner is queued. The worker checks it again before AI.
                    if case != before:
                        store.save(connection, case, "research-material-check", "research-material-ready", {"modelCalled": False})
                    due.append(case)
        if not due:
            continue
        existing = connection.execute("SELECT task_id FROM ai_control_tasks WHERE account_id=%s AND symbol=%s "
            "AND capability='research-plan' AND status IN ('pending','processing') "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s LIMIT 1", scope).fetchone()
        if existing:
            connection.execute("UPDATE ai_control_tasks SET priority=GREATEST(priority,%s) WHERE task_id=%s "
                "AND status='pending' AND attempts=0", (work.PRIORITY, existing["task_id"]))
            continue
        task = {**subject, "capability": "research-plan", "priority": work.PRIORITY, "availableAt": now,
                "taskId": identity(work.VERSION, *scope, [[row["caseId"], row["revision"]] for row in due[:5]])}
        MySQLAIControlStore.insert(connection, task)
        counts["plansScheduled"] += 1
    return counts
