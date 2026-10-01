"""Frozen explanations of graph-authored relation transitions, never new rules."""

from copy import deepcopy
from typing import Mapping

from digital_twin.modules.decisions.contracts import is_graph_backed_relation_context
from digital_twin.modules.model_registry.contracts import relation_lifecycle_transition_contract


VERSION = "relation-change-evidence-v1"
STATE_LABELS = {"observed": "새로 성립", "maintained": "유지", "strengthened": "강화",
                "weakened": "약화", "invalidated": "반증·해소", "expired": "근거 만료"}
FACT_LABELS = {"currentPrice": "현재가", "profitLossRate": "수익률(%)", "tradeStrength": "체결강도",
               "volumeRatio": "거래량 비율", "timeAdjustedVolumeRatio": "시간 보정 거래량 비율",
               "ma20Distance": "20일선 이격(%)", "ma60Distance": "60일선 이격(%)",
               "bidAskImbalance": "호가 불균형(%)"}


def mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def relation_context(context):
    return mapping(context.get("ontologyRelationContext")) or mapping(mapping(context.get("metadata")).get("ontologyRelationContext"))


def rows(value):
    return [mapping(item) for item in value or [] if isinstance(item, Mapping)] if isinstance(value, (list, tuple)) else []


def strings(value):
    return list(dict.fromkeys(str(item) for item in value or [] if isinstance(item, (str, int)))) if isinstance(value, (list, tuple)) else []


def relation_change_authority(context):
    """Consume the lifecycle contract of verified RuleBox output, not tick deltas."""
    relation = relation_context(context)
    if not is_graph_backed_relation_context(relation):
        return []
    lifecycle = mapping(relation.get("hypothesisLifecycle"))
    transitions = []
    graph = mapping(relation.get("graphStoreInference"))
    generation = relation.get("inferenceGenerationId") or graph.get("inferenceGenerationId")
    case = mapping(context.get("investmentSubjectDecisionCase"))
    generations = {value for value in (relation.get("inferenceGenerationId"), graph.get("inferenceGenerationId"), case.get("inferenceGenerationId")) if value}
    aboxes = {value for value in (relation.get("sourceAboxSnapshotId"), graph.get("sourceAboxSnapshotId"), case.get("sourceAboxSnapshotId")) if value}
    if not generation or len(generations) != 1 or len(aboxes) > 1 or relation.get("generationAligned") is False:
        return []
    for item in rows(lifecycle.get("transitions")):
        if (item.get("currentState") or item.get("current_state")) not in {"observed", "strengthened", "weakened", "invalidated", "expired"}:
            continue
        if generation and item.get("inferenceGenerationId") and generation != item["inferenceGenerationId"]:
            continue
        contract = relation_lifecycle_transition_contract({"hypothesisLifecycle": {"transitions": [item]}})
        if not contract.get("material"):
            continue
        transitions.append({key: deepcopy(contract.get(key)) for key in (
            "transitionId", "lifecycleKey", "changeKind", "changeLabel", "previousState", "currentState",
            "previousStateLabel", "currentStateLabel", "occurredAt", "reason", "evidenceDelta",
        )})
    return transitions


def relation_change_snapshot(context):
    """Capture only authored subject facts and proof links from this generation."""
    relation = relation_context(context)
    subject = mapping(relation.get("subject"))
    case = mapping(context.get("investmentSubjectDecisionCase"))
    graph = mapping(relation.get("graphStoreInference"))
    facts = mapping(relation.get("facts"))
    brain = mapping(relation.get("investmentBrain"))
    hypothesis_set = mapping(brain.get("hypothesisSet")) or mapping(relation.get("hypothesisSet"))
    hypotheses = []
    for item in rows(hypothesis_set.get("hypotheses")):
        hypotheses.append({
            "id": str(item.get("hypothesisId") or item.get("hypothesis_id") or ""),
            "label": str(item.get("templateLabel") or item.get("label") or "가설"),
            "claim": str(item.get("claim") or ""), "state": str(item.get("evidenceState") or "미기록"),
            "ruleIds": strings(item.get("supportingRuleIds")),
            "evidenceIds": strings(item.get("supportingEvidenceIds")),
            "counterEvidenceIds": strings(item.get("counterEvidenceIds")),
            "invalidationConditions": strings(item.get("invalidationConditions")),
        })
    # Compact subject packets may retain only exact candidate IDs. Do not invent
    # a claim from an unrelated rule or promote a policy rule into a hypothesis.
    candidate = mapping(case.get("candidateSet")) or case
    known = {item["id"] for item in hypotheses}
    for key in ("eligibleHypothesisIds", "executionEligibleHypothesisIds", "referenceHypothesisIds"):
        for identity in strings(candidate.get(key)):
            if identity not in known:
                hypotheses.append({"id": identity, "label": "가설 설명 미보존", "state": "미기록",
                                   "claim": "", "ruleIds": [], "evidenceIds": [], "counterEvidenceIds": [], "invalidationConditions": []})
                known.add(identity)
    rules = {}
    for item in [*rows(relation.get("referenceRules")), *rows(relation.get("activeRules")), *rows(relation.get("matchedRules"))]:
        identity = str(item.get("ruleId") or item.get("rule_id") or "")
        if not identity:
            continue
        conditions = []
        for condition in rows(item.get("matchedConditions")) + rows(item.get("conditionMatches")):
            conditions.append({key: deepcopy(condition.get(key)) for key in (
                "conditionId", "label", "field", "operator", "expectedValue", "observedValue", "matched", "evidenceIds",
            ) if isinstance(condition.get(key), (str, int, float, bool, list))})
        rules[identity] = {"id": identity, "label": str(item.get("label") or item.get("ruleLabel") or identity),
                           "matched": item.get("matched") if isinstance(item.get("matched"), bool) else None,
                           "referenceOnly": bool(item.get("referenceOnly")) or mapping(item.get("knowledgeBasis")).get("decisionEligibility") == "reference-only", "conditions": conditions,
                           "traceId": str(item.get("inferenceTraceId") or "")}
    fact_rows = []
    for key, value in facts.items():
        # Structured/vendor payloads are not an ABox fact table. Never copy
        # secrets or opaque raw responses into a customer explanation.
        if any(part in key.lower() for part in ("token", "secret", "password", "credential", "account", "apikey", "api_key", "authorization", "rawresponse")):
            continue
        if value is None or not isinstance(value, (str, int, float, bool)):
            continue
        fact_rows.append({"id": key, "label": FACT_LABELS.get(key, key), "value": value})
    source = mapping(relation.get("sourceSnapshot"))
    return {
        "version": VERSION, "symbol": str(subject.get("symbol") or context.get("symbol") or ""),
        "sourceAboxSnapshotId": str(case.get("sourceAboxSnapshotId") or graph.get("sourceAboxSnapshotId") or relation.get("sourceAboxSnapshotId") or ""),
        "inferenceGenerationId": str(case.get("inferenceGenerationId") or relation.get("inferenceGenerationId") or graph.get("inferenceGenerationId") or ""),
        "observedAt": str(source.get("generatedAt") or facts.get("quoteUpdatedAt") or facts.get("observedAt") or mapping(context.get("reasoningDeliveryTrigger")).get("observedAt") or ""),
        "source": str(facts.get("quoteSource") or facts.get("apiSource") or ""),
        "dataState": str(mapping(relation.get("decisionState")).get("dataState") or "미기록"),
        "hypotheses": hypotheses, "rules": list(rules.values()), "facts": fact_rows,
        "transitions": relation_change_authority(context),
    }


def relation_change_evidence(context, baseline=None):
    current = relation_change_snapshot(context)
    previous = deepcopy(mapping(baseline))
    if previous.get("symbol") != current["symbol"]:
        previous = {}
    previous_ids = {row.get("transitionId") for row in rows(previous.get("transitions"))}
    transitions = [row for row in current["transitions"] if row["transitionId"] not in previous_ids]
    changes = {}
    for kind in ("hypotheses", "rules", "facts"):
        before = {row["id"]: row for row in rows(previous.get(kind))}
        after = {row["id"]: row for row in current[kind]}
        changes[kind] = [{"id": key, "previous": before.get(key), "current": after.get(key),
                          "change": "unknown" if not previous else "added" if key not in before else "removed" if key not in after else "changed" if {k: v for k, v in before[key].items() if k != "traceId"} != {k: v for k, v in after[key].items() if k != "traceId"} else "unchanged"}
                         for key in dict.fromkeys([*after, *before])]
    return {"version": VERSION, "authority": "typedb-hypothesis-lifecycle", "eligible": bool(transitions),
            "reason": " · ".join(str(row.get("changeLabel") or row.get("reason") or "관계 상태 변경") for row in transitions)
            if transitions else "룰박스 관계의 새 의미 변화가 없어 웹 이력에만 저장합니다.",
            "baselineAvailable": bool(previous), "baselineDeliveredAt": previous.get("deliveredAt") or "",
            "current": current, "previous": previous, "transitions": transitions, "changes": changes}


def relation_change_summary(packet):
    """Short customer explanation; the detail packet keeps every captured row."""
    current = mapping(packet.get("current"))
    transitions = rows(packet.get("transitions"))
    transition_rows = [
        (item.get("previousStateLabel") or "이전 상태 미기록") + " → "
        + str(item.get("currentStateLabel") or item.get("changeLabel") or "변경")
        for item in transitions
    ]
    hypotheses = rows(current.get("hypotheses"))
    rules = rows(current.get("rules"))
    hypothesis_rows = [str(item.get("claim") or item.get("label") or item["id"])
                       + " · " + str(STATE_LABELS.get(item.get("state"), item.get("state")) or "상태 미기록") for item in hypotheses[:3]]
    rule_rows = []
    for item in rules[:3]:
        conditions = []
        for condition in rows(item.get("conditions"))[:2]:
            conditions.append(str(condition.get("label") or FACT_LABELS.get(condition.get("field"), condition.get("field")) or "조건")
                              + " " + str(condition.get("operator") or "") + " " + str(condition.get("expectedValue", "미기록"))
                              + " / 관측 " + str(condition.get("observedValue", "미기록")))
        rule_rows.append(str(item.get("label") or item["id"]) + (" · " + "; ".join(conditions) if conditions else " · 조건값 미보존"))
    fact_changes = rows(mapping(packet.get("changes")).get("facts"))
    relevant = {condition.get("field") for rule in rules for condition in rows(rule.get("conditions"))}
    fact_changes.sort(key=lambda item: (item["id"] not in relevant, item["id"] not in FACT_LABELS, item.get("change") == "unchanged"))
    fact_rows = []
    for item in fact_changes[:3]:
        before, after = mapping(item.get("previous")), mapping(item.get("current"))
        old = str(before.get("value")) if before else "미보존" if not packet.get("baselineAvailable") else "없음"
        new = str(after.get("value")) if after else "없음"
        fact_rows.append(str(after.get("label") or before.get("label") or item["id"]) + ": " + old + " → " + new)
    limits = []
    if not packet.get("baselineAvailable"):
        limits.append("이전 발송의 상세 근거가 보존되지 않아 관측값의 전후 비교는 제한됩니다.")
    if not hypotheses:
        limits.append("이번 알림에 가설 상세가 보존되지 않았습니다. 관계 전환 기록을 기준으로 표시합니다.")
    opposing = sum(len(item.get("counterEvidenceIds") or []) for item in hypotheses)
    limits.append("반대 근거 " + str(opposing) + "건 · 상세에서 근거 식별자와 반증 조건을 확인할 수 있습니다." if opposing
                  else "이번 스냅샷에 반대 근거가 기록되지 않았습니다. 반대 근거가 없다는 뜻은 아닙니다.")
    limits.append("관계 변화 안내이며 매수·매도 판단은 아닙니다.")
    provenance = ["관측 " + str(current.get("observedAt") or "시각 미보존") + " · 출처 " + str(current.get("source") or "미보존")]
    return {"lead": " · ".join(str(STATE_LABELS.get(item.get("currentState"), item.get("changeLabel")) or "관계 상태 변경") for item in transitions[:3]) or str(packet.get("reason") or "관계 변화"),
            "sections": [
                ("change", "관계의 전후 변화", transition_rows[:3]),
                ("hypotheses", "가설", hypothesis_rows or ["가설 상세 미보존"]),
                ("rules", "판정 규칙", rule_rows or ["규칙 상세 미보존"]),
                ("facts", "관측 사실 · 이전 발송 → 이번", fact_rows or ["관측값 미보존"]),
                ("provenance", "관측 시점과 출처", provenance),
                ("limitations", "반대 근거와 한계", limits),
                ("next-update", "다음 알림", ["가설이 새로 성립하거나 근거가 강화·약화·반증·만료되면 발송 간격을 확인해 알려드립니다."]),
                ("detail", "전체 근거", ["가설 " + str(len(hypotheses)) + "개 · 규칙 " + str(len(rules)) + "개 · 관측 사실 " + str(len(current.get("facts") or [])) + "개. 전체 비교는 알림 상세에서 확인할 수 있습니다."]),
            ]}
