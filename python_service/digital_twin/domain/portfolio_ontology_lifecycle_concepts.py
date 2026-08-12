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
