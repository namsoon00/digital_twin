"""Recheck the frozen evidence used by a rule notice, without changing its judgment."""

from datetime import datetime, timezone
import math

from digital_twin.modules.market_data.contracts import max_age_minutes_for_message_type


VERSION = "rule-notice-dispatch-validity-v1"


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


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
    checks = ([] if not events or rule_ids else [{"status": "missing-proof", "reasonCode": "rule_dispatch_proof_missing"}])
    for rid in rule_ids:
        rule = rules.get(rid) or {}
        conditions = rule.get("conditions") or []
        if not rule.get("nativeProof") or not conditions:
            checks.append({"ruleId": rid, "status": "missing-proof", "reasonCode": "rule_dispatch_proof_missing"})
            continue
        for condition in conditions:
            row = {"ruleId": rid, "conditionId": condition.get("conditionId"), "field": condition.get("field"),
                   "observedAt": condition.get("observedAt"), "source": condition.get("source")}
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
            checks.append({**row, "status": "unusable" if invalid else "expired" if expired else "fresh",
                           "ageMinutes": round(age, 2) if age is not None else None, "maxAgeMinutes": maximum,
                           "reasonCode": "rule_evidence_unavailable_at_dispatch" if invalid else
                                         "rule_evidence_expired_at_dispatch" if expired else ""})
    failed = [c for c in checks if c["status"] not in {"fresh", "not-applicable"}]
    return {"version": VERSION, "applies": bool(events), "allowed": not failed,
            "checkedAt": clock.isoformat().replace("+00:00", "Z"),
            "reasonCode": failed[0]["reasonCode"] if failed else "",
            "reason": ("알림에 사용한 규칙 근거의 유효기간이 지났거나 원천 시각을 확인할 수 없어 새 자료로 재분석합니다."
                       if failed else "알림에 사용한 규칙 근거의 원천 시각과 유효기간을 확인했습니다."),
            "checks": checks}
