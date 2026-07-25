from typing import Dict, List

from ..domain.investment_evidence_governance import claim_policy, claim_quality_summary, governed_evidence
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 1, upper: int = 10000) -> int:
    try:
        value = int(float(str(settings.get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


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

    def revalidate(self, symbol: str = "", limit: int = 500) -> Dict[str, object]:
        normalized_symbol = str(symbol or "").upper().strip()
        items = self.load_items(normalized_symbol, limit)
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
        written = self.evidence_store.upsert_many(written_items) if written_items else 0
        return {
            "status": "ok",
            "loadedCount": len(items),
            "writtenCount": written,
            "symbolCount": len(groups),
            "eligibleEvidenceCount": eligible_count,
            "rejectedEvidenceCount": rejected_count,
            "claimQuality": claim_quality_summary(written_items),
            "maxAgeMinutes": self.max_age_minutes(),
        }
