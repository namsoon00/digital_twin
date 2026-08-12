"""ABox projection for account lifecycle facts and policy arithmetic candidates."""

from typing import Dict

from .market_data import number
from .ontology_schema import add_entity, add_relation


def add_portfolio_lifecycle_concepts(graph, portfolio_node_id: str, runtime_context: Dict[str, object]) -> None:
    lifecycle = runtime_context.get("portfolioLifecycle") if isinstance(runtime_context, dict) else {}
    if not isinstance(lifecycle, dict) or not lifecycle:
        return

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

    activity_sync = lifecycle.get("brokerActivitySync") if isinstance(lifecycle.get("brokerActivitySync"), dict) else {}
    if activity_sync:
        sync_id = add_entity(
            graph,
            "broker-activity-sync",
            str(activity_sync.get("sync_id") or activity_sync.get("syncId") or "latest"),
            "증권사 거래 활동 동기화",
            {
                "tboxClass": "BrokerActivitySyncState",
                "provider": activity_sync.get("provider"),
                "status": activity_sync.get("status"),
                "importedCount": activity_sync.get("imported_count") or activity_sync.get("importedCount") or 0,
                "rejectedCount": activity_sync.get("rejected_count") or activity_sync.get("rejectedCount") or 0,
                "lastActivityAt": activity_sync.get("last_activity_at") or activity_sync.get("lastActivityAt"),
                "lastSuccessAt": activity_sync.get("last_success_at") or activity_sync.get("lastSuccessAt"),
                "missingData": list(activity_sync.get("missing_data") or activity_sync.get("missingData") or []),
                "source": "portfolio-lifecycle-store",
            },
        )
        add_relation(graph, portfolio_node_id, sync_id, "HAS_ACTIVITY_SYNC_STATE", properties={"source": "portfolio-lifecycle-store"})

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
