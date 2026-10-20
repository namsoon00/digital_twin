"""Source-bound portfolio facts shared by both independent validation jobs."""

from copy import deepcopy

from .reasoning_shadow import pack_projection_runtime_contexts, unpack_projection_runtime_contexts


def freeze_validation_portfolio_input(account_id, source_snapshot_id, as_of, context):
    if not account_id or not source_snapshot_id or not as_of:
        raise ValueError("Validation portfolio input requires an exact source boundary")
    if context.get("portfolioId") != "portfolio:" + account_id or context.get("asOf") != as_of:
        raise ValueError("Validation portfolio input does not match the source cutoff")
    return pack_projection_runtime_contexts({account_id: {
        "accountId": account_id, "sourceSnapshotId": source_snapshot_id,
        "asOf": as_of, "portfolioLifecycle": deepcopy(context),
    }})


def read_validation_portfolio_input(packet, account_id, source_snapshot_id, as_of):
    contexts = unpack_projection_runtime_contexts(packet)
    if set(contexts) != {account_id}:
        raise ValueError("Validation portfolio input account mismatch")
    row = contexts[account_id]
    if (row.get("accountId") != account_id or row.get("sourceSnapshotId") != source_snapshot_id
            or row.get("asOf") != as_of):
        raise ValueError("Validation portfolio input source boundary mismatch")
    context = row.get("portfolioLifecycle")
    if (not isinstance(context, dict) or context.get("portfolioId") != "portfolio:" + account_id
            or context.get("asOf") != as_of):
        raise ValueError("Validation portfolio input cutoff is missing")
    return deepcopy(context)
