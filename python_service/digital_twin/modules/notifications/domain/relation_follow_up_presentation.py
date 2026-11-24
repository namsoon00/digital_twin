"""Specific source interruptions and their effect on an earlier explanation."""

from digital_twin.modules.notifications.domain.observation_market_snapshot import clock, decimal, numeric, observed, stage, snapshot_facts
from digital_twin.modules.notifications.domain.alert_formatting import price_money
from digital_twin.modules.notifications.domain.relation_observation_language import hypothesis_words
from digital_twin.modules.notifications.domain.relation_follow_up import delivered_continuity, matching_hypotheses
from digital_twin.modules.notifications.domain.notifications import notification_debug_number


def data_follow_up_label(transitions):
    labels = list(dict.fromkeys(issue["label"] for row in transitions for issue in row.get("dataIssues") or []))
    restored = all(row.get("changeKind") == "data-restored" for row in transitions)
    issues = [issue for row in transitions for issue in row.get("dataIssues") or []]
    if not restored and issues and all(issue.get("freshnessStatus") in {"stale", "expired"} for issue in issues):
        return "·".join(labels[:2]) + " 자료 지연"
    return "·".join(labels[:2] or ["자료"]) + (" 확인 재개" if restored else " 확인 중단")


def recovered_value_rows(packet):
    current = packet.get("current") or {}
    facts = snapshot_facts(current)
    result = []
    for transition in packet.get("transitions") or []:
        if transition.get("changeKind") != "data-restored":
            continue
        for issue in transition.get("dataIssues") or []:
            stamp = issue.get("sourceAsOf")
            if not stamp:
                continue
            if issue.get("domain") == "quote" and stamp == current.get("observedAt"):
                price = numeric(facts.get("currentPrice"))
                if price is not None and price > 0:
                    result.append("가격 " + price_money(price, str(facts.get("currency") or ("USD" if current.get("market") == "US" else "KRW"))) + " · " + clock(stamp))
            if issue.get("domain") != "flow":
                continue
            for domain, field, label, unit in (("ccnl", "tradeStrength", "체결강도", ""),
                    ("volume", "volumeRatio", "거래량 비율", "배"),
                    ("investor", "foreignNetVolume", "외국인 순매수·매도 차이", "주"),
                    ("investor", "institutionNetVolume", "기관 순매수·매도 차이", "주")):
                source = stage(current, domain)
                fields = source.get("observedFields", source.get("fields")) or []
                participant = (source.get("participantStatus") or {}).get(field.replace("NetVolume", ""))
                unavailable = {"stale", "stale-at-dispatch", "expired", "unavailable", "missing", "unsupported", "unsupported-market", "not-yet-published"}
                if (source.get("sourceAsOf") != stamp or source.get("judgementEvidenceUsable") is False
                        or field not in fields or source.get("status") in unavailable or participant in unavailable
                        or source.get("freshnessStatus") in unavailable
                        or (field == "tradeStrength" and source.get("tradeStrengthDecisionUsable") is False)):
                    continue
                value = observed(current, field, domain)
                if value is not None:
                    result.append(label + " " + decimal(value, 0 if unit == "주" else 2, signed=unit == "주") + unit + " · " + clock(stamp))
    return list(dict.fromkeys(result))[:4]


def data_follow_up_summary(packet):
    changes = packet.get("transitions") or []
    previous = packet.get("previous") or {}
    history = delivered_continuity(previous)
    names, source_rows = [], []
    restored = all(row.get("changeKind") == "data-restored" for row in changes)
    for row in changes:
        anchor = history.get(row.get("lifecycleKey")) or {}
        hypotheses = anchor.get("hypotheses") or previous.get("hypotheses") or []
        matched = matching_hypotheses(hypotheses, row)
        # Saved continuity belongs to this exact lifecycle, unlike a subject's
        # entire current hypothesis list.
        selected = matched or anchor.get("hypotheses") or []
        words = [hypothesis_words(h) for h in selected]
        topics = list(dict.fromkeys(claim if topic == "관찰 가설" else topic for topic, claim in words))
        names.extend(topics)
        for issue in row.get("dataIssues") or []:
            label = issue["label"]
            age, maximum = numeric(issue.get("ageMinutes")), numeric(issue.get("maxAgeMinutes"))
            parts = [label + " 원천 기준 " + clock(issue.get("sourceAsOf")), "유효성 검사 " + clock(issue.get("checkedAt") or row.get("occurredAt"))]
            if age is not None and age >= 0:
                parts.append("원천 관측 후 " + decimal(age) + "분 경과")
            if maximum is not None and maximum > 0:
                parts.append("허용 경과 시간 " + decimal(maximum) + "분")
            if issue.get("source"):
                parts.append("출처 " + issue["source"])
            if issue.get("ruleLabel") and issue["ruleLabel"] != issue.get("ruleId"):
                parts.append("관련 규칙 " + issue["ruleLabel"])
            source_rows.append(" · ".join(parts))
    parent = (packet.get("followUp") or {}).get("parentJobId") or ""
    reference = ["원본 알림 " + notification_debug_number(parent)] if parent else []
    if names:
        reference.append("대상 가설: " + " · ".join(dict.fromkeys(names)))
    lead = ("새 자료로 앞선 신호를 다시 확인할 수 있습니다." if restored
            else "새 자료가 들어올 때까지 앞선 신호의 판단을 보류합니다.")
    interpretation = ("이번에는 자료가 다시 들어왔는지 확인했습니다."
                      if restored else "현재 자료로는 앞선 근거가 지금도 유지되는지 확인할 수 없습니다.")
    return {"lead": lead, "compactFollowUp": True, "sections": [
        ("previous-alert", "앞선 알림", reference),
        ("delivery-cause", "자료 기준", list(dict.fromkeys(source_rows))),
        ("recovered-values", "새 자료에서 다시 확인한 수치", recovered_value_rows(packet)),
        ("impact", "현재 판단", [interpretation]),
        ("next-update", "다음 확인", ["새 자료로 앞선 규칙 조건이 유지되는지 확인합니다."
          if restored else "다음 자료의 기준 시각이 허용 범위 안에 들어오면 다시 확인합니다."]),
    ]}
