"""ABox projection for account lifecycle facts and policy arithmetic candidates."""

from typing import Dict

from .market_data import number
from .ontology_schema import add_entity, add_relation
from .ontology_contracts import entity_id


def add_portfolio_lifecycle_concepts(graph, portfolio_node_id: str, runtime_context: Dict[str, object]) -> None:
    lifecycle = runtime_context.get("portfolioLifecycle") if isinstance(runtime_context, dict) else {}
    if not isinstance(lifecycle, dict) or not lifecycle:
        return
    existing_entity_ids = {item.entity_id for item in graph.entities}

    def available_stock_id(symbol: object) -> str:
        stock_id = entity_id("stock", str(symbol or "").upper().strip())
        return stock_id if stock_id in existing_entity_ids else ""

    reconciliation = lifecycle.get("reconciliation") if isinstance(lifecycle.get("reconciliation"), dict) else {}
    if reconciliation:
        reconciliation_id = add_entity(
            graph,
            "portfolio-reconciliation",
            str(reconciliation.get("reconciliationId") or reconciliation.get("reconciliation_id") or "latest"),
            "계좌 원장 대사",
            {
                "tboxClass": "PortfolioReconciliation",
                "status": reconciliation.get("status"),
                "sourceSnapshotAt": reconciliation.get("sourceSnapshotAt") or reconciliation.get("source_snapshot_at"),
                "balanceFingerprint": reconciliation.get("balanceFingerprint") or reconciliation.get("balance_fingerprint"),
                "differenceCount": len(reconciliation.get("differences") or []),
                "source": "portfolio-lifecycle-store",
            },
        )
        add_relation(graph, reconciliation_id, portfolio_node_id, "RECONCILES_PORTFOLIO", properties={"source": "portfolio-lifecycle-store"})

    activities = lifecycle.get("inferredActivities") or lifecycle.get("recentInferredActivities") or []
    activity_ids = {}
    for activity in activities[:20] if isinstance(activities, list) else []:
        if not isinstance(activity, dict):
            continue
        symbol = str(activity.get("symbol") or "").upper().strip()
        stock_id = available_stock_id(symbol) if symbol else ""
        if symbol and not stock_id:
            continue
        activity_id = add_entity(
            graph,
            "inferred-portfolio-activity",
            str(activity.get("entryId") or activity.get("entry_id") or "latest"),
            str(activity.get("instrumentName") or activity.get("symbol") or "현금 잔액") + " 잔고 변화",
            {
                "tboxClass": "InferredPortfolioActivity",
                "entryType": activity.get("entryType") or activity.get("entry_type"),
                "classification": activity.get("classification"),
                "confidence": activity.get("confidence"),
                "symbol": activity.get("symbol"),
                "currency": activity.get("currency"),
                "previousQuantity": activity.get("previousQuantity"),
                "observedQuantity": activity.get("observedQuantity"),
                "quantityDelta": activity.get("quantityDelta"),
                "cashDelta": activity.get("cashDelta"),
                "previousSnapshotAt": activity.get("previousSnapshotAt"),
                "currentSnapshotAt": activity.get("currentSnapshotAt"),
                "replaceableByActualActivity": bool(activity.get("replaceableByActualActivity")),
                "realizedProfitLossKnown": bool(activity.get("realizedProfitLossKnown")),
                "executable": False,
                "source": "complete-account-snapshot-difference",
            },
        )
        add_relation(graph, portfolio_node_id, activity_id, "RECORDS_PORTFOLIO_ACTIVITY", properties={"source": "portfolio-lifecycle-store"})
        add_relation(graph, activity_id, portfolio_node_id, "INFERRED_FROM_SNAPSHOT_CHANGE", properties={"source": "complete-account-snapshot-difference"})
        if stock_id:
            add_relation(graph, stock_id, activity_id, "HAS_PORTFOLIO_ACTIVITY", properties={
                "source": "complete-account-snapshot-difference",
                "classification": activity.get("classification"),
                "confidence": activity.get("confidence"),
            })
        activity_ids[str(activity.get("entryId") or activity.get("entry_id") or "")] = activity_id

    episodes = lifecycle.get("recentActivityEpisodes") or (
        [lifecycle.get("activityEpisode")] if isinstance(lifecycle.get("activityEpisode"), dict) and lifecycle.get("activityEpisode") else []
    )
    for episode in episodes[:8] if isinstance(episodes, list) else []:
        if not isinstance(episode, dict):
            continue
        scoped_symbols = [
            str(symbol or "").upper().strip()
            for symbol in episode.get("symbols") or []
            if available_stock_id(symbol)
        ]
        if episode.get("symbols") and not scoped_symbols:
            continue
        episode_id = add_entity(
            graph,
            "portfolio-activity-episode",
            str(episode.get("episodeId") or "latest"),
            " · ".join(episode.get("symbols") or []) + " 계좌 변화" if episode.get("symbols") else "현금 잔고 변화",
            {
                "tboxClass": "PortfolioActivityEpisode",
                "classification": episode.get("classification"),
                "confidence": episode.get("confidence"),
                "observedAt": episode.get("observedAt"),
                "previousObservedAt": episode.get("previousObservedAt"),
                "cashDelta": number(episode.get("cashDelta")),
                "estimatedNotional": number(episode.get("estimatedNotional")),
                "symbolCount": len(episode.get("symbols") or []),
                "replaceableByActualActivity": True,
                "executable": False,
                "source": "portfolio-activity-episode-store",
            },
        )
        add_relation(graph, portfolio_node_id, episode_id, "RECORDS_PORTFOLIO_ACTIVITY", properties={"source": "portfolio-activity-episode-store"})
        for entry_id in episode.get("ledgerEntryIds") or []:
            if activity_ids.get(str(entry_id)):
                add_relation(graph, episode_id, activity_ids[str(entry_id)], "GROUPS_LEDGER_ACTIVITY", properties={"source": "portfolio-activity-episode-store"})
        for symbol in scoped_symbols:
            add_relation(graph, available_stock_id(symbol), episode_id, "HAS_PORTFOLIO_ACTIVITY", properties={
                "source": "portfolio-activity-episode-store",
                "classification": episode.get("classification"),
                "confidence": episode.get("confidence"),
            })

    state = lifecycle.get("portfolioState") if isinstance(lifecycle.get("portfolioState"), dict) else {}
    for item in state.get("positions") or []:
        if not isinstance(item, dict) or not str(item.get("symbol") or "").strip():
            continue
        symbol = str(item.get("symbol") or "").upper().strip()
        stock_id = available_stock_id(symbol)
        if not stock_id:
            continue
        state_id = add_entity(
            graph,
            "portfolio-state",
            str(state.get("stateId") or "latest") + ":" + symbol,
            str(item.get("name") or symbol) + " 원장 기반 상태",
            {
                "tboxClass": "PortfolioStateSnapshot",
                **dict(item),
                "cashWeightPct": number(state.get("cashWeightPct")),
                "sourceCheckpointVersion": state.get("sourceCheckpointVersion"),
                "observedAt": state.get("observedAt"),
                "executable": False,
                "source": "portfolio-state-store",
            },
        )
        add_relation(graph, stock_id, state_id, "HAS_PORTFOLIO_STATE", properties={"source": "portfolio-state-store"})

    for observation in (lifecycle.get("decisionActionObservations") or [])[:8]:
        if not isinstance(observation, dict) or not str(observation.get("symbol") or "").strip():
            continue
        symbol = str(observation.get("symbol") or "").upper().strip()
        stock_id = available_stock_id(symbol)
        if not stock_id:
            continue
        observation_id = add_entity(
            graph,
            "decision-action-observation",
            str(observation.get("observationId") or "latest"),
            symbol + " 판단 이후 계좌 행동 관측",
            {
                "tboxClass": "DecisionActionObservation",
                **dict(observation),
                "executable": False,
                "source": "decision-action-observation-store",
            },
        )
        add_relation(graph, stock_id, observation_id, "OBSERVES_ACCOUNT_ACTION", properties={"source": "decision-action-observation-store"})
        prior_id = str(observation.get("priorDecisionEpisodeId") or "")
        prior_entity_id = entity_id("decision-episode", prior_id) if prior_id else ""
        if prior_entity_id in existing_entity_ids:
            add_relation(graph, observation_id, prior_entity_id, "OBSERVED_AFTER_DECISION", properties={"causalityClaimed": False})

    risk = lifecycle.get("portfolioRiskSnapshot") if isinstance(lifecycle.get("portfolioRiskSnapshot"), dict) else {}
    if risk:
        risk_id = add_entity(graph, "portfolio-risk-snapshot", str(risk.get("riskSnapshotId") or "latest"),
            "포트폴리오 시계열 위험", {
                "tboxClass": "PortfolioRiskSnapshot",
                "annualizedVolatilityPct": number(risk.get("annualizedVolatilityPct")),
                "maximumDrawdownPct": number(risk.get("maximumDrawdownPct")),
                "maximumPairwiseCorrelation": number(risk.get("maximumPairwiseCorrelation")),
                "periodReturnPct": number(risk.get("periodReturnPct")),
                "activeReturnPct": number(risk.get("activeReturnPct")),
                "volatilityPolicyDeltaPct": number(risk.get("volatilityPolicyDeltaPct")),
                "drawdownPolicyDeltaPct": number(risk.get("drawdownPolicyDeltaPct")),
                "correlationPolicyDelta": number(risk.get("correlationPolicyDelta")),
                "sampleCount": number(risk.get("sampleCount")),
                "dataState": risk.get("dataState"), "observedAt": risk.get("observedAt"),
                "source": "portfolio-risk-analytics",
            })
        add_relation(graph, portfolio_node_id, risk_id, "HAS_RISK_SNAPSHOT", properties={"source": "portfolio-risk-analytics"})
        if number(risk.get("correlationPolicyDelta")) > 0:
            add_relation(graph, portfolio_node_id, risk_id, "HAS_CORRELATION_RISK", properties={"source": "portfolio-risk-analytics"})
        for metric in risk.get("positions") or []:
            if not isinstance(metric, dict):
                continue
            symbol = str(metric.get("symbol") or "").upper().strip()
            stock_id = available_stock_id(symbol)
            if not stock_id:
                continue
            metric_id = add_entity(graph, "position-risk-metric",
                str(risk.get("riskSnapshotId") or "latest") + ":" + symbol,
                symbol + " 시계열 위험", {
                    "tboxClass": "PositionRiskMetric", "symbol": symbol,
                    "positionWeight": number(metric.get("weight_pct") or metric.get("weightPct")),
                    "periodReturnPct": number(metric.get("period_return_pct") or metric.get("periodReturnPct")),
                    "annualizedVolatilityPct": number(metric.get("annualized_volatility_pct") or metric.get("annualizedVolatilityPct")),
                    "maximumDrawdownPct": number(metric.get("maximum_drawdown_pct") or metric.get("maximumDrawdownPct")),
                    "beta": metric.get("beta"),
                    "activeReturnPct": metric.get("active_return_pct") or metric.get("activeReturnPct"),
                    "sampleCount": number(metric.get("sample_count") or metric.get("sampleCount")),
                    "dataState": metric.get("data_state") or metric.get("dataState"),
                    "source": "portfolio-risk-analytics",
                })
            add_relation(graph, stock_id, metric_id, "HAS_POSITION_RISK", properties={"source": "portfolio-risk-analytics"})
            benchmark_symbol = str(metric.get("benchmark_symbol") or metric.get("benchmarkSymbol") or "").upper().strip()
            if benchmark_symbol and metric.get("beta") is not None:
                benchmark_id = add_entity(graph, "benchmark-index",
                    str(risk.get("riskSnapshotId") or "latest") + ":" + symbol + ":" + benchmark_symbol,
                    benchmark_symbol + " 실측 베타", {
                        "tboxClass": "BenchmarkIndex", "symbol": benchmark_symbol,
                        "beta": number(metric.get("beta")),
                        "periodReturnPct": number(metric.get("benchmark_return_pct") or metric.get("benchmarkReturnPct")),
                        "sampleCount": number(metric.get("sample_count") or metric.get("sampleCount")),
                        "dataState": metric.get("data_state") or metric.get("dataState"),
                        "source": "portfolio-risk-analytics",
                    })
                add_relation(graph, stock_id, benchmark_id, "HAS_BETA_TO", properties={"source": "portfolio-risk-analytics"})

    rebalance = lifecycle.get("rebalanceProposal") if isinstance(lifecycle.get("rebalanceProposal"), dict) else {}
    if rebalance:
        proposal_id = add_entity(graph, "rebalance-proposal", str(rebalance.get("proposalId") or "latest"),
            "포트폴리오 리밸런싱 제안", {
                "tboxClass": "RebalanceProposal", "mandateVersion": rebalance.get("mandateVersion"),
                "scenarioCount": len(rebalance.get("scenarios") or []), "status": rebalance.get("status"),
                "createdAt": rebalance.get("createdAt"), "source": "portfolio-rebalance-analysis",
            })
        add_relation(graph, portfolio_node_id, proposal_id, "HAS_REBALANCE_PROPOSAL", properties={"source": "portfolio-rebalance-analysis"})
        for scenario in rebalance.get("scenarios") or []:
            if not isinstance(scenario, dict):
                continue
            scenario_id = add_entity(graph, "rebalance-scenario",
                str(scenario.get("scenario_id") or scenario.get("scenarioId") or "scenario"),
                str(scenario.get("label") or "리밸런싱 시나리오"), {
                    "tboxClass": "RebalanceScenario",
                    "scenarioType": scenario.get("scenario_type") or scenario.get("scenarioType"),
                    "estimatedCost": number(scenario.get("estimated_cost") or scenario.get("estimatedCost")),
                    "turnoverPct": number(scenario.get("turnover_pct") or scenario.get("turnoverPct")),
                    "dataState": scenario.get("data_state") or scenario.get("dataState"),
                    "source": "portfolio-rebalance-analysis",
                })
            add_relation(graph, proposal_id, scenario_id, "HAS_REBALANCE_SCENARIO", properties={"source": "portfolio-rebalance-analysis"})

    cycle = lifecycle.get("portfolioDecisionCycle") if isinstance(lifecycle.get("portfolioDecisionCycle"), dict) else {}
    if not cycle:
        return
    cycle_id = add_entity(
        graph,
        "portfolio-decision-cycle",
        str(cycle.get("cycleId") or "latest"),
        "계좌 의사결정 후보 주기",
        {
            "tboxClass": "PortfolioDecisionCycle",
            "policyVersion": cycle.get("policyVersion"),
            "sourceSnapshotId": cycle.get("sourceSnapshotId"),
            "candidateFingerprint": cycle.get("candidateFingerprint"),
            "candidateCount": len(cycle.get("candidates") or []),
            "dataState": cycle.get("dataState"),
            "missingData": list(cycle.get("missingData") or []),
            "createdAt": cycle.get("createdAt"),
            "source": "portfolio-lifecycle-store",
        },
    )
    add_relation(graph, portfolio_node_id, cycle_id, "OBSERVES_DECISION_CYCLE", properties={"source": "portfolio-lifecycle-store"})
    for candidate in cycle.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_id = add_entity(
            graph,
            "portfolio-action-candidate",
            str(candidate.get("candidate_id") or candidate.get("candidateId") or "candidate"),
            str(candidate.get("label") or "정책 산술 행동 후보"),
            {
                "tboxClass": "PortfolioActionCandidate",
                "candidateType": candidate.get("candidate_type") or candidate.get("candidateType"),
                "affectedSymbol": candidate.get("affected_symbol") or candidate.get("affectedSymbol"),
                "maximumNotional": number(candidate.get("maximum_notional") or candidate.get("maximumNotional")),
                "beforeMetrics": dict(candidate.get("before_metrics") or candidate.get("beforeMetrics") or {}),
                "afterMetrics": dict(candidate.get("after_metrics") or candidate.get("afterMetrics") or {}),
                "policyEffects": list(candidate.get("policy_effects") or candidate.get("policyEffects") or []),
                "requiredRelationTypes": list(candidate.get("required_relation_types") or candidate.get("requiredRelationTypes") or []),
                "dataState": candidate.get("data_state") or candidate.get("dataState"),
                "executable": False,
                "source": "policy-bounded-arithmetic",
            },
        )
        add_relation(graph, cycle_id, candidate_id, "EVALUATES_PORTFOLIO_CANDIDATE", properties={"source": "policy-bounded-arithmetic"})
