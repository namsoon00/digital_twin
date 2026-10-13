"""Receipt-backed follow-ups; delivery policy, never investment inference."""

from copy import deepcopy
from datetime import datetime


VERSION = "relation-follow-up-v1"
SOURCE_LABELS = {"quote": "시세", "flow": "체결·수급", "financial": "재무", "financials": "재무",
                 "news": "뉴스", "disclosure": "공시", "macro": "경제 지표"}


def mapping(value):
    return value if isinstance(value, dict) else {}


def source_age(source_at, checked_at):
    try:
        source = datetime.fromisoformat(str(source_at).replace("Z", "+00:00"))
        checked = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
        if source.tzinfo and checked.tzinfo and checked >= source:
            return (checked - source).total_seconds() / 60
    except (ValueError, TypeError):
        pass
    return None


def source_issues(relation, transition):
    """Retain only the affected hypothesis's authored source requirements."""
    snapshot = mapping(mapping(transition.get("record")).get("snapshot"))
    refresh = mapping(snapshot.get("judgementRefresh"))
    checks = [row for row in refresh.get("rules", []) if isinstance(row, dict)]
    domains = set(mapping(snapshot.get("policy")).get("requiredFreshnessDomains") or [])
    domains.update(row.get("domain") for row in checks)
    rule_ids = set(snapshot.get("sourceRuleIds") or transition.get("sourceRuleIds") or [])
    for identity in rule_ids:
        signal = str(mapping(mapping(relation.get("modelEvidenceObservations")).get(identity)).get("signalType") or "")
        if signal.startswith("price-"):
            domains.add("quote")
        elif signal.startswith("flow-"):
            domains.add("flow")
    restored = mapping(transition.get("dataAvailabilityChange")).get("kind") == "data-restored"
    availability = mapping(snapshot.get("dataAvailability"))
    if availability.get("version") == "hypothesis-data-readiness-v1" and not restored:
        # New packets preserve the actual failing input. A fresh quote beside
        # missing model proof is not a quote interruption, nor a flow outage.
        return [{**deepcopy(row), "label": SOURCE_LABELS[row["domain"]],
                 "ageMinutes": source_age(row.get("sourceAsOf"), transition.get("occurredAt")),
                 "source": str(row.get("source") or "").split(" /api/")[0]}
                for row in availability.get("issues") or []
                if isinstance(row, dict) and row.get("sourceProblem") is True
                and row.get("domain") in SOURCE_LABELS
                and (not rule_ids or row.get("ruleId") in rule_ids)]
    result = []
    for domain in sorted(value for value in domains if value in SOURCE_LABELS):
        profile = mapping(mapping(relation.get("observationProfiles")).get(domain))
        check = next((row for row in checks if row.get("domain") == domain), {})
        # A failed model input can coexist with a perfectly current quote.
        # The generic refresh status alone cannot prove a feed interruption.
        unavailable = (profile.get("judgementEvidenceUsable") is False
                       or profile.get("freshnessStatus") in {"stale", "expired", "unavailable"})
        if (not profile and not check) or (not restored and not unavailable):
            continue
        if restored and (unavailable or profile.get("judgementEvidenceUsable") is not True):
            continue
        source_at = profile.get("sourceAsOf") or check.get("sourceAsOf") or ""
        result.append({"domain": domain, "label": SOURCE_LABELS[domain],
                       "sourceAsOf": source_at,
                       "source": str(profile.get("observationSource") or "").split(" /api/")[0],
                       "freshnessStatus": profile.get("freshnessStatus") or "unavailable",
                       "ageMinutes": source_age(source_at, transition.get("occurredAt")), "maxAgeMinutes": profile.get("maxAgeMinutes"),
                       "marketSessionStatus": profile.get("marketSessionStatus") or check.get("marketSessionStatus") or "unknown"})
    return result


def delivered_continuity(previous):
    """Only a successful receipt can seed a hypothesis conversation."""
    if not previous.get("deliveredAt") or not previous.get("receiptJobId"):
        return {}
    if previous.get("followUpVersion") == VERSION:
        return deepcopy(mapping(previous.get("hypothesisContinuity")))
    # Upgrade retained pre-policy receipts conservatively. An old data-status
    # notice is not proof that its hypothesis was ever explained to the user.
    result = {}
    for row in previous.get("transitions") or []:
        if (not row.get("lifecycleKey") or row.get("changeCategory") == "data-availability"
                or row.get("currentState") not in {"observed", "strengthened", "weakened"}):
            continue
        names = matching_hypotheses(previous.get("hypotheses") or [], row)
        result[row["lifecycleKey"]] = {
            "rootJobId": previous["receiptJobId"], "lastJobId": previous["receiptJobId"],
            "lastState": row.get("currentState"), "unavailabilityNotified": False,
            "hypotheses": compact_names(names),
        }
    return result


def compact_names(hypotheses):
    return [{key: deepcopy(h.get(key)) for key in ("id", "label", "claim", "ruleIds", "expectedOutcome")}
            for h in hypotheses[:3]]


def matching_hypotheses(hypotheses, transition):
    exact = [h for h in hypotheses if h.get("id") in (transition.get("hypothesisIds") or [])]
    return exact or [h for h in hypotheses if set(h.get("ruleIds") or []) & set(transition.get("sourceRuleIds") or [])]


def follow_up_policy(transitions, previous, *, require_receipt):
    history = delivered_continuity(previous)
    admitted, excluded = [], []
    for raw in transitions:
        row = deepcopy(raw)
        anchor = history.get(row.get("lifecycleKey")) or {}
        data = row.get("changeCategory") == "data-availability"
        reason = ""
        # A timer expiring without a changed premise is not a customer event.
        if row.get("currentState") == "expired" and str(row.get("reason") or "").startswith("RuleBox 유효기간 "):
            reason = "새 근거 변화 없이 확인 기간만 지나 웹 이력에 저장합니다."
        elif data:
            issues = row.get("dataIssues") or []
            if not issues:
                detail = mapping(row.get("judgementReadiness")).get("reason")
                reason = ("판단 보류: " + detail + " 웹 이력에 저장합니다." if detail else
                          "어떤 원천 자료를 확인하지 못했는지 특정할 수 없어 웹 이력에 저장합니다.")
            elif all(issue.get("marketSessionStatus") == "closed" for issue in issues):
                reason = "휴장에 따른 자료 갱신 대기는 알리지 않습니다."
            elif require_receipt:
                if not anchor.get("rootJobId"):
                    reason = "이 가설을 실제 전달한 원본 알림이 없어 자료 안내를 보내지 않습니다."
                elif row.get("changeKind") == "data-restored" and not anchor.get("unavailabilityNotified"):
                    reason = "자료 중단을 실제 알린 적이 없어 복구 답글을 보내지 않습니다."
                elif row.get("changeKind") == "data-unavailable" and anchor.get("unavailabilityNotified"):
                    reason = "이미 안내한 자료 중단이 계속돼 답글을 반복하지 않습니다."
                elif row.get("changeKind") == "data-unavailable" and anchor.get("lastState") in {"invalidated", "expired"}:
                    reason = "이미 종료한 가설의 자료 중단은 웹 이력에 저장합니다."
        if reason:
            excluded.append({"transitionId": row.get("transitionId"), "reason": reason})
        else:
            if anchor.get("rootJobId"):
                row["parentJobId"] = anchor["rootJobId"]
            admitted.append(row)
    parents = {row.get("parentJobId") for row in admitted}
    # Do not attach several unrelated hypotheses to an arbitrary original.
    if len(parents) > 1 and any(row.get("changeCategory") == "data-availability" for row in admitted):
        for row in admitted[:]:
            if row.get("changeCategory") == "data-availability":
                admitted.remove(row)
                excluded.append({"transitionId": row.get("transitionId"), "reason": "서로 다른 원본의 자료 안내는 웹 이력에 저장합니다."})
    parents = {row.get("parentJobId") for row in admitted}
    parent = next(iter(parents)) if len(parents) == 1 and None not in parents else ""
    admitted.sort(key=lambda row: row.get("changeCategory") == "data-availability")
    return admitted, excluded, {"version": VERSION, "parentJobId": parent or "",
                                "mode": "reply" if parent else "new"}


def delivery_snapshot(packet, job_id, account_id):
    """Carry compact per-hypothesis receipts across unrelated subject notices."""
    current = deepcopy(packet["current"])
    previous = packet.get("previous") or {}
    same_scope = (current.get("symbol") == previous.get("symbol") and current.get("worldId")
                  and current.get("worldId") == previous.get("worldId") and previous.get("accountId") == account_id)
    history = delivered_continuity(previous) if same_scope else {}
    for row in packet.get("transitions") or []:
        identity = row.get("lifecycleKey")
        if not identity:
            continue
        prior = history.pop(identity, {})
        data = row.get("changeCategory") == "data-availability"
        if data and not prior.get("rootJobId"):
            continue  # A legacy partially delivered data notice is no hypothesis origin.
        entry = {**prior, "rootJobId": prior.get("rootJobId") or job_id, "lastJobId": job_id}
        if data:
            entry["unavailabilityNotified"] = row.get("changeKind") == "data-unavailable"
        else:
            entry.update(lastState=row.get("currentState"), unavailabilityNotified=prior.get("unavailabilityNotified", False))
            names = matching_hypotheses(current.get("hypotheses") or [], row)
            if names:
                entry["hypotheses"] = compact_names(names)
        history[identity] = entry
    # Bound the small receipt independently from large graph payload retention.
    current.update(followUpVersion=VERSION, accountId=account_id,
                   hypothesisContinuity=dict(list(history.items())[-64:]),
                   transitions=deepcopy(packet.get("transitions") or []))
    return current
