"""Source identifiers needed by open questions; no AI prose becomes a fact."""
import json


def question_document_sources(connection, symbol):
    rows = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(3000) */ "
        "JSON_EXTRACT(payload_json,'$.latestSourceAnswer.sources') AS sources, "
        "JSON_EXTRACT(payload_json,'$.questionSourceReferences') AS retained, "
        "JSON_EXTRACT(payload_json,'$.answerMemory[*].answer.sources') AS history "
        "FROM ai_brain_cases WHERE symbol=%s AND kind='question' "
        "AND status IN ('open','waiting','review-needed','blocked') "
        "ORDER BY (COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.answerResolution.status')),'')='answered') DESC,"
        "next_check_at,created_at,case_id LIMIT 12", (symbol,)).fetchall()
    ids = []
    for row in rows:
        history = json.loads(row.get("history") or "[]") or []
        previous = [source for group in reversed(history[-3:]) for source in group]
        for source in (json.loads(row["sources"] or "[]") or []) + (json.loads(row.get("retained") or "[]") or []) + previous:
            key = source.get("evidenceId")
            if isinstance(key, str) and key.startswith("research:" + symbol + ":") and key not in ids:
                ids.append(key)
    return ids[:8]
