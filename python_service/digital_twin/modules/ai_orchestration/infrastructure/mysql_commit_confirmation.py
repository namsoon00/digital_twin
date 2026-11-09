"""Confirm immutable AI audit writes after a lost commit reply; never repeat writes."""
from functools import wraps
from contextlib import contextmanager
import gzip
import json

from digital_twin.infrastructure.mysql_operational_connection import mysql_error_code
from ..domain import activity
from ..domain.execution_input import validate_execution_input
from ..domain.planning import identity, stamp
from digital_twin.modules.reasoning.contracts import content_hash


@contextmanager
def confirmed_transaction(store, verify):
    """One transaction, then at most one read-only confirmation of its exact result."""
    try:
        with store.transaction() as connection:
            yield connection
    except Exception as error:
        if (getattr(error, "orbit_mysql_statement", "") != "COMMIT"
                or mysql_error_code(error) not in {2006, 2013, 2055}):
            raise
        try:
            with store.connect() as connection:
                verified = verify(connection)
        except Exception:
            error.orbit_mysql_commit_verification = "read-failed"
            raise error from None
        if not verified:
            error.orbit_mysql_commit_verification = "not-confirmed"
            raise
        store.last_commit_reconciliation = {"status": "confirmed"}


def call_completion_confirmed(connection, call_id, expected):
    if not expected:
        return False
    row = connection.execute("SELECT status,error_kind,completed_at,task_id FROM ai_control_calls WHERE call_id=%s",
                             (call_id,)).fetchone()
    if not row or any(row.get(k) != expected[k] for k in ("status", "error_kind", "completed_at", "task_id")):
        return False
    metrics = connection.execute("SELECT metrics_json FROM ai_control_call_metrics WHERE call_id=%s", (call_id,)).fetchone()
    if not metrics or json.loads(metrics["metrics_json"]) != expected["metrics"]:
        return False
    return not expected.get("activityId") or bool(connection.execute(
        "SELECT event_id FROM ai_activity_events WHERE event_id=%s", (expected["activityId"],)).fetchone())


def _confirmed(store, job, value, kind):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if kind == "input":
        digest = validate_execution_input(value)
        key = identity(job["taskId"], job["attempts"], digest)
        table, stage = "ai_control_inputs", "input-prepared"
        expected = {"input_hash": digest, "prompt_hash": value["promptHash"],
                    "protocol_version": value["protocolVersion"]}
    else:
        key, table, stage = value["inputId"], "ai_control_retrieval_rounds", "retrieval-completed"
        expected = {"artifact_hash": content_hash(value), "round_status": value["status"]}
    with store.connect() as connection:
        row = connection.execute(
            "SELECT a.* FROM " + table + " a JOIN ai_control_tasks t ON t.task_id=a.task_id "
            "WHERE a.input_id=%s AND a.task_id=%s AND a.attempt=%s "
            "AND t.status='processing' AND t.attempts=a.attempt AND t.lease_token=%s "
            "AND t.lease_until>=%s AND t.account_id=%s AND t.symbol=%s",
            (key, job["taskId"], job["attempts"], job["leaseToken"], stamp(), job["accountId"], job["symbol"])).fetchone()
        if not row or any(row.get(k) != v for k, v in expected.items()):
            return False
        if gzip.decompress(row["artifact_gzip"]) != raw:
            return False
        if kind == "input":
            envelope = value
        else:
            parent = connection.execute("SELECT artifact_gzip FROM ai_control_inputs WHERE input_id=%s "
                "AND task_id=%s AND attempt=%s", (key, job["taskId"], job["attempts"])).fetchone()
            if not parent:
                return False
            envelope = json.loads(gzip.decompress(parent["artifact_gzip"]))
            validate_execution_input(envelope)
        if any(envelope["current"].get(k) != job[k] for k in ("accountId", "symbol", "worldId", "taskId")):
            return False
        if activity.enabled(store.runtime_settings):
            event_key = identity(activity.VERSION, stage, key)
            if not connection.execute("SELECT event_id FROM ai_activity_events WHERE event_id=%s", (event_key,)).fetchone():
                return False
    return True


def confirm_artifact_commit(kind):
    """Only these two audit operations have an exact, side-effect-free proof."""
    if kind not in {"input", "round"}:
        raise ValueError("unsupported immutable AI artifact")
    def decorate(method):
        @wraps(method)
        def run(store, job, value):
            try:
                return method(store, job, value)
            except Exception as error:
                if (getattr(error, "orbit_mysql_statement", "") != "COMMIT"
                        or mysql_error_code(error) not in {2006, 2013, 2055}):
                    raise
                try:
                    verified = _confirmed(store, job, value, kind)
                except Exception:
                    error.orbit_mysql_commit_verification = "read-failed"
                    raise error from None
                if not verified:
                    error.orbit_mysql_commit_verification = "not-confirmed"
                    raise
                store.last_artifact_commit_reconciliation = {"kind": kind, "status": "confirmed"}
                return identity(job["taskId"], job["attempts"], validate_execution_input(value)) if kind == "input" else True
        return run
    return decorate
