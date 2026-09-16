"""Local MySQL audit/repair adapter with source and cache concurrency guards."""

from datetime import datetime, timezone, timedelta
import hashlib
import json
import gzip

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.infrastructure.operational_store import company_knowledge_cache, external_data_store, ai_inference_queue_store
from digital_twin.modules.market_data.public import ExternalSignalsReadModelService
from digital_twin.modules.news_intelligence.public import financial_repair_plan, financial_input_requires_revalidation
from digital_twin.modules.outcomes.public import quarantine_financial_input


def retire_legacy_financial_requests(connection, queue, correction_id, stamp, *, apply=False):
    rows = connection.execute(
        "SELECT * FROM ai_inference_requests WHERE status IN ('pending', 'processing', 'retry')"
        + (" FOR UPDATE" if apply else ""),
    ).fetchall()
    affected = []
    for row in rows:
        context = json.loads(row["context_json"] or "{}")
        relation = context.get("ontologyRelationContext") or {}
        company = (relation.get("facts") or {}).get("companyContext") or {}
        if not financial_input_requires_revalidation(company, relation.get("activeRules") or []):
            continue
        affected.append(row["request_id"])
        if apply:
            queue.supersede_request_with_connection(connection, row, correction_id, stamp)
    return affected


def run_financial_evidence_maintenance(settings, *, apply=False, limit=2000):
    cache_store, facts = company_knowledge_cache(settings), external_data_store(settings)
    cache = cache_store.load()
    symbols = list((cache.get("symbols") or {}).keys())
    sources = facts.list_current(symbols)
    source_identity = sorted((row["datasetId"], row["subjectKey"], row.get("payloadHash")) for row in sources)
    signals = ExternalSignalsReadModelService(facts).signals_for_subjects(symbols)
    replacement, changes = financial_repair_plan(cache, signals)
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    correction = {"version": "financial-input-correction-v1", "correctedAt": stamp,
                  "reason": "financial-comparison-source-revalidation-required"}
    audit_id = "financial-repair:" + hashlib.sha256(json_dumps({"at": stamp, "sources": source_identity}).encode()).hexdigest()[:24]
    db = MySQLOperationalConnection(settings)
    report = {"mode": "apply" if apply else "preview", "symbolsAudited": len(symbols), "changes": changes,
              "historicalCasesRequiringRevalidation": 0, "followUpsCanceled": 0, "targetsExcluded": 0, "outcomesExcluded": 0,
              "immutableHistoryRewritten": False, "historicalScope": "retained frozen AI contexts; no look-ahead replay"}
    with db.transaction() as connection:
        report["supersededRequestIds"] = retire_legacy_financial_requests(
            connection, ai_inference_queue_store(settings), audit_id, stamp, apply=apply,
        )
        report["aiRequestsSuperseded"] = len(report["supersededRequestIds"])
        report["actionCoverage24h"] = connection.execute(
            "SELECT COUNT(*) AS subjectCases, "
            "SUM(JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.synthesis.graph_candidate_action')) IN ('BUY', 'ADD')) AS graphEntryCandidates, "
            "SUM(JSON_CONTAINS(JSON_EXTRACT(payload_json, '$.synthesis.allowed_actions'), '\"BUY\"') "
            "OR JSON_CONTAINS(JSON_EXTRACT(payload_json, '$.synthesis.allowed_actions'), '\"ADD\"')) AS permittedEntryCases, "
            "SUM(JSON_LENGTH(JSON_EXTRACT(payload_json, '$.synthesis.execution_eligible_hypothesis_ids')) > 0) AS executableHypothesisCases "
            "FROM investment_subject_decision_cases WHERE created_at >= %s",
            ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z"),),
        ).fetchone()
        report["actionCoverage24h"] = {key: int(value or 0) for key, value in report["actionCoverage24h"].items()}
        cases = connection.execute(
            "SELECT c.subject_case_id, c.candidate_set_id, c.symbol, a.artifact_gzip "
            "FROM investment_subject_decision_cases c JOIN ai_inference_execution_audits a ON a.request_id = c.ai_request_id "
            "WHERE c.payload_json LIKE '%%graph.company.%%' "
            "ORDER BY c.created_at DESC LIMIT %s", (max(1, min(10000, int(limit))),),
        ).fetchall()
        report["retainedCasesAudited"] = len(cases)
        report["limitReached"] = len(cases) == int(limit)
        report["affectedCaseIds"] = []
        for case in cases:
            artifact = json.loads(gzip.decompress(bytes(case["artifact_gzip"])))
            brief = artifact.get("executionAudit", {}).get("decisionBrief", {})
            company = brief.get("currentSituation", {}).get("companyContext", {})
            rules = brief.get("inference", {}).get("activeRules", [])
            if not financial_input_requires_revalidation(company, rules):
                continue
            report["historicalCasesRequiringRevalidation"] += 1
            report["affectedCaseIds"].append(case["subject_case_id"])
            for key, count in quarantine_financial_input(connection, case, correction, apply=apply).items():
                report[key] += count
        if apply:
            current_sources = facts.list_current(symbols)
            if sorted((row["datasetId"], row["subjectKey"], row.get("payloadHash")) for row in current_sources) != source_identity:
                raise RuntimeError("Source facts changed during repair; rerun preview before applying")
            locked = connection.execute("SELECT payload_json FROM app_store WHERE store_id = 'company_knowledge' FOR UPDATE").fetchone()
            if json.loads(locked["payload_json"] if locked else "{}") != cache:
                raise RuntimeError("Company cache changed during repair; retry without overwriting concurrent updates")
            replacement["updatedAt"] = stamp
            connection.execute("UPDATE app_store SET payload_json = %s, updated_at = %s WHERE store_id = 'company_knowledge'",
                               (json_dumps(replacement), stamp))
            report["auditId"] = audit_id
            connection.execute("INSERT INTO app_store (store_id, payload_json, updated_at) VALUES (%s, %s, %s)",
                               (audit_id, json_dumps({**report, **correction}), stamp))
    if apply:
        report["officialRefreshQueued"] = facts.make_due(["opendart.company_facts"])
    return report
