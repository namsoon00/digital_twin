"""Schedule complementary official sources without replacing a failed source."""

from digital_twin.modules.market_data.domain.issuer_ir import issuer_ir_source
from .configuration_recovery_service import sec_metadata_access_ready


class ExternalCollectionRecoveryService:
    def __init__(self, registry, store, settings, now):
        self.registry, self.store, self.settings, self.now = registry, store, settings, now

    def recover(self, job, result):
        status = result.get("status")
        reason = result.get("reason")
        if not (status in {"error", "no-data"}
                or (status == "success" and result.get("dataUsable") is False)
                or (status == "deferred" and reason in {"access-denied", "circuit-open", "subject-unavailable"})):
            return {}
        if str((result.get("diagnostics") or {}).get("stage", "")).startswith("store."):
            return {}
        targets = []
        if job.dataset_id == "official.bls-release":
            targets = ["official.bls-statistics"]
        elif job.dataset_id == "issuer.ir_documents":
            source = issuer_ir_source(job.subject.symbol or job.subject.subject_key)
            if source:
                targets = [{"sec.document": "sec.submissions", "opendart.document": "opendart.disclosures"}[key]
                           for key in source.fallback_datasets if key in {"sec.document", "opendart.document"}]
        if not targets:
            return {}
        routes = []
        for dataset_id in targets:
            route = {"datasetId": dataset_id, "replacesOriginal": False}
            try:
                adapter = self.registry.adapter(dataset_id)
            except KeyError:
                routes.append({**route, "status": "unsupported"})
                continue
            descriptor = adapter.descriptor
            if not descriptor.enabled(self.settings):
                routes.append({**route, "status": "disabled"})
                continue
            if dataset_id == "sec.submissions" and not sec_metadata_access_ready(self.settings):
                routes.append({**route, "status": "configuration-required"})
                continue
            partitions = adapter.partitions([job.subject], self.settings)
            if not partitions:
                routes.append({**route, "status": "unavailable"})
                continue
            for partition in partitions[:1]:
                if not callable(getattr(self.store, "request_recovery_partition", None)):
                    routes.append({**route, "status": "unsupported"})
                    continue
                outcome = self.store.request_recovery_partition(descriptor, partition, now=self.now())
                routes.append({**route, "subjectKey": partition.subject.subject_key, **outcome})
        return {"version": "complementary-collection-v1", "originalSourceRecovered": False, "routes": routes}
