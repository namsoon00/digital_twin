import json
from typing import Dict, List

from ..domain.investment_evidence_governance import claim_policy, claim_quality_summary, governed_evidence
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence, disclosure_evidence_payload
from ..news_intelligence.application.analyze_article import annotate_evidence_eligibility
from ..news_intelligence.application.normalize_sources import normalize_evidence_sources


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 1, upper: int = 10000) -> int:
    try:
        value = int(float(str(settings.get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


def payload_signature(payload: Dict[str, object]) -> str:
    def stable(value):
        if isinstance(value, dict):
            return {
                key: stable(item)
                for key, item in value.items()
                if key not in {"checkedAt"}
            }
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    return json.dumps(stable(payload or {}), ensure_ascii=False, sort_keys=True, default=str)


class ResearchEvidenceGovernanceService:
    """Reapply deterministic claim governance to persisted evidence in batches."""

    def __init__(self, evidence_store, settings: Dict[str, object]):
        self.evidence_store = evidence_store
        self.settings = dict(settings or {})

    def max_age_minutes(self) -> int:
        return int_setting(
            self.settings,
            "newsEvidenceMaxAgeMinutes",
            int_setting(self.settings, "newsCollectionLookbackMinutes", 360, 5, 43200),
            5,
            43200,
        )

    def load_items(self, symbol: str = "", limit: int = 500) -> List[ResearchEvidence]:
        bounded_limit = max(1, min(5000, int(limit or 500)))
        if not hasattr(self.evidence_store, "latest_page"):
            return list(self.evidence_store.latest(symbol=symbol, limit=bounded_limit))
        loaded: List[ResearchEvidence] = []
        offset = 0
        while len(loaded) < bounded_limit:
            page_size = min(100, bounded_limit - len(loaded))
            page, total = self.evidence_store.latest_page(symbol=symbol, limit=page_size, offset=offset)
            loaded.extend(item for item in page if isinstance(item, ResearchEvidence))
            offset += len(page)
            if not page or offset >= int(total or 0):
                break
        return loaded

    def target_for_items(self, symbol: str, items: List[ResearchEvidence]) -> NewsCollectionTarget:
        raw = next((item.raw_payload for item in items if isinstance(item.raw_payload, dict)), {}) or {}
        return NewsCollectionTarget(
            symbol=symbol,
            name=str(raw.get("name") or raw.get("companyName") or raw.get("corpName") or symbol),
            market=str(raw.get("market") or ("KOSPI" if symbol.isdigit() else "NASDAQ")),
            currency=str(raw.get("currency") or ("KRW" if symbol.isdigit() else "USD")),
            sector=str(raw.get("sector") or ""),
        )

    def revalidate(self, symbol: str = "", limit: int = 500, dry_run: bool = False) -> Dict[str, object]:
        normalized_symbol = str(symbol or "").upper().strip()
        items = self.load_items(normalized_symbol, limit)
        before_payloads = {
            str(item.evidence_id or id(item)): payload_signature(item.raw_payload or {})
            for item in items
        }
        news_items = [item for item in items if str(item.kind or "").lower() == "news"]
        disclosure_items = [item for item in items if str(item.kind or "").lower() in {"disclosure", "filing", "sec-filing"}]
        for item in disclosure_items:
            payload = dict(item.raw_payload or {})
            item.raw_payload = disclosure_evidence_payload(
                payload,
                title=str(item.title or payload.get("reportName") or payload.get("officialDocumentType") or "공시"),
                source=str(item.source or payload.get("sourcePublisher") or "공식 공시"),
                document_text=payload.get("officialDocumentText"),
                document_quality=payload.get("officialDocumentQuality"),
                metadata_verified=bool(
                    str(item.title or "").strip()
                    and str(item.published_at or item.observed_at or "").strip()
                ),
            )
        normalize_evidence_sources(
            news_items,
            self.settings.get("researchClaimSourceRegistry") or "",
        )
        groups: Dict[str, List[ResearchEvidence]] = {}
        for item in items:
            item_symbol = str(item.symbol or "").upper().strip()
            if item_symbol:
                groups.setdefault(item_symbol, []).append(item)
        written_items: List[ResearchEvidence] = []
        rejected_count = 0
        eligible_count = 0
        for item_symbol, group in groups.items():
            _accepted, verified, rejected = governed_evidence(
                group,
                self.target_for_items(item_symbol, group),
                self.max_age_minutes(),
                str(self.settings.get("investmentBrainResearchMinimumSourceTrustState") or "standard"),
                policy=claim_policy(self.settings),
            )
            written_items.extend(group)
            rejected_count += len(rejected)
            eligible_count += len(verified)
        normalize_evidence_sources(
            news_items,
            self.settings.get("researchClaimSourceRegistry") or "",
        )
        for item in news_items:
            annotate_evidence_eligibility(
                item,
                self.settings.get("researchClaimSourceRegistry") or "",
            )
        for item in written_items:
            payload = dict(item.raw_payload or {})
            payload["evidenceQualityAuthority"] = "revalidation-v1"
            item.raw_payload = payload
        provenance_complete_count = 0
        duplicate_publication_count = 0
        unresolved_publisher_count = 0
        for item in news_items:
            payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
            provenance = payload.get("sourceProvenance") if isinstance(payload.get("sourceProvenance"), dict) else {}
            original = provenance.get("originalPublisher") if isinstance(provenance.get("originalPublisher"), dict) else {}
            provenance_complete_count += int(bool(provenance.get("provenanceComplete")))
            duplicate_publication_count += int(str(provenance.get("evidenceRelationship") or "") in {"exact-duplicate", "syndicated-copy"})
            unresolved_publisher_count += int(not original.get("publisherId") or original.get("publisherId") == "unknown")
        changed_items = [
            item for item in written_items
            if before_payloads.get(str(item.evidence_id or id(item)), "")
            != payload_signature(item.raw_payload or {})
        ]
        written = self.evidence_store.upsert_many(changed_items) if changed_items and not dry_run else 0
        return {
            "status": "ok",
            "dryRun": bool(dry_run),
            "notificationReplay": False,
            "loadedCount": len(items),
            "changedCount": len(changed_items),
            "writtenCount": written,
            "symbolCount": len(groups),
            "eligibleEvidenceCount": eligible_count,
            "rejectedEvidenceCount": rejected_count,
            "claimQuality": claim_quality_summary(written_items),
            "provenanceCompleteCount": provenance_complete_count,
            "duplicatePublicationCount": duplicate_publication_count,
            "unresolvedPublisherCount": unresolved_publisher_count,
            "disclosureCount": len(disclosure_items),
            "documentVerifiedDisclosureCount": len([
                item for item in disclosure_items
                if bool((item.raw_payload or {}).get("documentVerified"))
            ]),
            "metadataOnlyDisclosureCount": len([
                item for item in disclosure_items
                if str((item.raw_payload or {}).get("officialDocumentState") or "") == "metadata-only"
            ]),
            "maxAgeMinutes": self.max_age_minutes(),
        }
