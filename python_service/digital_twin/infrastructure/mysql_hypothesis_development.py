import hashlib
from pathlib import Path
from typing import Dict, List

from ..domain.hypothesis_development import HypothesisDevelopmentCase
from ..domain.ontology_experiments import OntologyExperiment
from ..domain.portfolio import utc_now_iso
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps


class MySQLHypothesisDevelopmentStore(MySQLOperationalConnection):
    def get(self, case_id: str):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM hypothesis_development_cases WHERE case_id = %s",
                (str(case_id or ""),),
            ).fetchone()
        return HypothesisDevelopmentCase.from_dict(_json_loads(row.get("payload_json"), {})) if row else None

    def get_by_fingerprint(self, fingerprint: str):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM hypothesis_development_cases WHERE fingerprint = %s",
                (str(fingerprint or ""),),
            ).fetchone()
        return HypothesisDevelopmentCase.from_dict(_json_loads(row.get("payload_json"), {})) if row else None

    def list(self, status: str = "", symbol: str = "", limit: int = 100) -> List[HypothesisDevelopmentCase]:
        clauses = []
        params: List[object] = []
        if status:
            clauses.append("status = %s")
            params.append(str(status))
        if symbol:
            clauses.append("symbol = %s")
            params.append(str(symbol).upper())
        params.append(max(1, min(500, int(limit or 100))))
        sql = "SELECT payload_json FROM hypothesis_development_cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, case_id LIMIT %s"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [HypothesisDevelopmentCase.from_dict(_json_loads(row.get("payload_json"), {})) for row in rows or []]

    def save(self, case: HypothesisDevelopmentCase, event_type: str = "updated", reason: str = "") -> HypothesisDevelopmentCase:
        stamp = utc_now_iso()
        case.updated_at = stamp
        payload = case.to_dict()
        latest_proposal = case.source_proposal_ids[-1] if case.source_proposal_ids else ""
        rule_id = str(case.candidate_rule.get("rule_id") or case.candidate_rule.get("ruleId") or "")
        event_seed = "|".join([case.case_id, event_type, case.status, case.stage, stamp])
        event_id = "hypothesis-case-event:" + hashlib.sha256(event_seed.encode("utf-8")).hexdigest()[:24]
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO hypothesis_development_cases (
                    case_id, fingerprint, account_id, symbol, status, stage, title,
                    latest_proposal_id, candidate_rule_id, experiment_id,
                    payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE status = VALUES(status), stage = VALUES(stage),
                    title = VALUES(title), latest_proposal_id = VALUES(latest_proposal_id),
                    candidate_rule_id = VALUES(candidate_rule_id), experiment_id = VALUES(experiment_id),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    case.case_id, case.fingerprint, case.account_id, case.symbol,
                    case.status, case.stage, case.title, latest_proposal, rule_id,
                    case.experiment_id, json_dumps(payload), case.created_at or stamp, stamp,
                ),
            )
            connection.execute(
                """
                INSERT IGNORE INTO hypothesis_development_events (
                    event_id, case_id, event_type, status, stage, reason,
                    payload_json, occurred_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event_id, case.case_id, str(event_type or "updated")[:80],
                    case.status, case.stage, str(reason or "")[:1000],
                    json_dumps({"caseId": case.case_id, "status": case.status, "stage": case.stage}),
                    stamp,
                ),
            )
        return case

    def events(self, case_id: str = "", limit: int = 200) -> List[Dict[str, object]]:
        params: List[object] = []
        where = ""
        if case_id:
            where = " WHERE case_id = %s"
            params.append(str(case_id))
        params.append(max(1, min(1000, int(limit or 200))))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM hypothesis_development_events" + where + " ORDER BY occurred_at DESC, event_id LIMIT %s",
                tuple(params),
            ).fetchall()
        return [
            {
                "eventId": str(row.get("event_id") or ""),
                "caseId": str(row.get("case_id") or ""),
                "eventType": str(row.get("event_type") or ""),
                "status": str(row.get("status") or ""),
                "stage": str(row.get("stage") or ""),
                "reason": str(row.get("reason") or ""),
                "occurredAt": str(row.get("occurred_at") or ""),
                **_json_loads(row.get("payload_json"), {}),
            }
            for row in rows or []
        ]


class MySQLOntologyExperimentStore(MySQLOperationalConnection):
    _migration_checked = set()

    def __init__(self, settings: Dict[str, str] = None, legacy_path: Path = None):
        super().__init__(settings)
        self.legacy_path = Path(legacy_path) if legacy_path else None
        self.migrate_legacy_once()

    def list(self) -> List[OntologyExperiment]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM ontology_experiments ORDER BY updated_at DESC, experiment_id"
            ).fetchall()
        return [OntologyExperiment.from_dict(_json_loads(row.get("payload_json"), {})) for row in rows or []]

    def get(self, experiment_id: str):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM ontology_experiments WHERE experiment_id = %s",
                (str(experiment_id or ""),),
            ).fetchone()
        return OntologyExperiment.from_dict(_json_loads(row.get("payload_json"), {})) if row else None

    def save(self, experiment: OntologyExperiment) -> None:
        stamp = utc_now_iso()
        experiment.created_at = experiment.created_at or stamp
        experiment.updated_at = experiment.updated_at or stamp
        payload = experiment.to_dict()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO ontology_experiments (
                    experiment_id, status, title, source_case_id, source_proposal_id,
                    symbols_json, payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE status = VALUES(status), title = VALUES(title),
                    source_case_id = VALUES(source_case_id), source_proposal_id = VALUES(source_proposal_id),
                    symbols_json = VALUES(symbols_json), payload_json = VALUES(payload_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    experiment.experiment_id, experiment.status, experiment.title,
                    experiment.source_case_id, experiment.source_proposal_id,
                    json_dumps(experiment.symbols), json_dumps(payload),
                    experiment.created_at, experiment.updated_at,
                ),
            )
            for run in experiment.run_history or []:
                if not isinstance(run, dict) or not str(run.get("runId") or ""):
                    continue
                connection.execute(
                    """
                    INSERT INTO ontology_experiment_runs (
                        run_id, experiment_id, status, completed_at, payload_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE status = VALUES(status),
                        completed_at = VALUES(completed_at), payload_json = VALUES(payload_json)
                    """,
                    (
                        str(run.get("runId")), experiment.experiment_id,
                        str(run.get("status") or ""), str(run.get("completedAt") or ""),
                        json_dumps(run), str(run.get("completedAt") or stamp),
                    ),
                )

    def migrate_legacy_once(self) -> None:
        key = self.schema_key()
        if key in self._migration_checked or not self.legacy_path or not self.legacy_path.exists():
            return
        self._migration_checked.add(key)
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM ontology_experiments").fetchone() or {}
        if int(row.get("count") or 0) > 0:
            return
        try:
            import json

            payload = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in payload.get("experiments") or []:
            if isinstance(item, dict):
                self.save(OntologyExperiment.from_dict(item))
