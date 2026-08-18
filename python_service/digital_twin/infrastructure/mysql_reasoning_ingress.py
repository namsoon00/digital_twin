"""Version-aware durable ingress for ontology reasoning requests."""

from __future__ import annotations

from typing import Dict, Mapping

from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_reasoning_mailbox import MySQLOntologyReasoningMailboxStore
from .mysql_versioned_runtime import MySQLReasoningEngineJobStore
from .settings import utc_now


def _text(value: object) -> str:
    return str(value or "").strip()


def active_reasoning_engine_with_connection(connection) -> Dict[str, str]:
    """Read the authoritative active engine without depending on process env."""

    row = connection.execute(
        """
        SELECT control.active_deployment_id, deployment.engine_version
        FROM reasoning_engine_control control
        LEFT JOIN reasoning_engine_deployments deployment
          ON deployment.deployment_id = control.active_deployment_id
        WHERE control.control_id = 'global'
        """
    ).fetchone() or {}
    version = _text(row.get("engine_version")).lower()
    if not version:
        fallback = connection.execute(
            "SELECT value FROM runtime_settings WHERE `key` = %s",
            ("reasoningEngineActiveVersion",),
        ).fetchone() or {}
        version = _text(fallback.get("value")).lower()
    return {
        "deploymentId": _text(row.get("active_deployment_id")),
        "engineVersion": version,
    }


def ingress_reasoning_event_with_connection(connection, event) -> Dict[str, object]:
    """Route one durable request only to engines selected by the control plane.

    V1 receives work only while it is active. A V2 candidate may still receive
    shadow work while V1 is active because the V2 job store reads explicit
    active/delivery/candidate pointers. After V2 promotion the rollback V1
    release is rebuilt from the domain event log instead of accumulating a
    permanently idle mailbox.
    """

    active = active_reasoning_engine_with_connection(connection)
    bounded_event = MySQLReasoningEngineJobStore.bind_source_boundaries_with_connection(
        connection,
        event,
    )
    result: Dict[str, object] = {
        "activeDeploymentId": active["deploymentId"],
        "activeEngineVersion": active["engineVersion"],
        "legacyV1": {"saved": False, "status": "inactive"},
        "independentV2": {"saved": False, "status": "not-targeted"},
    }
    if active["engineVersion"] == "v1":
        try:
            result["legacyV1"] = MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(
                connection,
                bounded_event,
            )
        except Exception as error:  # The source event remains the repair boundary.
            result["legacyV1"] = {"saved": False, "status": "error", "reason": str(error)[:240]}
    try:
        result["independentV2"] = MySQLReasoningEngineJobStore.ingress_event_with_connection(
            connection,
            bounded_event,
        )
    except Exception as error:  # The source event remains the repair boundary.
        result["independentV2"] = {"saved": False, "status": "error", "reason": str(error)[:240]}
    return result


class MySQLReasoningIngressRouter(MySQLOperationalConnection):
    """Reconcile transient inboxes with the active version control pointer."""

    def reconcile(self) -> Dict[str, object]:
        with self.transaction() as connection:
            active = active_reasoning_engine_with_connection(connection)
            if active["engineVersion"] != "v2":
                return {
                    "status": "unchanged",
                    "activeDeploymentId": active["deploymentId"],
                    "activeEngineVersion": active["engineVersion"],
                    "retiredLegacyEntryCount": 0,
                }
            stamp = utc_now()
            mailbox_count = int((connection.execute(
                "SELECT COUNT(*) AS row_count FROM ontology_reasoning_mailbox"
            ).fetchone() or {}).get("row_count") or 0)
            direct_count = int((connection.execute(
                "SELECT COUNT(*) AS row_count FROM ontology_reasoning_mailbox_events "
                "WHERE state IN ('pending', 'direct-pending')"
            ).fetchone() or {}).get("row_count") or 0)
            connection.execute("DELETE FROM ontology_reasoning_work_items")
            connection.execute("DELETE FROM ontology_reasoning_mailbox")
            connection.execute(
                "UPDATE ontology_reasoning_mailbox_events "
                "SET state = 'superseded', unresolved_entry_count = 0, "
                "terminal_reason = %s, updated_at = %s "
                "WHERE state IN ('pending', 'direct-pending')",
                ("V2 is active; replay the domain event log only if V1 rollback is selected.", stamp),
            )
            boundary_backfill = MySQLReasoningEngineJobStore.backfill_source_boundaries_with_connection(
                connection,
                active["deploymentId"],
            )
            MySQLOntologyReasoningMailboxStore._refresh_queue_state_with_connection(connection)
        return {
            "status": "retired" if mailbox_count or direct_count else "unchanged",
            "activeDeploymentId": active["deploymentId"],
            "activeEngineVersion": active["engineVersion"],
            "retiredLegacyEntryCount": mailbox_count,
            "retiredLegacyEventCount": direct_count,
            "sourceBoundaryBackfill": boundary_backfill,
        }
