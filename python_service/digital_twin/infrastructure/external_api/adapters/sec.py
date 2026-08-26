import hashlib
from typing import Dict, Iterable, List

from ....application.external_data.contracts import (
    CollectionJob,
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
    FollowupCollectionRequest,
    SourceObservation,
    bounded_int,
)
from ....domain.disclosure_quality import assess_disclosure_document
from ...external_signal_provider_sec import DEFAULT_SEC_COMPANY_CIKS, sec_document_text
from ...external_signal_utils import symbol_assignments
from .base import empty_signals, equity_partitions, legacy_provider, observation, position_for, require_payload, source_as_of


def is_us_equity(subject: ExternalSubject) -> bool:
    symbol = str(subject.symbol or "").upper().strip()
    return bool(symbol and not symbol.isdigit() and (
        subject.market in {"US", "NASDAQ", "NYSE", "AMEX"} or subject.currency == "USD"
    ))


def sec_collectable_subjects(
    descriptor: DatasetDescriptor,
    subjects: Iterable[ExternalSubject],
    settings: Dict[str, object],
) -> List[CollectionPartition]:
    provider = legacy_provider(settings, externalSecEnabled="1")
    mapped = {
        provider.sec_symbol_key(symbol)
        for symbol, cik in {
            **DEFAULT_SEC_COMPANY_CIKS,
            **symbol_assignments(settings.get("externalSecCompanyCiks") or ""),
        }.items()
        if provider.normalize_cik(cik)
    }
    ticker_lookup_enabled = provider.sec_ticker_lookup_configured()
    return equity_partitions(
        descriptor,
        subjects,
        lambda subject: is_us_equity(subject)
        and (provider.sec_symbol_key(subject.symbol) in mapped or ticker_lookup_enabled),
    )


class SecSubmissionsAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="sec.submissions",
        provider_id="sec-edgar",
        capability="incremental-document",
        cadence_seconds=900,
        freshness_seconds=3600,
        priority=80,
        rate_limit_seconds=1,
        enabled_setting="externalSecEnabled",
        cadence_setting="externalDataSecSubmissionsCadenceSeconds",
        freshness_setting="externalDataSecSubmissionsFreshnessSeconds",
        max_partitions_setting="externalDataSecMaxSymbols",
        max_partitions=100,
        revision_mode="immutable",
        materiality_policy="source-revision",
    )

    def partitions(self, subjects: Iterable[ExternalSubject], settings: Dict[str, object]) -> List[CollectionPartition]:
        return sec_collectable_subjects(self.descriptor, subjects, settings)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        provider = legacy_provider(
            settings,
            externalSecEnabled="1",
            externalSecMaxSymbols="1",
        )
        signals = empty_signals()
        provider.add_sec_edgar(
            signals,
            [position_for(job.subject)],
            include_facts=False,
            include_document=False,
        )
        row = require_payload(signals, "secFilings", job.subject.symbol)
        latest = row.get("latestFiling") if isinstance(row.get("latestFiling"), dict) else {}
        revision = str(latest.get("accessionNumber") or "")
        as_of = source_as_of(row, signals.get("fetchedAt"))
        return observation(
            self.descriptor,
            job.subject.symbol,
            {"secFilings": {job.subject.symbol: row}},
            preferred_revision=revision,
            preferred_source_as_of=as_of,
            watermark={"accessionNumber": revision},
        )

    def followup_requests(
        self,
        observation: SourceObservation,
        settings: Dict[str, object],
    ) -> List[FollowupCollectionRequest]:
        provider = legacy_provider(settings, externalSecEnabled="1")
        if not provider.sec_document_access_configured():
            return []
        symbol = str(observation.subject_key or "").upper().strip()
        group = observation.payload.get("secFilings") if isinstance(observation.payload, dict) else {}
        row = group.get(symbol) if isinstance(group, dict) and isinstance(group.get(symbol), dict) else {}
        latest = row.get("latestFiling") if isinstance(row.get("latestFiling"), dict) else {}
        candidates = [latest, *(row.get("recentFilings") if isinstance(row.get("recentFilings"), list) else [])]
        limit = bounded_int(settings.get("externalSecDocumentMaxPerSymbol"), 3, 1, 10)
        requests = []
        seen = set()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            accession = str(item.get("accessionNumber") or "").strip()
            url = str(item.get("url") or "").strip()
            if not accession or not url or accession in seen:
                continue
            seen.add(accession)
            requests.append(FollowupCollectionRequest(
                dataset_id="sec.document",
                partition_key=symbol + ":" + accession + ":body-v1",
                subject=ExternalSubject(
                    subject_key=symbol,
                    symbol=symbol,
                    name=str(row.get("companyName") or symbol),
                    market="US",
                    currency="USD",
                    source="sec-submissions",
                ),
                watermark={
                    "accessionNumber": accession,
                    "cik": str(row.get("cik") or ""),
                    "companyName": str(row.get("companyName") or symbol),
                    "metadata": dict(item),
                },
                priority=72 if not requests else 68,
            ))
            if len(requests) >= limit:
                break
        return requests


class SecDocumentAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="sec.document",
        provider_id="sec-edgar",
        capability="official-document-body",
        cadence_seconds=86400,
        freshness_seconds=90 * 86400,
        priority=72,
        rate_limit_seconds=1,
        enabled_setting="externalSecDocumentTextEnabled",
        max_partitions=1000,
        revision_mode="immutable",
        materiality_policy="document-hash",
        partition_strategy="followup",
        completion_mode="once",
    )

    def partitions(self, _subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return []

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        provider = legacy_provider(settings, externalSecEnabled="1")
        metadata = dict(job.watermark.get("metadata") or {})
        accession = str(job.watermark.get("accessionNumber") or metadata.get("accessionNumber") or "").strip()
        url = str(metadata.get("url") or "").strip()
        if not provider.sec_document_access_configured() or not accession or not url:
            raise RuntimeError("SEC document job requires a contact email, accession number, and URL")
        raw = provider.guarded_call(
            "SEC EDGAR",
            "filing-document:" + job.subject.symbol + ":" + accession,
            lambda: provider.fetch_text(url, provider.sec_document_headers()),
        )
        text = sec_document_text(
            raw,
            bounded_int(settings.get("externalSecDocumentTextMaxChars"), 6000, 500, 20000),
        )
        assessment = assess_disclosure_document(text, "body")
        metadata.update({
            "accessionNumber": accession,
            "url": url,
            "documentText": assessment.document_text,
            "documentTextPreview": assessment.document_text[:700],
            "documentTextQuality": "body" if assessment.document_verified else "insufficient",
            "documentTextStatus": assessment.state,
        })
        row = {
            "provider": "SEC EDGAR",
            "symbol": job.subject.symbol,
            "cik": str(job.watermark.get("cik") or ""),
            "companyName": str(job.watermark.get("companyName") or job.subject.name or job.subject.symbol),
            "latestFiling": metadata,
            "recentFilings": [metadata],
        }
        digest = hashlib.sha256(assessment.document_text.encode("utf-8")).hexdigest()
        return observation(
            self.descriptor,
            job.subject.symbol,
            {"secFilings": {job.subject.symbol: row}},
            preferred_revision=accession + ":" + digest,
            preferred_source_as_of=str(metadata.get("filingDate") or metadata.get("reportDate") or ""),
            watermark={"accessionNumber": accession, "documentHash": digest},
            quality={
                "dataUsable": bool(assessment.document_verified),
                "provider": "sec-edgar",
                "documentState": assessment.state,
            },
        )


class SecCompanyFactsAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="sec.company_facts",
        provider_id="sec-edgar",
        capability="polling-snapshot",
        cadence_seconds=21600,
        freshness_seconds=86400,
        priority=45,
        rate_limit_seconds=1,
        enabled_setting="externalSecEnabled",
        cadence_setting="externalDataSecFactsCadenceSeconds",
        freshness_setting="externalDataSecFactsFreshnessSeconds",
        max_partitions_setting="externalDataSecMaxSymbols",
        max_partitions=100,
        revision_mode="changes",
        materiality_policy="source-revision",
    )

    def partitions(self, subjects: Iterable[ExternalSubject], settings: Dict[str, object]) -> List[CollectionPartition]:
        return sec_collectable_subjects(self.descriptor, subjects, settings)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        provider = legacy_provider(
            settings,
            externalSecEnabled="1",
            externalSecMaxSymbols="1",
        )
        signals = empty_signals()
        provider.add_sec_edgar(
            signals,
            [position_for(job.subject)],
            include_facts=True,
            include_document=False,
        )
        row = require_payload(signals, "secFilings", job.subject.symbol)
        facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
        as_of = source_as_of(facts, source_as_of(row, signals.get("fetchedAt")))
        return observation(
            self.descriptor,
            job.subject.symbol,
            {"secFilings": {job.subject.symbol: row}},
            preferred_source_as_of=as_of,
            watermark={"sourceAsOf": as_of},
        )
