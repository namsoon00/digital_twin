import hashlib
import urllib.parse
from typing import Callable, Dict, Iterable, List

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
from ....domain.disclosure_taxonomy import classify_disclosure
from ...external_signal_utils import dart_document_text, symbol_assignments
from .base import empty_signals, equity_partitions, legacy_provider, observation, position_for, require_payload, source_as_of


def is_korean_equity(subject: ExternalSubject) -> bool:
    symbol = str(subject.symbol or "").strip()
    return symbol.isdigit() and len(symbol) == 6


def successful_empty_disclosure_result(signals: Dict[str, object], symbol: str) -> Dict[str, object]:
    normalized = str(symbol or "").upper().strip()
    return next((
        item
        for item in signals.get("statuses") or []
        if isinstance(item, dict)
        and str(item.get("source") or "") == "OpenDART"
        and bool(item.get("ok"))
        and bool(item.get("emptyResult"))
        and str(item.get("target") or "").upper().strip() == normalized
    ), {})


class OpenDartCorpCodeResolver:
    def __init__(self, lookup: Callable[[], Dict[str, str]] = None):
        self.lookup = lookup
        self.cached: Dict[str, str] = {}
        self.loaded = False

    def assignments(self) -> Dict[str, str]:
        if not self.loaded:
            self.loaded = True
            recovered = self.lookup() if callable(self.lookup) else {}
            self.cached.update({
                str(symbol or "").zfill(6): str(code or "").zfill(8)
                for symbol, code in dict(recovered or {}).items()
                if str(symbol or "").strip() and str(code or "").strip()
            })
        return dict(self.cached)

    def settings_for(self, job: CollectionJob, settings: Dict[str, object]) -> Dict[str, object]:
        configured = dict(settings or {})
        assignments = symbol_assignments(configured.get("externalDartCorpCodes") or "")
        symbol = str(job.subject.symbol or job.partition_key or "").upper().strip()
        code = str(
            job.watermark.get("corpCode")
            or assignments.get(symbol)
            or self.assignments().get(symbol)
            or ""
        ).strip()
        if code:
            assignments[symbol] = code.zfill(8)
            configured["externalDartCorpCodes"] = "\n".join(
                key + "=" + str(value)
                for key, value in sorted(assignments.items())
            )
        return configured

    def remember(self, symbol: str, row: Dict[str, object]) -> str:
        normalized = str(symbol or "").upper().strip()
        code = str((row or {}).get("corpCode") or "").strip()
        if normalized and code:
            self.cached[normalized] = code.zfill(8)
        return code.zfill(8) if code else ""


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

    def __init__(self, corp_code_lookup: Callable[[], Dict[str, str]] = None):
        self.corp_codes = OpenDartCorpCodeResolver(corp_code_lookup)

    def partitions(self, subjects: Iterable[ExternalSubject], settings: Dict[str, object]) -> List[CollectionPartition]:
        if not str(settings.get("opendartApiKey") or "").strip():
            return []
        return equity_partitions(self.descriptor, subjects, is_korean_equity)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        configured = self.corp_codes.settings_for(job, settings)
        provider = legacy_provider(
            configured,
            externalDartEnabled="1",
            externalDartMaxSymbols="1",
        )
        signals = empty_signals()
        provider.add_opendart(
            signals,
            [position_for(job.subject)],
            include_fundamentals=False,
            include_document=False,
        )
        group = signals.get("dartDisclosures") if isinstance(signals.get("dartDisclosures"), dict) else {}
        row = group.get(job.subject.symbol) if isinstance(group.get(job.subject.symbol), dict) else {}
        empty_result = successful_empty_disclosure_result(signals, job.subject.symbol)
        if not row and empty_result:
            corp_code = self.corp_codes.remember(job.subject.symbol, empty_result)
            return observation(
                self.descriptor,
                job.subject.symbol,
                {"dartDisclosures": {}},
                preferred_revision="no-disclosures",
                preferred_source_as_of=str(signals.get("fetchedAt") or ""),
                watermark={"emptyResult": True, "corpCode": corp_code},
                quality={
                    "dataUsable": True,
                    "provider": "opendart",
                    "emptyResult": True,
                },
            )
        if not row:
            row = require_payload(signals, "dartDisclosures", job.subject.symbol)
        corp_code = self.corp_codes.remember(job.subject.symbol, row)
        for item in row.get("items") if isinstance(row.get("items"), list) else []:
            if isinstance(item, dict):
                item.update(classify_disclosure(item.get("reportName"), item.get("reportName"), "OpenDART"))
        receipt = str(row.get("receiptNo") or "")
        as_of = source_as_of(row, signals.get("fetchedAt"))
        return observation(
            self.descriptor,
            job.subject.symbol,
            {"dartDisclosures": {job.subject.symbol: row}},
            preferred_revision=receipt,
            preferred_source_as_of=as_of,
            watermark={"receiptNo": receipt, "corpCode": corp_code},
        )

    def followup_requests(
        self,
        observation: SourceObservation,
        settings: Dict[str, object],
    ) -> List[FollowupCollectionRequest]:
        symbol = str(observation.subject_key or "").upper().strip()
        group = observation.payload.get("dartDisclosures") if isinstance(observation.payload, dict) else {}
        row = group.get(symbol) if isinstance(group, dict) and isinstance(group.get(symbol), dict) else {}
        items = [item for item in row.get("items") or [] if isinstance(item, dict)]
        prioritized = [
            item for index, item in enumerate(items)
            if index == 0 or str(item.get("materialityState") or "") in {"notable", "material"}
        ]
        limit = bounded_int(settings.get("externalDartDocumentMaxPerSymbol"), 3, 1, 5)
        requests = []
        for item in prioritized[:limit]:
            receipt = str(item.get("receiptNo") or "").strip()
            if not receipt:
                continue
            requests.append(FollowupCollectionRequest(
                dataset_id="opendart.document",
                partition_key=symbol + ":" + receipt + ":body-v1",
                subject=ExternalSubject(
                    subject_key=symbol,
                    symbol=symbol,
                    name=str(item.get("corpName") or row.get("corpName") or symbol),
                    market="KR",
                    currency="KRW",
                    source="opendart-disclosure",
                ),
                watermark={"receiptNo": receipt, "metadata": dict(item)},
                priority=75 if item is prioritized[0] else 70,
            ))
        return requests


class OpenDartDocumentAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="opendart.document",
        provider_id="opendart",
        capability="official-document-body",
        cadence_seconds=86400,
        freshness_seconds=90 * 86400,
        priority=75,
        rate_limit_seconds=1,
        enabled_setting="externalDartDocumentTextEnabled",
        max_partitions=1000,
        revision_mode="immutable",
        materiality_policy="document-hash",
        partition_strategy="followup",
        completion_mode="once",
    )

    def partitions(self, _subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return []

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        api_key = str(settings.get("opendartApiKey") or "").strip()
        receipt = str(job.watermark.get("receiptNo") or "").strip()
        if not api_key or not receipt:
            raise RuntimeError("OpenDART document job is missing API key or receipt number")
        provider = legacy_provider(settings, externalDartEnabled="1")
        url = "https://opendart.fss.or.kr/api/document.xml?" + urllib.parse.urlencode({
            "crtfc_key": api_key,
            "rcept_no": receipt,
        })
        raw = provider.guarded_call(
            "OpenDART",
            "document:" + job.subject.symbol + ":" + receipt,
            lambda: provider.fetch_bytes(url, {"Accept": "application/zip,application/xml"}),
        )
        text = dart_document_text(raw, bounded_int(settings.get("externalDartDocumentTextMaxChars"), 6000, 500, 20000))
        assessment = assess_disclosure_document(text, "body")
        metadata = dict(job.watermark.get("metadata") or {})
        metadata.update({
            "provider": "OpenDART",
            "receiptNo": receipt,
            "documentText": assessment.document_text,
            "documentTextPreview": assessment.document_text[:700],
            "documentTextQuality": "body" if assessment.document_verified else "insufficient",
            "documentState": assessment.state,
        })
        row = {
            "provider": "OpenDART",
            "corpName": str(metadata.get("corpName") or job.subject.name or job.subject.symbol),
            "reportName": str(metadata.get("reportName") or "OpenDART 공시"),
            "receiptNo": receipt,
            "receiptDate": str(metadata.get("receiptDate") or ""),
            "items": [metadata],
            "documentText": assessment.document_text,
            "documentTextPreview": assessment.document_text[:700],
            "documentTextQuality": "body" if assessment.document_verified else "insufficient",
            "documentState": assessment.state,
        }
        digest = hashlib.sha256(assessment.document_text.encode("utf-8")).hexdigest()
        return observation(
            self.descriptor,
            job.subject.symbol,
            {"dartDisclosures": {job.subject.symbol: row}},
            preferred_revision=receipt + ":" + digest,
            preferred_source_as_of=str(metadata.get("receiptDate") or ""),
            watermark={"receiptNo": receipt, "documentHash": digest},
            quality={
                "dataUsable": bool(assessment.document_verified),
                "provider": "opendart",
                "documentState": assessment.state,
            },
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

    def __init__(self, corp_code_lookup: Callable[[], Dict[str, str]] = None):
        self.corp_codes = OpenDartCorpCodeResolver(corp_code_lookup)

    def partitions(self, subjects: Iterable[ExternalSubject], settings: Dict[str, object]) -> List[CollectionPartition]:
        if not str(settings.get("opendartApiKey") or "").strip():
            return []
        return equity_partitions(self.descriptor, subjects, is_korean_equity)

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        configured = self.corp_codes.settings_for(job, settings)
        provider = legacy_provider(
            configured,
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
        corp_code = self.corp_codes.remember(job.subject.symbol, row)
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
            watermark={"financialStatementRevision": revision, "corpCode": corp_code},
        )
