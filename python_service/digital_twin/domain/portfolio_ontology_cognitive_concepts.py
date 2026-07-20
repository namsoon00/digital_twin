from typing import Dict, Iterable

from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_schema import add_entity, add_relation


def add_investment_brain_concepts(
    graph: PortfolioOntology,
    portfolio_id: str,
    decision_episodes: Iterable[Dict[str, object]],
    hypothesis_proposals: Iterable[Dict[str, object]] = None,
    decision_performance: Dict[str, object] = None,
) -> None:
    portfolio_node_id = entity_id("portfolio", portfolio_id)
    episode_rows = [item for item in decision_episodes or [] if isinstance(item, dict)]
    for episode in episode_rows:
        if not isinstance(episode, dict):
            continue
        episode_key = str(episode.get("episodeId") or "").strip()
        symbol = str(episode.get("symbol") or "").upper().strip()
        if not episode_key or not symbol:
            continue
        stock_id = entity_id("stock", symbol)
        question = episode.get("question") if isinstance(episode.get("question"), dict) else {}
        hypothesis_set = episode.get("hypothesisSet") if isinstance(episode.get("hypothesisSet"), dict) else {}
        research_plan = episode.get("researchPlan") if isinstance(episode.get("researchPlan"), dict) else {}
        research_audit = episode.get("researchAudit") if isinstance(episode.get("researchAudit"), dict) else {}
        episode_id = add_entity(graph, "decision-episode", episode_key, str(episode.get("subjectName") or symbol) + " 판단 에피소드", {
            "tboxClass": "DecisionEpisode",
            "symbol": symbol,
            "action": episode.get("action"),
            "confidence": episode.get("confidence"),
            "selectedHypothesisId": episode.get("selectedHypothesisId"),
            "inferenceGenerationId": episode.get("inferenceGenerationId"),
            "decidedAt": episode.get("decidedAt"),
            "status": episode.get("status"),
            "source": episode.get("source"),
        })
        add_relation(graph, stock_id, episode_id, "HAS_DECISION_EPISODE", weight=1.0, properties={"source": "investment-brain-memory"})
        add_relation(graph, portfolio_node_id, episode_id, "HAS_DECISION_EPISODE", weight=1.0, properties={"source": "investment-brain-memory"})
        question_key = str(question.get("questionId") or "").strip()
        if question_key:
            question_id = add_entity(graph, "investment-question", question_key, str(question.get("text") or "투자 질문"), {
                "tboxClass": "InvestmentQuestion" if question.get("source") != "system-self-question" else "SelfQuestion",
                "intent": question.get("intent"),
                "horizon": question.get("horizon"),
                "askedAt": question.get("askedAt"),
                "source": question.get("source"),
            })
            add_relation(graph, question_id, stock_id, "ASKS_ABOUT", weight=1.0, properties={"source": "investment-brain-memory"})
            add_relation(graph, question_id, episode_id, "ANSWERED_BY", weight=1.0, properties={"source": "investment-brain-memory"})
        else:
            question_id = ""
        set_key = str(hypothesis_set.get("hypothesisSetId") or "").strip()
        if set_key:
            set_id = add_entity(graph, "hypothesis-set", set_key, str(episode.get("subjectName") or symbol) + " 경쟁 가설", {
                "tboxClass": "HypothesisSet",
                "minimumComparisonCount": hypothesis_set.get("minimumComparisonCount"),
                "comparisonRequired": hypothesis_set.get("comparisonRequired"),
                "inferenceGenerationId": hypothesis_set.get("inferenceGenerationId"),
                "version": hypothesis_set.get("version"),
            })
            if question_id:
                add_relation(graph, question_id, set_id, "HAS_HYPOTHESIS_SET", weight=1.0, properties={"source": "investment-brain-memory"})
        else:
            set_id = ""
        plan_key = str(research_plan.get("planId") or "").strip()
        if plan_key:
            plan_id = add_entity(graph, "research-plan", plan_key, str(episode.get("subjectName") or symbol) + " 근거 조사 계획", {
                "tboxClass": "ResearchPlan",
                "status": research_plan.get("status"),
                "maxRounds": research_plan.get("maxRounds"),
                "createdAt": research_plan.get("createdAt"),
            })
            if question_id:
                add_relation(graph, question_id, plan_id, "HAS_RESEARCH_PLAN", weight=1.0, properties={"source": "investment-brain-memory"})
            policy_id = add_research_source_policy(graph, plan_key, research_plan)
        else:
            plan_id = ""
            policy_id = ""
        research_task_ids = {}
        for task in research_plan.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            task_key = str(task.get("taskId") or "").strip()
            if not task_key:
                continue
            task_id = add_entity(graph, "research-task", task_key, str(task.get("question") or "조사 작업"), {
                "tboxClass": "ResearchTask",
                "purpose": task.get("purpose"),
                "priority": task.get("priority"),
                "status": task.get("status"),
                "sourceTypes": task.get("sourceTypes") or [],
                "maxAgeMinutes": task.get("maxAgeMinutes"),
                "decisionImpact": task.get("decisionImpact"),
                "executionMode": task.get("executionMode"),
                "resultEvidenceIds": task.get("resultEvidenceIds") or [],
            })
            research_task_ids[task_key] = task_id
            if plan_id:
                add_relation(graph, plan_id, task_id, "DECOMPOSES_INTO", weight=1.0, properties={"source": "investment-brain-memory"})
            if policy_id:
                add_relation(graph, task_id, policy_id, "USES_SOURCE_POLICY", weight=1.0, properties={"source": "investment-brain-research"})
            for evidence_type in task.get("requiredEvidenceTypes") or []:
                need_id = add_entity(graph, "information-need", task_key + ":" + str(evidence_type), str(evidence_type), {
                    "tboxClass": "InformationNeed",
                    "status": task.get("status"),
                })
                add_relation(graph, task_id, need_id, "REQUIRES_EVIDENCE", weight=1.0, properties={"source": "investment-brain-memory"})
        hypothesis_ids = []
        hypothesis_id_by_key = {}
        for hypothesis in hypothesis_set.get("hypotheses") or []:
            if not isinstance(hypothesis, dict):
                continue
            hypothesis_key = str(hypothesis.get("hypothesisId") or "").strip()
            if not hypothesis_key:
                continue
            hypothesis_id = add_entity(graph, "competing-hypothesis", hypothesis_key, str(hypothesis.get("claim") or hypothesis_key), {
                "tboxClass": "CompetingHypothesis",
                "stance": hypothesis.get("stance"),
                "horizon": hypothesis.get("horizon"),
                "priorConfidence": hypothesis.get("priorConfidence"),
                "status": hypothesis.get("status"),
                "templateId": hypothesis.get("templateId"),
                "templateLabel": hypothesis.get("templateLabel"),
                "approvalStatus": hypothesis.get("approvalStatus"),
                "verificationStatus": hypothesis.get("verificationStatus"),
                "supportingRuleIds": hypothesis.get("supportingRuleIds") or [],
                "counterRuleIds": hypothesis.get("counterRuleIds") or [],
                "invalidationConditions": hypothesis.get("invalidationConditions") or [],
                "causalPathIds": hypothesis.get("causalPathIds") or [],
                "requiredEvidenceTypes": hypothesis.get("requiredEvidenceTypes") or [],
            })
            hypothesis_ids.append(hypothesis_id)
            hypothesis_id_by_key[hypothesis_key] = hypothesis_id
            if set_id:
                add_relation(graph, set_id, hypothesis_id, "CONTAINS_HYPOTHESIS", weight=1.0, properties={"source": "investment-brain-memory"})
            template_key = str(hypothesis.get("templateId") or "").strip()
            if template_key:
                template_id = add_entity(graph, "hypothesis-template", template_key, str(hypothesis.get("templateLabel") or template_key), {
                    "tboxClass": "ApprovedHypothesisTemplate",
                    "approvalStatus": hypothesis.get("approvalStatus"),
                    "sourceRuleIds": hypothesis.get("supportingRuleIds") or [],
                    "stance": hypothesis.get("stance"),
                    "requiredEvidenceTypes": hypothesis.get("requiredEvidenceTypes") or [],
                })
                add_relation(graph, hypothesis_id, template_id, "INSTANTIATES_HYPOTHESIS_TEMPLATE", weight=1.0, properties={"source": "typedb-hypothesis-template"})
                add_relation(graph, template_id, stock_id, "APPLICABLE_TO", weight=1.0, properties={"source": "typedb-current-generation"})
            if hypothesis_key == str(episode.get("selectedHypothesisId") or ""):
                add_relation(graph, episode_id, hypothesis_id, "SELECTS_HYPOTHESIS", weight=1.0, properties={"source": "ai-hypothesis-competition"})
            for assumption_index, assumption in enumerate(hypothesis.get("assumptions") or []):
                assumption_id = add_entity(graph, "assumption", hypothesis_key + ":" + str(assumption_index), str(assumption), {
                    "tboxClass": "Assumption",
                    "source": "investment-brain-memory",
                })
                add_relation(graph, hypothesis_id, assumption_id, "DEPENDS_ON_ASSUMPTION", weight=1.0, properties={"source": "investment-brain-memory"})
            for evidence_key in hypothesis.get("supportingEvidenceIds") or []:
                evidence_id = add_entity(graph, "evidence-reference", str(evidence_key), str(evidence_key), {
                    "tboxClass": "Evidence",
                    "source": "typedb-inference-reference",
                })
                add_relation(graph, hypothesis_id, evidence_id, "USED_AS_EVIDENCE", weight=1.0, properties={"polarity": "support"})
            for evidence_key in hypothesis.get("counterEvidenceIds") or []:
                evidence_id = add_entity(graph, "evidence-reference", str(evidence_key), str(evidence_key), {
                    "tboxClass": "Evidence",
                    "source": "typedb-inference-reference",
                })
                add_relation(graph, evidence_id, hypothesis_id, "CONTRADICTS", weight=1.0, properties={"polarity": "risk"})
            for path_key in hypothesis.get("causalPathIds") or []:
                path_id = add_entity(graph, "inference-trace-reference", str(path_key), str(path_key), {
                    "tboxClass": "InferenceTrace",
                    "source": "typedb-inference-reference",
                })
                add_relation(graph, hypothesis_id, path_id, "EXPLAINED_BY_TRACE", weight=1.0, properties={"source": "investment-brain-memory"})
        for task in research_plan.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            task_id = research_task_ids.get(str(task.get("taskId") or ""))
            if not task_id:
                continue
            for hypothesis_key in task.get("relatedHypothesisIds") or []:
                hypothesis_id = hypothesis_id_by_key.get(str(hypothesis_key))
                if hypothesis_id:
                    add_relation(graph, task_id, hypothesis_id, "TESTS_HYPOTHESIS", weight=1.0, properties={"source": "investment-brain-memory"})
        run_key = str(research_audit.get("runId") or "").strip()
        if run_key:
            run_id = add_entity(graph, "verification-run", run_key, str(episode.get("subjectName") or symbol) + " 근거 검증 실행", {
                "tboxClass": "VerificationRun",
                "status": research_audit.get("status"),
                "roundCount": research_audit.get("roundCount"),
                "changedEvidenceCount": research_audit.get("changedEvidenceCount"),
                "startedAt": research_audit.get("startedAt"),
                "completedAt": research_audit.get("completedAt"),
                "reasoningRefreshed": research_audit.get("reasoningRefreshed"),
                "verifiedClaimCount": len(research_audit.get("verifiedClaims") or []),
                "rejectedClaimCount": len(research_audit.get("rejectedClaims") or []),
            })
            add_relation(graph, episode_id, run_id, "HAS_VERIFICATION_RUN", weight=1.0, properties={"source": "investment-brain-research"})
            if question_id:
                add_relation(graph, question_id, run_id, "HAS_VERIFICATION_RUN", weight=1.0, properties={"source": "investment-brain-research"})
            add_verified_claim_concepts(
                graph,
                run_id,
                stock_id,
                research_audit.get("verifiedClaims") or [],
            )
        for index, hypothesis_id in enumerate(hypothesis_ids):
            for competitor_id in hypothesis_ids[index + 1:]:
                add_relation(graph, hypothesis_id, competitor_id, "COMPETES_WITH_HYPOTHESIS", weight=1.0, properties={"source": "investment-brain-memory"})
        for index, question_text in enumerate(episode.get("unresolvedQuestions") or []):
            unresolved_id = add_entity(graph, "self-question", episode_key + ":" + str(index), str(question_text), {
                "tboxClass": "SelfQuestion",
                "status": "unresolved",
                "source": "investment-brain-memory",
            })
            add_relation(graph, episode_id, unresolved_id, "HAS_UNRESOLVED_QUESTION", weight=1.0, properties={"source": "investment-brain-memory"})
            add_relation(graph, unresolved_id, stock_id, "ASKS_ABOUT", weight=1.0, properties={"source": "investment-brain-memory"})
        for outcome in episode.get("outcomes") or []:
            if not isinstance(outcome, dict):
                continue
            outcome_key = str(outcome.get("outcomeId") or "").strip()
            if not outcome_key:
                continue
            outcome_id = add_entity(graph, "observed-outcome", outcome_key, str(episode.get("subjectName") or symbol) + " 판단 후 결과", {
                "tboxClass": "ObservedOutcome",
                "observedAt": outcome.get("observedAt"),
                "price": outcome.get("price"),
                "profitLossRate": outcome.get("profitLossRate"),
                "priceChangeFromDecisionPct": outcome.get("priceChangeFromDecisionPct"),
                "selectedHypothesisStatus": outcome.get("selectedHypothesisStatus"),
                "source": "investment-brain-feedback",
            })
            add_relation(graph, episode_id, outcome_id, "RESULTED_IN_OUTCOME", weight=1.0, properties={"source": "investment-brain-feedback"})
            add_relation(graph, stock_id, outcome_id, "OBSERVES_OUTCOME", weight=1.0, properties={"source": "investment-brain-feedback"})
    add_hypothesis_calibration_concepts(graph, portfolio_id, episode_rows)
    add_decision_performance_concepts(graph, portfolio_id, decision_performance or {})
    add_novel_hypothesis_proposal_concepts(graph, portfolio_id, hypothesis_proposals or [])


def add_decision_performance_concepts(
    graph: PortfolioOntology,
    portfolio_id: str,
    performance: Dict[str, object],
) -> None:
    if not isinstance(performance, dict) or not int(performance.get("outcomeCount") or 0):
        return
    portfolio_node_id = entity_id("portfolio", portfolio_id)
    summary = performance.get("summary") if isinstance(performance.get("summary"), dict) else {}
    summary_id = add_entity(graph, "decision-performance", portfolio_id, "투자 판단 성과", {
        "tboxClass": "DecisionPerformance",
        **dict(summary),
        "episodeCount": performance.get("episodeCount"),
        "episodeWithOutcomeCount": performance.get("episodeWithOutcomeCount"),
        "outcomeCoveragePct": performance.get("outcomeCoveragePct"),
        "byHorizon": list(performance.get("byHorizon") or []),
        "byAction": list(performance.get("byAction") or []),
        "automaticDeployment": False,
        "source": "DecisionEpisode+ObservedOutcome",
    })
    add_relation(graph, portfolio_node_id, summary_id, "HAS_DECISION_PERFORMANCE", weight=1.0, properties={"source": "investment-brain-feedback"})
    for metric in performance.get("byRule") or []:
        if not isinstance(metric, dict) or not str(metric.get("key") or "").strip():
            continue
        rule_key = str(metric.get("key") or "")
        performance_id = add_entity(graph, "rule-performance", rule_key, rule_key + " 성과", {
            "tboxClass": "RulePerformance",
            **dict(metric),
            "automaticDeployment": False,
        })
        rule_id = entity_id("graph-inference-rule", rule_key)
        if not any(item.entity_id == rule_id for item in graph.entities):
            add_entity(graph, "graph-inference-rule", rule_key, rule_key, {"tboxClass": "GraphInferenceRule", "ruleId": rule_key})
        add_relation(graph, summary_id, performance_id, "HAS_PERFORMANCE_SLICE", weight=1.0, properties={"source": "investment-brain-feedback"})
        add_relation(graph, performance_id, rule_id, "EVALUATES_RULE", weight=1.0, properties={"source": "investment-brain-feedback"})
    for metric in performance.get("byHypothesis") or []:
        if not isinstance(metric, dict) or not str(metric.get("key") or "").strip():
            continue
        template_key = str(metric.get("key") or "")
        performance_id = add_entity(graph, "hypothesis-performance", template_key, str(metric.get("label") or template_key) + " 성과", {
            "tboxClass": "HypothesisPerformance",
            **dict(metric),
            "automaticDeployment": False,
        })
        template_id = entity_id("hypothesis-template", template_key)
        if not any(item.entity_id == template_id for item in graph.entities):
            add_entity(graph, "hypothesis-template", template_key, str(metric.get("label") or template_key), {"tboxClass": "ApprovedHypothesisTemplate"})
        add_relation(graph, summary_id, performance_id, "HAS_PERFORMANCE_SLICE", weight=1.0, properties={"source": "investment-brain-feedback"})
        add_relation(graph, performance_id, template_id, "EVALUATES_HYPOTHESIS", weight=1.0, properties={"source": "investment-brain-feedback"})


def add_hypothesis_calibration_concepts(
    graph: PortfolioOntology,
    portfolio_id: str,
    decision_episodes: Iterable[Dict[str, object]],
) -> None:
    grouped: Dict[str, Dict[str, object]] = {}
    for episode in decision_episodes or []:
        hypothesis_set = episode.get("hypothesisSet") if isinstance(episode.get("hypothesisSet"), dict) else {}
        selected_id = str(episode.get("selectedHypothesisId") or "")
        selected = next((
            item for item in hypothesis_set.get("hypotheses") or []
            if isinstance(item, dict) and str(item.get("hypothesisId") or "") == selected_id
        ), None)
        outcomes = [item for item in episode.get("outcomes") or [] if isinstance(item, dict)]
        if not selected or not outcomes:
            continue
        latest = sorted(outcomes, key=lambda item: str(item.get("observedAt") or ""))[-1]
        status = str(latest.get("selectedHypothesisStatus") or "")
        template_id = str(selected.get("templateId") or "").strip()
        episode_id = str(episode.get("episodeId") or "").strip()
        if not episode_id or not template_id or status not in {"directionally-corroborated", "directionally-contradicted", "inconclusive"}:
            continue
        row = grouped.setdefault(template_id, {
            "templateId": template_id,
            "templateLabel": str(selected.get("templateLabel") or template_id),
            "episodeOutcomes": {},
        })
        previous = row["episodeOutcomes"].get(episode_id) or {}
        if str(latest.get("observedAt") or "") >= str(previous.get("observedAt") or ""):
            row["episodeOutcomes"][episode_id] = {
                "status": status,
                "observedAt": str(latest.get("observedAt") or ""),
            }
    portfolio_node_id = entity_id("portfolio", portfolio_id)
    for template_id, row in sorted(grouped.items()):
        statuses = [str(item.get("status") or "") for item in row["episodeOutcomes"].values()]
        corroborated_count = statuses.count("directionally-corroborated")
        contradicted_count = statuses.count("directionally-contradicted")
        inconclusive_count = statuses.count("inconclusive")
        decisive_count = corroborated_count + contradicted_count
        independent_count = len(row["episodeOutcomes"])
        smoothed_rate = (corroborated_count + 1) / (decisive_count + 2)
        adjustment = max(-10.0, min(10.0, (smoothed_rate - 0.5) * 20.0)) if decisive_count >= 3 else 0.0
        calibration_id = add_entity(graph, "hypothesis-calibration", template_id, str(row["templateLabel"]) + " 결과 보정", {
            "tboxClass": "HypothesisCalibration",
            "templateId": template_id,
            "independentEpisodeCount": independent_count,
            "decisiveOutcomeCount": decisive_count,
            "corroboratedCount": corroborated_count,
            "contradictedCount": contradicted_count,
            "inconclusiveCount": inconclusive_count,
            "smoothedCorroborationRate": round(smoothed_rate, 4),
            "suggestedPriorAdjustmentPoints": round(adjustment, 2),
            "calibrationStatus": "usable" if decisive_count >= 3 else "insufficient-sample",
            "minimumDecisiveOutcomes": 3,
            "automaticDeployment": False,
            "source": "investment-brain-feedback",
        })
        template_id_node = entity_id("hypothesis-template", template_id)
        if not any(item.entity_id == template_id_node for item in graph.entities):
            add_entity(graph, "hypothesis-template", template_id, str(row["templateLabel"]), {
                "tboxClass": "ApprovedHypothesisTemplate",
                "source": "investment-brain-feedback",
            })
        add_relation(graph, template_id_node, calibration_id, "CALIBRATED_BY_OUTCOME", weight=1.0, properties={
            "source": "investment-brain-feedback",
            "automaticDeployment": False,
        })
        add_relation(graph, portfolio_node_id, calibration_id, "HAS_HYPOTHESIS_CALIBRATION", weight=1.0, properties={
            "source": "investment-brain-feedback",
        })


def add_research_source_policy(graph: PortfolioOntology, plan_key: str, research_plan: Dict[str, object]) -> str:
    tasks = [item for item in research_plan.get("tasks") or [] if isinstance(item, dict)]
    source_types = sorted({
        str(source or "").strip()
        for task in tasks
        for source in task.get("sourceTypes") or []
        if str(source or "").strip()
    })
    max_ages = [positive_int(task.get("maxAgeMinutes")) for task in tasks if positive_int(task.get("maxAgeMinutes")) > 0]
    return add_entity(graph, "research-source-policy", plan_key, "공식 원문 우선 조사 정책", {
        "tboxClass": "ResearchSourcePolicy",
        "approvedSourceTypes": source_types,
        "maximumAgeMinutes": min(max_ages) if max_ages else 360,
        "cachePolicy": "cache-first",
        "maximumRounds": research_plan.get("maxRounds"),
        "investmentJudgmentEligibility": "verified-claims-only",
    })


def positive_int(value: object) -> int:
    try:
        return max(0, int(float(str(value or 0))))
    except (TypeError, ValueError):
        return 0


def add_verified_claim_concepts(
    graph: PortfolioOntology,
    run_id: str,
    stock_id: str,
    claims: Iterable[Dict[str, object]],
) -> None:
    for claim in claims or []:
        if not isinstance(claim, dict) or not str(claim.get("claimId") or "").strip():
            continue
        claim_key = str(claim.get("claimId") or "").strip()
        evidence_key = str(claim.get("evidenceId") or claim_key).strip()
        document_id = add_entity(graph, "retrieved-document", evidence_key, str(claim.get("statement") or evidence_key), {
            "tboxClass": "RetrievedDocument",
            "source": claim.get("source"),
            "sourceUrl": claim.get("sourceUrl"),
            "publishedAt": claim.get("publishedAt"),
            "observedAt": claim.get("observedAt"),
        })
        claim_id = add_entity(graph, "verified-claim", claim_key, str(claim.get("statement") or claim_key), {
            "tboxClass": "VerifiedClaim",
            "verificationStatus": claim.get("verificationStatus"),
            "entityResolutionStatus": claim.get("entityResolutionStatus"),
            "confidence": claim.get("confidence"),
            "evidenceId": evidence_key,
        })
        assessment_id = add_entity(graph, "evidence-assessment", claim_key, "근거 품질 검증", {
            "tboxClass": "EvidenceAssessment",
            "verificationStatus": claim.get("verificationStatus"),
            "entityResolutionStatus": claim.get("entityResolutionStatus"),
            "confidence": claim.get("confidence"),
            "reasons": claim.get("reasons") or [],
        })
        source_id = add_entity(graph, "research-source", str(claim.get("source") or "unknown"), str(claim.get("source") or "출처 미상"), {
            "tboxClass": "DataSource",
            "sourceUrl": claim.get("sourceUrl"),
        })
        add_relation(graph, run_id, claim_id, "PRODUCES_VERIFICATION_RESULT", weight=1.0, properties={"source": "investment-brain-research"})
        add_relation(graph, document_id, source_id, "RETRIEVED_FROM", weight=1.0, properties={"source": "investment-brain-research"})
        add_relation(graph, document_id, claim_id, "ASSERTS", weight=1.0, properties={"source": "investment-brain-research"})
        add_relation(graph, claim_id, stock_id, "RESOLVES_TO", weight=1.0, properties={"source": "investment-brain-research"})
        add_relation(graph, claim_id, assessment_id, "VERIFIED_BY", weight=1.0, properties={"source": "investment-brain-research"})


def add_novel_hypothesis_proposal_concepts(
    graph: PortfolioOntology,
    portfolio_id: str,
    proposals: Iterable[Dict[str, object]],
) -> None:
    portfolio_node_id = entity_id("portfolio", portfolio_id)
    for proposal in proposals or []:
        if not isinstance(proposal, dict):
            continue
        proposal_key = str(proposal.get("proposalId") or "").strip()
        symbol = str(proposal.get("symbol") or "").upper().strip()
        if not proposal_key or not symbol:
            continue
        proposal_id = add_entity(graph, "novel-hypothesis-proposal", proposal_key, str(proposal.get("title") or proposal.get("claim") or proposal_key), {
            "tboxClass": "NovelHypothesisProposal",
            "claim": proposal.get("claim"),
            "causalPath": proposal.get("causalPath") or [],
            "requiredEvidenceTypes": proposal.get("requiredEvidenceTypes") or [],
            "invalidationConditions": proposal.get("invalidationConditions") or [],
            "status": proposal.get("status"),
            "source": proposal.get("source"),
            "sourceQuestionId": proposal.get("sourceQuestionId"),
            "createdAt": proposal.get("createdAt"),
            "governance": "not-deployed-until-rulebox-promotion",
        })
        stock_id = entity_id("stock", symbol)
        add_relation(graph, proposal_id, stock_id, "PROPOSES_HYPOTHESIS_FOR", weight=1.0, properties={"source": "ai-hypothesis-governance"})
        add_relation(graph, portfolio_node_id, proposal_id, "HAS_EVIDENCE", weight=1.0, properties={"source": "ai-hypothesis-governance"})
        for evidence_key in proposal.get("supportingEvidenceIds") or []:
            evidence_id = add_entity(graph, "evidence-reference", str(evidence_key), str(evidence_key), {
                "tboxClass": "Evidence",
                "source": "hypothesis-proposal-reference",
            })
            add_relation(graph, proposal_id, evidence_id, "USED_AS_EVIDENCE", weight=1.0, properties={"polarity": "support"})
