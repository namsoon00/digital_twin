"""Serialize one scenario head and its immutable observations in one transaction."""
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.modules.decisions.domain.strategy_scenario import project_scenario
from digital_twin.modules.decisions.domain.strategy_scenario_events import strategy_scenario_changed_event
from digital_twin.shared_kernel.clock import utc_now_iso


class MySQLStrategyScenarioStore(MySQLOperationalConnection):
    def record(self, contract, observation):
        key, oid, stamp = observation["scenarioKey"], observation["observationId"], utc_now_iso()
        with self.transaction() as connection:
            # Insert-first serializes even two workers creating the first head.
            connection.execute("INSERT INTO investment_strategy_scenario_heads "
                "(scenario_key, payload_json, updated_at) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE scenario_key = VALUES(scenario_key)", (key, "{}", stamp))
            row = connection.execute("SELECT payload_json FROM investment_strategy_scenario_heads "
                "WHERE scenario_key = %s FOR UPDATE", (key,)).fetchone()
            existing = connection.execute("SELECT payload_json FROM investment_strategy_scenario_observations "
                "WHERE observation_id = %s", (oid,)).fetchone()
            if existing:
                return _json_loads(existing["payload_json"], {})
            previous = _json_loads(row["payload_json"], {})
            value, head = project_scenario(contract, observation, previous)
            connection.execute("INSERT INTO investment_strategy_scenario_observations "
                "(observation_id, scenario_key, source_observed_at, payload_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s)", (oid, key, value["sourceObservedAt"], json_dumps(value), stamp))
            if head:
                connection.execute("UPDATE investment_strategy_scenario_heads SET payload_json = %s, updated_at = %s "
                    "WHERE scenario_key = %s", (json_dumps(head), stamp, key))
                change = head.get("transition") or {}
                if change.get("transitionId") != (previous.get("transition") or {}).get("transitionId"):
                    insert_domain_event_with_connection(connection, strategy_scenario_changed_event(head))
        return value

    def current(self, scenario_key):
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM investment_strategy_scenario_heads "
                "WHERE scenario_key = %s", (scenario_key,)).fetchone()
        return _json_loads(row["payload_json"], {}) if row else {}

    def history(self, scenario_key, limit=50):
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM investment_strategy_scenario_observations "
                "WHERE scenario_key = %s ORDER BY source_observed_at DESC, created_at DESC LIMIT %s",
                (scenario_key, max(1, min(200, int(limit))))).fetchall()
        return [_json_loads(row["payload_json"], {}) for row in rows]
