"""Transport-only lineage. Matching text, prices or symbols never imply causality."""
import hashlib
import json
from digital_twin.modules.notifications.domain.relation_follow_up import delivered_continuity

TYPES = {"marketObservation", "investmentInsight", "aiObservation"}
VERSION = "investment-conversation-v1"


def key(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()


def relation_parent_job_id(job):
    context = job.context or {}
    evidence = context.get("relationChangeEvidence") or {}
    prior, follow_up = evidence.get("previous") or {}, evidence.get("followUp") or {}
    relation = context.get("ontologyRelationContext") or (context.get("metadata") or {}).get("ontologyRelationContext") or {}
    world = relation.get("worldId") or ""
    symbol = str(context.get("symbol") or context.get("rawSymbol") or (relation.get("subject") or {}).get("symbol") or "").strip().upper()
    if (job.message_type == "investmentInsight" and follow_up.get("version") == "relation-follow-up-v1"
            and prior.get("deliveredAt") and prior.get("receiptJobId") and world
            and prior.get("accountId") == job.account_id and prior.get("worldId") == world
            and prior.get("symbol", "").upper() == symbol):
        parent = follow_up.get("parentJobId") or ""
        history = delivered_continuity(prior)
        changes = evidence.get("transitions") or []
        if parent and changes and all((history.get(row.get("lifecycleKey")) or {}).get("rootJobId") == parent for row in changes):
            return parent
    return ""


def conversation_sources(job):
    if job.message_type not in TYPES or not job.account_id:
        return None
    context = job.context or {}
    if context.get("notificationReplayPreserveOriginal") or context.get("transportDelivery"):
        return None  # Never retrofit the destination of an existing delivery.
    relation = context.get("ontologyRelationContext") or (context.get("metadata") or {}).get("ontologyRelationContext") or {}
    symbol = str(context.get("symbol") or context.get("rawSymbol") or (relation.get("subject") or {}).get("symbol") or "").strip().upper()
    if not symbol or context.get("accountId", job.account_id) != job.account_id:
        return None
    world, sources, parent = "", [], ""
    if job.message_type == "marketObservation":
        movement = context.get("priceMovement") or (context.get("metadata") or {}).get("priceMovement") or {}
        if movement.get("version") == "price-movement-episode-v1" and movement.get("episodeId"):
            sources.append(("price-movement", "", movement["episodeId"]))
            if movement.get("reversalOf"):
                sources.append(("price-movement", "", movement["reversalOf"]))
    elif job.message_type == "investmentInsight":
        origin = context.get("notificationAnalysisSources") or {}
        relation = context.get("ontologyRelationContext") or (context.get("metadata") or {}).get("ontologyRelationContext") or {}
        world = relation.get("worldId") or origin.get("worldId") or ""
        if origin.get("worldId") and relation.get("worldId") and origin["worldId"] != relation["worldId"]:
            return None
        if origin.get("accountId") == job.account_id and origin.get("symbol", "").upper() == symbol:
            sources += [("event", "", value) for value in origin.get("sourceEventIds", []) if value]
        snapshot = relation.get("sourceAboxSnapshotId") or context.get("sourceAboxSnapshotId")
        if world and snapshot:
            sources.append(("graph", world, snapshot))
        parent = relation_parent_job_id(job)
    elif job.message_type == "aiObservation":
        result = context.get("aiControlObservation") or {}
        packet = result.get("input") or {}
        if packet.get("accountId") != job.account_id or packet.get("symbol", "").upper() != symbol:
            return None
        world = packet.get("worldId") or ""
        if world and packet.get("sourceSnapshotId"):
            sources.append(("graph", world, packet["sourceSnapshotId"]))
        baseline = packet.get("lastDeliveredNotification") or {}
        # A verified condition transition explicitly continues an earlier question.
        if baseline.get("accountId", job.account_id) == job.account_id and baseline.get("symbol", symbol) == symbol and any(
                row.get("transitionVerified") is True and row.get("baselineJobId") == baseline.get("jobId")
                for row in packet.get("followUpEvaluations", [])):
            parent = baseline.get("jobId") or ""
    return {"accountId": job.account_id, "symbol": symbol, "worldId": world,
            "sources": sorted(set(sources)), "parentJobId": parent}
