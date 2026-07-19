from typing import Dict, Iterable

from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_schema import add_entity, add_relation


def add_investment_brain_concepts(
    graph: PortfolioOntology,
    portfolio_id: str,
    decision_episodes: Iterable[Dict[str, object]],
) -> None:
    portfolio_node_id = entity_id("portfolio", portfolio_id)
    for episode in decision_episodes or []:
        if not isinstance(episode, dict):
            continue
        episode_key = str(episode.get("episodeId") or "").strip()
        symbol = str(episode.get("symbol") or "").upper().strip()
        if not episode_key or not symbol:
            continue
        stock_id = entity_id("stock", symbol)
        question = episode.get("question") if isinstance(episode.get("question"), dict) else {}
        hypothesis_set = episode.get("hypothesisSet") if isinstance(episode.get("hypothesisSet"), dict) else {}
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
        hypothesis_ids = []
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
                "supportingRuleIds": hypothesis.get("supportingRuleIds") or [],
                "counterRuleIds": hypothesis.get("counterRuleIds") or [],
                "invalidationConditions": hypothesis.get("invalidationConditions") or [],
            })
            hypothesis_ids.append(hypothesis_id)
            if set_id:
                add_relation(graph, set_id, hypothesis_id, "CONTAINS_HYPOTHESIS", weight=1.0, properties={"source": "investment-brain-memory"})
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
