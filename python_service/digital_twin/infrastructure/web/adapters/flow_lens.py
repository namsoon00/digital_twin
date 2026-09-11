"""Web flow lens boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.service_factory import build_flow_lens_service
from digital_twin.infrastructure.service_factory import flow_lens_snapshot
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.web.adapters.capital_flow import capital_flow_api_payload
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.events import REALTIME_HUB
from digital_twin.modules.market_data.domain.data_freshness import age_minutes
from digital_twin.modules.read_models.infrastructure.flow_lens_read_model import FlowLensReadModel
from typing import Dict
from typing import List
import threading


FLOW_LENS_READ_MODEL = None


FLOW_LENS_READ_MODEL_LOCK = threading.Lock()


def flow_lens_data_freshness(generated_at: object, settings: Dict[str, object] = None) -> Dict[str, object]:
    """Expose one freshness contract for every Flow Lens consumer.

    ``toss.mode`` describes the provider connection, not the age of the
    monitor projection.  Keeping this calculation in the API prevents each
    screen from treating an old live connection as current market data.
    """
    settings = settings or {}
    try:
        max_age = int(float(settings.get("marketDataMaxAgeMinutes") or settings.get("dataFreshnessDefaultMaxAgeMinutes") or 30))
    except (TypeError, ValueError):
        max_age = 30
    max_age = max(1, min(1440, max_age))
    age = age_minutes(generated_at)
    if age is None:
        status, label, reason = "unknown", "기준시각 없음", "스냅샷 생성 시각이 없습니다."
    elif age > max_age:
        status, label, reason = "stale", "데이터 지연", "최근 스냅샷이 신선도 기준을 넘었습니다."
    else:
        status, label, reason = "fresh", "데이터 신선", "최근 스냅샷이 신선도 기준 안에 있습니다."
    return {
        "status": status,
        "label": label,
        "reason": reason,
        "ageMinutes": age,
        "maxAgeMinutes": max_age,
        "generatedAt": str(generated_at or ""),
    }


def compact_flow_lens_payload(payload: Dict[str, object]) -> Dict[str, object]:
    """Keep initial dashboard payload small; full ontology rows load on demand."""
    if not isinstance(payload, dict):
        return payload
    compact = dict(payload)
    decision = compact.get("tossDecision")
    if not isinstance(decision, dict):
        return compact
    decision = dict(decision)
    compact["tossDecision"] = decision

    market_item_keys = {
        "symbol", "name", "symbolName", "displayName", "market", "exchange", "currency", "sector",
        "source", "currentPrice", "changeRate", "quantity", "averagePrice", "marketValue",
        "marketValueKrw", "profitLoss", "profitLossRate", "ma5", "ma20", "ma60", "volume",
        "volumeRatio", "tradeStrength", "buyVolume", "sellVolume", "bidAskImbalance",
        "foreignBuyVolume", "foreignSellVolume", "foreignNet", "foreignNetVolume",
        "institutionBuyVolume", "institutionSellVolume", "institutionNet", "institutionNetVolume", "individualNet",
        "individualNetVolume", "marketSignalCoverage", "freshnessStatus", "quoteStatus", "quoteSource",
        "provider", "sourceAsOf", "updatedAt", "dataQuality", "dataMode", "isMock",
    }
    decision_item_keys = market_item_keys | {
        "decision", "action", "actionCode", "reviewLevel", "reason", "nextAction", "decisionBasis",
        "portfolioRole", "accountId", "accountLabel", "decisionKey", "decisionEpisodeId", "updatedAt",
    }

    def compact_rows(value, allowed_keys):
        if not isinstance(value, list):
            return value
        return [
            {key: item.get(key) for key in sorted(allowed_keys) if key in item}
            for item in value
            if isinstance(item, dict)
        ]

    toss = compact.get("toss")
    if isinstance(toss, dict):
        toss = dict(toss)
        for key in ["positions", "watchlistQuotes", "watchlist"]:
            toss[key] = compact_rows(toss.get(key), market_item_keys)
        external = toss.get("externalSignals")
        if isinstance(external, dict):
            external = dict(external)
            omitted = []
            for key in [
                "yfinanceData",
                "researchEvidence",
                "companyOverviews",
                "earningsReports",
                "secFilings",
                "dartDisclosures",
                "companyKnowledge",
            ]:
                value = external.pop(key, None)
                if value not in (None, [], {}, ""):
                    omitted.append(key)
                    if isinstance(value, (list, dict)):
                        external[key + "Count"] = len(value)
            external["detailLevel"] = "summary"
            external["heavyFieldsOmitted"] = omitted
            toss["externalSignals"] = external
        metadata = toss.get("metadata")
        if isinstance(metadata, dict):
            metadata = dict(metadata)
            proxies = metadata.pop("marketProxyQuotes", None)
            if isinstance(proxies, (list, dict)):
                metadata["marketProxyQuoteCount"] = len(proxies)
            omitted_metadata = []
            for key in ["cryptoTransitionBaseline", "marketObservationBaselines", "ontology", "kis"]:
                value = metadata.pop(key, None)
                if value not in (None, [], {}, ""):
                    omitted_metadata.append(key)
            metadata["detailLevel"] = "summary"
            metadata["heavyFieldsOmitted"] = omitted_metadata
            toss["metadata"] = metadata
        compact["toss"] = toss

    portfolio = compact.get("portfolio")
    if isinstance(portfolio, dict):
        portfolio = dict(portfolio)
        portfolio["positions"] = compact_rows(portfolio.get("positions"), market_item_keys)
        compact["portfolio"] = portfolio

    for key in ["positions", "items"]:
        decision[key] = compact_rows(decision.get(key), decision_item_keys)

    strategy = decision.get("ontologyStrategy")
    if isinstance(strategy, dict):
        omitted = []
        strategy = dict(strategy)
        for key in [
            "prompt",
            "tbox",
            "aiInferencePacket",
            "reasoningCards",
            "entities",
            "relations",
            "tboxEntities",
            "tboxRelations",
            "aboxEntities",
            "aboxRelations",
            "evidence",
            "beliefs",
            "opinions",
            "activeInvestmentOpinions",
            "executionPlans",
            "insights",
            "dataQuality",
        ]:
            value = strategy.pop(key, None)
            if value not in (None, [], {}, ""):
                omitted.append(key)
                if isinstance(value, list):
                    strategy[key + "Count"] = len(value)
        strategy["detailLevel"] = "summary"
        strategy["detailAvailable"] = True
        strategy["heavyFieldsOmitted"] = omitted
        decision["ontologyStrategy"] = strategy

    analysis = decision.get("investmentAnalysis")
    if isinstance(analysis, dict):
        analysis = dict(analysis)
        reasoning_cards = analysis.pop("reasoningCards", None)
        if isinstance(reasoning_cards, list):
            analysis["reasoningCardCount"] = len(reasoning_cards)
        analysis["actionQueue"] = compact_rows(analysis.get("actionQueue"), decision_item_keys)
        analysis["detailLevel"] = "summary"
        analysis["detailAvailable"] = True
        decision["investmentAnalysis"] = analysis

    # The same decision graph is also projected at the root for the compact
    # dashboard.  Strip only duplicated explanatory packets here; the board,
    # queue and lineage remain available to the initial screen.
    root_analysis = compact.get("investmentAnalysis")
    if isinstance(root_analysis, dict):
        root_analysis = dict(root_analysis)
        omitted = []
        for key in ["reasoningCards", "aiInferencePacket", "entities", "relations", "evidence", "beliefs", "opinions"]:
            value = root_analysis.pop(key, None)
            if value not in (None, [], {}, ""):
                omitted.append(key)
                if isinstance(value, list):
                    root_analysis[key + "Count"] = len(value)
        root_analysis["actionQueue"] = compact_rows(root_analysis.get("actionQueue"), decision_item_keys)
        root_analysis["detailLevel"] = "summary"
        root_analysis["detailAvailable"] = True
        root_analysis["heavyFieldsOmitted"] = omitted
        compact["investmentAnalysis"] = root_analysis

    compact["payloadDetail"] = "summary"
    compact["fullDetailPath"] = "/api/flow-lens?detail=full"
    return compact


def persisted_flow_lens_snapshot(watchlist_symbols: str = "") -> Dict[str, object]:
    """Read the latest verified monitor projection without calling vendors."""
    try:
        settings = operational_read_settings()
        states = stores.monitor_store(settings).previous
        candidates = [item for item in states.values() if isinstance(item, dict) and item]
        if not candidates:
            return {}
        latest = sorted(candidates, key=lambda item: str(item.get("generatedAt") or ""), reverse=True)[0]
        return build_flow_lens_service(settings).snapshot_from_monitor_state(
            latest,
            watchlist_symbols=watchlist_symbols,
        )
    except Exception:
        return {}


def persisted_flow_lens_snapshot_is_fresh(snapshot: Dict[str, object]) -> bool:
    """Prevent an old monitor projection from satisfying a live refresh."""
    if not isinstance(snapshot, dict):
        return False
    freshness = flow_lens_data_freshness(snapshot.get("generatedAt"), operational_read_settings())
    return freshness.get("status") == "fresh"


def flow_lens_read_model() -> FlowLensReadModel:
    global FLOW_LENS_READ_MODEL
    with FLOW_LENS_READ_MODEL_LOCK:
        if FLOW_LENS_READ_MODEL is None:
            def refresh_snapshot(mock: bool, watchlist_symbols: str) -> Dict[str, object]:
                return flow_lens_snapshot(mock=mock, watchlist_symbols=watchlist_symbols)

            def notify_ready(snapshot: Dict[str, object]) -> None:
                REALTIME_HUB.broadcast("dashboard.snapshot_ready", {
                    "generatedAt": snapshot.get("generatedAt"),
                    "dataMode": snapshot.get("dataMode"),
                })

            FLOW_LENS_READ_MODEL = FlowLensReadModel(
                snapshot_provider=refresh_snapshot,
                persisted_provider=persisted_flow_lens_snapshot,
                on_refresh=notify_ready,
                persisted_validator=persisted_flow_lens_snapshot_is_fresh,
            )
        return FLOW_LENS_READ_MODEL


def flow_lens_read_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    mock_value = configured(first_query(query, "mock") or first_query(query, "mode")).lower()
    detail = configured(first_query(query, "detail") or first_query(query, "view")).lower()
    refresh = request_bool(first_query(query, "refresh"), False)
    result = flow_lens_read_model().read(
        mock=mock_value in {"1", "true", "mock"},
        watchlist_symbols=first_query(query, "watchlistSymbols"),
        refresh=refresh,
    )
    if not result.snapshot:
        pending_payload = {
            "generatedAt": now(),
            "dataMode": "pending",
            "readModel": result.metadata(),
            "portfolio": {},
            "tossDecision": {},
        }
        if detail in {"status", "freshness"}:
            pending_payload.pop("portfolio", None)
            pending_payload.pop("tossDecision", None)
            pending_payload["dataFreshness"] = flow_lens_data_freshness(None, {})
        return pending_payload
    payload = dict(result.snapshot)
    payload["readModel"] = result.metadata()
    payload["dataFreshness"] = flow_lens_data_freshness(payload.get("generatedAt"), runtime_settings())
    if detail in {"status", "freshness"}:
        return {
            "generatedAt": payload.get("generatedAt"),
            "dataMode": payload.get("dataMode"),
            "readModel": payload.get("readModel"),
            "dataFreshness": payload.get("dataFreshness"),
        }
    try:
        payload["capitalFlow"] = capital_flow_api_payload(query, snapshot=payload)
    except Exception as error:  # noqa: BLE001 - portfolio snapshot remains available when the analytical store is down.
        payload["capitalFlow"] = {
            "contract": "capital-flow-summary-v1",
            "status": "unavailable",
            "error": str(error)[:240],
            "markets": [],
            "sectors": [],
            "subjects": [],
        }
    if detail not in {"full", "detail", "all"}:
        payload = compact_flow_lens_payload(payload)
        payload["readModel"] = result.metadata()
    return payload
