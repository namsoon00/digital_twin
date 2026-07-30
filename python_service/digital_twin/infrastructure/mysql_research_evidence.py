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


class MySQLResearchEvidenceStore(MySQLOperationalConnection):
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
            states = item.state_payload()
            previous_row = connection.execute(
                """
                SELECT *
                FROM research_evidence
                WHERE evidence_id = %s
                """,
                (evidence_id,),
            ).fetchone()
            previous = research_evidence_from_row(previous_row) if previous_row else None
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
                  AND COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), NULLIF(last_seen_at, '')) < %s
                ORDER BY last_seen_at ASC, evidence_id ASC
                LIMIT %s
                """,
                (cutoff, candidate_limit),
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
              AND COALESCE(NULLIF(published_at, ''), NULLIF(observed_at, ''), NULLIF(last_seen_at, '')) < %s
              AND evidence_id IN (""" + placeholders + """
              )
            ORDER BY evidence_id ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            [cutoff, *ids, row_limit],
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
        items, _ = self.latest_page(symbol=symbol, kind=kind, limit=limit, include_inactive=include_inactive)
        return items

    def latest_page(self, symbol: str = "", kind: str = "", limit: int = 50, offset: int = 0, query: str = "", include_inactive: bool = False) -> Tuple[List[ResearchEvidence], int]:
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
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        page_size = max(1, min(100, int(limit or 50)))
        page_offset = max(0, int(offset or 0))
        with self.connect() as connection:
            total_row = connection.execute("SELECT COUNT(*) AS count FROM research_evidence" + where, params).fetchone()
            rows = connection.execute(
                "SELECT * FROM research_evidence" + where + " ORDER BY last_seen_at DESC, published_at DESC, evidence_id DESC LIMIT %s OFFSET %s",
                params + [page_size, page_offset],
            ).fetchall()
        return [research_evidence_from_row(row) for row in rows], int(total_row["count"] or 0) if total_row else 0

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
