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
    names, source_rows, effects = [], [], []
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
        subject = "‘" + " · ".join(topics) + "’" if topics else "이전에 알린 가설"
        for issue in row.get("dataIssues") or []:
            label = issue["label"]
            age, maximum = numeric(issue.get("ageMinutes")), numeric(issue.get("maxAgeMinutes"))
            if row.get("changeKind") == "data-restored":
                effects.append(label + " 자료를 다시 사용할 수 있어 " + subject + "의 근거를 확인할 수 있습니다.")
            else:
                cause = ("확인된 " + label + " 자료는 " + decimal(age) + "분 전 기준으로, 허용 경과 시간 " + decimal(maximum) + "분을 넘었습니다. "
                         if age is not None and maximum is not None and maximum > 0 and age > maximum
                         else "유효한 " + label + " 자료를 확인하지 못했습니다. ")
                effects.append(cause + subject + "의 근거가 지금도 유지되는지 확인할 수 없습니다.")
                if issue.get("ruleLabel") and issue["ruleLabel"] != issue.get("ruleId") and issue.get("reason"):
                    effects.append("해당 규칙: " + issue["ruleLabel"] + " — " + issue["reason"])
            parts = [label + " 원천 기준 " + clock(issue.get("sourceAsOf")), "유효성 검사 " + clock(issue.get("checkedAt") or row.get("occurredAt"))]
            if age is not None and age >= 0:
                parts.append("원천 관측 후 " + decimal(age) + "분 경과")
            if maximum is not None and maximum > 0:
                parts.append("허용 경과 시간 " + decimal(maximum) + "분")
            if issue.get("source"):
                parts.append("출처 " + issue["source"])
            if issue.get("nextCheck"):
                parts.append("재확인: " + issue["nextCheck"])
            source_rows.append(" · ".join(parts))
    effects = list(dict.fromkeys(effects))
    parent = (packet.get("followUp") or {}).get("parentJobId") or ""
    reference = ["원본 알림 " + notification_debug_number(parent)] if parent else []
    if names:
        reference.append("대상 가설: " + " · ".join(dict.fromkeys(names)))
    interpretation = ("새 자료의 수신·사용 가능 여부를 확인했습니다. 이번 확인에 가설의 새로운 강화·약화 판정은 없습니다."
                      if restored else "앞선 알림은 당시 확인한 자료에 따른 설명입니다. 이후의 근거 유지 여부는 새 자료가 들어와야 확인할 수 있습니다.")
    return {"lead": " ".join(effects[:3]), "compactFollowUp": True, "sections": [
        ("previous-alert", "어떤 알림의 후속 안내인가요", reference),
        ("delivery-cause", "이번 알림이 온 이유 · 원천 자료", list(dict.fromkeys(source_rows))),
        ("recovered-values", "새 자료에서 다시 확인한 수치", recovered_value_rows(packet)),
        ("impact", "앞선 설명을 어떻게 읽어야 하나요", [interpretation]),
        ("next-update", "다음 확인", ["새 자료에서 가설의 근거가 달라지면 해당 수치와 기준을 비교해 알려드립니다."
          if restored else "필요한 원천 자료가 갱신되고 사용 기준을 충족하면 다시 확인합니다. 같은 중단 상태는 반복해서 알리지 않습니다."]),
    ]}
