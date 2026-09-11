"""Atomic receipt/anchor writes shared by completion and persisted-result repair."""

from typing import Dict, Mapping
from digital_twin.domain.market_observation_reasoning import (
    COMPLETION_MODE_VERIFIED_LATER_BOUNDARY,
    MARKET_OBSERVATION_REASONING_RECEIPT_VERSION,
    MarketObservationReasoningReceipt,
    completion_mode,
    market_observation_completion_scope,
)
from digital_twin.infrastructure.storage_values import json_value
from digital_twin.infrastructure.transaction_port import BoundWriteConnection


def publish_job_receipts(
    connection: BoundWriteConnection,
    job_id: str,
    values: Mapping[str, object],
    stamp: str,
) -> Dict[str, object]:
    completion_summary = {
        "contractVersion": MARKET_OBSERVATION_REASONING_RECEIPT_VERSION,
        "anchorCompletionAtomic": True,
        "status": "not-required",
        "completedCount": 0,
        "receiptCount": 0,
        "eventIds": [],
        "accountIds": [],
        "symbols": [],
        "receipts": [],
    }
    job = (
        connection.execute(
            "SELECT job_id, deployment_id, source_event_id, source_snapshot_id, source_snapshot_at, "
            "request_json, release_fingerprint FROM reasoning_engine_jobs "
            "WHERE job_id = %s FOR UPDATE",
            (str(job_id or ""),),
        ).fetchone()
        or {}
    )
    if not job:
        raise RuntimeError(
            "The V2 reasoning job disappeared before completion publication."
        )
    request_json = json_value(job.get("request_json"), {})
    source_event = dict(request_json.get("sourceEvent") or {})
    scope = market_observation_completion_scope(source_event)
    lineage_cursor = connection.execute(
        "SELECT source_event_id, account_id, symbol, source_snapshot_id, source_snapshot_at, "
        "representation_mode FROM reasoning_engine_job_sources "
        "WHERE survivor_job_id = %s ORDER BY source_event_id, account_id, symbol",
        (str(job_id or ""),),
    )
    lineage_rows = (
        lineage_cursor.fetchall() or []
        if callable(getattr(lineage_cursor, "fetchall", None))
        else []
    )
    evaluated_symbols = {
        str(value or "").upper().strip()
        for value in values.get("evaluated_symbols")
        or values.get("evaluatedSymbols")
        or []
        if str(value or "").strip()
    }
    account_ids = {
        str(value or "").strip()
        for value in values.get("account_ids") or values.get("accountIds") or []
        if str(value or "").strip()
    }
    projection_results = dict(
        values.get("projection_results") or values.get("projectionResults") or {}
    )
    account_ids.update(
        (
            str(value or "").strip()
            for value in projection_results
            if str(value or "").strip()
        )
    )
    if not account_ids:
        account_ids.update(scope["accountIds"])
    if not evaluated_symbols:
        evaluated_symbols.update(scope["symbols"])
    event_ids = tuple(
        sorted(
            {
                *scope["eventIds"],
                *{
                    str(row.get("source_event_id") or "").strip()
                    for row in lineage_rows
                    if str(row.get("source_event_id") or "").strip()
                },
            }
        )
    )
    matching_anchors = []
    if account_ids and evaluated_symbols:
        account_placeholders = ",".join(["%s"] * len(account_ids))
        symbol_placeholders = ",".join(["%s"] * len(evaluated_symbols))
        source_snapshot_at = str(job.get("source_snapshot_at") or "").strip()
        identity_clause = ""
        identity_params = ()
        if event_ids:
            event_placeholders = ",".join(["%s"] * len(event_ids))
            identity_clause = "pending_event_id IN (" + event_placeholders + ")"
            identity_params = tuple(event_ids)
        if source_snapshot_at:
            boundary_clause = "(pending_at <> '' AND pending_at <= %s)"
            identity_clause = (
                "(" + identity_clause + " OR " + boundary_clause + ")"
                if identity_clause
                else boundary_clause
            )
            identity_params = identity_params + (source_snapshot_at,)
        if not identity_clause:
            identity_clause = "1 = 0"
        matching_anchors = connection.execute(
            "SELECT account_id, symbol, pending_event_id FROM market_observation_reasoning_anchors WHERE pending_event_id <> '' AND "
            + identity_clause
            + " AND account_id IN ("
            + account_placeholders
            + ") AND symbol IN ("
            + symbol_placeholders
            + ") FOR UPDATE",
            identity_params
            + tuple(sorted(account_ids))
            + tuple(sorted(evaluated_symbols)),
        ).fetchall()
    release_identity = dict(
        values.get("release_identity") or values.get("releaseIdentity") or {}
    )
    release_fingerprint = str(
        release_identity.get("releaseFingerprint")
        or values.get("release_fingerprint")
        or values.get("releaseFingerprint")
        or job.get("release_fingerprint")
        or ""
    )
    tbox_fingerprint = str(release_identity.get("tboxFingerprint") or "")
    tbox_release_id = str(release_identity.get("tboxReleaseId") or "")
    rulebox_release_id = str(release_identity.get("ruleboxReleaseId") or "")
    rulebox_fingerprint = str(release_identity.get("ruleboxFingerprint") or "")
    receipts = []
    for anchor in matching_anchors or []:
        account_id = str(anchor.get("account_id") or "").strip()
        symbol = str(anchor.get("symbol") or "").upper().strip()
        represented_event_id = str(anchor.get("pending_event_id") or "").strip()
        projection = dict(projection_results.get(account_id) or {})
        receipt_mode = (
            completion_mode(str(job.get("source_event_id") or ""), represented_event_id)
            if represented_event_id in event_ids
            else COMPLETION_MODE_VERIFIED_LATER_BOUNDARY
        )
        receipt = MarketObservationReasoningReceipt(
            source_event_id=represented_event_id,
            account_id=account_id,
            symbol=symbol,
            survivor_job_id=str(job_id or ""),
            deployment_id=str(job.get("deployment_id") or ""),
            source_snapshot_id=str(job.get("source_snapshot_id") or ""),
            source_snapshot_at=str(job.get("source_snapshot_at") or ""),
            source_abox_snapshot_id=str(
                projection.get("sourceAboxSnapshotId")
                or projection.get("source_abox_snapshot_id")
                or ""
            ),
            inference_generation_id=str(
                projection.get("inferenceGenerationId")
                or projection.get("inference_generation_id")
                or ""
            ),
            release_fingerprint=release_fingerprint,
            tbox_release_id=tbox_release_id,
            tbox_fingerprint=tbox_fingerprint,
            rulebox_release_id=rulebox_release_id,
            rulebox_fingerprint=rulebox_fingerprint,
            completion_mode=receipt_mode,
            completed_at=stamp,
        ).to_dict()
        connection.execute(
            """
                    INSERT INTO market_observation_reasoning_receipts (
                        source_event_id, account_id, symbol, survivor_job_id, deployment_id,
                        source_snapshot_id, source_snapshot_at, source_abox_snapshot_id,
                        inference_generation_id, release_fingerprint,
                        tbox_release_id, tbox_fingerprint,
                        rulebox_release_id, rulebox_fingerprint,
                        completion_mode, completed_at, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        survivor_job_id = VALUES(survivor_job_id),
                        deployment_id = VALUES(deployment_id),
                        source_snapshot_id = VALUES(source_snapshot_id),
                        source_snapshot_at = VALUES(source_snapshot_at),
                        source_abox_snapshot_id = VALUES(source_abox_snapshot_id),
                        inference_generation_id = VALUES(inference_generation_id),
                        release_fingerprint = VALUES(release_fingerprint),
                        tbox_release_id = VALUES(tbox_release_id),
                        tbox_fingerprint = VALUES(tbox_fingerprint),
                        rulebox_release_id = VALUES(rulebox_release_id),
                        rulebox_fingerprint = VALUES(rulebox_fingerprint),
                        completion_mode = VALUES(completion_mode),
                        completed_at = VALUES(completed_at)
                    """,
            (
                receipt["sourceEventId"],
                receipt["accountId"],
                receipt["symbol"],
                receipt["survivorJobId"],
                receipt["deploymentId"],
                receipt["sourceSnapshotId"],
                receipt["sourceSnapshotAt"],
                receipt["sourceAboxSnapshotId"],
                receipt["inferenceGenerationId"],
                receipt["releaseFingerprint"],
                receipt["tboxReleaseId"],
                receipt["tboxFingerprint"],
                receipt["ruleboxReleaseId"],
                receipt["ruleboxFingerprint"],
                receipt["completionMode"],
                stamp,
                stamp,
            ),
        )
        updated = connection.execute(
            """
                    UPDATE market_observation_reasoning_anchors
                    SET completed_price = pending_price, completed_at = %s,
                        pending_price = 0, pending_event_id = '', pending_at = '', updated_at = %s
                    WHERE account_id = %s AND symbol = %s AND pending_event_id = %s
                    """,
            (stamp, stamp, account_id, symbol, represented_event_id),
        )
        if int(getattr(updated, "rowcount", 0) or 0) == 1:
            receipts.append(receipt)
    completion_summary.update(
        {
            "status": "completed" if receipts else "not-required",
            "completedCount": len(receipts),
            "receiptCount": len(receipts),
            "eventIds": sorted({item["sourceEventId"] for item in receipts}),
            "accountIds": sorted({item["accountId"] for item in receipts}),
            "symbols": sorted({item["symbol"] for item in receipts}),
            "receipts": receipts,
        }
    )
    return completion_summary
