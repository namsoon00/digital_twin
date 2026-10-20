"""Operational draft recovery events; these never authorize an investment view."""
from .planning import identity


VERSION = "ai-diagnostic-progress-v1"


def successor_key(task):
    result = task.get("result") or {}
    if task["status"] == "failed":
        return "recovery"
    if task["status"] != "completed" or result.get("status") == "retired":
        return ""
    if result.get("summary"):
        return "next"
    if result.get("status") == "unchanged":
        return "unchanged"
    if result.get("status") in {"awaiting-evidence", "unchanged-wait"}:
        return "wait"
    return ""


def progress_state(task, delivery=None, receipt=None):
    result = task.get("result") or {}
    publication = result.get("publication") or {}
    outcome = result.get("processingOutcome") or {}
    base = {"sourceTaskId": task["taskId"], "observedAt": task["updatedAt"],
            "nextCheckAt": task.get("nextCheckAt", "")}
    if receipt:
        return {**base, "sourceTaskId": receipt.get("taskId") or task["taskId"], "observedAt": receipt["deliveredAt"],
                "stage": "delivered", "terminal": True, "reasons": [],
                "deliveryJobId": receipt["jobId"], "deliveredAt": receipt["deliveredAt"]}
    if delivery and delivery.get("status") in {"failed", "suppressed", "cancelled"}:
        return {**base, "stage": "delivery-blocked", "deliveryJobId": delivery.get("jobId", ""), "reasons": [
            "후속 분석의 검증은 통과했지만 전송 과정 또는 발송 직전 조건 확인에서 보류됐습니다."]}
    if publication.get("status") == "queued":
        return {**base, "stage": "validated", "reasons": ["후속 분석의 검증을 통과했습니다. 실제 전송은 아직 확인되지 않았습니다."]}
    if publication.get("status") == "suppressed":
        return {**base, "stage": "delivery-blocked", "deliveryJobId": publication.get("jobId", ""),
                "reasons": [publication.get("reason") or "후속 분석의 알림을 발송 대기열에 등록하지 못했습니다."]}
    if task["status"] == "processing":
        return {**base, "stage": "reviewing", "reasons": ["원본 초안에 이어 자료를 다시 확인하고 있습니다. 아직 검증 결과는 확정되지 않았습니다."]}
    if task["status"] in {"pending", "failed"}:
        if not task.get("lastError"):
            return None
        return {**base, "stage": "retry", "reasons": ["분석을 완료하지 못해 재시도 일정에 따라 다시 확인합니다."],
                "nextCheckAt": task.get("nextCheckAt") or task["availableAt"]}
    if outcome.get("status") in {"data-wait", "retrieval-blocked"}:
        # Legacy captures did not record a typed retrieval blocker. Their
        # renewal receipt still identifies the same operational quote wait.
        reason_code = outcome.get("reasonCode") or (result.get("dataRecovery") or {}).get("reasonCode")
        return {**base, "stage": "waiting", "reasonCode": reason_code or "required-source",
                "resumeCondition": outcome.get("resumeCondition", "필수 자료 또는 실행 조건의 변경"),
                "reasons": list(outcome.get("requirements") or ["분석에 필요한 자료가 아직 준비되지 않았습니다."])}
    if ((result.get("quality") or {}).get("status") == "rejected"
            or (publication.get("status") == "recorded" and result.get("notification", {}).get("send") is True)):
        reasons = list((result.get("quality") or {}).get("errors") or [])
        if publication.get("reason"):
            reasons.append(publication["reason"])
        return {**base, "stage": "rejected", "reasons": list(dict.fromkeys(reasons)),
                "repair": {"status": (result.get("repair") or {}).get("status", "")},
                "validationRepair": {"status": (result.get("validationRepair") or {}).get("status", "")}}
    if result.get("status") == "retired":
        return {**base, "stage": "closed", "terminal": True, "reasons": ["현재 관찰 대상에서 제외되어 이 초안의 후속 확인을 종료했습니다."]}
    if result.get("notification", {}).get("send") is False:
        return {**base, "stage": "closed", "terminal": True, "reasons": [
            "후속 분석은 마쳤지만 새로 전달할 해석이 없어 정식 알림을 보내지 않았습니다. 원본 초안을 승인한 것은 아닙니다."]}
    return None


def progress_fingerprint(state):
    # Polling clocks, the next scheduled wake and repeated task IDs are not news.
    if state["stage"] == "waiting":
        code = state.get("reasonCode", "required-source")
        return identity("waiting", "current-quote" if code == "unusable-current-quote-clock" else code)
    return identity(state["stage"], sorted(set(state.get("reasons") or [])),
                    (state.get("repair") or {}).get("status"),
                    (state.get("validationRepair") or {}).get("status"), state.get("deliveryJobId", ""))


def should_notify(state, update):
    # Every latest state remains visible in the durable record. Intermediate
    # work is not a customer result, and repeating paraphrased gaps is noise.
    if update["stage"] in {"reviewing", "validated"}:
        return False
    if update.get("terminal"):
        return True
    return progress_fingerprint(update) not in state.get("notifiedStates", [])
