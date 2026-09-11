"""Read Models runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.read_models.public import (
        FlowLensService,
        InstrumentTimelineQueryService,
        InstrumentValuationQueryService,
        InvestmentAnalysisService,
    )


def build_instrument_timeline_query_service(settings=None) -> InstrumentTimelineQueryService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.read_models.public import InstrumentTimelineQueryService

    configured_settings = settings or runtime_settings()
    return InstrumentTimelineQueryService(
        time_series_store=stores.market_time_series_store(configured_settings),
        evidence_store=stores.research_evidence_store(configured_settings),
        calendar_store=stores.investment_calendar_store(configured_settings),
        decision_episode_store=stores.investment_decision_episode_store(configured_settings),
        hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(configured_settings),
        notification_job_store=stores.notification_job_store(configured_settings),
        symbol_store=stores.symbol_universe_store(configured_settings),
    )


def build_instrument_valuation_query_service(settings=None) -> InstrumentValuationQueryService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.read_models.public import InstrumentValuationQueryService

    configured_settings = settings or runtime_settings()
    return InstrumentValuationQueryService(
        monitor_store=stores.monitor_store(configured_settings),
        settings=configured_settings,
    )


def build_flow_lens_service(settings=None) -> FlowLensService:
    from digital_twin.domain.market_data import number
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.instruments import build_symbol_universe_service
    from digital_twin.infrastructure.settings import currency_rates, runtime_settings
    from digital_twin.infrastructure.toss_snapshots import build_snapshot, demo_positions
    from digital_twin.modules.read_models.public import FlowLensService

    configured_settings = settings or runtime_settings()
    flow_lens_external_settings = dict(configured_settings)
    def capped_int(key: str, fallback: int, cap: int) -> str:
        return str(min(cap, int(number(flow_lens_external_settings.get(key)) or fallback)))

    flow_lens_external_settings["externalApiRetryAttempts"] = "1"
    flow_lens_external_settings["externalApiTimeoutSeconds"] = str(min(2.0, number(flow_lens_external_settings.get("externalApiTimeoutSeconds")) or 2.0))
    flow_lens_external_settings["externalFredTimeoutSeconds"] = str(min(2.0, number(flow_lens_external_settings.get("externalFredTimeoutSeconds")) or 2.0))
    flow_lens_external_settings["externalAlphaMaxSymbols"] = capped_int("externalAlphaMaxSymbols", 1, 1)
    flow_lens_external_settings["externalSecMaxSymbols"] = capped_int("externalSecMaxSymbols", 1, 1)
    flow_lens_external_settings["externalDartMaxSymbols"] = capped_int("externalDartMaxSymbols", 1, 1)
    flow_lens_external_settings["externalNewsMaxSymbols"] = capped_int("externalNewsMaxSymbols", 1, 1)
    flow_lens_external_settings["externalCryptoMaxIds"] = capped_int("externalCryptoMaxIds", 2, 2)
    flow_lens_external_settings["externalFredMaxSeries"] = capped_int("externalFredMaxSeries", 2, 2)
    symbol_service = build_symbol_universe_service(configured_settings)
    return FlowLensService(
        account_repository=stores.account_reader(configured_settings),
        snapshot_builder=lambda account: build_snapshot(account, external_settings=flow_lens_external_settings),
        demo_positions_provider=demo_positions,
        settings_provider=lambda: configured_settings,
        fx_rates_provider=currency_rates,
        symbol_enricher=symbol_service.enrich,
        market_quote_cache=stores.market_quote_cache(configured_settings),
    )


def flow_lens_snapshot(mock: bool = False, watchlist_symbols: str = ""):
    return build_flow_lens_service().snapshot(mock=mock, watchlist_symbols=watchlist_symbols)


def build_investment_analysis_service(settings=None) -> InvestmentAnalysisService:
    from digital_twin.modules.read_models.public import InvestmentAnalysisService

    flow_service = build_flow_lens_service(settings)
    return InvestmentAnalysisService(
        snapshot_provider=lambda mock=False, watchlist_symbols="": flow_service.snapshot(
            mock=mock,
            watchlist_symbols=watchlist_symbols,
        ),
    )


def investment_analysis_snapshot(mock: bool = False, watchlist_symbols: str = ""):
    return build_investment_analysis_service().snapshot(mock=mock, watchlist_symbols=watchlist_symbols)
