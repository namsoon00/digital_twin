"""Recognize the graph-proven, deterministic publication independently of AI."""

from digital_twin.modules.notifications.domain.context_observation_notifications import typedb_context_observation_contract


def independent_typedb_publication(context, *, account_id=None):
    """Writer flags alone cannot reopen the retired investment AI transport."""
    values = context if isinstance(context, dict) else {}
    writer = values.get("notificationWriterProvenance") or {}
    dispatch = values.get("inferenceDispatchDecision") or {}
    case = values.get("investmentSubjectDecisionCase") or {}
    if not all(isinstance(item, dict) for item in (writer, dispatch, case)):
        return False
    if not (
        values.get("notificationDecisionOwner") == "typedb"
        and writer.get("writerKind") == "deterministic"
        and writer.get("aiAuthored") is False
        and writer.get("decisionOwner") == "typedb"
        and writer.get("narrativeOwner") == "typedb"
        and dispatch.get("route") == "PUBLISH_TYPEDB"
    ):
        return False
    contract = typedb_context_observation_contract(values)
    if not contract or contract.get("action") != "NO_ACTION":
        return False
    case_id = case.get("subjectCaseId")
    generation = contract.get("inferenceGenerationId")
    abox = contract.get("sourceAboxSnapshotId")
    metadata = values.get("metadata") if isinstance(values.get("metadata"), dict) else {}
    relation = values.get("ontologyRelationContext") or metadata.get("ontologyRelationContext") or {}
    if not isinstance(relation, dict):
        return False
    graph = relation.get("graphStoreInference") or {}
    if not isinstance(graph, dict):
        return False
    for source in (relation, graph):
        if source.get("inferenceGenerationId") and source["inferenceGenerationId"] != generation:
            return False
        if source.get("sourceAboxSnapshotId") and source["sourceAboxSnapshotId"] != abox:
            return False
    return bool(
        case_id and case_id == dispatch.get("subjectCaseId")
        and case_id == values.get("investmentSubjectDecisionCaseId")
        and generation and generation == case.get("inferenceGenerationId")
        and generation == dispatch.get("inferenceGenerationId")
        and abox and abox == case.get("sourceAboxSnapshotId")
        and contract.get("symbol")
        and case.get("symbol") == contract.get("symbol") == dispatch.get("symbol")
        and case.get("accountId") and case.get("accountId") == dispatch.get("accountId")
        and (account_id is None or account_id == case.get("accountId"))
        and (not values.get("accountId") or values["accountId"] == case.get("accountId"))
    )
