from typing import Dict, Iterable, List

from ....application.external_data.contracts import CollectionJob, CollectionPartition, DatasetDescriptor, ExternalSubject
from .base import empty_signals, equity_partitions, legacy_provider, observation, position_for, require_payload, source_as_of


def is_korean_equity(subject: ExternalSubject) -> bool:
    symbol = str(subject.symbol or "").strip()
    return symbol.isdigit() and len(symbol) == 6


class OpenDartDisclosureAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="opendart.disclosures",
        provider_id="opendart",
        capability="incremental-document",
        cadence_seconds=600,
        freshness_seconds=1800,
        priority=85,
        rate_limit_seconds=1,
        enabled_setting="externalDartEnabled",
        cadence_setting="externalDataDartDisclosuresCadenceSeconds",
        freshness_setting="externalDataDartDisclosuresFreshnessSeconds",
        max_partitions_setting="externalDataDartMaxSymbols",
        max_partitions=100,
        revision_mode="immutable",
        materiality_policy="source-revision",
    )

    def partitions(self, subjects: Iterable[ExternalSubject], settings: Dict[str, object]) -> List[CollectionPartition]:
        if not str(settings.get("opendartApiKey") or "").strip():
            return []
        return equity_partitions(self.descriptor, subjects, is_korean_equity)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        provider = legacy_provider(
            settings,
            externalDartEnabled="1",
            externalDartMaxSymbols="1",
        )
        signals = empty_signals()
        provider.add_opendart(
            signals,
            [position_for(job.subject)],
            include_fundamentals=False,
            include_document=True,
        )
        row = require_payload(signals, "dartDisclosures", job.subject.symbol)
        receipt = str(row.get("receiptNo") or "")
        as_of = source_as_of(row, signals.get("fetchedAt"))
        return observation(
            self.descriptor,
            job.subject.symbol,
            {"dartDisclosures": {job.subject.symbol: row}},
            preferred_revision=receipt,
            preferred_source_as_of=as_of,
            watermark={"receiptNo": receipt},
        )


class OpenDartCompanyFactsAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="opendart.company_facts",
        provider_id="opendart",
        capability="polling-snapshot",
        cadence_seconds=86400,
        freshness_seconds=172800,
        priority=40,
        rate_limit_seconds=1,
        enabled_setting="externalDartCompanyFundamentalsEnabled",
        cadence_setting="externalDataDartFactsCadenceSeconds",
        freshness_setting="externalDataDartFactsFreshnessSeconds",
        max_partitions_setting="externalDataDartMaxSymbols",
        max_partitions=100,
        revision_mode="changes",
        materiality_policy="source-revision",
    )

    def partitions(self, subjects: Iterable[ExternalSubject], settings: Dict[str, object]) -> List[CollectionPartition]:
        if not str(settings.get("opendartApiKey") or "").strip():
            return []
        return equity_partitions(self.descriptor, subjects, is_korean_equity)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        provider = legacy_provider(
            settings,
            externalDartEnabled="1",
            externalDartCompanyFundamentalsEnabled="1",
            externalDartMaxSymbols="1",
        )
        signals = empty_signals()
        provider.add_opendart(
            signals,
            [position_for(job.subject)],
            include_fundamentals=True,
            include_document=False,
        )
        row = require_payload(signals, "dartDisclosures", job.subject.symbol)
        basis = row.get("financialStatementBasis") if isinstance(row.get("financialStatementBasis"), dict) else {}
        revision = ":".join([
            str(basis.get("businessYear") or ""),
            str(basis.get("reportCode") or ""),
            str(row.get("receiptNo") or ""),
        ]).strip(":")
        as_of = source_as_of(row, signals.get("fetchedAt"))
        return observation(
            self.descriptor,
            job.subject.symbol,
            {"dartDisclosures": {job.subject.symbol: row}},
            preferred_revision=revision,
            preferred_source_as_of=as_of,
            watermark={"financialStatementRevision": revision},
        )
