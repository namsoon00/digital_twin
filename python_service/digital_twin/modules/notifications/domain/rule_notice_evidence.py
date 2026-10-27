"""Preserve the cause of a policy notice through every presentation boundary.

This is a projection of frozen native decisions, never a policy evaluator.
Numbers, operators and release proof come from the same rule and condition.
"""

from copy import deepcopy

from .observation_market_snapshot import decimal, numeric, snapshot_facts
from .relation_change_presentation import FIELDS, OPERATORS, measurement


VERSION = "rule-notice-evidence-v1"
SCOPE = ("accountId", "symbol", "worldId", "sourceAboxSnapshotId", "inferenceGenerationId")


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, (list, tuple)) else []


def _value(field, value, currency):
    return decimal(value) + "%" if field in {"positionAccountWeight", "positionWeight"} else measurement(field, value, currency)


def _measurement(rule_id, condition, previous, role, currency):
    field = condition.get("field")
    actual = numeric(condition.get("observedValue"))
    target = numeric((condition.get("resolvedThreshold") or {}).get("value"))
    resolution = condition.get("resolutionMatchedByTypeDB") is True
    operator = condition.get("resolutionOperator") if resolution else condition.get("operator")
    if field not in FIELDS or actual is None or target is None or operator not in OPERATORS:
        return None
    old = [r for r in previous if r.get("field") == field and r.get("conditionId") == condition.get("conditionId")]
    prior = old[0] if len(old) == 1 else {}
    before = numeric(prior.get("observedValue"))
    old_target = numeric((prior.get("resolvedThreshold") or {}).get("value"))
    prefix = "해제 근거: " if resolution else "변경 근거: "
    text = prefix + FIELDS[field][0] + " "
    text += (_value(field, before, currency) + " → ") if before is not None and before != actual else "현재 "
    text += _value(field, actual, currency)
    text += (" / 해제 기준 " if resolution else " / 적용 기준 ") + _value(field, target, currency) + " " + OPERATORS[operator]
    if before is None:
        text += " (이전 비교값 없음)"
    if old_target is not None and old_target != target:
        text += " · 기준 변경 " + _value(field, old_target, currency) + " → " + _value(field, target, currency)
    return {"role": role, "ruleId": rule_id, "conditionId": condition.get("conditionId"), "field": field,
            "observedValue": actual, "previousValue": before, "threshold": target, "previousThreshold": old_target,
            "operator": operator, "resolution": resolution, "observedAt": condition.get("observedAt"), "text": text}


def policy_notice_evidence(packet):
    """Bind selected policy events, their measurements and remaining actions."""
    events = [e for e in _rows(packet.get("customerEvents")) if e.get("policyState")]
    if not events:
        return {}
    current = packet.get("current") or {}
    brief = current.get("decisionBrief") or {}
    management = brief.get("managementDecision") or {}
    currency = snapshot_facts(current).get("currency") or ("USD" if current.get("market") == "US" else "KRW")
    issues, causes, remaining = [], [], []
    identity = {key: current.get(key) for key in SCOPE}
    if (brief.get("authority") != "typedb-materialized-decision-effects"
            or not all(identity.values()) or any(brief.get(k) != v for k, v in identity.items())):
        issues.append("policy-evidence-scope-mismatch")
    rules = {r.get("id"): r for r in _rows(current.get("rules"))}
    for event in events:
        if any(event.get(k) != v for k, v in identity.items()):
            issues.append("policy-event-scope-mismatch")
            continue
        if not event.get("sourceRuleIds"):
            issues.append("policy-source-rule-missing")
        released = event.get("policyState") == "released"
        for rid in event.get("sourceRuleIds") or []:
            rows = []
            rule = rules.get(rid) or {}
            if not rule.get("nativeProof"):
                issues.append("policy-native-proof-missing")
                continue
            for condition in _rows(event.get("conditions")):
                if (condition.get("judgementEvidenceUsable") is False
                        or condition.get("freshnessStatus") in {"stale", "expired", "missing", "unknown"}):
                    continue
                if released:
                    if condition.get("resolutionMatchedByTypeDB") is not True:
                        continue
                elif not (condition.get("matchedByTypeDB") is True or condition.get("matched") is True):
                    continue
                row = _measurement(rid, condition, _rows(event.get("previousConditions")), "cause", currency)
                if row:
                    row.update(eventId=event.get("eventId"), policyState=event.get("policyState"))
                    if rule.get("label") and rule["label"] != rid:
                        row["text"] = rule["label"] + " · " + row["text"]
                    rows.append(row)
            responses = [r for r in _rows(management.get("resolutions" if released else "responses")) if r.get("ruleId") == rid]
            # A native resolution and its condition must describe the same fact.
            for response in responses:
                objective = response.get("objective") or {}
                matches = [r for r in rows if r["field"] == objective.get("field")]
                if not matches:
                    issues.append("policy-response-condition-missing")
                if matches and any(r["observedValue"] != numeric(response.get("observedValue"))
                                   or r["threshold"] != numeric(response.get("targetValue"))
                                   or (released and r["operator"] != objective.get("operator")) for r in matches):
                    issues.append("policy-response-condition-mismatch")
                    rows = []
            if not rows:
                issues.append("policy-cause-measurement-missing")
            causes.extend(rows)
    cause_ids = {rid for e in events for rid in e.get("sourceRuleIds") or []}
    for response in _rows(management.get("responses")):
        rid = response.get("ruleId")
        if rid in cause_ids:
            continue
        objective = response.get("objective") or {}
        field, operator = objective.get("field"), objective.get("operator")
        actual, target = numeric(response.get("observedValue")), numeric(response.get("targetValue"))
        if field not in FIELDS or actual is None or target is None or operator not in OPERATORS:
            issues.append("remaining-policy-measurement-missing")
            continue
        remaining.append({"role": "remaining", "ruleId": rid, "field": field,
            "observedValue": actual, "threshold": target, "operator": operator,
            "text": "유지 중인 조건: " + FIELDS[field][0] + " " + _value(field, actual, currency)
                    + " / 해제 기준 " + _value(field, target, currency) + " " + OPERATORS[operator]})
    measurements = causes + remaining
    if any("scope-mismatch" in issue for issue in issues):
        measurements = []
    elif issues:
        for row in measurements:
            row["text"] = row["text"].replace("해제 근거: ", "기록된 비교: ").replace("변경 근거: ", "기록된 비교: ")
    return {"version": VERSION, "sourceIdentity": identity, "status": "partial" if issues else "complete",
            "issues": sorted(set(issues)), "events": [{"eventId": e.get("eventId"), "policyState": e.get("policyState"),
                "sourceRuleIds": list(e.get("sourceRuleIds") or [])} for e in events],
            "measurements": measurements, "remainingRestrictions": deepcopy(brief.get("restrictions") or []),
            "blockedActions": list(brief.get("blockedActions") or []), "currency": currency}


def bind_policy_notice(summary, packet):
    """Direct cause rows cannot be replaced by unrelated market measurements."""
    evidence = policy_notice_evidence(packet)
    if not evidence:
        return summary
    result = deepcopy(summary)
    result["evidenceContract"] = evidence
    sections = {key: (title, list(rows)) for key, title, rows in result["sections"]}
    required = [r["text"] for r in evidence["measurements"] if r["role"] == "cause"]
    remaining = [r["text"] for r in evidence["measurements"] if r["role"] == "remaining"]
    # A simultaneous market event keeps its own proven measurements, clearly
    # separate from the policy cause. Unrelated reference groups stay in detail.
    other_events = [e for e in _rows(packet.get("customerEvents")) if not e.get("policyState")]
    if other_events:
        from .composed_rule_presentation import exact_measurements
        previous = ((packet.get("previous") or {}).get("decisionBrief") or {}).get("judgment") or {}
        required += ["함께 바뀐 근거: " + line for line in exact_measurements(
            summary.get("judgment") or {}, previous, other_events, evidence["currency"], cause_only=True)]
    # Keep every cause and remaining restriction, even when several use the
    # same field. A reference measurement is never a substitute for a cause.
    sections["decision-impact"] = ("핵심 수치", required or ["판정에 사용한 비교값과 기준이 보존되지 않아 확인이 필요합니다."])
    if remaining:
        sections["remaining-policy"] = ("계속 적용되는 조건", remaining)
    if evidence["status"] != "complete":
        result["lead"] = "관리 조건 변경의 근거를 확인해야 합니다."
        sections["notification-purpose"] = ("알림 이유", ["저장된 판정과 직접 비교 근거를 함께 확인할 수 없어 변경 내용을 확정해서 안내할 수 없습니다."])
        sections["decision-basis"] = ("함께 보면", ["자료 누락을 제한 해제나 추가매수 허용으로 해석하지 않습니다."])
        sections["next-check-contract"] = ("다음 확인", ["해당 관리 조건의 원본 비교값·기준·적용 상태를 상세 근거에서 확인합니다."])
    else:
        released = any(e["policyState"] == "released" for e in evidence["events"])
        if any(r.get("previousThreshold") is not None and r["previousThreshold"] != r["threshold"]
               for r in evidence["measurements"] if r["role"] == "cause"):
            sections["notification-purpose"] = ("알림 이유", ["설정된 관리 기준이 바뀌었습니다. 새 기준에 따른 적용 상태를 안내하며 시장 개선을 뜻하지 않습니다."])
        if released and "ADD" in evidence["blockedActions"]:
            sections["decision-basis"] = ("함께 보면", ["다른 관리 조건에 따른 추가매수 제한은 계속 적용됩니다. 이번 해제는 추가매수 허용을 뜻하지 않습니다."])
    result["sections"] = [(key, title, rows) for key, (title, rows) in sections.items()]
    return result
