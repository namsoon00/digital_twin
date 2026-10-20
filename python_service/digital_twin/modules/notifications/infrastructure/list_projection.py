"""Small persisted inbox fields, independent of the immutable evidence packet."""

import json
import time


PRESENTATION_PATHS = (
    "notificationContent.kind", "notificationContent.subject", "notificationSubject",
    "symbolDisplayName", "displayTarget", "target", "rawSymbol",
    "notificationDecisionMode", "notificationWriterProvenance.aiAuthored",
    "notificationWriterProvenance.writerRole", "notificationAiValidatedResponse.action",
    "validatedDecisionResponse.action", "notificationInferenceResponse.action",
    "decisionPublication.outcomeKind", "ontologyRelationContext.decision.selectedRuleId",
    "ontologyRelationContext.engine", "reasoningDeliveryTrigger.facts.cryptoTransitions",
    "reasoningDeliveryTrigger.facts.confirmedSignalTransitions",
    "customerInvestmentDocument.headline", "customerInvestmentDocument.lead",
    "notificationContent.summary",
)


def presentation_json(context):
    context = context if isinstance(context, dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}

    def at(source, path):
        for key in path.split("."):
            if not isinstance(source, dict):
                return None
            source = source.get(key)
        return source

    fields = {}
    for path in PRESENTATION_PATHS:
        value = at(context, path)
        if value is None or value == "null":
            value = at(metadata, path)
        if value is not None and value != "null":
            fields[path] = value
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":"))


def backfill_list_presentations(connection, limit=100, budget_seconds=5):
    """Resumable startup migration; never alter messages, evidence or timestamps.

    Load one legacy packet at a time. A concurrent writer wins over this
    projection through both the null and source-version predicates.
    """
    started = time.monotonic()
    rows = connection.execute(
        "SELECT job_id, updated_at FROM notification_jobs "
        "WHERE list_presentation_json IS NULL ORDER BY updated_at DESC, job_id DESC LIMIT %s",
        (max(1, min(100, int(limit))),),
    ).fetchall()
    changed = 0
    for row in rows:
        if time.monotonic() - started >= budget_seconds:
            break
        original = connection.execute(
            "SELECT payload_json FROM notification_jobs WHERE job_id = %s "
            "AND updated_at = %s AND list_presentation_json IS NULL",
            (row["job_id"], row["updated_at"]),
        ).fetchone()
        if not original:
            continue
        payload = json.loads(original["payload_json"])
        projected = presentation_json(payload.get("context"))
        changed += int(connection.execute(
            "UPDATE notification_jobs SET list_presentation_json = %s "
            "WHERE job_id = %s AND updated_at = %s AND list_presentation_json IS NULL",
            (projected, row["job_id"], row["updated_at"]),
        ).rowcount or 0)
    return changed
