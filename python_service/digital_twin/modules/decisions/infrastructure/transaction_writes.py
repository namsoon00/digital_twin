"""Connection-bound writes; the caller owns commit, rollback and ordering."""

from __future__ import annotations
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from typing import Any, Callable
import gzip
from digital_twin.modules.decisions.domain.ai_inference_queue import AI_INFERENCE_COMPLETED, AI_INFERENCE_SUPERSEDED, AIInferenceRequest
from digital_twin.infrastructure.operational_common import json_dumps


def insert_ai_request(
    connection: BoundWriteConnection,
    request: AIInferenceRequest,
    *,
    _bound_compact_ai_queue_context: Callable[..., Any],
):
    durable_context = _bound_compact_ai_queue_context(request.context)
    connection.execute(
        """
            INSERT INTO ai_inference_requests (
                request_id, notification_job_id, origin_kind, origin_id,
                material_fingerprint, account_id, account_label,
                message_type, subject_key, symbol, inference_generation_id,
                context_hash, prompt_version, model, reasoning_effort, priority,
                status, attempts, available_at, lease_owner, lease_expires_at,
                heartbeat_at, superseded_by, created_at, updated_at, started_at,
                completed_at, last_error, context_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
        (
            request.request_id,
            request.notification_job_id,
            request.origin_kind,
            request.origin_id,
            request.material_fingerprint,
            request.account_id,
            request.account_label,
            request.message_type,
            request.subject_key,
            request.symbol,
            request.inference_generation_id,
            request.context_hash,
            request.prompt_version,
            request.model,
            request.reasoning_effort,
            request.priority,
            request.status,
            request.attempts,
            request.available_at,
            request.lease_owner,
            request.lease_expires_at,
            request.heartbeat_at,
            request.superseded_by,
            request.created_at,
            request.updated_at,
            request.started_at,
            request.completed_at,
            request.last_error,
            json_dumps(durable_context),
        ),
    )


def supersede_unpublishable_ai_request(
    connection: BoundWriteConnection,
    request: Any,
    stamp: Any,
):
    return connection.execute(
        """
                        UPDATE ai_inference_requests SET status = %s, lease_owner = '',
                            lease_expires_at = '', completed_at = %s, updated_at = %s
                        WHERE request_id = %s
                        """,
        (AI_INFERENCE_SUPERSEDED, stamp, stamp, request.request_id),
    )


def insert_ai_execution_audit(
    connection: BoundWriteConnection,
    artifact_fingerprint: Any,
    audit_json: Any,
    execution_audit: Any,
    request: Any,
    stamp: Any,
    *,
    _bound__clean: Callable[..., Any],
):
    return connection.execute(
        "INSERT INTO ai_inference_execution_audits ("
        "request_id, notification_job_id, artifact_fingerprint, prompt_hash, model, "
        "reasoning_effort, artifact_gzip, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            request.request_id,
            request.notification_job_id,
            artifact_fingerprint,
            _bound__clean(execution_audit.get("promptHash")),
            request.model,
            request.reasoning_effort,
            gzip.compress(audit_json.encode("utf-8"), compresslevel=6),
            stamp,
        ),
    )


def upsert_ai_result(
    connection: BoundWriteConnection,
    ai_authored: Any,
    contract_error: Any,
    publication_contract_passed: Any,
    publication_mode: Any,
    result: Any,
    *,
    _bound_ai_contract_failure_code: Callable[..., Any],
):
    return connection.execute(
        """
                INSERT INTO ai_inference_results (
                    result_id, request_id, notification_job_id, model,
                    reasoning_effort, source, validation_state, publication_mode,
                    ai_authored, publication_contract_passed, contract_failure_code, latency_ms,
                    prompt_bytes, response_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE result_id = VALUES(result_id),
                    source = VALUES(source), validation_state = VALUES(validation_state),
                    publication_mode = VALUES(publication_mode), ai_authored = VALUES(ai_authored),
                    publication_contract_passed = VALUES(publication_contract_passed),
                    contract_failure_code = VALUES(contract_failure_code),
                    latency_ms = VALUES(latency_ms), prompt_bytes = VALUES(prompt_bytes),
                    response_json = VALUES(response_json), created_at = VALUES(created_at)
                """,
        (
            result.result_id,
            result.request_id,
            result.notification_job_id,
            result.model,
            result.reasoning_effort,
            result.source,
            result.validation_state,
            publication_mode,
            1 if ai_authored else 0,
            1 if publication_contract_passed else 0,
            _bound_ai_contract_failure_code(contract_error),
            result.latency_ms,
            result.prompt_bytes,
            json_dumps(result.response),
            result.created_at,
        ),
    )


def complete_ai_request(
    connection: BoundWriteConnection,
    request: Any,
    stamp: Any,
):
    return connection.execute(
        """
                UPDATE ai_inference_requests
                SET status = %s, lease_owner = '', lease_expires_at = '',
                    heartbeat_at = '', completed_at = %s, updated_at = %s,
                    last_error = '', context_json = '{}'
                WHERE request_id = %s
                """,
        (AI_INFERENCE_COMPLETED, stamp, stamp, request.request_id),
    )


def supersede_unreleased_ai_request(
    connection: BoundWriteConnection,
    request: Any,
    stamp: Any,
):
    return connection.execute(
        """
                    UPDATE ai_inference_requests SET status = %s, lease_owner = '',
                        lease_expires_at = '', completed_at = %s, updated_at = %s,
                        last_error = '' WHERE request_id = %s
                    """,
        (AI_INFERENCE_SUPERSEDED, stamp, stamp, request.request_id),
    )


def insert_ai_insight_episode(
    connection: BoundWriteConnection,
    insight_episode: Any,
):
    return connection.execute(
        """
                    INSERT INTO investment_ai_insight_episodes (
                        episode_id, request_id, result_id, handoff_id,
                        subject_case_id, account_id, symbol,
                        source_abox_snapshot_id, inference_generation_id,
                        candidate_fingerprint, model, reasoning_effort,
                        validation_state, notification_job_id, payload_json,
                        created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
        (
            insight_episode.episode_id,
            insight_episode.request_id,
            insight_episode.result_id,
            insight_episode.handoff_id,
            insight_episode.subject_case_id,
            insight_episode.account_id,
            insight_episode.symbol,
            insight_episode.source_abox_snapshot_id,
            insight_episode.inference_generation_id,
            insight_episode.candidate_fingerprint,
            insight_episode.model,
            insight_episode.reasoning_effort,
            insight_episode.validation_state,
            insight_episode.notification_job_id,
            json_dumps(insight_episode.to_dict()),
            insight_episode.created_at,
        ),
    )


def upsert_decision_episode(
    connection: BoundWriteConnection,
    episode: Any,
    payload: Any,
    stamp: Any,
):
    return connection.execute(
        """
                INSERT INTO investment_decision_episodes (
                    episode_id, account_id, symbol, subject_name, question_id,
                    hypothesis_set_id, selected_hypothesis_id, action,
                    review_level, data_state, validation_state,
                    inference_generation_id, status, decided_at, source,
                    payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE selected_hypothesis_id = VALUES(selected_hypothesis_id),
                    action = VALUES(action), review_level = VALUES(review_level),
                    data_state = VALUES(data_state), validation_state = VALUES(validation_state),
                    inference_generation_id = VALUES(inference_generation_id),
                    status = VALUES(status), decided_at = VALUES(decided_at),
                    source = VALUES(source),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
        (
            episode.episode_id,
            episode.account_id,
            episode.symbol,
            episode.subject_name,
            episode.question.question_id,
            episode.hypothesis_set.hypothesis_set_id,
            episode.selected_hypothesis_id,
            episode.action,
            episode.review_level,
            episode.data_state,
            episode.validation_state,
            episode.inference_generation_id,
            episode.status,
            episode.decided_at,
            episode.source,
            json_dumps(payload),
            stamp,
            stamp,
        ),
    )


def advance_current_decision_flow(
    connection: BoundWriteConnection,
    episode: Any,
    flow_id: Any,
    stamp: Any,
):
    return connection.execute(
        """
                INSERT INTO investment_flow_current (
                    account_id, symbol, flow_id, decision_episode_id,
                    source_abox_snapshot_id, inference_generation_id,
                    selected_hypothesis_id, action, data_state,
                    validation_state, decided_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    flow_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(flow_id), flow_id),
                    decision_episode_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(decision_episode_id), decision_episode_id),
                    source_abox_snapshot_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(source_abox_snapshot_id), source_abox_snapshot_id),
                    inference_generation_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(inference_generation_id), inference_generation_id),
                    selected_hypothesis_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(selected_hypothesis_id), selected_hypothesis_id),
                    action = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(action), action),
                    data_state = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(data_state), data_state),
                    validation_state = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(validation_state), validation_state),
                    updated_at = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(updated_at), updated_at),
                    decided_at = GREATEST(investment_flow_current.decided_at, VALUES(decided_at))
                """,
        (
            episode.account_id,
            episode.symbol,
            flow_id,
            episode.episode_id,
            episode.source_abox_snapshot_id,
            episode.inference_generation_id,
            episode.selected_hypothesis_id,
            episode.action,
            episode.data_state,
            episode.validation_state,
            episode.decided_at,
            stamp,
        ),
    )


def upsert_decision_flow_head(
    connection: BoundWriteConnection,
    episode: Any,
    flow_id: Any,
    stamp: Any,
):
    return connection.execute(
        """
                INSERT INTO investment_flow_heads (
                    flow_id, account_id, symbol, decision_episode_id,
                    source_abox_snapshot_id, inference_generation_id,
                    selected_hypothesis_id, action, data_state,
                    validation_state, decided_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE decision_episode_id = VALUES(decision_episode_id),
                    source_abox_snapshot_id = VALUES(source_abox_snapshot_id),
                    inference_generation_id = VALUES(inference_generation_id),
                    selected_hypothesis_id = VALUES(selected_hypothesis_id),
                    action = VALUES(action), data_state = VALUES(data_state),
                    validation_state = VALUES(validation_state),
                    decided_at = VALUES(decided_at), updated_at = VALUES(updated_at)
                """,
        (
            flow_id,
            episode.account_id,
            episode.symbol,
            episode.episode_id,
            episode.source_abox_snapshot_id,
            episode.inference_generation_id,
            episode.selected_hypothesis_id,
            episode.action,
            episode.data_state,
            episode.validation_state,
            episode.decided_at,
            stamp,
        ),
    )
