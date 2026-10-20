"""Fence observation reads to one active, delivery-aligned deployment."""
from .observation_evidence import EvidenceContractError

VERSION = "active-observation-binding-v2-world-routes"


def observation_binding(row):
    if (not row or not row.get("active_deployment_id") or not row.get("graph_store_binding")
            or row.get("active_deployment_id") != row.get("delivery_deployment_id")
            or row.get("deployment_status") != "active" or not row.get("engine_version")
            or type(row.get("version")) is not int or not row.get("shared_graph_store_binding")):
        raise EvidenceContractError("active observation graph binding unavailable")
    return {"version": VERSION, "deploymentId": row["active_deployment_id"], "engineVersion": row["engine_version"],
            "graphStoreBinding": row["graph_store_binding"], "controlVersion": row["version"],
            "sharedGraphStoreBinding": row["shared_graph_store_binding"]}
