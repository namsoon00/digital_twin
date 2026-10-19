"""Frozen explanations of graph-authored relation transitions, never new rules."""

from copy import deepcopy
from typing import Mapping

from digital_twin.modules.decisions.contracts import is_graph_backed_relation_context, rulebox_decision_brief, resolved_rule_threshold
from digital_twin.modules.model_registry.contracts import relation_lifecycle_transition_contract
from digital_twin.modules.notifications.domain.relation_observation_proof import capture_model_proof
from digital_twin.modules.notifications.domain.relation_follow_up import follow_up_policy, source_issues
from digital_twin.modules.notifications.domain.rule_notification_event import customer_rule_events
from digital_twin.modules.notifications.domain.rule_policy_event import policy_events
from digital_twin.modules.notifications.domain.rule_outcome_event import outcome_events
from digital_twin.modules.notifications.domain.rule_policy_lifecycle import policy_lifecycle
from digital_twin.modules.notifications.domain.rule_dispatch_validity import rule_event_proof


VERSION = "relation-change-evidence-v1"
STATE_LABELS = {"observed": "새로 성립", "maintained": "유지", "strengthened": "강화",
                "weakened": "약화", "invalidated": "반증·해소", "expired": "근거 만료"}
FACT_LABELS = {"currentPrice": "현재가", "profitLossRate": "수익률(%)", "tradeStrength": "체결강도",
               "positionAccountWeight": "계좌 전체 대비 비중(%)", "positionWeight": "보유 종목 평가액 내 비중(%)",
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
        transition["dataIssues"] = source_issues(relation, item)
        transition["judgementReadiness"] = deepcopy(mapping(snapshot.get("judgementRefresh")))
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
    native_rule_ids = {str(row.get("ruleId") or row.get("rule_id") or row.get("sourceRuleId") or "")
                       for row in [*rows(graph.get("traces")), *rows(graph.get("relations"))]}
    for item in [*rows(relation.get("referenceRules")), *rows(relation.get("activeRules")), *rows(relation.get("matchedRules")), *rows(graph.get("relations")), *rows(graph.get("traces"))]:
        identity = str(item.get("ruleId") or item.get("rule_id") or item.get("sourceRuleId") or "")
        if not identity:
            continue
        existing = rules.get(identity, {})
        conditions = {row.get("conditionId") or str(index): row for index, row in enumerate(existing.get("conditions") or [])}
        for condition in rows(item.get("matchedConditions")) + rows(item.get("conditionMatches")):
            captured = {key: deepcopy(condition.get(key)) for key in (
                "conditionId", "kind", "role", "minimumCount", "detailDeferred", "matchedConditionIds",
                "label", "field", "operator", "observedValue", "matched", "evidenceIds", "ruleConditionShape",
                "matchedByTypeDB", "observedAt", "source", "freshnessStatus", "judgementEvidenceUsable",
                "sourceFetchedAt", "freshnessRequired", "maxAgeMinutes", "validUntil", "marketSessionStatus",
                "resolutionMatchedByTypeDB", "resolutionOperator",
            ) if isinstance(condition.get(key), (str, int, float, bool, list, dict))}
            captured["expectedValue"] = deepcopy(condition.get("expectedValue", condition.get("value")))
            captured["resolvedThreshold"] = resolved_rule_threshold(captured, facts)
            measured = mapping(condition.get("matchedTargetProperties"))
            captured.update(capture_model_proof(measured, symbol))
            captured["modelSignalMatched"] = bool(measured.get("contractMatched") or condition.get("matchedByModelSignalInterpretationPolicy"))
            conditions[str(condition.get("conditionId") or len(conditions))] = captured
        rules[identity] = {"id": identity, "nativeProof": identity in native_rule_ids,
                           "label": existing.get("label") or str(item.get("label") or item.get("ruleLabel") or identity),
                           "matched": item.get("matched") if isinstance(item.get("matched"), bool) else existing.get("matched"),
                           "referenceOnly": bool(existing.get("referenceOnly") or item.get("referenceOnly") or item.get("reference_only")) or mapping(item.get("knowledgeBasis")).get("decisionEligibility") == "reference-only",
                           "conditions": list(conditions.values()),
                           "traceId": str(item.get("inferenceTraceId") or item.get("id") or existing.get("traceId") or ""),
                           "evidenceUsableForJudgement": item.get("evidenceUsableForJudgement", existing.get("evidenceUsableForJudgement")),
                           "freshnessGateReason": str(item.get("freshnessGateReason") or existing.get("freshnessGateReason") or ""),
                           "nextChecks": strings(item.get("nextChecks")) or existing.get("nextChecks", []),
                           "invalidationContract": deepcopy(mapping(item.get("ruleInvalidationContract"))) or existing.get("invalidationContract", {}),
                           "claimContract": deepcopy(mapping(item.get("claimContract"))) or existing.get("claimContract", {})}
        if item.get('managementResolution'):
            rules[identity]['managementResolution'] = deepcopy(item['managementResolution'])
    fact_rows = []
    for key, value in facts.items():
        # Structured/vendor payloads are not an ABox fact table. Never copy
        # secrets or opaque raw responses into a customer explanation.
        # This named numeric investment fact is not an account credential.
        # Losing it made readers substitute invested-only positionWeight for
        # the total-account denominator used by the native concentration rule.
        public_account_weight = key == "positionAccountWeight" and type(value) in (int, float)
        if not public_account_weight and any(part in key.lower() for part in ("token", "secret", "password", "credential", "account", "apikey", "api_key", "authorization", "rawresponse")):
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
    coverage_keys += ("amountUnit", "sourceAmountUnit", "amountNormalizationVersion")
    price_source = mapping(coverage.get("price"))
    # Position clocks belong to the selected currentPrice. KIS coverage can
    # describe a different last-close quote retained alongside a Toss quote.
    # Keep a missing source clock missing; never borrow another feed's fetch.
    selected_quote = "sourceAsOf" in facts or "sourceFetchedAt" in facts
    observed_at = (facts.get("sourceAsOf") if selected_quote else
                   price_source.get("sourceAsOf") or facts.get("quoteUpdatedAt") or facts.get("observedAt"))
    fetched_at = facts.get("sourceFetchedAt") if selected_quote else price_source.get("fetchedAt")
    world = relation.get("worldId") or graph.get("worldId") or case.get("worldId")
    identity_verified = bool(is_graph_backed_relation_context(relation) and world
        and context.get("accountId") and context.get("accountId") == case.get("accountId")
        and symbol and symbol == case.get("symbol")
        and (not graph.get("symbol") or graph["symbol"] == symbol)
        and (not context.get("investmentSubjectDecisionCaseId") or context["investmentSubjectDecisionCaseId"] == case.get("subjectCaseId"))
        and all(case.get(key) and case[key] == (relation.get(key) or graph.get(key))
                and (not graph.get(key) or graph[key] == case[key])
                for key in ("sourceAboxSnapshotId", "inferenceGenerationId"))
        and all(not source.get("accountId") or source["accountId"] == case["accountId"] for source in (relation, graph))
        and all(not source.get("worldId") or source["worldId"] == world for source in (relation, graph, case))
        and relation.get("generationAligned") is not False)
    return {
        "version": VERSION, "symbol": symbol,
        "proofIdentityVerified": identity_verified,
        "accountId": str(case.get("accountId") or context.get("accountId") or ""),
        "worldId": str(world or ""),
        "market": str(subject.get("market") or facts.get("market") or ""),
        "sourceAboxSnapshotId": str(case.get("sourceAboxSnapshotId") or graph.get("sourceAboxSnapshotId") or relation.get("sourceAboxSnapshotId") or ""),
        "inferenceGenerationId": str(case.get("inferenceGenerationId") or relation.get("inferenceGenerationId") or graph.get("inferenceGenerationId") or ""),
        "observedAt": str(observed_at or ""),
        "sourceFetchedAt": str(fetched_at or ""),
        "sourceClockOrigin": "selected-price" if selected_quote else "legacy-price-coverage",
        "capturedAt": str(source.get("generatedAt") or ""),
        "source": str(facts.get("quoteSource") or facts.get("apiSource") or ""),
        "dataState": str(mapping(relation.get("decisionState")).get("dataState") or "미기록"),
        "decisionBrief": rulebox_decision_brief(context),
        "outcomeTracking": [deepcopy(row) for row in rows(context.get("ruleObservationTracking"))
                            if row.get("version") == "rule-outcome-registration-v1"
                            and row.get("accountId") == (case.get("accountId") or context.get("accountId"))
                            and row.get("symbol") == symbol
                            and row.get("projectionAboxSnapshotId") == case.get("sourceAboxSnapshotId")
                            and row.get("projectionGenerationId") == case.get("inferenceGenerationId")],
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
    if any(previous.get(key) != current.get(key) for key in ("symbol", "accountId", "worldId")):
        previous = {}
    previous_ids = {row.get("transitionId") for row in rows(previous.get("transitions"))}
    transitions = [row for row in current["transitions"] if row["transitionId"] not in previous_ids]
    receipt_scope = bool(current.get("worldId") and previous.get("worldId") == current["worldId"]
                         and current.get("accountId") and previous.get("accountId") == current["accountId"])
    transitions, suppressed, follow_up = follow_up_policy(
        transitions, previous if receipt_scope else {}, require_receipt=require_recovery_receipt)
    if not current["proofIdentityVerified"]:
        transitions = []
        suppressed.append({"reasonCode": "rule_notice_proof_identity_mismatch",
                           "reason": "계좌·종목·규칙 원본의 연결을 확인하지 못해 상세 이력에 남깁니다."})
    events, rejected = customer_rule_events(current, previous, transitions) if current["proofIdentityVerified"] else ([], [])
    if current["proofIdentityVerified"]:
        events.extend(policy_events(current, previous))
    if receipt_scope and current["proofIdentityVerified"]:
        events.extend(outcome_events(current, previous))
    # An unavailable rule in one hypothesis must not discard an independent
    # event whose native proof is complete. Rejected events remain unconsumed
    # in receipt continuity, and can be reconsidered only with real new proof.
    for event in events:
        if not event.get("eligible") or event.get("outcome"):
            continue
        proof = rule_event_proof(event, current)
        if not proof["allowed"]:
            event.update(eligible=False, reasonCode=proof["reasonCode"])
            rejected.append({"transitionId": event.get("transitionId"), "eventId": event.get("eventId"),
                "reasonCode": proof["reasonCode"], "reason": proof["reason"], "checks": proof["checks"]})
    admitted_events = [event for event in events if event.get("eligible")]
    # Outcome notices belong to the exact original. Other originals remain
    # pending until a subsequent pass sees the first successful reply receipt.
    primary_events = [e for e in admitted_events if not e.get("outcome")]
    result_parents = {e.get("parentJobId") or "" for e in primary_events}
    result_parent = (next(iter(result_parents)) if len(result_parents) == 1 else "") if primary_events else next(
        (e.get("parentJobId") for e in admitted_events if e.get("outcome")), "")
    for event in admitted_events[:]:
        if event.get("outcome") and (not result_parent or event.get("parentJobId") != result_parent):
            admitted_events.remove(event)
            suppressed.append({"reasonCode": "outcome-reply-deferred", "reason": "다른 원본 알림의 결과 답글은 다음 확인으로 넘깁니다."})
    allowed = {event.get("transitionId") for event in admitted_events}
    transitions = [row for row in transitions if row.get("changeCategory") == "data-availability" or row.get("transitionId") in allowed]
    suppressed.extend(rejected)
    suppressed.extend({"transitionId": e.get("transitionId"), "reasonCode": e["reasonCode"],
                       "reason": "같은 사안의 의미와 대응 상태를 이미 전달해 상세 이력에 남깁니다."}
                      for e in events if not e.get("eligible") and e.get("reasonCode") == "same-customer-meaning")
    event_parents = {e.get("parentJobId") or "" for e in admitted_events}
    if admitted_events:
        # Data availability notices can only be replies to their own original.
        for row in transitions[:]:
            if row.get("changeCategory") == "data-availability" and event_parents != {row.get("parentJobId") or ""}:
                transitions.remove(row)
                suppressed.append({"transitionId": row.get("transitionId"), "reasonCode": "different-original-alert",
                                   "reason": "다른 원본에 속한 자료 안내는 상세 이력에 남깁니다."})
        parent = next(iter(event_parents)) if len(event_parents) == 1 else ""
        follow_up = {**follow_up, "parentJobId": parent, "mode": "reply" if parent else "new"}
    waits = [value for value in mapping(current.get("dataAvailability")).values()
             if isinstance(value, Mapping) and value.get("state") == "unavailable"]
    wait_reasons = strings([reason for value in waits for reason in value.get("reasons") or []])
    non_delivery_code = ("relation_followup_not_deliverable" if suppressed else
                         "rule_evidence_unavailable" if wait_reasons else "unchanged_rulebox_relation")
    if any(r.get("reasonCode", "").startswith("rule_dispatch_") for r in rejected):
        non_delivery_code = next(r["reasonCode"] for r in rejected if r.get("reasonCode", "").startswith("rule_dispatch_"))
    elif rejected:
        non_delivery_code = "rule_notification_purpose_missing" if any(
            r.get("reasonCode") == "missing-rule-notification-purpose" for r in rejected) else "rule_notification_change_not_selected"
    elif any(e.get("reasonCode") == "same-customer-meaning" for e in events):
        non_delivery_code = "same_rule_notification_meaning"
    elif not current["proofIdentityVerified"]:
        non_delivery_code = "rule_notice_proof_identity_mismatch"
    changes = {}
    for kind in ("hypotheses", "rules", "facts"):
        before = {row["id"]: row for row in rows(previous.get(kind))}
        after = {row["id"]: row for row in current[kind]}
        changes[kind] = [{"id": key, "previous": before.get(key), "current": after.get(key),
                          "change": "unknown" if not previous else "added" if key not in before else "removed" if key not in after else "changed" if {k: v for k, v in before[key].items() if k != "traceId"} != {k: v for k, v in after[key].items() if k != "traceId"} else "unchanged"}
                         for key in dict.fromkeys([*after, *before])]
    return {"version": VERSION, "authority": "typedb-hypothesis-lifecycle", "eligible": bool(transitions or admitted_events),
            "reason": " · ".join(str(row.get("changeLabel") or row.get("reason") or "관계 상태 변경") for row in transitions)
            if transitions else ("등록된 가설의 확인 결과가 새로 저장됐습니다." if any(e.get("outcome") for e in admitted_events) else
                                 "관리 기준의 적용 상태가 달라졌습니다." if admitted_events else suppressed[0]["reason"] if suppressed else
                                 "판단 보류: " + " · ".join(wait_reasons) if wait_reasons else
                                 "룰박스 관계의 새 의미 변화가 없어 웹 이력에만 저장합니다."),
            "nonDeliveryReasonCode": "" if transitions or admitted_events else non_delivery_code,
            "baselineAvailable": bool(previous), "baselineDeliveredAt": previous.get("deliveredAt") or "",
            "current": current, "previous": previous, "transitions": transitions, "changes": changes,
            "followUp": follow_up, "suppressedTransitions": suppressed,
            "customerEventVersion": "rule-notification-event-v1", "customerEvents": admitted_events,
            'policyLifecycle': policy_lifecycle(current, previous, admitted_events)}


def relation_change_summary(packet):
    """Render the frozen evidence through the customer presentation policy."""
    from digital_twin.modules.notifications.domain.relation_change_presentation import readable_relation_change
    return readable_relation_change(packet)
