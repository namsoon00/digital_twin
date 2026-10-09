"""Append-only public assertion ledger, committed with the source and its events."""
import json
from ..domain.company_relationships import valid_assertions


def persist_relationship_assertions(connection, item):
    assertions = valid_assertions(item)
    if not assertions:
        return
    saved = []
    for row in assertions:
        connection.execute("INSERT IGNORE INTO company_relationship_assertions "
            "(assertion_id,subject_symbol,counterparty_symbol,source_evidence_id,source_revision,payload_json,first_known_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (row["assertionId"], row["subjectSymbol"], row["counterparty"].get("symbol", ""),
             row["sourceEvidenceId"], row["sourceRevision"], json.dumps(row, ensure_ascii=False, allow_nan=False), row["firstKnownAt"]))
        original = connection.execute("SELECT payload_json FROM company_relationship_assertions WHERE assertion_id=%s",
                                      (row["assertionId"],)).fetchone()
        saved.append(json.loads(original["payload_json"]))
    # Re-collection cannot move firstKnownAt forward or rewrite identity history.
    item.raw_payload["companyRelationships"]["assertions"] = saved
