from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..domain.events import DomainEvent
from ..domain.data_freshness import parse_datetime
from ..domain.evidence_delta import (
    EvidenceMutation,
    clean_lifecycle_state,
    clean_symbol,
    evidence_content_signature,
    evidence_delta,
    evidence_inference_signature,
    eligible_evidence_set_revision,
    inference_eligible,
)
from ..domain.investment_research import ResearchEvidence
from ..domain import news_analysis as news_domain
from ..news_intelligence.domain.article import (
    apply_enrichment_snapshot,
    article_enrichment_revision,
    article_source_revision,
    authoritative_enrichment,
    enrichment_payload_snapshot,
)
from ..news_intelligence.domain.story import event_episode_identity, news_event_fingerprint
from .operational_common import (
    json_dumps,
    research_evidence_from_row,
)
from .settings import read_json, settings_path, utc_now
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_events import insert_domain_event_with_connection


DERIVED_EVIDENCE_PAYLOAD_KEYS = {
    "evidenceQualityAuthority",
    "articleCanonicalUrl",
    "aiAnalysis",
    "articleAiAnalysisVersion",
    "articleSummaryKo",
    "articleSummaryQuality",
    "summaryQualityState",
    "entityResolution",
    "publisherIdentity",
    "qualityGate",
    "newsEligibility",
    "newsIntelligenceVersion",
    "sourceIdentity",
    "sourceOrigin",
    "sourcePublisher",
    "sourceProvenance",
    "articleVerification",
    "eventClassificationVersion",
    "eventEpisodeId",
    "eventFingerprint",
    "storyClusterId",
    "storyIdentityVersion",
    "storyRootEvidenceId",
    "evidenceRelationship",
    "syndicationRootEvidenceId",
    "evidenceGovernance",
    "claimLedger",
    "disclosureDocumentQuality",
    "officialDocumentState",
    "metadataVerified",
    "documentVerified",
    "analysisReady",
    "disclosureAnalysis",
    "promptEvidenceAdmission",
    "documentHash",
    "documentCharCount",
    "documentLifecycle",
    "officialDocumentDatasetId",
    "officialDocumentFactRevision",
    "officialDocumentFactPayloadHash",
    "officialDocumentFetchedAt",
}

SOURCE_EVIDENCE_PAYLOAD_KEYS = {
    "articleSourceSummary",
    "articleText",
    "articleTextPreview",
    "sourceLanguage",
}

AUTHORITATIVE_EVIDENCE_STATE_KEYS = {
    "sourceTrustState",
    "materialityState",
    "dataState",
    "validationState",
}


def _payload_source_text(payload: Dict[str, object]) -> str:
    values = payload if isinstance(payload, dict) else {}
    facts = values.get("articleFacts") if isinstance(values.get("articleFacts"), dict) else {}
    return str(
        values.get("articleText")
        or facts.get("bodyText")
        or facts.get("bodyPreview")
        or values.get("officialDocumentText")
        or ""
    ).strip()


def merge_derived_evidence_payload(
    previous_payload: Dict[str, object],
    incoming_payload: Dict[str, object],
) -> Dict[str, object]:
    """Keep verified enrichment when another collector replays the same source row."""
    previous = dict(previous_payload or {})
    incoming = dict(incoming_payload or {})
    if str(incoming.get("evidenceQualityAuthority") or "") == "revalidation-v1":
        return incoming
    previous_text = _payload_source_text(previous)
    incoming_text = _payload_source_text(incoming)
    previous_analysis = previous.get("aiAnalysis") if isinstance(previous.get("aiAnalysis"), dict) else {}
    incoming_analysis = incoming.get("aiAnalysis") if isinstance(incoming.get("aiAnalysis"), dict) else {}
    same_analysis_source = bool(
        str(previous_analysis.get("sourceTextHash") or "").strip()
        and str(previous_analysis.get("sourceTextHash") or "").strip()
        == str(incoming_analysis.get("sourceTextHash") or "").strip()
    )
    if previous_text and incoming_text and previous_text != incoming_text and not same_analysis_source:
        return incoming
    preserve_authoritative_enrichment = bool(
        authoritative_enrichment(previous)
        and not authoritative_enrichment(incoming)
        and (
            not str(previous.get("articleSourceRevision") or "").strip()
            or not str(incoming.get("articleSourceRevision") or "").strip()
            or str(previous.get("articleSourceRevision") or "").strip()
            == str(incoming.get("articleSourceRevision") or "").strip()
        )
    )
    merged = dict(incoming)
    for key in SOURCE_EVIDENCE_PAYLOAD_KEYS:
        if merged.get(key) in (None, "", [], {}) and previous.get(key) not in (None, "", [], {}):
            merged[key] = previous.get(key)
    for key in DERIVED_EVIDENCE_PAYLOAD_KEYS:
        if merged.get(key) in (None, "", [], {}) and previous.get(key) not in (None, "", [], {}):
            merged[key] = previous.get(key)
    for key in ["articleFacts", "qualityGate"]:
        previous_nested = previous.get(key) if isinstance(previous.get(key), dict) else {}
        incoming_nested = incoming.get(key) if isinstance(incoming.get(key), dict) else {}
        if previous_nested:
            merged[key] = {**previous_nested, **incoming_nested}
    if str(previous.get("evidenceQualityAuthority") or "") == "revalidation-v1":
        for key in AUTHORITATIVE_EVIDENCE_STATE_KEYS:
            if key in previous:
                merged[key] = previous.get(key)
    for key in ["officialDocumentText", "officialDocumentPreview", "officialDocumentQuality"]:
        if merged.get(key) in (None, "") and previous.get(key) not in (None, ""):
            merged[key] = previous.get(key)
    previous_document_quality = previous.get("disclosureDocumentQuality") if isinstance(previous.get("disclosureDocumentQuality"), dict) else {}
    incoming_document_quality = incoming.get("disclosureDocumentQuality") if isinstance(incoming.get("disclosureDocumentQuality"), dict) else {}
    incoming_document_verified = bool(
        incoming.get("documentVerified")
        or incoming_document_quality.get("documentVerified")
    )
    if previous_document_quality and not incoming_document_verified:
        for key in [
            "dataState",
            "validationState",
            "officialDocumentState",
            "metadataVerified",
            "documentVerified",
            "analysisReady",
            "disclosureDocumentQuality",
            "disclosureAnalysis",
            "promptEvidenceAdmission",
            "documentHash",
            "documentCharCount",
            "documentLifecycle",
            "officialDocumentDatasetId",
            "officialDocumentFactRevision",
            "officialDocumentFactPayloadHash",
            "officialDocumentFetchedAt",
        ]:
            if key in previous:
                merged[key] = previous.get(key)
    terminal_rejection = bool(
        str(previous.get("validationState") or "").lower() == "blocked"
        and (
            previous.get("excludedReason")
            or str(previous.get("relationScope") or "").lower() == "entity_mismatch"
            or previous.get("bodyQualityPassed") is False
        )
    )
    if terminal_rejection:
        for key in [
            "relationScope",
            "relevanceState",
            "dataState",
            "validationState",
            "directMention",
            "excludedReason",
            "bodyQualityState",
            "bodyQualityPassed",
        ]:
            if key in previous:
                merged[key] = previous.get(key)
    if preserve_authoritative_enrichment:
        merged = apply_enrichment_snapshot(merged, enrichment_payload_snapshot(previous))
    return merged


class MySQLResearchEvidenceStore(MySQLOperationalConnection):
    @staticmethod
    def _news_analysis_release(payload: Dict[str, object]) -> str:
        values = dict(payload or {})
        analysis = values.get("aiAnalysis") if isinstance(values.get("aiAnalysis"), dict) else {}
        return str(values.get("articleAiAnalysisVersion") or analysis.get("version") or "unknown").strip()[:191]

    def _news_enrichment_snapshot_with_connection(
        self,
        connection,
        evidence_id: str,
        source_revision: str,
        analyzer_release: str = "",
        enrichment_revision: str = "",
    ) -> Dict[str, object]:
        clauses = ["evidence_id = %s", "source_revision = %s"]
        params: List[object] = [evidence_id, source_revision]
        if analyzer_release:
            clauses.append("analyzer_release = %s")
            params.append(analyzer_release)
        if enrichment_revision:
            clauses.append("enrichment_revision = %s")
            params.append(enrichment_revision)
        row = connection.execute(
            "SELECT enrichment_revision, payload_json FROM news_article_enrichment_revisions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC LIMIT 1",
            params,
        ).fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        payload["articleEnrichmentRevision"] = str(row.get("enrichment_revision") or "")
        return payload

    def get_news_evidence_revision(
        self,
        evidence_id: str,
        source_revision: str,
        enrichment_revision: str,
    ) -> Optional[ResearchEvidence]:
        current = self.get(str(evidence_id or "").strip())
        if not current or current.kind != "news":
            return None
        current_source_revision = str(
            (current.raw_payload or {}).get("articleSourceRevision")
            or article_source_revision(current)
        ).strip()
        if current_source_revision != str(source_revision or "").strip():
            return None
        with self.connect() as connection:
            snapshot = self._news_enrichment_snapshot_with_connection(
                connection,
                current.evidence_id,
                current_source_revision,
                enrichment_revision=str(enrichment_revision or "").strip(),
            )
        if not snapshot:
            return None
        result = copy.deepcopy(current)
        result.raw_payload = apply_enrichment_snapshot(dict(result.raw_payload or {}), snapshot)
        states = news_domain.news_state_payload(result.raw_payload)
        result.source_trust_state = states["sourceTrustState"]
        result.materiality_state = states["materialityState"]
        result.data_state = states["dataState"]
        result.validation_state = states["validationState"]
        return result

    def news_enrichment_status(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count,
                       SUM(CASE WHEN analysis_status IN ('ok', 'complete', 'success', 'verified') THEN 1 ELSE 0 END) AS authoritative_count,
                       MAX(updated_at) AS latest_updated_at
                FROM news_article_enrichment_revisions
                """
            ).fetchone() or {}
        return {
            "revisionCount": int(row.get("count") or 0),
            "authoritativeCount": int(row.get("authoritative_count") or 0),
            "latestUpdatedAt": str(row.get("latest_updated_at") or ""),
        }

    def repair_news_enrichment_revisions(self, limit: int = 5000, dry_run: bool = True) -> Dict[str, object]:
        row_limit = max(1, min(10000, int(limit or 5000)))
        stamp = utc_now()

        def operation(connection):
            rows = connection.execute(
                """
                SELECT * FROM research_evidence
                WHERE kind = 'news' AND lifecycle_state = 'active'
                ORDER BY last_seen_at DESC, evidence_id DESC
                LIMIT %s
                """,
                (row_limit,),
            ).fetchall()
            result = {
                "scannedCount": len(rows or []),
                "sourceRevisionUpdatedCount": 0,
                "authoritativePersistedCount": 0,
                "authoritativeRestoredCount": 0,
                "provisionalCount": 0,
                "changedCount": 0,
            }
            for row in rows or []:
                item = research_evidence_from_row(row)
                payload = dict(item.raw_payload or {})
                original_payload = dict(payload)
                source_revision = article_source_revision(item)
                if str(payload.get("articleSourceRevision") or "") != source_revision:
                    payload["articleSourceRevision"] = source_revision
                    result["sourceRevisionUpdatedCount"] += 1
                item.raw_payload = payload
                if authoritative_enrichment(payload):
                    result["authoritativePersistedCount"] += 1
                    enrichment_revision = article_enrichment_revision(item)
                    existing_snapshot = self._news_enrichment_snapshot_with_connection(
                        connection,
                        item.evidence_id,
                        source_revision,
                        enrichment_revision=enrichment_revision,
                    )
                    if existing_snapshot:
                        payload = apply_enrichment_snapshot(payload, existing_snapshot)
                        item.raw_payload = payload
                    if not dry_run:
                        self._persist_news_enrichment_with_connection(connection, item, stamp)
                        payload = dict(item.raw_payload or {})
                else:
                    result["provisionalCount"] += 1
                    snapshot = self._news_enrichment_snapshot_with_connection(
                        connection,
                        item.evidence_id,
                        source_revision,
                        self._news_analysis_release(payload),
                    )
                    if snapshot:
                        payload = apply_enrichment_snapshot(payload, snapshot)
                        item.raw_payload = payload
                        result["authoritativeRestoredCount"] += 1
                if payload != original_payload:
                    result["changedCount"] += 1
                    if not dry_run:
                        states = news_domain.news_state_payload(payload)
                        connection.execute(
                            """
                            UPDATE research_evidence
                            SET source_trust_state = %s, materiality_state = %s,
                                data_state = %s, validation_state = %s,
                                payload_json = %s
                            WHERE evidence_id = %s AND payload_json = %s
                            """,
                            (
                                states["sourceTrustState"],
                                states["materialityState"],
                                states["dataState"],
                                states["validationState"],
                                json_dumps(payload),
                                item.evidence_id,
                                str(row.get("payload_json") or ""),
                            ),
                        )
            result["dryRun"] = bool(dry_run)
            return result

        return dict(self.transaction_with_deadlock_retry("news-enrichment-repair", operation) or {})

    def _persist_news_enrichment_with_connection(
        self,
        connection,
        item: ResearchEvidence,
        stamp: str,
    ) -> str:
        payload = dict(item.raw_payload or {})
        if item.kind != "news" or not authoritative_enrichment(payload):
            return ""
        source_revision = str(payload.get("articleSourceRevision") or article_source_revision(item)).strip()
        payload["articleSourceRevision"] = source_revision
        enrichment_revision = article_enrichment_revision(item)
        payload["articleEnrichmentRevision"] = enrichment_revision
        item.raw_payload = payload
        analyzer_release = self._news_analysis_release(payload)
        snapshot = enrichment_payload_snapshot(payload)
        snapshot["articleSourceRevision"] = source_revision
        snapshot["articleEnrichmentRevision"] = enrichment_revision
        connection.execute(
            """
            INSERT IGNORE INTO news_article_enrichment_revisions (
                enrichment_revision, evidence_id, source_revision, analyzer_release,
                analysis_status, translation_status, summary_quality_state,
                payload_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                enrichment_revision,
                item.evidence_id,
                source_revision,
                analyzer_release,
                str((payload.get("aiAnalysis") or {}).get("status") or "")[:32],
                str(payload.get("translationStatus") or "")[:32],
                str(payload.get("summaryQualityState") or "")[:32],
                json_dumps(snapshot),
                stamp,
                stamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO news_article_enrichment_heads (
                evidence_id, source_revision, analyzer_release,
                enrichment_revision, updated_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                enrichment_revision = VALUES(enrichment_revision),
                updated_at = VALUES(updated_at)
            """,
            (
                item.evidence_id,
                source_revision,
                analyzer_release,
                enrichment_revision,
                stamp,
            ),
        )
        return enrichment_revision

    @staticmethod
    def _news_event_claim_statements(payload: Dict[str, object]) -> List[Tuple[str, str]]:
        rows: List[Tuple[str, str]] = []
        ledger = payload.get("claimLedger") if isinstance(payload.get("claimLedger"), dict) else {}
        for raw_claim in ledger.get("claims") or []:
            claim = raw_claim if isinstance(raw_claim, dict) else {}
            statement = " ".join(str(claim.get("statement") or claim.get("excerpt") or "").split()).strip()
            if len(statement) >= 24:
                rows.append((str(claim.get("claimKind") or "reported-claim"), statement))
        analysis = payload.get("aiAnalysis") if isinstance(payload.get("aiAnalysis"), dict) else {}
        summary = analysis.get("summary") if isinstance(analysis.get("summary"), dict) else {}
        for value in [summary.get("oneLineKo"), *(summary.get("keyTakeaways") or [])]:
            statement = " ".join(str(value or "").split()).strip()
            if len(statement) >= 24:
                rows.append(("analysis-summary", statement))
        unique: List[Tuple[str, str]] = []
        seen = set()
        for claim_kind, statement in rows:
            normalized = re.sub(r"[^0-9a-z가-힣%$]+", " ", statement.casefold()).strip()
            if len(normalized) < 16 or normalized in seen:
                continue
            seen.add(normalized)
            unique.append((claim_kind, statement))
        return unique[:24]

    def _persist_news_event_episode_with_connection(
        self,
        connection,
        item: ResearchEvidence,
        stamp: str,
    ) -> str:
        if item.kind != "news":
            return ""
        payload = dict(item.raw_payload or {})
        context = item.to_dict()
        episode_id = str(payload.get("eventEpisodeId") or event_episode_identity(context) or "").strip()
        if not episode_id:
            return ""
        fingerprint = payload.get("eventFingerprint") if isinstance(payload.get("eventFingerprint"), dict) else news_event_fingerprint(context).to_dict()
        payload["eventEpisodeId"] = episode_id
        payload["eventFingerprint"] = fingerprint
        item.raw_payload = payload
        provenance = payload.get("sourceProvenance") if isinstance(payload.get("sourceProvenance"), dict) else {}
        relationship = str(payload.get("evidenceRelationship") or provenance.get("evidenceRelationship") or "original")
        connection.execute(
            """
            INSERT INTO news_event_episodes (
                episode_id, symbol, event_family, event_phase, event_date,
                reporting_period, current_evidence_id, article_count, claim_count,
                first_seen_at, updated_at, payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 0, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                current_evidence_id = VALUES(current_evidence_id),
                updated_at = VALUES(updated_at),
                payload_json = VALUES(payload_json)
            """,
            (
                episode_id,
                item.symbol,
                str(fingerprint.get("family") or "")[:64],
                str(fingerprint.get("phase") or "")[:32],
                str(fingerprint.get("eventDate") or "")[:40],
                str(fingerprint.get("reportingPeriod") or "")[:32],
                item.evidence_id,
                stamp,
                stamp,
                json_dumps(fingerprint),
            ),
        )
        connection.execute(
            """
            INSERT INTO news_event_episode_articles (
                episode_id, evidence_id, evidence_relationship, first_seen_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                evidence_relationship = VALUES(evidence_relationship),
                updated_at = VALUES(updated_at)
            """,
            (episode_id, item.evidence_id, relationship[:32], stamp, stamp),
        )
        for claim_kind, statement in self._news_event_claim_statements(payload):
            normalized = re.sub(r"[^0-9a-z가-힣%$]+", " ", statement.casefold()).strip()
            statement_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
            claim_key = "news-event-claim:" + hashlib.sha256((episode_id + "|" + statement_hash).encode("utf-8")).hexdigest()[:32]
            connection.execute(
                """
                INSERT IGNORE INTO news_event_claims (
                    claim_key, episode_id, evidence_id, claim_kind,
                    statement_hash, statement_text, first_seen_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (claim_key, episode_id, item.evidence_id, claim_kind[:64], statement_hash, statement, stamp, stamp),
            )
        connection.execute(
            """
            UPDATE news_event_episodes
            SET article_count = (
                    SELECT COUNT(*) FROM news_event_episode_articles article
                    WHERE article.episode_id = %s
                ),
                claim_count = (
                    SELECT COUNT(*) FROM news_event_claims claim
                    WHERE claim.episode_id = %s
                )
            WHERE episode_id = %s
            """,
            (episode_id, episode_id, episode_id),
        )
        return episode_id

    def enqueue_news_analysis_work(self, jobs: Iterable[Dict[str, object]]) -> int:
        """Upsert latest-wins durable work without copying article payloads."""
        rows = []
        for job in jobs or []:
            evidence_id = str((job or {}).get("evidenceId") or "").strip()
            revision = str((job or {}).get("subjectRevision") or "").strip()
            work_class = str((job or {}).get("workClass") or "model").strip().lower()
            if evidence_id and revision and work_class in {"local", "model"}:
                rows.append((
                    evidence_id,
                    revision[:191],
                    work_class,
                    max(0, min(1000000, int((job or {}).get("priority") or 0))),
                ))
        if not rows:
            return 0
        stamp = utc_now()

        def operation(connection):
            connection.executemany(
                """
                INSERT INTO news_analysis_work_items (
                    evidence_id, subject_revision, work_class, work_state,
                    priority, last_error, created_at, updated_at
                ) VALUES (%s, %s, %s, 'pending', %s, '', %s, %s)
                ON DUPLICATE KEY UPDATE
                    lease_owner = IF(subject_revision <> VALUES(subject_revision) OR work_state = 'completed', '', lease_owner),
                    lease_until = IF(subject_revision <> VALUES(subject_revision) OR work_state = 'completed', '', lease_until),
                    not_before_at = IF(subject_revision <> VALUES(subject_revision) OR work_state = 'completed', '', not_before_at),
                    attempt_count = IF(subject_revision <> VALUES(subject_revision), 0, attempt_count),
                    last_error = IF(subject_revision <> VALUES(subject_revision) OR work_state = 'completed', '', last_error),
                    completed_at = IF(subject_revision <> VALUES(subject_revision) OR work_state = 'completed', '', completed_at),
                    work_state = IF(
                        subject_revision <> VALUES(subject_revision) OR work_state = 'completed',
                        'pending',
                        work_state
                    ),
                    subject_revision = VALUES(subject_revision),
                    work_class = VALUES(work_class),
                    priority = VALUES(priority),
                    updated_at = VALUES(updated_at)
                """,
                [
                    (evidence_id, revision, work_class, priority, stamp, stamp)
                    for evidence_id, revision, work_class, priority in rows
                ],
            )
            return len(rows)

        return int(self.transaction_with_deadlock_retry("news-analysis-work-enqueue", operation) or 0)

    def claim_news_analysis_work(
        self,
        worker_id: str,
        work_class: str,
        limit: int,
        lease_seconds: int = 300,
    ) -> List[Dict[str, object]]:
        owner = str(worker_id or "").strip()[:191]
        category = str(work_class or "model").strip().lower()
        row_limit = max(1, min(100, int(limit or 1)))
        if not owner or category not in {"local", "model"}:
            return []
        now = datetime.now(timezone.utc)
        stamp = now.isoformat().replace("+00:00", "Z")
        lease_until = (now + timedelta(seconds=max(30, min(1800, int(lease_seconds or 300))))).isoformat().replace("+00:00", "Z")

        def operation(connection):
            rows = connection.execute(
                """
                SELECT evidence_id, subject_revision, work_class, priority, attempt_count
                FROM news_analysis_work_items
                WHERE work_class = %s
                  AND (
                    (work_state IN ('pending', 'retrying') AND (not_before_at = '' OR not_before_at <= %s))
                    OR (work_state = 'running' AND (lease_until = '' OR lease_until <= %s))
                  )
                ORDER BY priority DESC, updated_at ASC, evidence_id ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (category, stamp, stamp, row_limit),
            ).fetchall()
            evidence_ids = [str(row.get("evidence_id") or "") for row in rows or [] if str(row.get("evidence_id") or "")]
            if evidence_ids:
                placeholders = ",".join(["%s"] * len(evidence_ids))
                connection.execute(
                    """
                    UPDATE news_analysis_work_items
                    SET work_state = 'running', lease_owner = %s, lease_until = %s,
                        attempt_count = attempt_count + 1, updated_at = %s
                    WHERE evidence_id IN (""" + placeholders + ")",
                    (owner, lease_until, stamp, *evidence_ids),
                )
            claimed = []
            for row in rows or []:
                evidence_id = str(row.get("evidence_id") or "")
                claimed.append({
                    "evidenceId": evidence_id,
                    "subjectRevision": str(row.get("subject_revision") or ""),
                    "workClass": category,
                    "priority": int(row.get("priority") or 0),
                    "attemptCount": int(row.get("attempt_count") or 0) + 1,
                    "leaseOwner": owner,
                    "leaseUntil": lease_until,
                })
            return claimed

        return list(self.transaction_with_deadlock_retry("news-analysis-work-claim", operation) or [])

    def finish_news_analysis_work(
        self,
        jobs: Iterable[Dict[str, object]],
        worker_id: str,
        retry_minutes: int = 0,
        error: str = "",
    ) -> int:
        rows = [dict(job or {}) for job in jobs or [] if str((job or {}).get("evidenceId") or "").strip()]
        if not rows:
            return 0
        owner = str(worker_id or "").strip()[:191]
        now = datetime.now(timezone.utc)
        stamp = now.isoformat().replace("+00:00", "Z")
        retry_at = (
            now + timedelta(minutes=max(1, min(1440, int(retry_minutes or 0))))
        ).isoformat().replace("+00:00", "Z") if retry_minutes else ""
        state = "retrying" if retry_at else "completed"

        def operation(connection):
            cursor = connection.executemany(
                """
                UPDATE news_analysis_work_items
                SET work_state = %s, lease_owner = '', lease_until = '',
                    not_before_at = %s, last_error = %s, updated_at = %s,
                    completed_at = %s
                WHERE evidence_id = %s AND subject_revision = %s
                  AND lease_owner = %s AND work_state = 'running'
                """,
                [
                    (
                        state,
                        retry_at,
                        str(error or "")[:1000],
                        stamp,
                        "" if retry_at else stamp,
                        str(job.get("evidenceId") or ""),
                        str(job.get("subjectRevision") or ""),
                        owner,
                    )
                    for job in rows
                ],
            )
            return max(0, int(getattr(cursor, "rowcount", 0) or 0))

        return int(self.transaction_with_deadlock_retry("news-analysis-work-finish", operation) or 0)

    def news_analysis_work_status(self) -> Dict[str, object]:
        stamp = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT work_state, work_class, COUNT(*) AS count,
                       MIN(updated_at) AS oldest_updated_at,
                       MAX(updated_at) AS latest_updated_at
                FROM news_analysis_work_items
                GROUP BY work_state, work_class
                """
            ).fetchall()
            reclaimable = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM news_analysis_work_items
                WHERE work_state = 'running' AND (lease_until = '' OR lease_until <= %s)
                """,
                (stamp,),
            ).fetchone()
            ready = connection.execute(
                """
                SELECT COUNT(*) AS count, MIN(updated_at) AS oldest_updated_at,
                       MAX(priority) AS highest_priority
                FROM news_analysis_work_items
                WHERE (work_state = 'pending')
                   OR (work_state = 'retrying' AND (not_before_at = '' OR not_before_at <= %s))
                   OR (work_state = 'running' AND (lease_until = '' OR lease_until <= %s))
                """,
                (stamp, stamp),
            ).fetchone() or {}
        oldest_ready = str(ready.get("oldest_updated_at") or "")
        oldest_parsed = parse_datetime(oldest_ready)
        oldest_age_minutes = max(
            0,
            int((datetime.now(timezone.utc) - oldest_parsed).total_seconds() // 60),
        ) if oldest_parsed else 0
        return {
            "durable": True,
            "states": [
                {
                    "state": str(row.get("work_state") or ""),
                    "workClass": str(row.get("work_class") or ""),
                    "count": int(row.get("count") or 0),
                    "oldestUpdatedAt": str(row.get("oldest_updated_at") or ""),
                    "latestUpdatedAt": str(row.get("latest_updated_at") or ""),
                }
                for row in rows or []
            ],
            "reclaimableLeaseCount": int((reclaimable or {}).get("count") or 0),
            "readyCount": int(ready.get("count") or 0),
            "oldestReadyAt": oldest_ready,
            "oldestReadyAgeMinutes": oldest_age_minutes,
            "highestReadyPriority": int(ready.get("highest_priority") or 0),
        }

    def news_analysis_work_items(self, evidence_ids: Iterable[str]) -> Dict[str, Dict[str, object]]:
        ids = sorted({str(value or "").strip() for value in evidence_ids or [] if str(value or "").strip()})
        if not ids:
            return {}
        placeholders = ",".join(["%s"] * len(ids))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT evidence_id, subject_revision, work_class, work_state,
                       attempt_count, not_before_at, lease_until, last_error, updated_at
                FROM news_analysis_work_items
                WHERE evidence_id IN (""" + placeholders + ")",
                ids,
            ).fetchall()
        return {
            str(row.get("evidence_id") or ""): {
                "evidenceId": str(row.get("evidence_id") or ""),
                "subjectRevision": str(row.get("subject_revision") or ""),
                "workClass": str(row.get("work_class") or ""),
                "workState": str(row.get("work_state") or ""),
                "attemptCount": int(row.get("attempt_count") or 0),
                "notBeforeAt": str(row.get("not_before_at") or ""),
                "leaseUntil": str(row.get("lease_until") or ""),
                "lastError": str(row.get("last_error") or ""),
                "updatedAt": str(row.get("updated_at") or ""),
            }
            for row in rows or []
            if str(row.get("evidence_id") or "")
        }

    def write_batch_size(self) -> int:
        try:
            configured = int(float(str(self.runtime_settings.get("researchEvidenceWriteBatchSize") or "50").strip()))
        except (TypeError, ValueError):
            configured = 50
        return max(1, min(50, configured))

    @staticmethod
    def _ordered_items(items: Iterable[ResearchEvidence]) -> List[ResearchEvidence]:
        """Deduplicate and sort writes so competing workers lock rows alike."""
        by_id = {}
        for item in items or []:
            evidence_id = str(getattr(item, "evidence_id", "") or "").strip()
            if evidence_id:
                by_id[evidence_id] = item
        return [by_id[evidence_id] for evidence_id in sorted(by_id)]

    def _item_batches(self, items: Iterable[ResearchEvidence]) -> List[List[ResearchEvidence]]:
        ordered = self._ordered_items(items)
        batch_size = self.write_batch_size()
        return [ordered[index:index + batch_size] for index in range(0, len(ordered), batch_size)]

    @staticmethod
    def _merged_mutation(mutations: Iterable[EvidenceMutation]) -> EvidenceMutation:
        merged = EvidenceMutation()
        changed_ids = set()
        for mutation in mutations or []:
            merged.written_count += int(getattr(mutation, "written_count", 0) or 0)
            merged.expired_count += int(getattr(mutation, "expired_count", 0) or 0)
            merged.retracted_count += int(getattr(mutation, "retracted_count", 0) or 0)
            for symbol in list(getattr(mutation, "changed_symbols", []) or []):
                if symbol and symbol not in merged.changed_symbols:
                    merged.changed_symbols.append(symbol)
            for item in list(getattr(mutation, "changed_items", []) or []):
                evidence_id = str(getattr(item, "evidence_id", "") or "").strip()
                if evidence_id and evidence_id not in changed_ids:
                    changed_ids.add(evidence_id)
                    merged.changed_items.append(item)
            merged.deltas.extend(list(getattr(mutation, "deltas", []) or []))
            merged.eligible_set_revisions.update(dict(getattr(mutation, "eligible_set_revisions", {}) or {}))
            merged.previous_eligible_set_revisions.update(dict(
                getattr(mutation, "previous_eligible_set_revisions", {}) or {}
            ))
            explicit_inference_symbols = getattr(mutation, "inference_changed_symbols_override", None)
            if explicit_inference_symbols is not None:
                if merged.inference_changed_symbols_override is None:
                    merged.inference_changed_symbols_override = []
                merged.inference_changed_symbols_override.extend(list(explicit_inference_symbols or []))
        return merged.with_revisions()

    def _row_lifecycle_state(self, row) -> str:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        return clean_lifecycle_state(row["lifecycle_state"] if "lifecycle_state" in keys else "active")

    def _row_lifecycle_changed_at(self, row) -> str:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        value = row["lifecycle_changed_at"] if "lifecycle_changed_at" in keys else ""
        return str(value or "").strip()

    def _active_eligible_signatures_by_symbol(self, connection, symbols: Iterable[str]) -> Dict[str, List[str]]:
        normalized = sorted({clean_symbol(symbol) for symbol in symbols or [] if clean_symbol(symbol)})
        if not normalized:
            return {}
        placeholders = ", ".join(["%s"] * len(normalized))
        rows = connection.execute(
            "SELECT * FROM research_evidence WHERE lifecycle_state = 'active' AND symbol IN (" + placeholders + ")",
            normalized,
        ).fetchall()
        result: Dict[str, List[str]] = {symbol: [] for symbol in normalized}
        for row in rows:
            item = research_evidence_from_row(row)
            lifecycle_state = self._row_lifecycle_state(row)
            if inference_eligible(item, lifecycle_state, self.runtime_settings):
                result.setdefault(clean_symbol(item.symbol), []).append(evidence_inference_signature(item))
        return result

    def _eligible_set_revisions_by_symbol(self, connection, symbols: Iterable[str]) -> Dict[str, str]:
        signatures = self._active_eligible_signatures_by_symbol(connection, symbols)
        return {
            symbol: eligible_evidence_set_revision(symbol, values)
            for symbol, values in signatures.items()
        }

    def _finalize_mutation(self, connection, mutation: EvidenceMutation) -> EvidenceMutation:
        affected_symbols = sorted({
            clean_symbol(delta.symbol)
            for delta in mutation.deltas
            if clean_symbol(delta.symbol)
        })
        if not affected_symbols:
            return mutation.with_revisions()
        after_revisions = self._eligible_set_revisions_by_symbol(connection, affected_symbols)
        before_revisions = dict(mutation.previous_eligible_set_revisions or {})
        for symbol in affected_symbols:
            before_revisions.setdefault(symbol, eligible_evidence_set_revision(symbol, []))
        inference_changed_symbols = [
            symbol
            for symbol in affected_symbols
            if before_revisions.get(symbol) != after_revisions.get(symbol)
        ]
        # Only changed active fact sets are passed to the reasoning queue.
        # This also collapses syndicated copies that have the same semantic
        # signature while retaining every source row for audit/provenance.
        mutation.inference_changed_symbols_override = inference_changed_symbols
        mutation.eligible_set_revisions = {
            symbol: after_revisions[symbol]
            for symbol in inference_changed_symbols
            if after_revisions.get(symbol)
        }
        return mutation.with_revisions()

    def _remember_mutation(self, mutation: EvidenceMutation) -> None:
        self.last_changed_symbols = list(mutation.changed_symbols or [])
        self.last_changed_items = list(mutation.changed_items or [])
        self.last_evidence_deltas = [delta.to_dict() for delta in mutation.deltas]
        self.last_eligible_evidence_revisions = dict(mutation.eligible_set_revisions or {})

    def _upsert_many_with_connection(
        self,
        connection,
        items: Iterable[ResearchEvidence],
        stamp: str,
    ) -> EvidenceMutation:
        rows = list(items or [])
        mutation = EvidenceMutation()
        mutation.previous_eligible_set_revisions = self._eligible_set_revisions_by_symbol(
            connection,
            [getattr(item, "symbol", "") for item in rows],
        )
        for item in rows:
            evidence_id = str(item.evidence_id or "").strip()
            if not evidence_id:
                continue
            symbol = str(item.symbol or "").upper().strip()
            kind = str(item.kind or "").strip()
            source = str(item.source or "").strip()
            title = str(item.title or "").strip()
            observed_at = str(item.observed_at or item.published_at or stamp).strip()
            published_at = str(item.published_at or item.observed_at or "").strip()
            dedupe_key = "|".join([symbol, kind, source, title, str(item.url or "").strip()])[:191]
            payload = dict(item.raw_payload or {})
            previous_row = connection.execute(
                """
                SELECT *
                FROM research_evidence
                WHERE evidence_id = %s
                """,
                (evidence_id,),
            ).fetchone()
            previous = research_evidence_from_row(previous_row) if previous_row else None
            if previous:
                payload = merge_derived_evidence_payload(previous.raw_payload, payload)
            item.raw_payload = payload
            source_revision = article_source_revision(item) if kind == "news" else ""
            if source_revision:
                payload["articleSourceRevision"] = source_revision
                analyzer_release = self._news_analysis_release(payload)
                if not authoritative_enrichment(payload):
                    authoritative = self._news_enrichment_snapshot_with_connection(
                        connection,
                        evidence_id,
                        source_revision,
                        analyzer_release,
                    )
                    if authoritative:
                        payload = apply_enrichment_snapshot(payload, authoritative)
                item.raw_payload = payload
                self._persist_news_enrichment_with_connection(connection, item, stamp)
                payload = dict(item.raw_payload or {})
                self._persist_news_event_episode_with_connection(connection, item, stamp)
                payload = dict(item.raw_payload or {})
            merged_states = news_domain.news_state_payload(payload)
            item.source_trust_state = merged_states["sourceTrustState"]
            item.materiality_state = merged_states["materialityState"]
            item.data_state = merged_states["dataState"]
            item.validation_state = merged_states["validationState"]
            states = item.state_payload()
            previous_lifecycle_state = self._row_lifecycle_state(previous_row) if previous_row else ""
            lifecycle_changed_at = stamp if not previous_row or previous_lifecycle_state != "active" else self._row_lifecycle_changed_at(previous_row)
            payload["evidenceLifecycleState"] = "active"
            payload["evidenceLifecycleChangedAt"] = lifecycle_changed_at
            current_signature = evidence_content_signature(item)
            previous_signature = evidence_content_signature(previous)
            connection.execute(
                """
                INSERT INTO research_evidence (
                    evidence_id, symbol, kind, source, title, summary, url, published_at,
                    observed_at, first_seen_at, last_seen_at, polarity, source_trust_state,
                    materiality_state, data_state, validation_state, lifecycle_state,
                    lifecycle_changed_at, dedupe_key, payload_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    symbol = VALUES(symbol),
                    kind = VALUES(kind),
                    source = VALUES(source),
                    title = VALUES(title),
                    summary = VALUES(summary),
                    url = VALUES(url),
                    published_at = VALUES(published_at),
                    observed_at = VALUES(observed_at),
                    last_seen_at = VALUES(last_seen_at),
                    polarity = VALUES(polarity),
                    source_trust_state = VALUES(source_trust_state),
                    materiality_state = VALUES(materiality_state),
                    data_state = VALUES(data_state),
                    validation_state = VALUES(validation_state),
                    lifecycle_state = VALUES(lifecycle_state),
                    lifecycle_changed_at = VALUES(lifecycle_changed_at),
                    dedupe_key = VALUES(dedupe_key),
                    payload_json = VALUES(payload_json)
                """,
                (
                    evidence_id,
                    symbol,
                    kind,
                    source,
                    title,
                    str(item.summary or ""),
                    str(item.url or ""),
                    published_at,
                    observed_at,
                    stamp,
                    stamp,
                    str(item.polarity or "context"),
                    states["sourceTrustState"],
                    states["materialityState"],
                    states["dataState"],
                    states["validationState"],
                    "active",
                    lifecycle_changed_at,
                    dedupe_key,
                    json_dumps(payload),
                ),
            )
            if not previous_row or current_signature != previous_signature or previous_lifecycle_state != "active":
                mutation.written_count += 1
                if symbol and symbol not in mutation.changed_symbols:
                    mutation.changed_symbols.append(symbol)
                mutation.changed_items.append(item)
                mutation.deltas.append(evidence_delta(
                    previous,
                    item,
                    previous_lifecycle_state=previous_lifecycle_state,
                    lifecycle_state="active",
                    occurred_at=stamp,
                    reason="evidence-upsert",
                    settings=self.runtime_settings,
                ))
        return self._finalize_mutation(connection, mutation)

    def _transition_rows_with_connection(
        self,
        connection,
        rows,
        lifecycle_state: str,
        transition: str,
        stamp: str,
        reason: str,
    ) -> EvidenceMutation:
        rows = list(rows or [])
        mutation = EvidenceMutation()
        mutation.previous_eligible_set_revisions = self._eligible_set_revisions_by_symbol(
            connection,
            [getattr(research_evidence_from_row(row), "symbol", "") for row in rows],
        )
        target_state = clean_lifecycle_state(lifecycle_state)
        for row in rows:
            previous = research_evidence_from_row(row)
            previous_state = self._row_lifecycle_state(row)
            if previous_state == target_state:
                continue
            payload = dict(previous.raw_payload or {})
            payload["evidenceLifecycleState"] = target_state
            payload["evidenceLifecycleChangedAt"] = stamp
            connection.execute(
                """
                UPDATE research_evidence
                SET lifecycle_state = %s,
                    lifecycle_changed_at = %s,
                    payload_json = %s,
                    last_seen_at = %s
                WHERE evidence_id = %s
                """,
                (target_state, stamp, json_dumps(payload), stamp, previous.evidence_id),
            )
            if transition == "expiration":
                mutation.expired_count += 1
            elif transition == "retraction":
                mutation.retracted_count += 1
            symbol = clean_symbol(previous.symbol)
            if symbol and symbol not in mutation.changed_symbols:
                mutation.changed_symbols.append(symbol)
            mutation.changed_items.append(previous)
            mutation.deltas.append(evidence_delta(
                previous,
                previous,
                previous_lifecycle_state=previous_state,
                lifecycle_state=target_state,
                transition=transition,
                occurred_at=stamp,
                reason=reason,
                settings=self.runtime_settings,
            ))
        return self._finalize_mutation(connection, mutation)

    def _active_rows_by_ids(self, connection, evidence_ids: Iterable[str], skip_locked: bool = False):
        ids = sorted({str(value or "").strip() for value in evidence_ids or [] if str(value or "").strip()})
        if not ids:
            return []
        placeholders = ", ".join(["%s"] * len(ids))
        return connection.execute(
            "SELECT * FROM research_evidence WHERE lifecycle_state = 'active' AND evidence_id IN ("
            + placeholders
            + ") ORDER BY evidence_id ASC FOR UPDATE"
            + (" SKIP LOCKED" if skip_locked else ""),
            ids,
        ).fetchall()

    def _stale_news_candidate_ids(self, cutoff: str, row_limit: int) -> List[str]:
        candidate_limit = max(row_limit, min(300, row_limit * 3))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT evidence_id
                FROM research_evidence
                WHERE lifecycle_state = 'active'
                  AND kind = 'news'
                  AND (
                    COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), NULLIF(last_seen_at, '')) < %s
                    OR (
                      COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), NULLIF(last_seen_at, ''))
                        REGEXP '^[0-9]{8}T?[0-9]{6}Z?$'
                      AND STR_TO_DATE(
                        REPLACE(REPLACE(
                          COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), NULLIF(last_seen_at, '')),
                          'T', ''
                        ), 'Z', ''),
                        '%%Y%%m%%d%%H%%i%%s'
                      ) < STR_TO_DATE(
                        LEFT(REPLACE(REPLACE(%s, 'T', ' '), 'Z', ''), 19),
                        '%%Y-%%m-%%d %%H:%%i:%%s'
                      )
                    )
                  )
                ORDER BY last_seen_at ASC, evidence_id ASC
                LIMIT %s
                """,
                (cutoff, cutoff, candidate_limit),
            ).fetchall()
        return [str(row.get("evidence_id") or "").strip() for row in rows if str(row.get("evidence_id") or "").strip()]

    def _stale_news_rows(self, connection, cutoff: str, row_limit: int, candidate_ids: Iterable[str]):
        ids = sorted({str(value or "").strip() for value in candidate_ids or [] if str(value or "").strip()})
        if not ids:
            return []
        placeholders = ", ".join(["%s"] * len(ids))
        return connection.execute(
            """
            SELECT * FROM research_evidence
            WHERE lifecycle_state = 'active'
              AND kind = 'news'
              AND (
                COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), NULLIF(last_seen_at, '')) < %s
                OR (
                  COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), NULLIF(last_seen_at, ''))
                    REGEXP '^[0-9]{8}T?[0-9]{6}Z?$'
                  AND STR_TO_DATE(
                    REPLACE(REPLACE(
                      COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), NULLIF(last_seen_at, '')),
                      'T', ''
                    ), 'Z', ''),
                    '%%Y%%m%%d%%H%%i%%s'
                  ) < STR_TO_DATE(
                    LEFT(REPLACE(REPLACE(%s, 'T', ' '), 'Z', ''), 19),
                    '%%Y-%%m-%%d %%H:%%i:%%s'
                  )
                )
              )
              AND evidence_id IN (""" + placeholders + """
              )
            ORDER BY evidence_id ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            [cutoff, cutoff, *ids, row_limit],
        ).fetchall()

    def upsert_many(self, items: Iterable[ResearchEvidence]) -> int:
        mutations = []
        for batch in self._item_batches(items):
            mutation = self.transaction_with_deadlock_retry(
                "research-evidence-upsert",
                lambda connection, rows=batch: self._upsert_many_with_connection(connection, rows, utc_now()),
            )
            mutations.append(mutation)
        mutation = self._merged_mutation(mutations)
        self._remember_mutation(mutation)
        return mutation.written_count

    def upsert_many_with_events(
        self,
        items: Iterable[ResearchEvidence],
        event_builder: Callable[[EvidenceMutation], Iterable[DomainEvent]],
    ) -> Tuple[int, List[DomainEvent]]:
        mutations = []
        events = []
        for batch in self._item_batches(items):
            def persist(connection, rows=batch):
                mutation = self._upsert_many_with_connection(connection, rows, utc_now())
                # Pass the immutable transaction result directly. Reading a
                # process-wide ``last_*`` field here would let concurrently
                # collected symbols leak their evidence revisions into this
                # transaction's durable reasoning request.
                batch_events = list(event_builder(mutation) or [])
                for event in batch_events:
                    insert_domain_event_with_connection(connection, event)
                return mutation, batch_events

            mutation, batch_events = self.transaction_with_deadlock_retry(
                "research-evidence-upsert-with-events",
                persist,
            )
            mutations.append(mutation)
            events.extend(batch_events)
        mutation = self._merged_mutation(mutations)
        self._remember_mutation(mutation)
        return mutation.written_count, events

    def expire_stale_news_with_events(
        self,
        cutoff_iso: str,
        limit: int,
        event_builder: Callable[[EvidenceMutation], Iterable[DomainEvent]],
    ) -> Tuple[EvidenceMutation, List[DomainEvent]]:
        cutoff = str(cutoff_iso or "").strip()
        if not cutoff:
            return EvidenceMutation(), []
        row_limit = max(1, min(50, int(limit or 50)))
        candidate_ids = self._stale_news_candidate_ids(cutoff, row_limit)
        if not candidate_ids:
            return EvidenceMutation(), []

        def persist(connection):
            mutation = self._transition_rows_with_connection(
                connection,
                self._stale_news_rows(connection, cutoff, row_limit, candidate_ids),
                "expired",
                "expiration",
                utc_now(),
                "news-age-expired",
            )
            events = list(event_builder(mutation) or []) if mutation.lifecycle_changed_count else []
            for event in events:
                insert_domain_event_with_connection(connection, event)
            return mutation, events

        mutation, events = self.transaction_with_deadlock_retry(
            "research-evidence-expire-stale-news",
            persist,
        )
        self._remember_mutation(mutation)
        return mutation, events

    def retract_many_with_events(
        self,
        evidence_ids: Iterable[str],
        reason: str,
        event_builder: Callable[[EvidenceMutation], Iterable[DomainEvent]],
    ) -> Tuple[EvidenceMutation, List[DomainEvent]]:
        def persist(connection):
            mutation = self._transition_rows_with_connection(
                connection,
                self._active_rows_by_ids(connection, evidence_ids, skip_locked=True),
                "retracted",
                "retraction",
                utc_now(),
                reason or "evidence-retracted",
            )
            events = list(event_builder(mutation) or []) if mutation.lifecycle_changed_count else []
            for event in events:
                insert_domain_event_with_connection(connection, event)
            return mutation, events

        mutation, events = self.transaction_with_deadlock_retry(
            "research-evidence-retract-many",
            persist,
        )
        self._remember_mutation(mutation)
        return mutation, events

    def latest(self, symbol: str = "", kind: str = "", limit: int = 50, include_inactive: bool = False) -> List[ResearchEvidence]:
        conditions, params = self._latest_conditions(
            symbol=symbol,
            kind=kind,
            include_inactive=include_inactive,
        )
        page_size = max(1, min(1000, int(limit or 50)))
        with self.connect() as connection:
            rows = self._latest_rows(connection, conditions, params, page_size, 0)
        return [research_evidence_from_row(row) for row in rows]

    def latest_page(self, symbol: str = "", kind: str = "", limit: int = 50, offset: int = 0, query: str = "", include_inactive: bool = False) -> Tuple[List[ResearchEvidence], int]:
        conditions, params = self._latest_conditions(
            symbol=symbol,
            kind=kind,
            query=query,
            include_inactive=include_inactive,
        )
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        page_size = max(1, min(1000, int(limit or 50)))
        page_offset = max(0, int(offset or 0))
        with self.connect() as connection:
            total_row = connection.execute("SELECT COUNT(*) AS count FROM research_evidence" + where, params).fetchone()
            rows = self._latest_rows(connection, conditions, params, page_size, page_offset)
        return [research_evidence_from_row(row) for row in rows], int(total_row["count"] or 0) if total_row else 0

    @staticmethod
    def _latest_conditions(
        symbol: str = "",
        kind: str = "",
        query: str = "",
        include_inactive: bool = False,
    ) -> Tuple[List[str], List[object]]:
        conditions = []
        params: List[object] = []
        if not include_inactive:
            conditions.append("lifecycle_state = 'active'")
        normalized_symbol = str(symbol or "").upper().strip()
        normalized_kind = str(kind or "").strip()
        if normalized_symbol:
            conditions.append("symbol = %s")
            params.append(normalized_symbol)
        if normalized_kind:
            conditions.append("kind = %s")
            params.append(normalized_kind)
        needle = str(query or "").strip()
        if needle:
            conditions.append("(title LIKE %s OR summary LIKE %s OR source LIKE %s OR symbol LIKE %s)")
            like = "%" + needle[:120] + "%"
            params.extend([like, like, like, like])
        return conditions, params

    @staticmethod
    def _latest_rows(connection, conditions, params, page_size: int, page_offset: int):
        """Sort narrow IDs first so large JSON evidence never enters temp sort files."""

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        id_rows = connection.execute(
            "SELECT evidence_id FROM research_evidence"
            + where
            + " ORDER BY last_seen_at DESC, published_at DESC, evidence_id DESC LIMIT %s OFFSET %s",
            list(params) + [page_size, page_offset],
        ).fetchall()
        evidence_ids = [
            str(row.get("evidence_id") or "").strip()
            for row in id_rows or []
            if str(row.get("evidence_id") or "").strip()
        ]
        if not evidence_ids:
            return []
        placeholders = ", ".join(["%s"] * len(evidence_ids))
        rows = connection.execute(
            "SELECT * FROM research_evidence WHERE evidence_id IN (" + placeholders + ")",
            evidence_ids,
        ).fetchall()
        rows_by_id = {str(row.get("evidence_id") or ""): row for row in rows or []}
        return [rows_by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in rows_by_id]

    def get(self, evidence_id: str) -> Optional[ResearchEvidence]:
        target = str(evidence_id or "").strip()
        if not target:
            return None
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM research_evidence WHERE evidence_id = %s", (target,)).fetchone()
        return research_evidence_from_row(row) if row else None

    def delete(self, evidence_id: str) -> bool:
        normalized_id = str(evidence_id or "").strip()
        if not normalized_id:
            return False
        mutation = self.transaction_with_deadlock_retry(
            "research-evidence-retract-one",
            lambda connection: self._transition_rows_with_connection(
                connection,
                self._active_rows_by_ids(connection, [normalized_id]),
                "retracted",
                "retraction",
                utc_now(),
                "manual-evidence-retraction",
            ),
        )
        self._remember_mutation(mutation)
        return bool(mutation.retracted_count)

    def delete_stale_news(self, cutoff_iso: str, limit: int = 500) -> int:
        cutoff = str(cutoff_iso or "").strip()
        if not cutoff:
            return 0
        row_limit = max(1, min(50, int(limit or 50)))
        candidate_ids = self._stale_news_candidate_ids(cutoff, row_limit)
        if not candidate_ids:
            return 0
        mutation = self.transaction_with_deadlock_retry(
            "research-evidence-expire-stale-news",
            lambda connection: self._transition_rows_with_connection(
                connection,
                self._stale_news_rows(connection, cutoff, row_limit, candidate_ids),
                "expired",
                "expiration",
                utc_now(),
                "news-age-expired",
            ),
        )
        self._remember_mutation(mutation)
        return mutation.expired_count

    def summary_counts(self, column: str, limit: int = 20) -> List[Dict[str, object]]:
        if column not in {"symbol", "kind", "source", "polarity"}:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT """ + column + """ AS name, COUNT(*) AS count, MAX(last_seen_at) AS latest_seen_at
                FROM research_evidence
                WHERE lifecycle_state = 'active' AND """ + column + """ != ''
                GROUP BY """ + column + """
                ORDER BY count DESC, latest_seen_at DESC
                LIMIT %s
                """,
                (max(1, min(100, int(limit or 20))),),
            ).fetchall()
        return [
            {
                "name": row["name"],
                "count": int(row["count"] or 0),
                "latestSeenAt": row["latest_seen_at"],
            }
            for row in rows
        ]

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count, MAX(last_seen_at) AS latest_seen_at FROM research_evidence WHERE lifecycle_state = 'active'"
            ).fetchone()
            audit_row = connection.execute(
                "SELECT COUNT(*) AS count FROM research_evidence"
            ).fetchone()
            lifecycle_rows = connection.execute(
                "SELECT lifecycle_state AS state, COUNT(*) AS count FROM research_evidence GROUP BY lifecycle_state"
            ).fetchall()
        return {
            "total": int(row["count"] or 0) if row else 0,
            "latestSeenAt": row["latest_seen_at"] if row else "",
            "auditTotal": int(audit_row["count"] or 0) if audit_row else 0,
            "byLifecycleState": {
                str(item["state"] or "unknown"): int(item["count"] or 0)
                for item in lifecycle_rows or []
            },
            "bySymbol": self.summary_counts("symbol"),
            "byKind": self.summary_counts("kind"),
            "bySource": self.summary_counts("source"),
            "byPolarity": self.summary_counts("polarity"),
        }
