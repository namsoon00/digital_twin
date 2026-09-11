from typing import Dict


class NewsPipelineRepairService:
    """Repair persisted news revisions and unsafe delivery identities."""

    def __init__(self, evidence_store, notification_store):
        self.evidence_store = evidence_store
        self.notification_store = notification_store

    def run(self, limit: int = 5000, dry_run: bool = True) -> Dict[str, object]:
        evidence_repair = self.evidence_store.repair_news_enrichment_revisions(
            limit=max(1, min(10000, int(limit or 5000))),
            dry_run=bool(dry_run),
        )
        identity_repair = self.notification_store.remove_weak_article_delivery_identities(
            dry_run=bool(dry_run),
        )
        event_identity_backfill = self.notification_store.backfill_news_event_family_delivery_identities(
            self.evidence_store,
            limit=max(1, min(500, int(limit or 300))),
            dry_run=bool(dry_run),
        )
        admission_repair = self.notification_store.repair_news_notification_admissions(
            dry_run=bool(dry_run),
        )
        return {
            "status": "preview" if dry_run else "repaired",
            "dryRun": bool(dry_run),
            "evidence": dict(evidence_repair or {}),
            "deliveryIdentity": dict(identity_repair or {}),
            "eventIdentityBackfill": dict(event_identity_backfill or {}),
            "notificationAdmissions": dict(admission_repair or {}),
        }
