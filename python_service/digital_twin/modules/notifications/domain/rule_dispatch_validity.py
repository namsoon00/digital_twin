"""Recheck the frozen evidence used by a rule notice, without changing its judgment."""

from datetime import datetime, timezone
import math

from digital_twin.modules.market_data.contracts import max_age_minutes_for_message_type


VERSION = "rule-notice-dispatch-validity-v3"


def rule_event_proof(event, current):
    """Check native proof closure without inventing a missing rule or clock."""
    if event.get("outcome"):
        return {"allowed": True, "checks": [], "reasonCode": "", "reason": ""}
    rules = {r["id"]: r for r in current.get("rules", [])}
    ids = event.get("sourceRuleIds") or []
    failed = []
    if not ids:
        failed.append({"status": "missing-proof", "reasonCode": "rule_dispatch_proof_missing"})
    for rid in ids:
        rule = rules.get(rid) or {}
        conditions = rule.get("conditions") or []
        if not rule.get("nativeProof") or not conditions:
            failed.append({"ruleId": rid, "status": "missing-proof", "reasonCode": "rule_dispatch_proof_missing"})
            continue
        by_id = {c.get("conditionId"): c for c in conditions}
        for group in conditions:
            if group.get("kind") != "any-condition-group":
                continue
            members = set(group.get("matchedConditionIds") or [])
            minimum = group.get("minimumCount")
            if (group.get("matchedByTypeDB") is not True or group.get("detailDeferred") is not False
                    or type(minimum) is not int or minimum < 1 or len(members) < minimum
                    or any(by_id.get(cid, {}).get("matchedByTypeDB") is not True
                           or by_id[cid].get("role") not in {"any", "optional"} for cid in members)):
                failed.append({"ruleId": rid, "conditionId": group.get("conditionId"),
                    "status": "missing-group-proof", "reasonCode": "rule_dispatch_group_proof_missing"})
    comparisons = event.get('ruleComparisons') or []
    # An empty-baseline migration/identity change cannot prove that an
    # actively matching premise was invalidated. Preserve it as an audit
    # conflict, without suppressing independent, well-grounded events.
    if (event.get('changeKind') == 'invalidated' and not event.get('evidenceChanges') and comparisons
            and all(c.get('ruleId') in ids and (c.get('current') or {}).get('contractMatched') is True
                    and not c.get('previous') for c in comparisons)):
        failed.append({'status': 'transition-proof-conflict', 'reasonCode': 'rule_dispatch_transition_conflict'})
    return {"allowed": not failed, "checks": failed, "reasonCode": failed[0]["reasonCode"] if failed else "",
            "reason": ('현재도 성립한 근거와 가설 해제 기록이 일치하지 않아 이 변화는 상세 이력에 남깁니다.'
                       if any(c['reasonCode'] == 'rule_dispatch_transition_conflict' for c in failed) else
                       "규칙이 성립한 개별 조건의 원본 근거가 보존되지 않아 이 변화는 상세 이력에 남깁니다.") if failed else ""}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def failure_description(check):
    """Explain the recorded failure without treating every rejection as age."""
    code = check.get("reasonCode")
    if code == "rule_dispatch_transition_conflict":
        return "현재도 성립한 규칙 근거와 해제 기록이 서로 달라 변화 내용을 확인해야 합니다."
    if code in {"rule_dispatch_proof_missing", "rule_dispatch_group_proof_missing"}:
        return "규칙이 성립한 개별 조건의 원본 근거를 확인할 수 없습니다."
    subject = str(check.get("source") or "규칙 근거") + " 자료"
    kind = check.get("failureKind")
    if kind == "missing-source-clock":
        return subject + "의 원천 기준 시각을 확인할 수 없습니다."
    if kind == "future-source-clock":
        return subject + "의 원천 기준 시각이 발송 확인 시각보다 뒤여서 사용할 수 없습니다."
    if kind == "invalid-validity-contract":
        return subject + "의 허용 경과 시간 또는 유효 종료 시각이 올바르게 기록되지 않았습니다."
    if kind == "source-age-exceeded":
        return (subject + "의 원천 확인 후 " + str(check["ageMinutes"]) + "분이 지나 "
                + str(check["maxAgeMinutes"]) + "분의 허용 시간을 초과했습니다.")
    if kind == "validity-ended":
        return subject + "의 유효 종료 시각이 지났습니다."
    if kind == "quality-rejected":
        return subject + "가 분석에 사용할 수 없는 상태로 기록됐습니다." + (
            " 사유: " + str(check["freshnessGateReason"]) if check.get("freshnessGateReason") else
            " 원천 품질 판정 사유를 확인해야 합니다.")
    return subject + "의 신선도 상태가 사용 가능한 것으로 확인되지 않았습니다."


def rule_dispatch_validity(packet, settings=None, now=None):
    """Outcome/source-status replies retain their historical observation clock.

    Only the exact rules behind a new evidence/policy notice are required.
    An unrelated stale quote cannot expire a filing or a static policy fact.
    Fetching the same quote again never renews a price premise.
    """
    current = packet.get("current") or {}
    clock = now or datetime.now(timezone.utc)
    clock = clock.replace(tzinfo=timezone.utc) if clock.tzinfo is None else clock.astimezone(timezone.utc)
    events = [e for e in packet.get("customerEvents", []) if e.get("eligible") and not e.get("outcome")]
    rule_ids = sorted({rid for event in events for rid in event.get("sourceRuleIds", [])})
    rules = {r["id"]: r for r in current.get("rules", [])}
    checks = [check for event in events for check in rule_event_proof(event, current)["checks"]]
    for rid in rule_ids:
        rule = rules.get(rid) or {}
        conditions = rule.get("conditions") or []
        if not rule.get("nativeProof") or not conditions:
            continue
        for condition in conditions:
            if condition.get("kind") == "any-condition-group":
                continue  # Its measured members, not the structural marker, own validity.
            row = {"ruleId": rid, "conditionId": condition.get("conditionId"), "field": condition.get("field"),
                   "observedAt": condition.get("observedAt"), "source": condition.get("source"),
                   "freshnessStatus": condition.get("freshnessStatus"),
                   "judgementEvidenceUsable": condition.get("judgementEvidenceUsable"),
                   "freshnessGateReason": condition.get("freshnessGateReason")}
            static = condition.get("freshnessRequired") is False or condition.get("freshnessStatus") == "not-applicable"
            if static and condition.get("judgementEvidenceUsable") is not False:
                checks.append({**row, "status": "not-applicable"})
                continue
            observed = timestamp(condition.get("observedAt"))
            try:
                raw_maximum = condition.get("maxAgeMinutes")
                maximum = float(max_age_minutes_for_message_type("investmentInsight", settings or {})
                                if raw_maximum in (None, "") else raw_maximum)
            except (ValueError, TypeError):
                maximum = 0
            age = (clock - observed).total_seconds() / 60 if observed else None
            until = timestamp(condition.get("validUntil"))
            invalid = (condition.get("judgementEvidenceUsable") is False
                       or condition.get("freshnessStatus") not in {"fresh", "live", "current", "available"}
                       or age is None or age < -1 or not math.isfinite(maximum) or maximum <= 0
                       or bool(condition.get("validUntil") and until is None))
            expired = not invalid and (age > maximum or (until is not None and clock > until))
            failure_kind = (
                "quality-rejected" if static and condition.get("judgementEvidenceUsable") is False else
                "missing-source-clock" if age is None else
                "future-source-clock" if age < -1 else
                "invalid-validity-contract" if not math.isfinite(maximum) or maximum <= 0
                    or bool(condition.get("validUntil") and until is None) else
                "source-age-exceeded" if age > maximum else
                "validity-ended" if until is not None and clock > until else
                "quality-rejected" if condition.get("judgementEvidenceUsable") is False else
                "freshness-unconfirmed" if invalid else ""
            )
            checks.append({**row, "status": "unusable" if invalid else "expired" if expired else "fresh",
                           "ageMinutes": round(age, 2) if age is not None else None, "maxAgeMinutes": maximum,
                           "failureKind": failure_kind,
                           "reasonCode": "rule_evidence_unavailable_at_dispatch" if invalid else
                                         "rule_evidence_expired_at_dispatch" if expired else ""})
    failed = [c for c in checks if c["status"] not in {"fresh", "not-applicable"}]
    for check in failed:
        check["reason"] = failure_description(check)
    return {"version": VERSION, "applies": bool(events), "allowed": not failed,
            "checkedAt": clock.isoformat().replace("+00:00", "Z"),
            "reasonCode": failed[0]["reasonCode"] if failed else "",
            "reason": (failed[0]["reason"]
                       if failed else "알림에 사용한 규칙 근거의 원천 시각과 유효기간을 확인했습니다."),
            "checks": checks}
