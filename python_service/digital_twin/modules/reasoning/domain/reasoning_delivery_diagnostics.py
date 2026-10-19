"""Explain observed execution and admission states without inferring an action."""


def reasoning_delivery_diagnostics(state):
    queues = state.get("queues") or {}
    delivery = queues.get("delivery") or queues.get("active") or {}
    control = state.get("control") or {}
    last = (state.get("deliveryDeployment") or {}).get("lastRun") or {}
    dispatch_outcomes = last.get("dispatchOutcomes") or []
    contract_failures = [item for item in dispatch_outcomes if item.get("status") == "typedb-notification-contract-invalid"]
    reasons = set(state.get("reasons") or [])
    checks = []

    def add(code, label, detail, status="info"):
        checks.append({"code": code, "label": label, "detail": detail, "status": status})

    pending = int(delivery.get("pendingCount") or 0)
    if state.get("status") == "unavailable" or "delivery-reasoning-worker-heartbeat-missing" in reasons:
        add("reasoning-unavailable", "추론 실행 상태 확인 필요",
            "운영 추론을 사용할 수 없거나 워커의 최근 실행 신호가 확인되지 않습니다.", "warning")
    elif delivery.get("pendingCount") is None or delivery.get("status") in {"unavailable", "error"}:
        add("reasoning-queue-unavailable", "추론 대기열 상태 미확인",
            "대기열 조회 결과가 없어 처리 지연이나 대기 작업 유무를 확인하지 못했습니다.", "warning")
    elif "reasoning-queue-critical-age" in reasons:
        age = int(delivery.get("oldestPendingAgeSeconds") or 0)
        add("reasoning-delayed", "추론 처리 지연",
            f"운영 대기 {pending}건 · 가장 오래된 요청 {age // 60}분 대기. 알림 후보 확인도 늦어질 수 있습니다.", "warning")
    elif pending:
        add("reasoning-processing", "추론 작업 대기·처리 중", f"운영 대기 {pending}건입니다.")
    else:
        add("reasoning-idle", "운영 대기 작업 없음",
            "대기열 상태입니다. 투자 신호나 발송 대상이 없다는 뜻은 아닙니다.")
    waiting = int(delivery.get("awaitingSourceCount") or 0)
    if waiting:
        add("awaiting-source", "추론 원천 자료 대기", f"원천 스냅샷을 기다리는 요청이 {waiting}건 있습니다.", "warning")
    candidate = control.get("candidateDeploymentId")
    if candidate and candidate != control.get("deliveryDeploymentId"):
        add("candidate-not-delivery", "새 규칙 검증 중 · 고객 발송 미적용",
            "후보 규칙의 추론 성공과 운영 전환은 별도입니다. 현재 발송은 운영 배포의 규칙을 사용합니다.")
    count = last.get("candidateEventCount")
    if last.get("validationOnly"):
        add("last-run-validation-only", "최근 실행은 엔진 비교 검증",
            "고객 알림과 AI 접수를 하지 않는 검증 실행입니다. 일반 알림 후보 유무와 구분합니다.")
    elif contract_failures:
        add("last-run-notification-contract-invalid", "관리 판단의 알림 연결 오류",
            "TypeDB 관리 판단은 생성됐지만 알림 근거 검증을 통과하지 못했습니다. 아래 원인별 확인이 필요합니다.", "warning")
    elif last.get("status") == "ok" and last.get("traceComplete") and count is not None:
        symbols = " · ".join(str(value) for value in last.get("symbols") or [])
        if count == 0:
            add("last-run-no-candidate", "최근 완료 추론의 알림 후보 없음",
                f"{symbols or '해당 분석 범위'}에서 알림 후보가 생성되지 않았습니다. 전체 종목의 신호 부재를 뜻하지 않습니다.")
        else:
            add("last-run-has-candidate", "최근 완료 추론에서 알림 후보 생성",
                f"{symbols or '해당 분석 범위'} · {count}건. 발송 접수와 실제 전달은 전달 진단에서 확인합니다.")
    for outcome in dispatch_outcomes:
        detail = outcome.get("reason") or outcome.get("reasonCode")
        if detail:
            invalid = outcome in contract_failures
            add("last-admission-" + str(outcome.get("status") or "unknown"),
                "관리 알림 연결 실패 사유" if invalid else "최근 알림 접수 결과 · " + str(outcome.get("status") or "확인 필요"),
                str(detail), "warning" if invalid else "info")
    return {"version": "reasoning-delivery-diagnostics-v2", "checks": checks,
            "lastInferenceAt": str(last.get("completedAt") or ""),
            "oldestPendingSourceAt": str(delivery.get("oldestPendingSourceAt") or ""),
            "oldestPendingSourceAgeSeconds": delivery.get("oldestPendingSourceAgeSeconds"),
            "receiptVerification": "not-assessed-see-notification-delivery-diagnostics"}
