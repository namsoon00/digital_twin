"""Bounded financial-evidence audit and repair with an immutable manifest."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from dataclasses import replace
import gzip
import hashlib
import json
from typing import Iterable, Mapping

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.infrastructure.operational_store import (
    ai_inference_queue_store,
    company_knowledge_cache,
    external_data_store,
)
from digital_twin.modules.market_data.public import ExternalSignalsReadModelService
from digital_twin.modules.news_intelligence.public import (
    financial_input_revalidation_assessment,
    financial_repair_plan,
    normalized_repair_symbols,
)
from digital_twin.modules.outcomes.public import quarantine_financial_input
from digital_twin.modules.reasoning.contracts import ontology_reasoning_requested_event
from digital_twin.shared_kernel.events import DomainEvent


FINANCIAL_REPAIR_MANIFEST_VERSION = "financial-repair-manifest-v1"
ACTIVE_NOTIFICATION_STATES = ("pending", "processing", "failed", "awaiting_ai", "retry")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json_dumps(value).encode()).hexdigest()


def _source_identity(rows: Iterable[Mapping[str, object]]):
    return sorted([
        {
            "datasetId": str(row.get("datasetId") or row.get("dataset_id") or ""),
            "subjectKey": str(row.get("subjectKey") or row.get("subject_key") or ""),
            "revisionId": str(row.get("revisionId") or row.get("revision_id") or ""),
            "sourceRevision": str(row.get("sourceRevision") or row.get("source_revision") or ""),
            "payloadHash": str(row.get("payloadHash") or row.get("payload_hash") or ""),
            "availability": str(row.get("availability") or "unknown"),
        }
        for row in rows or []
    ], key=lambda row: (row["datasetId"], row["subjectKey"]))


def _target_rows(cache: Mapping[str, object], symbols: Iterable[str]):
    rows = cache.get("symbols") if isinstance(cache, Mapping) and isinstance(cache.get("symbols"), Mapping) else {}
    return {symbol: rows.get(symbol) or {} for symbol in symbols}


def build_financial_repair_manifest(cache, replacement, changes, source_rows, symbols, history_limit):
    """Create the exact deterministic precondition accepted by apply."""

    requested = normalized_repair_symbols(symbols)
    material = {
        "version": FINANCIAL_REPAIR_MANIFEST_VERSION,
        "symbols": requested,
        "historyLimit": int(history_limit),
        "sourceIdentity": _source_identity(source_rows),
        "cacheFingerprint": _fingerprint(cache or {}),
        "targetBeforeFingerprint": _fingerprint(_target_rows(cache or {}, requested)),
        "targetAfterFingerprint": _fingerprint(_target_rows(replacement or {}, requested)),
        "changeFingerprints": [
            {"symbol": row.get("symbol"), "before": row.get("previousFingerprint"), "after": row.get("currentFingerprint")}
            for row in changes or []
        ],
    }
    return {**material, "manifestId": _fingerprint(material)[:32]}


def _symbol_sql(symbols):
    placeholders = ", ".join(["%s"] * len(symbols))
    return placeholders, tuple(symbols)


def retire_legacy_financial_requests(
    connection,
    queue,
    correction_id,
    stamp,
    *,
    symbols=(),
    apply=False,
):
    requested = normalized_repair_symbols(symbols)
    sql = "SELECT * FROM ai_inference_requests WHERE status IN ('pending', 'processing', 'retry')"
    params = ()
    if requested:
        placeholders, params = _symbol_sql(requested)
        sql += " AND UPPER(symbol) IN (" + placeholders + ")"
    sql += " ORDER BY request_id" + (" FOR UPDATE" if apply else "")
    rows = connection.execute(sql, params).fetchall()
    affected = []
    for row in rows:
        try:
            context = json.loads(row.get("context_json") or "{}")
        except (TypeError, ValueError):
            continue
        relation = context.get("ontologyRelationContext") or {}
        company = (relation.get("facts") or {}).get("companyContext") or {}
        assessment = financial_input_revalidation_assessment(company, relation.get("activeRules") or [])
        if not assessment.get("requiresRevalidation"):
            continue
        affected.append(str(row.get("request_id") or ""))
        if apply:
            queue.supersede_request_with_connection(connection, row, correction_id, stamp)
    return affected


def _locked_sources(connection, symbols):
    placeholders, params = _symbol_sql(symbols)
    return connection.execute(
        "SELECT dataset_id, subject_key, revision_id, source_revision, payload_hash, availability "
        "FROM external_fact_current WHERE subject_key = 'global' OR UPPER(subject_key) IN ("
        + placeholders + ") ORDER BY dataset_id, subject_key FOR UPDATE",
        params,
    ).fetchall()


def _case_rows(connection, symbols, limit, *, apply=False):
    placeholders, params = _symbol_sql(symbols)
    sql = (
        "SELECT c.subject_case_id, c.candidate_set_id, c.symbol, c.source_abox_snapshot_id, "
        "c.ai_request_id, c.notification_job_id, a.artifact_gzip, n.status AS notification_status, "
        "EXISTS(SELECT 1 FROM notification_delivery_attempts d "
        "WHERE d.job_id = c.notification_job_id AND d.status = 'delivered') AS delivered "
        "FROM investment_subject_decision_cases c "
        "LEFT JOIN ai_inference_execution_audits a ON a.request_id = c.ai_request_id "
        "LEFT JOIN notification_jobs n ON n.job_id = c.notification_job_id "
        "WHERE UPPER(c.symbol) IN (" + placeholders + ") AND c.payload_json LIKE '%%graph.company.%%' "
        "ORDER BY c.created_at DESC LIMIT %s"
    )
    rows = connection.execute(sql, params + (limit,)).fetchall()
    if apply and rows:
        case_ids = [str(row.get("subject_case_id") or "") for row in rows]
        locked_placeholders = ", ".join(["%s"] * len(case_ids))
        connection.execute(
            "SELECT subject_case_id FROM investment_subject_decision_cases WHERE subject_case_id IN ("
            + locked_placeholders + ") ORDER BY subject_case_id FOR UPDATE",
            tuple(case_ids),
        ).fetchall()
    return rows


def _financial_refresh_datasets(replacement, symbols):
    datasets = set()
    for row in _target_rows(replacement or {}, symbols).values():
        financials = row.get("latestFinancials") or row.get("financials") or {}
        for frequency_rows in financials.values() if isinstance(financials, Mapping) else []:
            for period in frequency_rows or []:
                contract = period.get("reportContract") if isinstance(period, Mapping) else {}
                for reference in (contract or {}).get("sourceReferences") or []:
                    dataset = str(reference.get("datasetId") or "") if isinstance(reference, Mapping) else ""
                    if dataset:
                        datasets.add(dataset)
    return sorted(datasets)


def _already_applied(connection, audit_id):
    row = connection.execute(
        "SELECT payload_json FROM app_store WHERE store_id = %s LIMIT 1", (audit_id,),
    ).fetchone()
    return json.loads(row["payload_json"]) if row and row.get("payload_json") else {}


def _reassessment_events(manifest, changes, stamp):
    symbols = [str(row.get("symbol") or "") for row in changes or [] if str(row.get("symbol") or "")]
    correction_event = DomainEvent(
        name="financial_input.correction_applied",
        aggregate_id="financial-repair:" + manifest["manifestId"],
        occurred_at=stamp,
        event_id="financial-input-correction:" + manifest["manifestId"],
        correlation_id="financial-input-correction:" + manifest["manifestId"],
        payload={
            "eventContract": "financial-input-correction-applied-v1",
            "manifestId": manifest["manifestId"],
            "symbols": symbols,
            "changedCount": len(symbols),
            "sourceFingerprint": _fingerprint(manifest["sourceIdentity"]),
            "changedFieldsBySymbol": {symbol: ["companyKnowledge.financials"] for symbol in symbols},
            "factTypesBySymbol": {symbol: ["FinancialFact"] for symbol in symbols},
            "sourceObservedAt": stamp,
        },
    )
    reasoning_event = ontology_reasoning_requested_event(
        correction_event,
        "financial-input-correction",
        symbols=symbols,
        affected_symbols=symbols,
        changed_count=len(symbols),
        observed_count=len(symbols),
        fact_types=["FinancialFact"],
        fact_types_by_symbol={symbol: ["FinancialFact"] for symbol in symbols},
        changed_fields_by_symbol={symbol: ["companyKnowledge.financials"] for symbol in symbols},
        subject_kind="FINANCIAL_INPUT_CORRECTION",
        subject_id=manifest["manifestId"],
        subject_revision=manifest["targetAfterFingerprint"],
        reason="출처 계약에 맞게 현재 재무 근거를 정정하여 해당 종목의 현재 ABox와 규칙 판단을 다시 계산합니다.",
        source_facts=[],
    )
    return correction_event, replace(
        reasoning_event,
        occurred_at=stamp,
        event_id="financial-reassessment:" + manifest["manifestId"],
    )


def run_financial_evidence_maintenance(
    settings,
    *,
    apply=False,
    limit=2000,
    symbols=(),
    manifest_id="",
):
    requested = normalized_repair_symbols(symbols)
    if apply and not requested:
        raise ValueError("Apply requires an explicit --symbols scope")
    if apply and not str(manifest_id or "").strip():
        raise ValueError("Apply requires the manifest ID produced by preview")
    history_limit = max(1, min(10000, int(limit)))
    store_settings = dict(settings or {})
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    store_settings["_skipOperationalHistoryRetention"] = "1"
    cache_store = company_knowledge_cache(store_settings)
    facts = external_data_store(store_settings)
    cache = cache_store.load()
    if not requested:
        requested = normalized_repair_symbols((cache.get("symbols") or {}).keys())
    if not requested:
        raise ValueError("No symbols are available for financial evidence audit")

    sources = facts.list_current(requested)
    signals = ExternalSignalsReadModelService(facts).signals_for_subjects(requested)
    replacement, changes = financial_repair_plan(cache, signals, requested)
    manifest = build_financial_repair_manifest(cache, replacement, changes, sources, requested, history_limit)
    requested_manifest = str(manifest_id or "").strip()
    audit_id = "financial-repair-audit:" + (requested_manifest or manifest["manifestId"])
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    correction = {
        "version": "financial-input-correction-v1",
        "correctionId": audit_id,
        "correctedAt": stamp,
        "reason": "financial-comparison-source-revalidation-required",
        "manifestId": manifest["manifestId"],
        "sourceFingerprint": _fingerprint(manifest["sourceIdentity"]),
    }
    report = {
        "mode": "apply" if apply else "preview",
        "status": "ready" if not apply else "applying",
        "manifest": manifest,
        "symbolsAudited": requested,
        "changes": changes,
        "effectiveChanges": len(changes),
        "historicalCasesRequiringRevalidation": 0,
        "historicalCasesUnknown": 0,
        "followUpsCanceled": 0,
        "targetsExcluded": 0,
        "outcomesExcluded": 0,
        "pendingPublicationsSuppressed": 0,
        "deliveredMessagesPreserved": 0,
        "immutableHistoryRewritten": False,
        "externalCollectionStarted": False,
        "customerCorrectionSent": False,
        "historicalScope": "retained frozen AI contexts only; missing artifacts are unknown",
    }
    db = MySQLOperationalConnection(store_settings)
    queue = ai_inference_queue_store(store_settings)
    with db.transaction() as connection:
        if apply:
            prior = _already_applied(connection, audit_id)
            if prior:
                return {
                    **prior,
                    "mode": "apply",
                    "status": "already-applied",
                    "effectiveChanges": 0,
                    "aiRequestsSuperseded": 0,
                    "reassessmentRequests": 0,
                    "sourceRefreshQueued": 0,
                }
            if requested_manifest != manifest["manifestId"]:
                raise RuntimeError("Repair manifest no longer matches current source/cache state; rerun preview")
            locked_sources = _locked_sources(connection, requested)
            if _source_identity(locked_sources) != manifest["sourceIdentity"]:
                raise RuntimeError("Source facts changed during repair; rerun preview before applying")
            locked = connection.execute(
                "SELECT payload_json FROM app_store WHERE store_id = 'company_knowledge' FOR UPDATE"
            ).fetchone()
            locked_cache = json.loads(locked["payload_json"] if locked else "{}")
            if _fingerprint(locked_cache) != manifest["cacheFingerprint"]:
                raise RuntimeError("Company cache changed during repair; retry without overwriting concurrent updates")

        report["supersededRequestIds"] = retire_legacy_financial_requests(
            connection,
            queue,
            audit_id,
            stamp,
            symbols=requested,
            apply=apply,
        )
        report["aiRequestsSuperseded"] = len(report["supersededRequestIds"])
        placeholders, symbol_params = _symbol_sql(requested)
        coverage = connection.execute(
            "SELECT COUNT(*) AS subjectCases, "
            "SUM(JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.synthesis.graph_candidate_action')) IN ('BUY', 'ADD')) AS graphEntryCandidates, "
            "SUM(JSON_CONTAINS(JSON_EXTRACT(payload_json, '$.synthesis.allowed_actions'), '\"BUY\"') "
            "OR JSON_CONTAINS(JSON_EXTRACT(payload_json, '$.synthesis.allowed_actions'), '\"ADD\"')) AS permittedEntryCases, "
            "SUM(JSON_LENGTH(JSON_EXTRACT(payload_json, '$.synthesis.execution_eligible_hypothesis_ids')) > 0) AS executableHypothesisCases "
            "FROM investment_subject_decision_cases WHERE UPPER(symbol) IN ("
            + placeholders + ") AND created_at >= %s",
            symbol_params + ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z"),),
        ).fetchone()
        report["actionCoverage24h"] = {key: int(value or 0) for key, value in (coverage or {}).items()}
        cases = _case_rows(connection, requested, history_limit, apply=apply)
        report["retainedCasesAudited"] = len(cases)
        report["limitReached"] = len(cases) == history_limit
        report["affectedCaseIds"] = []
        report["unknownCaseIds"] = []
        for case in cases:
            artifact_blob = case.get("artifact_gzip")
            if not artifact_blob:
                report["historicalCasesUnknown"] += 1
                report["unknownCaseIds"].append(case["subject_case_id"])
                continue
            try:
                artifact = json.loads(gzip.decompress(bytes(artifact_blob)))
                brief = artifact.get("executionAudit", {}).get("decisionBrief", {})
                company = brief.get("currentSituation", {}).get("companyContext", {})
                rules = brief.get("inference", {}).get("activeRules", [])
                assessment = financial_input_revalidation_assessment(company, rules)
            except (OSError, TypeError, ValueError, AttributeError):
                report["historicalCasesUnknown"] += 1
                report["unknownCaseIds"].append(case["subject_case_id"])
                continue
            if not assessment.get("requiresRevalidation"):
                continue
            report["historicalCasesRequiringRevalidation"] += 1
            report["affectedCaseIds"].append(case["subject_case_id"])
            if bool(case.get("delivered")):
                report["deliveredMessagesPreserved"] += 1
            elif str(case.get("notification_status") or "") in ACTIVE_NOTIFICATION_STATES:
                report["pendingPublicationsSuppressed"] += 1
                if apply and case.get("notification_job_id"):
                    queue.set_notification_status_with_connection(
                        connection,
                        case["notification_job_id"],
                        "suppressed",
                        "재무 입력 정정으로 기존 발송을 중단했습니다.",
                        {"inputCorrection": correction},
                        only_if_statuses=ACTIVE_NOTIFICATION_STATES,
                    )
            for key, count in quarantine_financial_input(connection, case, correction, apply=apply).items():
                report[key] += count

        if apply:
            replacement["updatedAt"] = stamp
            connection.execute(
                "UPDATE app_store SET payload_json = %s, updated_at = %s WHERE store_id = 'company_knowledge'",
                (json_dumps(replacement), stamp),
            )
            report["status"] = "applied"
            report["auditId"] = audit_id
            report["reassessmentRequests"] = 0
            report["reassessmentSymbols"] = []
            if changes:
                correction_event, reasoning_event = _reassessment_events(manifest, changes, stamp)
                insert_domain_event_with_connection(connection, correction_event)
                insert_domain_event_with_connection(connection, reasoning_event)
                report["reassessmentRequests"] = 1
                report["reassessmentSymbols"] = normalized_repair_symbols(
                    row.get("symbol") for row in changes
                )
            connection.execute(
                "INSERT INTO app_store (store_id, payload_json, updated_at) VALUES (%s, %s, %s)",
                (audit_id, json_dumps({**report, **correction}), stamp),
            )

    report["sourceRefreshDatasets"] = _financial_refresh_datasets(replacement, requested)
    report["sourceRefreshQueued"] = 0
    report["reassessmentRequests"] = 0 if not apply else int(report.get("reassessmentRequests") or 0)
    if apply and changes and report["sourceRefreshDatasets"]:
        report["sourceRefreshQueued"] = facts.make_due(report["sourceRefreshDatasets"], requested)
    return report
