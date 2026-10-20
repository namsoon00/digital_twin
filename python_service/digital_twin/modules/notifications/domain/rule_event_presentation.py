"""Present the purpose, evidence role and consequence of a customer event."""

from .relation_observation_language import hypothesis_words
from .observation_market_snapshot import clock, decimal


PURPOSE_LABELS = {"hypothesis-evidence": "가설 재점검", "management-policy": "관리 기준 점검",
                  "execution-constraint": "거래 제약 확인", "context-evidence": "새 근거 확인"}
ACTION_LABELS = {"BUY": "신규 매수", "ADD": "추가매수", "HOLD": "보유 유지",
                 "TRIM": "일부 축소", "SELL": "전량 매도", "AVOID": "매수 회피"}


def event_conclusion(event):
    if event.get('contextState'):
        return '시장 관측: ' + str(event.get('ruleLabel') or '새 시장 문맥') + '. 투자 행동 판단은 포함하지 않습니다.'
    if event.get("purpose") in {"management-policy", "execution-constraint"}:
        if event.get("managementResponses"):
            return "관리 규칙의 대응: " + " · ".join(r.get("label") or "" for r in event["managementResponses"]) + ". 적용 기준과 규모는 ‘결정된 관리 대응과 조정 규모’에 표시했습니다."
        actions = "·".join(ACTION_LABELS[a] for a in event.get("blockedActions", []) if a in ACTION_LABELS)
        if event.get("policyState") == "released":
            released = "·".join(ACTION_LABELS[a] for a in event.get("previousBlockedActions", []) if a in ACTION_LABELS)
            return "앞서 " + released + "를 제한했던 관리 조건이 해제됐습니다. 매수·매도가 적절하다는 판단은 별도로 확인해야 합니다."
        prior = ((event.get("decisionImpact") or {}).get("previous") or {}).get("blockedActions") or []
        if actions and set(event.get("blockedActions") or []).issubset(prior):
            return actions + " 제한은 계속 적용됩니다. 이번에는 이 제한의 관리 조건이 " + (
                "추가로 확인됐습니다." if event.get("policyState") == "applied" else "변경됐습니다.")
        return "관리 기준에 따라 " + (actions + "를 제한하는 조건이 확인됐습니다." if actions else "현재 적용되는 제약을 다시 확인합니다.")
    topic, claim = hypothesis_words(event.get("hypothesis") or {})
    name = topic if topic != "관찰 가설" else claim
    if event.get("outcome"):
        horizon = (event["outcome"].get("payload") or {}).get("horizonMinutes")
        conclusion = {"outcome-confirmed": "미리 정한 예상 결과 조건을 충족했습니다.",
                      "outcome-contradicted": "미리 정한 가설 재검토 조건을 충족했습니다.",
                      "outcome-inconclusive": "예상 결과와 가설 재검토 조건 중 어느 쪽도 확정되지 않았습니다."}[event["changeKind"]]
        return "‘" + name + "’의 " + horizon_label(horizon) + " 결과: " + conclusion
    meanings = {
        "support-added": "을 뒷받침하는 근거가 추가됐습니다.",
        "support-removed": "을 뒷받침하던 근거 일부가 해제됐습니다.",
        "counter-added": "에 반대되는 근거가 추가돼 기존 가설을 다시 점검합니다.",
        "counter-removed": "에 반대되던 근거 일부가 해제됐습니다.",
        "mixed-evidence": "의 지지·반대 근거가 함께 달라져 기존 가설을 다시 점검합니다.",
        "invalidated": "을 뒷받침하던 규칙 조건이 더 이상 성립하지 않습니다.",
        "observed": "을 살펴볼 조건이 확인됐습니다.",
        "reactivated": "을 살펴볼 조건이 다시 확인됐습니다.",
    }
    if event.get("changeKind") == "mixed-evidence":
        roles = {c.get("kind", "").split("-", 1)[0] for c in event.get("evidenceChanges") or []}
        if len(roles) == 1:
            meanings["mixed-evidence"] = "의 " + ("지지" if "support" in roles else "반대") + " 근거 구성이 달라져 기존 가설을 다시 점검합니다."
    return "‘" + name + "’" + meanings.get(event.get("changeKind"), "의 근거를 다시 점검합니다.")


def event_impact(event, brief=None):
    if event.get('contextState'):
        return '관측된 시장 상태를 안내합니다. 이 규칙은 매수·매도나 보유 유지 판단을 정하지 않습니다.'
    impact = event.get("decisionImpact") or {}
    state = impact.get("status")
    before, after = impact.get("previous") or {}, impact.get("current") or {}
    if state == "outcome-only":
        return "앞서 알린 가설의 정해진 확인 구간에 대한 결과입니다. 이 한 건으로 가설 전체가 검증됐거나 매수·매도 판단이 확정된 것은 아닙니다."
    def actions(value, key):
        return "·".join(ACTION_LABELS[a] for a in value.get(key, []) if a in ACTION_LABELS) or "없음"
    if state == "unchanged":
        return "이전 알림과 관리 제한·대응 후보는 같습니다. 이번에는 위 가설의 근거가 달라진 점을 확인합니다."
    if state == "changed":
        parts = []
        if before.get("blockedActions") != after.get("blockedActions"):
            parts.append("관리 제한: " + actions(before, "blockedActions") + " → " + actions(after, "blockedActions"))
        if before.get("candidateActions") != after.get("candidateActions"):
            removed = set(before.get("candidateActions") or []) - set(after.get("candidateActions") or [])
            unavailable = {row.get("action") for row in (brief or {}).get("unavailableCandidates", [])}
            if removed:
                names = "·".join(ACTION_LABELS[a] for a in before.get("candidateActions", []) if a in removed and a in ACTION_LABELS)
                parts.append("이전 ‘" + names + "’ 후보는 이번 판단에 사용할 수 없습니다. "
                             + ("제외 이유는 대응 판단 설명에 표시했습니다." if removed.issubset(unavailable) else "제외 사유는 이번 기록에서 확인되지 않습니다.")
                             + " 보유 유지로 바뀌었거나 위험이 해소됐다는 뜻은 아닙니다.")
                if after.get("candidateActions"):
                    parts.append("이번에 남은 검토 후보: " + actions(after, "candidateActions"))
            else:
                parts.append("검토 후보: " + actions(before, "candidateActions") + " → " + actions(after, "candidateActions"))
        if after.get("candidateActions") and before.get("qualificationPending") != after.get("qualificationPending"):
            parts.append("후보 가설의 검증 상태가 달라져 사용 가능 범위를 다시 확인합니다.")
        if event.get("managementResponseChanged"):
            parts.append("이번에는 이 규칙이 요구하는 관리 대응 또는 목표가 새로 정해졌습니다.")
        elif not parts and after.get("blockedActions"):
            parts.append("관리 제한은 계속 적용됩니다. 이번에는 제한을 뒷받침하는 규칙 조건이 바뀌었습니다.")
        return " · ".join(parts)
    if after.get("blockedActions"):
        return "현재 관리 제한: " + actions(after, "blockedActions") + ". 이전에 전달한 적용 상태와의 비교는 미확인입니다."
    return "확인된 근거를 바탕으로 이 가설의 지속 여부를 살펴볼 단계입니다."


def horizon_label(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return "저장된 확인 구간"
    return str(minutes // 1440) + "일" if minutes > 0 and minutes % 1440 == 0 else str(minutes) + "분"


def tracking_rows(events):
    result = []
    for event in events:
        check = event.get("nextCheck") or {}
        if event.get("outcome"):
            outcome = event["outcome"]
            payload = outcome.get("payload") or {}
            result.append("결과 확인 " + clock(outcome.get("observedAt")) + " · 확인 구간 " + horizon_label(payload.get("horizonMinutes")))
            if outcome.get("priceChangeFromDecisionPct") is not None:
                result.append("추적 시작 이후 주가 등락 " + decimal(outcome["priceChangeFromDecisionPct"], signed=True) + "%")
            continue
        registrations = check.get("tracking") or []
        if check.get('trackingStatus') == 'context-reevaluation':
            result.extend(check.get('nextChecks') or [check.get('condition') or '다음 원천 자료에서 같은 시장 조건이 유지되는지 확인합니다.'])
            continue
        if registrations:
            for row in registrations[:2]:
                horizons = (row.get("outcomeContract") or {}).get("outcomeHorizonMinutes") or []
                result.append("자동 결과 확인 등록: " + " · ".join(horizon_label(v) for v in horizons)
                              + " 구간 · 추적 기준 " + clock(row.get("observedFromAt"))
                              + ". 같은 가설의 확인 결과는 원본 알림에 이어서 안내합니다.")
        elif check.get("trackingStatus") == "registration-unconfirmed":
            result.append("가설의 확인 조건은 아래에 표시했습니다. 자동 결과 확인 작업의 등록 기록은 없어 완료 알림을 약속할 수 없습니다.")
        elif check.get("trackingStatus") == "rule-reevaluation":
            result.append("새 자료로 관리 조건을 다시 검사합니다. 조건 해제는 신선한 자료로 확인된 경우에만 안내합니다.")
    return list(dict.fromkeys(result))


def customer_event_summary(events, brief=None):
    conclusions = list(dict.fromkeys(event_conclusion(e) for e in events))
    impacts = list(dict.fromkeys(event_impact(e, brief) for e in events))
    purposes = list(dict.fromkeys(PURPOSE_LABELS.get(e.get("purpose"), "근거 변화 확인") for e in events))
    return {"title": " · ".join(purposes[:2]), "lead": " ".join(conclusions[:2]),
            "sections": [("notification-purpose", "이번 알림의 목적", conclusions),
                         ("decision-impact", "이전 알림과 비교한 판단", impacts),
                         ("next-check-contract", "후속 확인", tracking_rows(events))]}
