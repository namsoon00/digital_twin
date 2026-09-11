from typing import Dict, Iterable, List, Mapping

from digital_twin.modules.reasoning.domain.reasoning_source_facts import ReasoningSourceFact
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps


def source_fact_from_row(row) -> ReasoningSourceFact:
    return ReasoningSourceFact(
        fact_id=str(row["fact_id"] or ""),
        fact_type=str(row["fact_type"] or ""),
        aggregate_id=str(row["aggregate_id"] or ""),
        subject_ids=tuple(_json_loads(row["subject_ids_json"], [])),
        revision=str(row["revision"] or ""),
        source_event_id=str(row["source_event_id"] or ""),
        source_event_name=str(row["source_event_name"] or ""),
        observed_at=str(row["observed_at"] or ""),
        ingested_at=str(row["ingested_at"] or ""),
        valid_from=str(row["valid_from"] or ""),
        valid_to=str(row["valid_to"] or ""),
        quality_state=str(row["quality_state"] or ""),
        payload=_json_loads(row["payload_json"], {}),
        version=str(row["contract_version"] or "reasoning-source-fact-v1"),
    )


def append_reasoning_source_facts_with_connection(
    connection,
    values: Iterable[Mapping[str, object]],
) -> Dict[str, object]:
    facts = [
        ReasoningSourceFact.from_request_payload(value)
        for value in values or []
        if isinstance(value, Mapping)
    ]
    inserted_count = 0
    for fact in facts:
        cursor = connection.execute(
            """
            INSERT IGNORE INTO reasoning_source_facts (
                fact_id, fact_type, aggregate_id, revision, source_event_id,
                source_event_name, subject_ids_json, observed_at, ingested_at,
                valid_from, valid_to, quality_state, contract_version, payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                fact.fact_id, fact.fact_type, fact.aggregate_id, fact.revision,
                fact.source_event_id, fact.source_event_name,
                json_dumps(list(fact.subject_ids)), fact.observed_at, fact.ingested_at,
                fact.valid_from, fact.valid_to, fact.quality_state, fact.version,
                json_dumps(fact.payload),
            ),
        )
        inserted_count += int(bool(cursor.rowcount))
    return {
        "factCount": len(facts),
        "insertedCount": inserted_count,
        "duplicateCount": max(0, len(facts) - inserted_count),
        "factIds": [fact.fact_id for fact in facts],
    }


class MySQLReasoningSourceFactStore(MySQLOperationalConnection):
    def append(self, fact: ReasoningSourceFact) -> Dict[str, object]:
        with self.transaction() as connection:
            result = append_reasoning_source_facts_with_connection(
                connection,
                [fact.request_payload()],
            )
        return {"inserted": bool(result["insertedCount"]), "fact": fact}

    def get(self, fact_id: str):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reasoning_source_facts WHERE fact_id = %s",
                (str(fact_id or ""),),
            ).fetchone()
        return source_fact_from_row(row) if row else None

    def latest(self, fact_type: str, aggregate_id: str, limit: int = 20) -> List[ReasoningSourceFact]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reasoning_source_facts
                WHERE fact_type = %s AND aggregate_id = %s
                ORDER BY ingested_at DESC, fact_id DESC LIMIT %s
                """,
                (str(fact_type or ""), str(aggregate_id or ""), max(1, min(100, int(limit or 20)))),
            ).fetchall()
        return [source_fact_from_row(row) for row in rows]
