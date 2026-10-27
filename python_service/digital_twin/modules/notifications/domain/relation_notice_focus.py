"""Separate selected rule transitions from unchanged supporting context."""

from copy import deepcopy


VERSION = "relation-notice-focus-v1"
CHANGES = {"support-added": "지지 근거 추가", "support-removed": "지지 근거 해제",
           "counter-added": "반대 근거 추가", "counter-removed": "반대 근거 해제",
           "mixed-evidence": "근거 구성 변경", "observed": "처음 확인", "reactivated": "다시 확인",
           "invalidated": "조건 미충족 확인", "expired": "근거 만료"}


def notice_rule_ids(event):
    """A lifecycle can contain unchanged rules; prefer the exact changed links."""
    changes = [r.get("ruleId") for r in event.get("ruleComparisons") or []]
    changes += [(r.get("evidence") or {}).get("ruleId")
                for change in event.get("evidenceChanges") or [] for r in change.get("evidence") or []]
    return list(dict.fromkeys(rid for rid in changes if rid)) or list(event.get("sourceRuleIds") or [])


def changed_rule_rows(packet):
    current = packet.get("current") or {}
    rules = {row.get("id"): row for snapshot in (packet.get("previous") or {}, current)
             for row in snapshot.get("rules") or []}
    rows, seen = [], set()
    for event in packet.get("customerEvents") or []:
        state = {"applied": "조건 적용", "updated": "적용 근거 변경", "released": "조건 해제"}.get(
            event.get("policyState"), CHANGES.get(event.get("changeKind"), "상태 변경"))
        if event.get("policyState") == "applied" and not event.get("previousPolicyState"):
            state = "적용 처음 확인"
        for rid in notice_rule_ids(event):
            own_changes = [CHANGES[change["kind"]] for change in event.get("evidenceChanges") or []
                           if change.get("kind") in CHANGES and any((r.get("evidence") or {}).get("ruleId") == rid
                               for r in change.get("evidence") or [])]
            own_state = "·".join(dict.fromkeys(own_changes)) or state
            if (rid, own_state) in seen:
                continue
            seen.add((rid, own_state))
            rule = rules.get(rid) or {}
            label = rule.get("label") or event.get("ruleLabel")
            # Unknown internal IDs remain in the audit packet, not the prose.
            if not label or label == rid:
                label = "연결된 조건"
            rows.append({"ruleId": rid, "eventId": event.get("eventId"), "state": own_state,
                         "text": str(label) + " · " + own_state})
    return rows


def focus_relation_notice(summary, packet):
    result = deepcopy(summary)
    transitions = changed_rule_rows(packet)
    evidence = result.get("evidenceContract") or {}
    # A failed scope/evidence contract cannot acquire an affirmative release
    # claim merely by adding a new presentation section.
    if evidence.get("status") == "partial":
        transitions = []
    result["focusContract"] = {"version": VERSION, "comparisonBasis": "previous-successful-relation-receipt",
                               "changedRules": transitions, "marketSnapshotRole": "reference-only"}
    sections = []
    for key, title, rows in result["sections"]:
        if key == "notification-purpose":
            sections.append((key, title, rows))
            if transitions:
                selected = [row["text"] for row in transitions[:4]]
                if len(transitions) > 4:
                    selected.append("외 " + str(len(transitions) - 4) + "개 변경 조건은 상세 근거에 표시")
                sections.append(("changed-rules", "달라진 규칙", [" / ".join(selected)]))
            continue
        if key == "decision-impact":
            title = "변경 근거 수치"
        elif key == "decision-basis":
            title = "종합 판단과 남은 제한"
        elif key == "current-snapshot":
            title = "참고 시세"
        sections.append((key, title, rows))
    # Keep a continuing restriction next to the combined judgment, before the
    # reference quote and next check.
    remaining = [section for section in sections if section[0] == "remaining-policy"]
    if remaining:
        sections = [section for section in sections if section[0] != "remaining-policy"]
        index = next((i + 1 for i, section in enumerate(sections) if section[0] == "decision-basis"), len(sections))
        sections[index:index] = remaining
    result["sections"] = sections
    return result
