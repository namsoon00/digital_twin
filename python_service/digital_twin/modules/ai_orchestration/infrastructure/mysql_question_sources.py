"""Source identifiers needed by open questions; no AI prose becomes a fact."""
import json


def question_document_sources(connection, symbol):
    rows = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(3000) */ "
        "JSON_EXTRACT(payload_json,'$.latestSourceAnswer.sources') AS sources "
        "FROM ai_brain_cases WHERE symbol=%s AND kind='question' "
        "AND status IN ('open','waiting','review-needed','blocked') "
        "ORDER BY (COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.answerResolution.status')),'')='answered') DESC,"
        "next_check_at,created_at,case_id LIMIT 12", (symbol,)).fetchall()
    ids = []
    for row in rows:
        for source in json.loads(row["sources"] or "[]") or []:
            key = source.get("evidenceId")
            if isinstance(key, str) and key.startswith("research:" + symbol + ":") and key not in ids:
                ids.append(key)
    return ids[:8]
