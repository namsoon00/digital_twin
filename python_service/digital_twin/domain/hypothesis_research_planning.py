"""Bounded AI research planning for TypeDB-derived competing hypotheses."""

from typing import Dict, Iterable, List, Tuple

from .investment_brain import stable_id
from .investment_evidence_governance import HypothesisResearchBrief, unique_texts


PLANNER_SOURCE = "ai-hypothesis-research-planner"
PLANNER_RELEVANCE = {"direct", "important", "supporting"}
APPROVED_DISCOVERY_SOURCE_TYPES = {
    "official", "official-filing", "company-ir", "market-data", "financial-data",
    "official-macro", "news", "news-full-text", "article",
}
APPROVED_DISCOVERY_EVIDENCE_TYPES = {
    "official-filing", "company-guidance", "financial-fact", "market-data",
    "price-reaction", "investor-flow", "macro-observation", "news-full-text",
    "competitive-event", "customer-event", "supplier-event", "regulatory-event",
    "industry-demand", "provenance", "observation-time", "source-reliability",
    "independent-confirmation",
}
APPROVED_DISCOVERY_KINDS = {
    "hypothesis-validation", "novel-connection", "causal-gap", "counter-evidence",
}
EXPECTED_DECISION_IMPACTS = {"BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID", "REVIEW_LEVEL"}


def clean_text(value: object, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:max(1, int(limit or 1))]


def baseline_research_plan(plan: Dict[str, object] = None) -> Dict[str, object]:
    """Copy the graph-derived plan before an AI can add collection tasks."""

    payload = dict(plan or {})
    payload["tasks"] = [dict(item) for item in payload.get("tasks") or [] if isinstance(item, dict)]
    payload["unresolvedQuestions"] = unique_texts(payload.get("unresolvedQuestions") or [], 20)
    payload.setdefault("planningSource", "typedb-hypothesis-set")
    payload.setdefault("planningAudit", {
        "status": "rule-derived",
        "preservesBaselineTasks": True,
        "decisionEligibility": "research-only",
    })
    return payload


def task_policies(
    plan: Dict[str, object],
    brief: HypothesisResearchBrief,
) -> Dict[str, Dict[str, object]]:
    """Derive the only source/freshness choices the AI is allowed to request."""

    candidates = [
        str(item.get("hypothesisId") or "").strip()
        for item in brief.candidate_hypotheses
        if isinstance(item, dict) and str(item.get("hypothesisId") or "").strip()
    ]
    policies: Dict[str, Dict[str, object]] = {}
    tasks = [item for item in plan.get("tasks") or [] if isinstance(item, dict)]
    for hypothesis_id in candidates:
        related = [
            item for item in tasks
            if hypothesis_id in [str(value or "").strip() for value in item.get("relatedHypothesisIds") or []]
        ]
        source_tasks = related or tasks
        source_types = unique_texts([
            source for item in source_tasks for source in (item.get("sourceTypes") or [])
        ], 12)
        evidence_types = unique_texts([
            evidence for item in source_tasks for evidence in (item.get("requiredEvidenceTypes") or [])
        ], 20)
        max_ages = []
        for item in source_tasks:
            try:
                max_ages.append(max(1, int(item.get("maxAgeMinutes") or 0)))
            except (TypeError, ValueError):
                continue
        policies[hypothesis_id] = {
            "sourceTypes": source_types,
            "requiredEvidenceTypes": evidence_types,
            "maxAgeMinutes": min(max_ages) if max_ages else 360,
        }
    return policies


def research_planner_input(
    brief: HypothesisResearchBrief,
    plan: Dict[str, object],
    question: Dict[str, object] = None,
    account_id: str = "",
    symbol: str = "",
) -> Dict[str, object]:
    """Build Graph RAG bounded to current hypotheses and allowed collection policy."""

    policies = task_policies(plan, brief)
    candidates = []
    for item in brief.candidate_hypotheses:
        if not isinstance(item, dict):
            continue
        hypothesis_id = str(item.get("hypothesisId") or "").strip()
        if not hypothesis_id:
            continue
        candidates.append({
            "hypothesisId": hypothesis_id,
            "templateLabel": str(item.get("templateLabel") or "").strip(),
            "claim": str(item.get("claim") or "").strip(),
            "stance": str(item.get("stance") or "").strip(),
            "evidenceState": str(item.get("evidenceState") or "").strip(),
            "verificationStatus": str(item.get("verificationStatus") or "").strip(),
            "counterHypothesisIds": list(item.get("counterHypothesisIds") or []),
            "invalidationConditions": list(item.get("invalidationConditions") or []),
            "allowedCollectionPolicy": dict(policies.get(hypothesis_id) or {}),
        })
    return {
        "accountId": str(account_id or ""),
        "symbol": str(symbol or "").upper().strip(),
        "question": dict(question or {}),
        "hypothesisSetId": brief.hypothesis_set_id,
        "reasoningGeneration": brief.reasoning_generation.to_dict(),
        "candidateHypotheses": candidates,
        "evidenceGaps": list(brief.evidence_gaps),
        "baselinePlan": {
            "planId": str(plan.get("planId") or ""),
            "unresolvedQuestions": list(plan.get("unresolvedQuestions") or []),
            "taskCount": len(plan.get("tasks") or []),
        },
        "dataCoverageMap": {
            "knownEvidenceGaps": list(brief.evidence_gaps),
            "approvedSourceTypes": sorted(APPROVED_DISCOVERY_SOURCE_TYPES),
            "approvedEvidenceTypes": sorted(APPROVED_DISCOVERY_EVIDENCE_TYPES),
        },
        "guardrails": {
            "cannotAssertUncollectedFacts": True,
            "cannotSelectInvestmentAction": True,
            "cannotRemoveBaselineTasks": True,
            "mayProposeNovelResearchQuestion": True,
            "novelQuestionMustExplainDecisionImpact": True,
            "mustUseApprovedSourceAndEvidenceTypes": True,
            "maximumAdditionalTaskCount": 3,
        },
    }


def apply_ai_research_guidance(
    plan: Dict[str, object],
    brief: HypothesisResearchBrief,
    guidance: Dict[str, object] = None,
    maximum_additional_tasks: int = 3,
) -> Tuple[Dict[str, object], Dict[str, object]]:
    """Accept only collection guidance bounded by the graph-derived plan.

    This is deliberately a research-plan merger, never a hypothesis or action
    evaluator. Existing hypotheses retain their graph links. Open discovery is
    allowed only as a decision-changing question over a fixed source catalog;
    it cannot assert a fact or create an investable hypothesis by itself.
    """

    baseline = baseline_research_plan(plan)
    raw = dict(guidance or {})
    candidates = {
        str(item.get("hypothesisId") or "").strip(): dict(item)
        for item in brief.candidate_hypotheses
        if isinstance(item, dict) and str(item.get("hypothesisId") or "").strip()
    }
    policies = task_policies(baseline, brief)
    rejected: List[Dict[str, object]] = []
    focused: List[str] = []
    for hypothesis_id in raw.get("focusHypothesisIds") or raw.get("focus_hypothesis_ids") or []:
        clean = str(hypothesis_id or "").strip()
        if clean in candidates:
            focused.append(clean)
        elif clean:
            rejected.append({"field": "focusHypothesisIds", "value": clean, "reason": "unknown-hypothesis-id"})
    focused = unique_texts(focused, 8)

    existing_questions = {
        clean_text(item.get("question"), 320).casefold()
        for item in baseline.get("tasks") or []
        if isinstance(item, dict) and clean_text(item.get("question"), 320)
    }
    additions: List[Dict[str, object]] = []
    raw_tasks = [item for item in raw.get("tasks") or [] if isinstance(item, dict)]
    for index, item in enumerate(raw_tasks[:max(1, int(maximum_additional_tasks or 1))]):
        hypothesis_id = str(item.get("hypothesisId") or item.get("hypothesis_id") or "").strip()
        discovery_kind = str(item.get("discoveryKind") or item.get("discovery_kind") or "").strip().lower()
        rationale = clean_text(
            item.get("decisionChangingRationale") or item.get("decision_changing_rationale"),
            320,
        )
        expected_impacts = [
            str(value or "").strip().upper()
            for value in item.get("expectedDecisionImpact") or item.get("expected_decision_impact") or []
            if str(value or "").strip().upper() in EXPECTED_DECISION_IMPACTS
        ]
        open_discovery = not hypothesis_id and discovery_kind in APPROVED_DISCOVERY_KINDS
        if hypothesis_id and hypothesis_id not in candidates:
            rejected.append({"index": index, "reason": "unknown-hypothesis-id"})
            continue
        if not hypothesis_id and not open_discovery:
            rejected.append({"index": index, "reason": "hypothesis-or-approved-discovery-kind-required"})
            continue
        if open_discovery and (not rationale or not expected_impacts):
            rejected.append({"index": index, "reason": "open-discovery-decision-impact-required"})
            continue
        question = clean_text(item.get("question"), 320)
        if not question:
            rejected.append({"index": index, "hypothesisId": hypothesis_id, "reason": "question-required"})
            continue
        if question.casefold() in existing_questions:
            rejected.append({"index": index, "hypothesisId": hypothesis_id, "reason": "duplicate-question"})
            continue
        policy = dict(policies.get(hypothesis_id) or {})
        allowed_sources = set(APPROVED_DISCOVERY_SOURCE_TYPES)
        if hypothesis_id and not rationale:
            allowed_sources.intersection_update(set(policy.get("sourceTypes") or []))
        requested_sources = unique_texts(item.get("sourceTypes") or item.get("source_types") or [], 12)
        source_types = [source for source in requested_sources if source in allowed_sources]
        if not source_types:
            rejected.append({"index": index, "hypothesisId": hypothesis_id, "reason": "source-policy-violation"})
            continue
        allowed_evidence = set(APPROVED_DISCOVERY_EVIDENCE_TYPES)
        if hypothesis_id and not rationale:
            allowed_evidence.intersection_update(set(policy.get("requiredEvidenceTypes") or []))
        requested_evidence = unique_texts(item.get("requiredEvidenceTypes") or item.get("required_evidence_types") or [], 20)
        evidence_types = [value for value in requested_evidence if value in allowed_evidence]
        if not evidence_types:
            rejected.append({"index": index, "hypothesisId": hypothesis_id, "reason": "evidence-policy-violation"})
            continue
        try:
            requested_age = int(item.get("maxAgeMinutes") or item.get("max_age_minutes") or policy.get("maxAgeMinutes") or 360)
        except (TypeError, ValueError):
            requested_age = int(policy.get("maxAgeMinutes") or 360)
        policy_age = int(policy.get("maxAgeMinutes") or (10080 if open_discovery else 360))
        maximum_age = max(1, min(requested_age, policy_age))
        relevance = str(item.get("decisionRelevance") or item.get("decision_relevance") or "supporting").strip().lower()
        if relevance not in PLANNER_RELEVANCE:
            relevance = "supporting"
        allowed_counters = set(candidates.get(hypothesis_id, {}).get("counterHypothesisIds") or [])
        counter_ids = [
            value for value in unique_texts(item.get("counterHypothesisIds") or item.get("counter_hypothesis_ids") or [], 8)
            if value in allowed_counters
        ]
        task = {
            "taskId": stable_id("ai-hypothesis-research-task", baseline.get("planId"), hypothesis_id, question, ",".join(source_types)),
            "question": question,
            "purpose": clean_text(item.get("purpose") or "가설의 근거와 반대 가설을 같은 출처 정책으로 확인합니다.", 320),
            "requiredEvidenceTypes": evidence_types,
            "relatedHypothesisIds": [hypothesis_id] if hypothesis_id else [],
            "counterHypothesisIds": counter_ids,
            "sourceTypes": source_types,
            "maxAgeMinutes": maximum_age,
            "decisionRelevance": relevance,
            "executionMode": "cache-first-on-demand",
            "priority": 50,
            "status": "ready",
            "planningSource": PLANNER_SOURCE,
            "discoveryKind": discovery_kind or "hypothesis-validation",
            "decisionChanging": bool(rationale or relevance == "direct"),
            "decisionChangingRationale": rationale,
            "expectedDecisionImpact": unique_texts(expected_impacts, 6),
            "queryTerms": unique_texts(item.get("queryTerms") or item.get("query_terms") or [], 6),
        }
        additions.append(task)
        existing_questions.add(question.casefold())

    unresolved = unique_texts(baseline.get("unresolvedQuestions") or [], 20)
    for text in raw.get("unresolvedQuestions") or raw.get("unresolved_questions") or []:
        clean = clean_text(text, 240)
        if clean:
            unresolved.append(clean)
    unresolved = unique_texts(unresolved, 20)
    status = "ai-augmented" if additions else "no-valid-guidance"
    audit = {
        "status": status,
        "planningSource": PLANNER_SOURCE,
        "requestedTaskCount": len(raw_tasks),
        "acceptedTaskIds": [item["taskId"] for item in additions],
        "focusedHypothesisIds": focused,
        "analystAssessment": clean_text(raw.get("initialAssessment"), 500),
        "decisionChangingGaps": unique_texts(raw.get("decisionChangingGaps") or [], 8),
        "openDiscoveryTaskIds": [item["taskId"] for item in additions if not item.get("relatedHypothesisIds")],
        "rejectedGuidance": rejected[:20],
        "preservesBaselineTasks": True,
        "decisionEligibility": "research-only",
    }
    return {
        **baseline,
        "tasks": list(baseline.get("tasks") or []) + additions,
        "unresolvedQuestions": unresolved,
        "planningSource": PLANNER_SOURCE if additions else baseline.get("planningSource") or "typedb-hypothesis-set",
        "planningAudit": audit,
    }, audit
