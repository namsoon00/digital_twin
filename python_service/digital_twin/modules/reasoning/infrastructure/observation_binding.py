"""Read existing control rows only; never initialize a registry or a graph."""
from ..domain.observation_binding import observation_binding


def active_observation_binding(connection):
    row = connection.execute("SELECT c.active_deployment_id,c.delivery_deployment_id,c.version,"
        "d.graph_store_binding,d.engine_version,d.deployment_status FROM reasoning_engine_control c "
        "LEFT JOIN reasoning_engine_deployments d ON d.deployment_id=c.active_deployment_id "
        "WHERE c.control_id='global'").fetchone()
    return observation_binding(row)
