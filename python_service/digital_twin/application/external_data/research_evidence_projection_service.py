"""Project durable official-provider facts into canonical research evidence."""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from ...domain.events import (
    EXTERNAL_FACT_CHANGED,
    DomainEvent,
    ontology_reasoning_requested_event,
    research_evidence_collected_event,
)
from ...domain.disclosure_analysis import DISCLOSURE_ANALYSIS_PROMPT_VERSION, disclosure_analysis_payload
from ...domain.investment_evidence_governance import claim_policy, governed_evidence
from ...domain.investment_research import (
    NewsCollectionTarget,
    ResearchEvidence,
    research_evidence_from_external_signals,
)
from ...domain.materiality import evidence_materiality
from ...domain.prompt_evidence_admission import assess_prompt_evidence, attach_prompt_evidence_admission


OFFICIAL_DATASET_IDS = {"opendart.disclosures", "sec.submissions"}
OFFICIAL_EVIDENCE_KINDS = {"disclosure", "filing", "sec-filing", "sec_filing"}
DEFAULT_INITIAL_LOOKBACK_MINUTES = 10
DEFAULT_MAX_REPLAY_AGE_MINUTES = 180
CURRENT_FACT_BACKFILL_VERSION = "official-evidence-projection-v2"


def _text(value: object) -> str:
    return str(value or "").strip()


def _parse_datetime(value: object):
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _timestamp(value: datetime) -> str:
    parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        value = int(float(str(settings.get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


def _synchronize_states(item: ResearchEvidence) -> None:
    states = item.state_payload()
    item.source_trust_state = states["sourceTrustState"]
    item.materiality_state = states["materialityState"]
    item.data_state = states["dataState"]
    item.validation_state = states["validationState"]


class ExternalOfficialEvidenceProjectionService:
    """Translate SEC/OpenDART facts without coupling their collectors to alerts."""

    def __init__(
        self,
        fact_store,
        evidence_store,
        event_publisher,
        settings: Dict[str, object],
        now_provider=None,
        disclosure_analyzer=None,
    ):
        self.fact_store = fact_store
        self.evidence_store = evidence_store
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.disclosure_analyzer = disclosure_analyzer

    def official_max_age_minutes(self) -> int:
        return _int_setting(self.settings, "officialEvidenceMaxAgeMinutes", 7 * 24 * 60, 60, 60 * 24 * 90)

    def project_event(self, event: DomainEvent, *, allow_alert: bool = True) -> Dict[str, object]:
        if event.name != EXTERNAL_FACT_CHANGED:
            return {"status": "ignored", "reason": "unsupported-event", "writtenCount": 0}
        payload = dict(event.payload or {})
        dataset_id = _text(payload.get("datasetId"))
        subject_key = _text(payload.get("subjectKey"))
        if dataset_id not in OFFICIAL_DATASET_IDS:
            return {"status": "ignored", "reason": "non-official-dataset", "writtenCount": 0}
        row = self.fact_store.current_fact(dataset_id, subject_key)
        if not row:
            raise RuntimeError("official external fact is missing: " + dataset_id + "/" + subject_key)
        return self.project_fact(row, source_event=event, allow_alert=allow_alert)

    def project_fact(
        self,
        fact_row: Dict[str, object],
        *,
        source_event: DomainEvent = None,
        allow_alert: bool = True,
    ) -> Dict[str, object]:
        row = dict(fact_row or {})
        dataset_id = _text(row.get("datasetId"))
        symbol = _text(row.get("subjectKey")).upper()
        if dataset_id not in OFFICIAL_DATASET_IDS or not symbol:
            return {"status": "ignored", "reason": "unsupported-fact", "writtenCount": 0}
        source_payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        items = [
            item for item in research_evidence_from_external_signals(symbol, source_payload)
            if isinstance(item, ResearchEvidence) and _text(item.kind).lower() in OFFICIAL_EVIDENCE_KINDS
        ]
        if not items:
            return {"status": "empty", "datasetId": dataset_id, "symbol": symbol, "writtenCount": 0}

        now = self.now_provider()
        if not isinstance(now, datetime):
            now = datetime.now(timezone.utc)
        if not now.tzinfo:
            now = now.replace(tzinfo=timezone.utc)
        source_revision = _text(row.get("sourceRevision"))
        source_as_of = _text(row.get("sourceAsOf"))
        for item in items:
            payload = dict(item.raw_payload or {})
            document_text = _text(payload.get("officialDocumentText"))
            document_hash = hashlib.sha256(document_text.encode("utf-8")).hexdigest() if document_text else ""
            document_revision = _text(payload.get("receiptNo") or payload.get("accessionNumber") or source_revision)
            document_as_of = _text(
                payload.get("receiptDate")
                or payload.get("filingDate")
                or item.published_at
                or item.observed_at
                or source_as_of
            )
            payload.update({
                "externalFactDatasetId": dataset_id,
                "externalFactPayloadHash": _text(row.get("payloadHash")),
                "externalFactSourceRevision": source_revision,
                "externalFactSourceAsOf": source_as_of,
                "sourceRevision": document_revision,
                "sourceAsOf": document_as_of,
                "sourceFetchedAt": _text(row.get("fetchedAt")),
                "documentHash": document_hash,
                "documentCharCount": len(document_text),
            })
            item.raw_payload = payload
            self.enrich_disclosure_analysis(item)

        first_payload = items[0].raw_payload if isinstance(items[0].raw_payload, dict) else {}
        target = NewsCollectionTarget(
            symbol=symbol,
            name=_text(first_payload.get("companyName") or first_payload.get("corpName") or symbol),
            market="KOSPI" if symbol.isdigit() else "NASDAQ",
            currency="KRW" if symbol.isdigit() else "USD",
        )
        governed_evidence(
            items,
            target,
            self.official_max_age_minutes(),
            _text(self.settings.get("investmentBrainResearchMinimumSourceTrustState") or "standard"),
            policy=claim_policy(self.settings),
            now=now,
        )
        for item in items:
            item.raw_payload = attach_prompt_evidence_admission(
                item.raw_payload,
                kind=item.kind,
                published_at=item.published_at,
                observed_at=item.observed_at,
                now=now,
            )
            _synchronize_states(item)

        def event_builder(mutation) -> Iterable[DomainEvent]:
            changed_items = list(getattr(mutation, "changed_items", []) or [])
            if not changed_items:
                return []
            mutation_payload = mutation.to_dict() if hasattr(mutation, "to_dict") else {}
            assessments = [evidence_materiality(item, self.settings).to_dict() for item in changed_items]
            material_items = [item for item, assessment in zip(changed_items, assessments) if assessment.get("passed")]
            alert_items = [
                item for item in changed_items
                if allow_alert and assess_prompt_evidence(
                    item.raw_payload,
                    kind=item.kind,
                    published_at=item.published_at,
                    observed_at=item.observed_at,
                    now=now,
                ).alert_eligible
            ]
            changed_symbols = sorted({_text(item.symbol).upper() for item in changed_items if _text(item.symbol)})
            inference_symbols = list(
                mutation_payload.get("inferenceChangedSymbols")
                or getattr(mutation, "inference_changed_symbols", [])
                or []
            )
            event_payload = {
                "source": "external-official-evidence-projection",
                "status": "ok",
                "targetCount": 1,
                "fetchedCount": len(items),
                "savedCount": int(getattr(mutation, "written_count", 0) or 0),
                "changedCount": len(changed_items),
                "symbols": changed_symbols,
                "changedSymbols": changed_symbols,
                "materialChangedCount": len(material_items),
                "materialChangedSymbols": sorted({_text(item.symbol).upper() for item in material_items}),
                "alertEligibleCount": len(alert_items),
                "alertEligibleSymbols": sorted({_text(item.symbol).upper() for item in alert_items}),
                "changedItems": [item.to_dict() for item in changed_items[:50]],
                "materialChangedItems": [item.to_dict() for item in material_items[:50]],
                "alertEligibleItems": [item.to_dict() for item in alert_items[:50]],
                "materialityAssessments": assessments,
                "evidenceDeltas": list(mutation_payload.get("evidenceDeltas") or []),
                "factRevisionsBySymbol": dict(mutation_payload.get("factRevisionsBySymbol") or {}),
                "inferenceChangedSymbols": inference_symbols,
                "providers": [_text(row.get("providerId")) or dataset_id],
                "allowHistoricalAlert": False,
            }
            collected = research_evidence_collected_event(event_payload)
            events: List[DomainEvent] = [collected]
            if inference_symbols:
                events.append(ontology_reasoning_requested_event(
                    collected,
                    "official-research-evidence-update",
                    inference_symbols,
                    changed_count=len(inference_symbols),
                    observed_count=len(items),
                    fact_types=["ResearchEvidence", "DisclosureFiling", "VerifiedClaim"],
                    fact_types_by_symbol={
                        value: ["ResearchEvidence", "DisclosureFiling", "VerifiedClaim"]
                        for value in inference_symbols
                    },
                    changed_fields_by_symbol={
                        value: ["external.researchEvidence", "external.officialDocument"]
                        for value in inference_symbols
                    },
                    reason="검증된 SEC/OpenDART 문서 변경을 TypeDB ABox에 반영합니다.",
                    materiality_assessments=assessments,
                    fact_revisions_by_symbol=event_payload["factRevisionsBySymbol"],
                    evidence_deltas=event_payload["evidenceDeltas"],
                ))
            return events

        saved, recorded = self.evidence_store.upsert_many_with_events(items, event_builder)
        for event in recorded:
            dispatcher = getattr(self.event_publisher, "dispatch_recorded", None)
            if callable(dispatcher):
                dispatcher(event)
        return {
            "status": "ok" if saved else "unchanged",
            "datasetId": dataset_id,
            "symbol": symbol,
            "sourceRevision": source_revision,
            "evidenceCount": len(items),
            "writtenCount": int(saved or 0),
            "eventCount": len(recorded),
            "alertReplayAllowed": bool(allow_alert),
        }

    def enrich_disclosure_analysis(self, item: ResearchEvidence) -> None:
        payload = dict(item.raw_payload or {})
        if not payload.get("analysisReady"):
            return
        getter = getattr(self.evidence_store, "get", None)
        if callable(getter):
            try:
                existing = getter(item.evidence_id)
            except Exception:  # noqa: BLE001 - analysis can still be regenerated from the current document.
                existing = None
            existing_payload = getattr(existing, "raw_payload", None)
            existing_payload = existing_payload if isinstance(existing_payload, dict) else {}
            existing_analysis = existing_payload.get("disclosureAnalysis")
            existing_analysis = existing_analysis if isinstance(existing_analysis, dict) else {}
            if (
                payload.get("documentHash")
                and payload.get("documentHash") == existing_payload.get("documentHash")
                and existing_analysis.get("version") == DISCLOSURE_ANALYSIS_PROMPT_VERSION
                and existing_analysis.get("status") == "ready"
            ):
                payload["disclosureAnalysis"] = dict(existing_analysis)
                item.raw_payload = payload
                return
        analyzer = self.disclosure_analyzer
        if not analyzer or not callable(getattr(analyzer, "analyze", None)):
            return
        context = {
            "symbol": item.symbol,
            "title": item.title,
            "reportName": payload.get("reportName") or item.title,
            "receiptNo": payload.get("receiptNo") or payload.get("accessionNumber"),
            "receiptDate": payload.get("receiptDate") or payload.get("filingDate") or item.published_at,
            "provider": item.source,
            "officialDocumentText": payload.get("officialDocumentText"),
            "analysisReady": True,
            "metadata": {
                "reportName": payload.get("reportName") or item.title,
                "receiptNo": payload.get("receiptNo") or payload.get("accessionNumber"),
                "receiptDate": payload.get("receiptDate") or payload.get("filingDate") or item.published_at,
                "provider": item.source,
            },
        }
        result = analyzer.analyze(context)
        payload["disclosureAnalysis"] = {
            **disclosure_analysis_payload(context, result),
            "version": DISCLOSURE_ANALYSIS_PROMPT_VERSION,
            "status": "ready",
            "sourceTextHash": payload.get("documentHash"),
        }
        item.raw_payload = payload


class ExternalFactResearchEvidenceReconciler:
    """Replay durable external fact changes into the research bounded context."""

    def __init__(
        self,
        event_reader,
        projector: ExternalOfficialEvidenceProjectionService,
        cursor_store,
        *,
        batch_size: int = 100,
        initial_lookback_minutes: int = DEFAULT_INITIAL_LOOKBACK_MINUTES,
        max_replay_age_minutes: int = DEFAULT_MAX_REPLAY_AGE_MINUTES,
        now_provider=None,
    ):
        self.event_reader = event_reader
        self.projector = projector
        self.cursor_store = cursor_store
        self.batch_size = max(1, min(500, int(batch_size or 100)))
        self.initial_lookback_minutes = max(1, int(initial_lookback_minutes or DEFAULT_INITIAL_LOOKBACK_MINUTES))
        self.max_replay_age_minutes = max(self.initial_lookback_minutes, int(max_replay_age_minutes or DEFAULT_MAX_REPLAY_AGE_MINUTES))
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.last_result: Dict[str, object] = {}

    def backfill_current_facts(self, state: Dict[str, object]) -> Dict[str, object]:
        """Project the latest official facts once without replaying alerts."""

        if state.get("currentFactBackfillVersion") == CURRENT_FACT_BACKFILL_VERSION:
            return {
                "status": "complete",
                "version": CURRENT_FACT_BACKFILL_VERSION,
                "processedCount": 0,
                "writtenCount": 0,
            }
        reader = getattr(self.projector.fact_store, "list_current", None)
        if not callable(reader):
            return {"status": "unsupported", "processedCount": 0, "writtenCount": 0}
        rows = [
            dict(row) for row in (reader() or [])
            if isinstance(row, dict) and _text(row.get("datasetId")) in OFFICIAL_DATASET_IDS
        ]
        processed = 0
        written = 0
        for row in rows:
            result = self.projector.project_fact(row, allow_alert=False)
            processed += int(result.get("status") not in {"ignored", "empty"})
            written += int(result.get("writtenCount") or 0)
        state["currentFactBackfillCompleted"] = True
        state["currentFactBackfillVersion"] = CURRENT_FACT_BACKFILL_VERSION
        state["currentFactBackfillCount"] = processed
        return {
            "status": "complete",
            "version": CURRENT_FACT_BACKFILL_VERSION,
            "processedCount": processed,
            "writtenCount": written,
        }

    def run_once(self) -> Dict[str, object]:
        reader = getattr(self.event_reader, "external_fact_events_after", None)
        if not callable(reader):
            return {"status": "unsupported", "processedCount": 0, "projectedCount": 0}
        state = dict(self.cursor_store.load() or {}) if self.cursor_store else {}
        now = self.now_provider()
        if not isinstance(now, datetime):
            now = datetime.now(timezone.utc)
        if not now.tzinfo:
            now = now.replace(tzinfo=timezone.utc)
        backfill = self.backfill_current_facts(state)
        initial_floor = now - timedelta(minutes=self.initial_lookback_minutes)
        replay_floor = now - timedelta(minutes=self.max_replay_age_minutes)
        stored_at = _parse_datetime(state.get("lastOccurredAt"))
        if not stored_at:
            after_at, after_id = initial_floor, ""
        elif stored_at < replay_floor:
            after_at, after_id = replay_floor, ""
        else:
            after_at, after_id = stored_at, _text(state.get("lastEventId"))
        events = list(reader(
            after_occurred_at=_timestamp(after_at),
            after_event_id=after_id,
            limit=self.batch_size,
        ) or [])
        processed = 0
        projected = 0
        written = 0
        last_at = _timestamp(after_at)
        last_id = after_id
        for event in events:
            result = self.projector.project_event(event, allow_alert=True)
            processed += 1
            projected += int(result.get("status") not in {"ignored", "empty"})
            written += int(result.get("writtenCount") or 0)
            last_at = _text(event.occurred_at) or last_at
            last_id = _text(event.event_id) or last_id
        if self.cursor_store:
            next_state = {
                **state,
                "lastOccurredAt": last_at,
                "lastEventId": last_id,
                "processedCount": int(state.get("processedCount") or 0) + processed,
                "lastProcessedCount": processed,
                "lastProjectedCount": projected,
                "lastWrittenCount": written,
                "updatedAt": _timestamp(now),
            }
            self.cursor_store.replace(next_state)
        self.last_result = {
            "status": "ok" if processed else "idle",
            "processedCount": processed,
            "projectedCount": projected,
            "writtenCount": written,
            "cursorOccurredAt": last_at,
            "cursorEventId": last_id,
            "hasMore": len(events) >= self.batch_size,
            "currentFactBackfill": backfill,
        }
        return dict(self.last_result)
