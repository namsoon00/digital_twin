from typing import Dict, Iterable, List

from ....application.external_data.contracts import CollectionJob, CollectionPartition, DatasetDescriptor, ExternalSubject
from ...external_signal_provider_sec import DEFAULT_SEC_COMPANY_CIKS
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
            include_document=True,
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
