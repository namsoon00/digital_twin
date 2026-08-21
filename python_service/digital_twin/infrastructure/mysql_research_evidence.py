from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..domain.events import DomainEvent
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
from .operational_common import (
    json_dumps,
    research_evidence_from_row,
)
from .settings import read_json, settings_path, utc_now
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_events import insert_domain_event_with_connection


DERIVED_EVIDENCE_PAYLOAD_KEYS = {
    "aiAnalysis",
    "articleAiAnalysisVersion",
    "articleSummaryKo",
    "articleSummaryQuality",
    "summaryQualityState",
    "entityResolution",
    "qualityGate",
    "newsEligibility",
    "newsIntelligenceVersion",
    "sourceIdentity",
    "sourceProvenance",
    "articleVerification",
    "storyClusterId",
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
    previous_text = _payload_source_text(previous)
    incoming_text = _payload_source_text(incoming)
    if previous_text and incoming_text and previous_text != incoming_text:
        return incoming
    merged = dict(incoming)
    for key in DERIVED_EVIDENCE_PAYLOAD_KEYS:
        if merged.get(key) in (None, "", [], {}) and previous.get(key) not in (None, "", [], {}):
            merged[key] = previous.get(key)
    for key in ["articleFacts", "qualityGate"]:
        previous_nested = previous.get(key) if isinstance(previous.get(key), dict) else {}
        incoming_nested = incoming.get(key) if isinstance(incoming.get(key), dict) else {}
        if previous_nested:
            merged[key] = {**previous_nested, **incoming_nested}
    for key in ["officialDocumentText", "officialDocumentPreview", "officialDocumentQuality"]:
        if merged.get(key) in (None, "") and previous.get(key) not in (None, ""):
            merged[key] = previous.get(key)
    return merged


class MySQLResearchEvidenceStore(MySQLOperationalConnection):
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
                    work_state = IF(
                        subject_revision <> VALUES(subject_revision),
                        'pending',
                        work_state
                    ),
                    lease_owner = IF(subject_revision <> VALUES(subject_revision), '', lease_owner),
                    lease_until = IF(subject_revision <> VALUES(subject_revision), '', lease_until),
                    not_before_at = IF(subject_revision <> VALUES(subject_revision), '', not_before_at),
                    attempt_count = IF(subject_revision <> VALUES(subject_revision), 0, attempt_count),
                    last_error = IF(subject_revision <> VALUES(subject_revision), '', last_error),
                    completed_at = IF(subject_revision <> VALUES(subject_revision), '', completed_at),
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
