"""Maintain the decision-memory read model on the owner's transaction."""

from digital_twin.modules.decisions.domain.decision_projection_input import (
    DECISION_PROJECTION_INPUT_VERSION, decision_projection_input,
)
from digital_twin.infrastructure.operational_common import json_dumps


def upsert_decision_projection_input(connection, episode_id, payload, stamp):
    return connection.execute(
        "INSERT INTO investment_decision_projection_inputs "
        "(episode_id, format_version, source_updated_at, payload_json) "
        "VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
        "format_version = VALUES(format_version), "
        "source_updated_at = VALUES(source_updated_at), payload_json = VALUES(payload_json)",
        (episode_id, DECISION_PROJECTION_INPUT_VERSION, stamp,
         json_dumps(decision_projection_input(payload))),
    )
