"""Small append-only approved opinions and bounded subject-scoped archive reads."""
from copy import deepcopy
import json
import re

from ..domain.planning import identity, stamp
from ..domain import opinion_memory

SCHEMA = """CREATE TABLE IF NOT EXISTS ai_opinion_records (
record_id CHAR(64) PRIMARY KEY, account_id VARCHAR(191) NOT NULL, symbol VARCHAR(64) NOT NULL,
world_id VARCHAR(191) NOT NULL, kind VARCHAR(32) NOT NULL, case_id VARCHAR(64) NOT NULL,
task_id VARCHAR(64) NOT NULL, execution_input_id VARCHAR(64) NOT NULL,
known_at VARCHAR(40) NOT NULL, search_text TEXT NOT NULL, payload_json LONGTEXT NOT NULL,
INDEX opinion_subject(account_id,symbol,world_id,known_at,record_id),
INDEX opinion_case(account_id,symbol,world_id,case_id,known_at),
INDEX opinion_task(task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""

APPROVED_RESULT_SQL = """((JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.quality.status')) IN ('accepted','observation-only')
AND COALESCE(JSON_LENGTH(JSON_EXTRACT(result_json,'$.quality.errors')),0)=0) OR
(JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.researchQuality.status')) IN ('accepted','partially-accepted')
AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.researchQuality.reviewInputId')),'')<>''
AND (JSON_EXTRACT(result_json,'$.researchQuality.admissions.assessment')=true OR
(JSON_EXTRACT(result_json,'$.researchQuality.admissions') IS NULL
AND JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.researchQuality.status'))='accepted'))))"""

APPROVED_NARRATIVE_SQL = """(JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.quality.status')) IN ('accepted','observation-only')
AND COALESCE(JSON_LENGTH(JSON_EXTRACT(result_json,'$.quality.errors')),0)=0)"""


def search_text(row):
    return " ".join(str(row.get(k, "")) for k in ("question", "questionContract", "answer", "summary", "hypothesis", "analystAssessment", "investmentMeaning", "missingRequirements", "asOf", "periods")).casefold()[:12000]


def archive_record(row):
    return {**deepcopy(row), "kind": "opinion-memory", "authority": "historical-reviewed-opinion-only"}


class OpinionMemoryPersistence:
    def record_opinions(self, connection, job, result):
        if not result.get("executionInputId") or not result.get("input") or not opinion_memory.approved_result(result):
            return []
        packet, records = result["input"], []
        if any(packet.get(k) != job[k] for k in ("accountId", "symbol", "worldId")):
            raise ValueError("approved opinion must belong to the completing task subject")
        # Agenda admission, revision fencing and independent review already ran
        # in this transaction. Read back only the assessments actually committed.
        for key in result.get("brain", {}).get("caseIds", []):
            row = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE case_id=%s AND account_id=%s AND symbol=%s",
                                     (key, job["accountId"], job["symbol"])).fetchone()
            case = json.loads(row["payload_json"]) if row else {}
            current = opinion_memory.current_question(case)
            if case.get("worldId") == job["worldId"] and current.get("executionInputId") == result["executionInputId"]:
                records.append({**current, "kind": "question", "summary": current.get("judgment", {}).get("summary", "")})
                from ..domain.gap_read_progress import reviewed
                progress = reviewed(packet, current)
                if progress:
                    records[-1]["gapReadProgress"] = progress
        if (result.get("quality", {}).get("status") in {"accepted", "observation-only"}
                and not result.get("quality", {}).get("errors")):
            records.append({"kind": "market" if job["capability"] == "market-observe" else "research",
                "caseId": "", **{k: deepcopy(result[k]) for k in
                    ("summary", "hypothesis", "counterEvidence", "comparison", "evidenceIds", "questionContributions", "analystAssessment", "investmentMeaning") if k in result},
                "reviewInputId": result.get("quality", {}).get("reviewInputId", "")})
        ids = []
        for value in records:
            key = identity("approved-opinion-v1", job["taskId"], value["caseId"])
            value.update(version=opinion_memory.VERSION, recordId=key,
                **{k: job[k] for k in ("accountId", "symbol", "worldId")}, name=packet.get("name", job["symbol"]),
                taskId=job["taskId"], executionInputId=result["executionInputId"], knownAt=stamp(),
                asOf=packet["capturedAt"], authority="historical-reviewed-opinion-only",
                periods=sorted({str(f.get("periodEnd") or (f.get("historicalDocumentReference") or {}).get("periodEnd"))
                    for f in packet.get("facts", []) if f["id"] in value.get("evidenceIds", [])
                    and (f.get("periodEnd") or (f.get("historicalDocumentReference") or {}).get("periodEnd"))}),
                basis=opinion_memory.basis(packet, value.get("evidenceIds", [])))
            connection.execute("INSERT IGNORE INTO ai_opinion_records "
                "(record_id,account_id,symbol,world_id,kind,case_id,task_id,execution_input_id,known_at,search_text,payload_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (key, job["accountId"], job["symbol"], job["worldId"], value["kind"], value["caseId"], job["taskId"],
                 result["executionInputId"], value["knownAt"], search_text(value), json.dumps(value, ensure_ascii=False)))
            ids.append(key)
        return ids

    def search_opinions(self, account, symbol, world, as_of, query, offset=0, limit=4):
        """Indexed metadata pages first, then <=4 PK payloads. Never bootstrap."""
        terms = re.findall(r"[\w가-힣]+", str(query).casefold())[:6]
        if not terms or len(str(query)) > 160 or type(offset) is not int or not 0 <= offset < 300000:
            raise ValueError("bounded opinion search terms required")
        limit = max(1, min(4, int(limit)))
        with self.connect() as connection:
            if offset >= 200000:
                return self.legacy_opinions(connection, account, symbol, world, as_of, terms, offset, limit)
            if offset < 100000:
                rows = connection.execute("SELECT record_id,search_text FROM ai_opinion_records FORCE INDEX(opinion_subject) "
                    "WHERE account_id=%s AND symbol=%s AND world_id=%s AND known_at<=%s "
                    "ORDER BY known_at DESC,record_id DESC LIMIT 81 OFFSET %s", (account, symbol, world, as_of, offset)).fetchall()
                matches, scanned = [], 0
                for row in rows[:80]:
                    scanned += 1
                    if all(t in row["search_text"] for t in terms):
                        matches.append(row["record_id"])
                        if len(matches) == limit:
                            break
                more = scanned < len(rows)
                if matches:
                    values = connection.execute("SELECT record_id,payload_json FROM ai_opinion_records WHERE record_id IN ("
                        + ",".join(["%s"] * len(matches)) + ")", matches).fetchall()
                    mapping = {r["record_id"]: json.loads(r["payload_json"]) for r in values}
                    return {"records": [archive_record(mapping[k]) for k in matches],
                        "nextOffset": offset + scanned if more else 100000, "scanned": scanned,
                        "coverage": "bounded-subject-archive", "authority": "historical-context-only"}
                if more:
                    return {"records": [], "nextOffset": offset + scanned, "scanned": scanned,
                            "coverage": "more-archive-pages", "authority": "historical-context-only"}
                offset = 100000
            # Existing approved cases remain searchable without rewriting old
            # execution artifacts or requiring a mutating migration on reads.
            rows = connection.execute("SELECT case_id,JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.question')) question,"
                "JSON_EXTRACT(payload_json,'$.questionContract') contract,"
                "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.questionAssessment.answer')) answer "
                "FROM ai_brain_cases c FORCE INDEX(brain_subject) WHERE account_id=%s AND symbol=%s AND kind='question' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.questionAssessment.at'))<=%s "
                "AND JSON_EXTRACT(payload_json,'$.questionAssessment.researchReviewInputId') IS NOT NULL "
                "AND NOT EXISTS(SELECT 1 FROM ai_opinion_records o WHERE o.account_id=c.account_id AND o.symbol=c.symbol "
                "AND o.world_id=%s AND o.case_id=c.case_id AND o.execution_input_id=JSON_UNQUOTE(JSON_EXTRACT(c.payload_json,'$.questionAssessment.executionInputId'))) "
                "ORDER BY status,next_check_at,case_id LIMIT 81 OFFSET %s", (account, symbol, world, as_of, world, offset-100000)).fetchall()
            matches, scanned = [], 0
            for row in rows[:80]:
                scanned += 1
                text = " ".join(str(row.get(k) or "") for k in ("question", "contract", "answer")).casefold()
                if all(t in text for t in terms):
                    matches.append(row["case_id"])
                    if len(matches) == limit:
                        break
            values = []
            for key in matches:
                row = connection.execute("SELECT payload_json FROM ai_brain_cases WHERE case_id=%s", (key,)).fetchone()
                case = json.loads(row["payload_json"])
                current = opinion_memory.current_question(case)
                if (current.get("status") == "reviewed" and current.get("at") and current["at"] <= as_of
                        and (case.get("accountId"), case.get("symbol"), case.get("worldId")) == (account, symbol, world)):
                    values.append(archive_record({**current, "recordId": identity(key, current["executionInputId"]),
                        "accountId": account, "symbol": symbol, "worldId": world, "knownAt": current["at"]}))
            if not rows:
                return self.legacy_opinions(connection, account, symbol, world, as_of, terms, 200000, limit)
            return {"records": values, "nextOffset": offset + scanned if scanned < len(rows) else 200000,
                    "scanned": scanned, "coverage": "legacy-reviewed-question-page", "authority": "historical-context-only"}

    def legacy_opinions(self, connection, account, symbol, world, as_of, terms, offset, limit):
        rows = connection.execute("SELECT task_id,JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.summary')) summary,"
            "JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.hypothesis')) hypothesis,"
            "JSON_EXTRACT(result_json,'$.questionResolutions[*].sourceQuestion.question') questions,"
            "JSON_EXTRACT(result_json,'$.input.facts[*].periodEnd') periods,"
            "JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.observedAt')) observed_at "
            "FROM ai_control_tasks t FORCE INDEX(ai_control_subject) WHERE account_id=%s AND symbol=%s "
            "AND status='completed' AND capability IN ('observe','market-observe') AND updated_at<=%s "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s AND "
            "((capability='market-observe' AND " + APPROVED_NARRATIVE_SQL + ") OR "
            "(capability<>'market-observe' AND " + APPROVED_RESULT_SQL + "))" +
            " AND JSON_EXTRACT(result_json,'$.summary') IS NOT NULL "
            "AND NOT EXISTS(SELECT 1 FROM ai_opinion_records o WHERE o.task_id=t.task_id) "
            "ORDER BY updated_at DESC,task_id DESC LIMIT 81 OFFSET %s", (account, symbol, as_of, world, offset-200000)).fetchall()
        matches, scanned = [], 0
        for row in rows[:80]:
            scanned += 1
            text = " ".join(str(row.get(k) or "") for k in ("summary", "hypothesis", "questions", "periods", "observed_at")).casefold()
            if all(t in text for t in terms):
                matches.append(row["task_id"])
                if len(matches) == limit:
                    break
        values = []
        for key in matches:
            row = connection.execute("SELECT capability,result_json,updated_at FROM ai_control_tasks WHERE task_id=%s AND account_id=%s AND symbol=%s",
                                     (key, account, symbol)).fetchone()
            result = json.loads(row["result_json"]) if row else {}
            packet = result.get("input", {})
            if (not opinion_memory.approved_result(result) or row["updated_at"] > as_of
                    or (row["capability"] == 'market-observe' and (result.get('quality', {}).get('status') not in
                        {'accepted', 'observation-only'} or result.get('quality', {}).get('errors')))
                    or (packet.get("accountId"), packet.get("symbol"), packet.get("worldId")) != (account, symbol, world)):
                continue
            values.append(archive_record({"recordId": identity("legacy-opinion", key), "taskId": key,
                "accountId": account, "symbol": symbol, "worldId": world, "knownAt": row["updated_at"],
                "asOf": result.get("observedAt", ""), "caseId": "",
                **{k: deepcopy(result[k]) for k in ("summary", "hypothesis", "counterEvidence", "executionInputId") if k in result}}))
        return {"records": values, "nextOffset": offset + scanned if scanned < len(rows) and offset + scanned < 300000 else None,
                "scanned": scanned, "coverage": "legacy-reviewed-opinion-page", "authority": "historical-context-only"}
