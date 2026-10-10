"""Frozen explanations of graph-authored relation transitions, never new rules."""

from copy import deepcopy
from typing import Mapping

from digital_twin.modules.decisions.contracts import is_graph_backed_relation_context
from digital_twin.modules.model_registry.contracts import relation_lifecycle_transition_contract
from digital_twin.modules.notifications.domain.relation_observation_proof import capture_model_proof


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
        if ((item.get("currentState") or item.get("current_state")) not in {"observed", "strengthened", "weakened", "invalidated", "expired"}
                and not item.get("dataAvailabilityChange")
                and mapping(item.get("changeBasis")).get("category") != "reassessment"):
            continue
        if generation and item.get("inferenceGenerationId") and generation != item["inferenceGenerationId"]:
            continue
        contract = relation_lifecycle_transition_contract({"hypothesisLifecycle": {"transitions": [item]}})
        if not (contract.get("material") or contract.get("deliverable")):
            continue
        transition = {key: deepcopy(contract.get(key)) for key in (
            "transitionId", "lifecycleKey", "changeKind", "changeLabel", "previousState", "currentState",
            "previousStateLabel", "currentStateLabel", "occurredAt", "reason", "evidenceDelta",
            "changeCategory", "dataAvailabilityChange", "evidenceChanges", "changeBasis",
        )}
        snapshot = mapping(mapping(item.get("record")).get("snapshot"))
        # Bind each change to its own hypothesis; never attach one lifecycle
        # state to every hypothesis in a multi-hypothesis observation.
        transition["hypothesisIds"] = strings(snapshot.get("hypothesisIds") or item.get("hypothesisIds"))
        transition["sourceRuleIds"] = strings(snapshot.get("sourceRuleIds") or item.get("sourceRuleIds"))
        transitions.append(transition)
    return transitions


def relation_change_snapshot(context):
    """Capture only authored subject facts and proof links from this generation."""
    relation = relation_context(context)
    subject = mapping(relation.get("subject"))
    symbol = str(subject.get("symbol") or context.get("symbol") or "")
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
            "counterRuleIds": strings(item.get("counterRuleIds")),
            "invalidationConditions": strings(item.get("invalidationConditions")),
            "expectedOutcome": str(item.get("expectedOutcome") or mapping(item.get("claimContract")).get("expectedOutcome") or ""),
            "falsificationContract": str(item.get("falsificationContract") or mapping(item.get("claimContract")).get("falsificationContract") or ""),
            "qualification": deepcopy(mapping(item.get("qualification"))),
            "claimContract": deepcopy(mapping(item.get("claimContract"))),
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
    for item in [*rows(relation.get("referenceRules")), *rows(relation.get("activeRules")), *rows(relation.get("matchedRules")), *rows(graph.get("relations")), *rows(graph.get("traces"))]:
        identity = str(item.get("ruleId") or item.get("rule_id") or item.get("sourceRuleId") or "")
        if not identity:
            continue
        existing = rules.get(identity, {})
        conditions = {row.get("conditionId") or str(index): row for index, row in enumerate(existing.get("conditions") or [])}
        for condition in rows(item.get("matchedConditions")) + rows(item.get("conditionMatches")):
            captured = {key: deepcopy(condition.get(key)) for key in (
                "conditionId", "label", "field", "operator", "observedValue", "matched", "evidenceIds",
                "matchedByTypeDB", "observedAt", "source", "freshnessStatus", "judgementEvidenceUsable",
            ) if isinstance(condition.get(key), (str, int, float, bool, list, dict))}
            captured["expectedValue"] = deepcopy(condition.get("expectedValue", condition.get("value")))
            measured = mapping(condition.get("matchedTargetProperties"))
            captured.update(capture_model_proof(measured, symbol))
            captured["modelSignalMatched"] = bool(measured.get("contractMatched") or condition.get("matchedByModelSignalInterpretationPolicy"))
            conditions[str(condition.get("conditionId") or len(conditions))] = captured
        rules[identity] = {"id": identity, "label": existing.get("label") or str(item.get("label") or item.get("ruleLabel") or identity),
                           "matched": item.get("matched") if isinstance(item.get("matched"), bool) else existing.get("matched"),
                           "referenceOnly": bool(existing.get("referenceOnly") or item.get("referenceOnly") or item.get("reference_only")) or mapping(item.get("knowledgeBasis")).get("decisionEligibility") == "reference-only",
                           "conditions": list(conditions.values()),
                           "traceId": str(item.get("inferenceTraceId") or item.get("id") or existing.get("traceId") or ""),
                           "evidenceUsableForJudgement": item.get("evidenceUsableForJudgement", existing.get("evidenceUsableForJudgement")),
                           "freshnessGateReason": str(item.get("freshnessGateReason") or existing.get("freshnessGateReason") or ""),
                           "nextChecks": strings(item.get("nextChecks")) or existing.get("nextChecks", []),
                           "claimContract": deepcopy(mapping(item.get("claimContract"))) or existing.get("claimContract", {})}
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
    coverage = mapping(facts.get("marketSignalCoverage"))
    coverage_keys = ("status", "sourceAsOf", "fetchedAt", "observedFields", "fields", "participantStatus",
                     "measurementType", "isEstimate", "provider", "freshnessStatus", "judgementEvidenceUsable",
                     "tradeStrengthQualityState", "tradeStrengthQualityReason", "tradeStrengthDecisionUsable",
                     "tradeStrengthSampleCount", "tradeStrengthSampleState", "sourceTimestampState", "marketSession",
                     "measurementScope", "ratioBasis", "numeratorVolume", "denominatorVolume", "sampleCount",
                     "providerUpdateSlot", "nextProviderUpdateAt", "validUntil")
    price_source = mapping(coverage.get("price"))
    # Position clocks belong to the selected currentPrice. KIS coverage can
    # describe a different last-close quote retained alongside a Toss quote.
    # Keep a missing source clock missing; never borrow another feed's fetch.
    selected_quote = "sourceAsOf" in facts or "sourceFetchedAt" in facts
    observed_at = (facts.get("sourceAsOf") if selected_quote else
                   price_source.get("sourceAsOf") or facts.get("quoteUpdatedAt") or facts.get("observedAt"))
    fetched_at = facts.get("sourceFetchedAt") if selected_quote else price_source.get("fetchedAt")
    return {
        "version": VERSION, "symbol": symbol,
        "market": str(subject.get("market") or facts.get("market") or ""),
        "sourceAboxSnapshotId": str(case.get("sourceAboxSnapshotId") or graph.get("sourceAboxSnapshotId") or relation.get("sourceAboxSnapshotId") or ""),
        "inferenceGenerationId": str(case.get("inferenceGenerationId") or relation.get("inferenceGenerationId") or graph.get("inferenceGenerationId") or ""),
        "observedAt": str(observed_at or ""),
        "sourceFetchedAt": str(fetched_at or ""),
        "sourceClockOrigin": "selected-price" if selected_quote else "legacy-price-coverage",
        "capturedAt": str(source.get("generatedAt") or ""),
        "source": str(facts.get("quoteSource") or facts.get("apiSource") or ""),
        "dataState": str(mapping(relation.get("decisionState")).get("dataState") or "미기록"),
        "hypotheses": hypotheses, "rules": list(rules.values()), "facts": fact_rows,
        "marketSignalCoverage": {name: {key: deepcopy(value[key]) for key in coverage_keys if key in value}
                                 for name, value in coverage.items() if isinstance(value, Mapping)},
        "investorFlowObservedFields": strings(facts.get("investorFlowObservedFields")),
        "investorFlowParticipantStatus": deepcopy(mapping(facts.get("investorFlowParticipantStatus"))),
        "transitions": relation_change_authority(context),
        "modelEvidenceObservations": deepcopy(mapping(relation.get("modelEvidenceObservations"))),
        "dataAvailability": {row.get("lifecycleKey"): deepcopy(mapping(row.get("dataAvailability")))
                             for row in rows(mapping(relation.get("hypothesisLifecycle")).get("records")) if row.get("lifecycleKey")},
        "evidenceComparisonPartial": any(mapping(row.get("evidenceComparison")).get("status") == "partial"
                                         for row in rows(mapping(relation.get("hypothesisLifecycle")).get("records"))),
    }


def relation_change_evidence(context, baseline=None, *, require_recovery_receipt=True):
    current = relation_change_snapshot(context)
    previous = deepcopy(mapping(baseline))
    if previous.get("symbol") != current["symbol"]:
        previous = {}
    previous_ids = {row.get("transitionId") for row in rows(previous.get("transitions"))}
    transitions = [row for row in current["transitions"] if row["transitionId"] not in previous_ids]
    transitions = [row for row in transitions if row.get("changeCategory") != "data-availability"
                   or mapping(mapping(previous.get("dataAvailability")).get(row.get("lifecycleKey"))).get("state")
                   != mapping(row.get("dataAvailabilityChange")).get("currentState")]
    if require_recovery_receipt:
        transitions = [row for row in transitions if row.get("changeKind") != "data-restored"
                       or mapping(mapping(previous.get("dataAvailability")).get(row.get("lifecycleKey"))).get("state") == "unavailable"]
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
    """Render the frozen evidence through the customer presentation policy."""
    from digital_twin.modules.notifications.domain.relation_change_presentation import readable_relation_change
    return readable_relation_change(packet)
