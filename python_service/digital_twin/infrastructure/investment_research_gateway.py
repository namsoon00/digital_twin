from typing import Dict, Iterable, List, Tuple

from ..domain.investment_research import (
    NewsCollectionTarget,
    ResearchEvidence,
    research_evidence_from_external_signals,
)
from ..domain import news_analysis as news_domain
from ..domain.portfolio import Position
from .external_signals import ExternalSignalProvider


class NoopResearchEvidenceStore:
    def latest(self, symbol: str = "", kind: str = "", limit: int = 50):
        return []

    def upsert_many(self, items: Iterable[ResearchEvidence]) -> int:
        return 0


class ExistingApiResearchGateway:
    SUPPORTED_SOURCE_TYPES = {
        "official",
        "official-filing",
        "company-ir",
        "market-data",
        "financial-data",
    }

    def __init__(self, settings: Dict[str, object] = None, provider=None):
        self.settings = dict(settings or {})
        self.provider = provider

    def collect_for_target(
        self,
        target: NewsCollectionTarget,
        source_types: Iterable[str] = None,
    ) -> Tuple[List[ResearchEvidence], List[Dict[str, object]]]:
        requested = normalized_source_types(source_types)
        if requested and not requested.intersection(self.SUPPORTED_SOURCE_TYPES):
            return [], [{
                "source": "existing-api-bundle",
                "symbol": target.normalized_symbol(),
                "ok": True,
                "status": "not-requested",
                "requestedSourceTypes": sorted(requested),
            }]
        position = Position(
            symbol=target.normalized_symbol(),
            name=str(target.name or target.normalized_symbol()),
            market=str(target.market or ""),
            currency=str(target.currency or ""),
            sector=str(target.sector or "기타"),
            source="research",
        )
        signals = self.provider_for(requested).signals_for_positions([position])
        items = research_evidence_from_external_signals(target.normalized_symbol(), signals)
        items = self.filter_items(items, requested)
        statuses = [dict(item) for item in signals.get("statuses") or [] if isinstance(item, dict)]
        statuses.append({
            "source": "existing-api-bundle",
            "symbol": target.normalized_symbol(),
            "ok": True,
            "count": len(items),
            "requestedSourceTypes": sorted(requested),
            "fetchedAt": signals.get("fetchedAt"),
        })
        return items, statuses

    def provider_for(self, requested: set):
        if self.provider:
            return self.provider
        official_requested = not requested or bool(requested.intersection({"official", "official-filing", "company-ir"}))
        market_requested = not requested or bool(requested.intersection({"market-data", "financial-data"}))
        provider_settings = {
            **self.settings,
            "externalNewsEnabled": "0",
            "externalCoinGeckoEnabled": "0",
            "externalFredEnabled": "0",
            "externalFxRateEnabled": "0",
            "externalDartEnabled": "1" if official_requested else "0",
            "externalSecEnabled": "1" if official_requested else "0",
            "externalAlphaEnabled": "1" if market_requested else "0",
            "externalYFinanceEnabled": "1" if market_requested else "0",
        }
        return ExternalSignalProvider(
            provider_settings,
            evidence_store=NoopResearchEvidenceStore(),
        )

    def filter_items(self, items: Iterable[ResearchEvidence], requested: set) -> List[ResearchEvidence]:
        rows = list(items or [])
        if not requested:
            return rows
        allowed_kinds = set()
        if requested.intersection({"official", "official-filing", "company-ir"}):
            allowed_kinds.update({"disclosure", "filing", "financial-fact"})
        if requested.intersection({"market-data", "financial-data"}):
            allowed_kinds.update({"market-move", "financial-fact"})
        return [item for item in rows if item.kind in allowed_kinds]


class CompositeInvestmentResearchGateway:
    def __init__(self, gateways: Iterable[object]):
        self.gateways = [item for item in gateways or [] if item]

    def collect_for_target(
        self,
        target: NewsCollectionTarget,
        source_types: Iterable[str] = None,
    ) -> Tuple[List[ResearchEvidence], List[Dict[str, object]]]:
        evidence_by_id: Dict[str, ResearchEvidence] = {}
        statuses: List[Dict[str, object]] = []
        for gateway in self.gateways:
            try:
                items, gateway_statuses = gateway.collect_for_target(target, source_types=source_types)
            except TypeError:
                items, gateway_statuses = gateway.collect_for_target(target)
            except Exception as error:  # noqa: BLE001 - one research source must not block the others.
                statuses.append({
                    "source": gateway.__class__.__name__,
                    "symbol": target.normalized_symbol(),
                    "ok": False,
                    "message": str(error)[:180],
                })
                continue
            for item in items or []:
                if isinstance(item, ResearchEvidence) and str(item.evidence_id or "").strip():
                    current = evidence_by_id.get(item.evidence_id)
                    if current is None or evidence_completeness_key(item) > evidence_completeness_key(current):
                        evidence_by_id[item.evidence_id] = item
            statuses.extend(dict(item) for item in gateway_statuses or [] if isinstance(item, dict))
        return list(evidence_by_id.values()), statuses

    def providers(self) -> List[str]:
        return [item.__class__.__name__ for item in self.gateways]


def normalized_source_types(values: Iterable[str]) -> set:
    return {
        str(item or "").strip().lower()
        for item in values or []
        if str(item or "").strip()
    }


def evidence_completeness_key(item: ResearchEvidence) -> tuple:
    """Prefer the more usable duplicate without inventing an evidence score."""
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    states = item.state_payload()
    return (
        news_domain.news_state_rank(states),
        bool(str(item.published_at or item.observed_at or "").strip()),
        bool(str(item.url or "").strip()),
        bool(str(item.summary or "").strip()),
        bool(payload.get("articleSummaryKo")),
        str(item.observed_at or item.published_at or ""),
    )
