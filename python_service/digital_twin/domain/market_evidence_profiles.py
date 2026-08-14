"""Market-specific evidence availability for investment inference.

The profile answers a data-contract question only: which observations can the
configured providers supply for this instrument, and which of them are usable
in the current snapshot. It never labels a value bullish or bearish; TypeDB
RuleBox rules own that investment meaning.
"""

from __future__ import annotations

from typing import Dict, Iterable

from .market_data import number
from .portfolio import Position, expects_kr_microstructure_signals
from .security_lines import security_lines_for_symbol


MARKET_EVIDENCE_PROFILE_VERSION = "market-evidence-profile-v1"

FRESH = "fresh"
STALE = "stale"
SESSION_UNAVAILABLE = "sessionUnavailable"
TEMPORARY_FAILURE = "temporaryFailure"
PROVIDER_UNSUPPORTED = "providerUnsupported"
NOT_APPLICABLE = "notApplicable"
MISSING = "missing"

US_MARKETS = {"US", "USA", "NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "XNYS", "XNAS", "CBOE"}
CRYPTO_MARKETS = {"CRYPTO", "COIN"}
CRYPTO_CURRENCIES = {"BTC", "ETH", "USDT", "USDC"}

PROFILE_DEFINITIONS = {
    "KR_EQUITY": {
        "label": "국내 주식 증거 프로필",
        "tboxClass": "KREquityEvidenceProfile",
        "required": ["pricePath", "volume"],
        "confirmationAny": ["tradeFlow", "orderBook", "investorFlow"],
    },
    "US_EQUITY": {
        "label": "미국 주식 증거 프로필",
        "tboxClass": "USEquityEvidenceProfile",
        "required": ["pricePath", "volume"],
        "confirmationAny": ["volume"],
    },
    "ADR": {
        "label": "ADR 증거 프로필",
        "tboxClass": "ADREvidenceProfile",
        "required": ["pricePath", "volume", "crossListingIdentity"],
        "confirmationAny": ["volume", "crossListingQuote"],
    },
    "CRYPTO": {
        "label": "크립토 증거 프로필",
        "tboxClass": "CryptoEvidenceProfile",
        "required": ["pricePath", "volume"],
        "confirmationAny": ["volume"],
    },
}

CAPABILITY_LABELS = {
    "pricePath": "가격 경로",
    "volume": "거래량",
    "tradeFlow": "체결 방향",
    "orderBook": "호가 잔량",
    "investorFlow": "투자자별 수급",
    "crossListingIdentity": "본주·ADR 연결 정보",
    "crossListingQuote": "본주·ADR 비교 시세",
}


def _symbol(position: Position) -> str:
    return str(position.symbol or position.name or "").upper().strip()


def market_evidence_profile_key(position: Position, settings: Dict[str, object] = None) -> str:
    symbol = _symbol(position)
    lines = security_lines_for_symbol(symbol, settings)
    if any(line.symbol == symbol and line.is_adr for line in lines):
        return "ADR"
    market = str(position.market or "").upper().strip()
    currency = str(position.currency or "").upper().strip()
    if market in CRYPTO_MARKETS or currency in CRYPTO_CURRENCIES:
        return "CRYPTO"
    if expects_kr_microstructure_signals(market, currency, symbol):
        return "KR_EQUITY"
    return "US_EQUITY"


def _coverage_item(position: Position, stage: str) -> Dict[str, object]:
    coverage = position.market_signal_coverage if isinstance(position.market_signal_coverage, dict) else {}
    item = coverage.get(stage) if isinstance(coverage.get(stage), dict) else {}
    return dict(item or {})


def _observed_fields(item: Dict[str, object]) -> set:
    return {
        str(field or "").strip()
        for field in (item.get("observedFields") or item.get("fields") or [])
        if str(field or "").strip()
    }


def _failure_state(item: Dict[str, object], position: Position) -> str:
    status = " ".join([
        str(item.get("status") or ""),
        str(item.get("freshnessStatus") or ""),
        str(item.get("sourceTimestampState") or ""),
        str(item.get("reason") or ""),
    ]).lower()
    if any(token in status for token in ("stale", "expired", "cached", "지연", "오래")):
        return STALE
    if any(token in status for token in ("error", "failed", "failure", "timeout", "오류", "실패")):
        return TEMPORARY_FAILURE
    if any(token in status for token in ("unsupported", "not-supported", "미지원")):
        return PROVIDER_UNSUPPORTED
    session = " ".join([
        str(position.market_session or ""),
        str(position.market_session_label or ""),
        str(item.get("sessionStatus") or ""),
        str(item.get("reason") or ""),
    ]).lower()
    if any(token in session for token in ("closed", "premarket", "장 시작 전", "정규장 시작 전", "휴장")):
        return SESSION_UNAVAILABLE
    return MISSING


def _stage_state(
    position: Position,
    stage: str,
    fields: Iterable[str],
    fallback_observed: bool = False,
) -> Dict[str, object]:
    item = _coverage_item(position, stage)
    observed = _observed_fields(item)
    expected = {str(field) for field in fields}
    position_freshness = " ".join([
        str(position.freshness_status or ""),
        str(position.source_timestamp_state or ""),
        str(position.latency_status or ""),
    ]).lower()
    position_is_stale = any(token in position_freshness for token in ("stale", "expired", "delayed"))
    usable = (
        item.get("judgementEvidenceUsable") is not False
        and item.get("aiUsableAsStrongEvidence") is not False
        and not position_is_stale
    )
    if (observed & expected or fallback_observed) and usable:
        state = FRESH
    elif (observed & expected or fallback_observed) and not usable:
        state = STALE
    else:
        state = _failure_state(item, position)
    return {
        "state": state,
        "observedFields": sorted(observed & expected),
        "sourceAsOf": str(item.get("sourceAsOf") or position.source_as_of or position.updated_at or ""),
        "fetchedAt": str(item.get("fetchedAt") or position.source_fetched_at or ""),
        "provider": str(item.get("provider") or position.quote_source or ""),
        "reason": str(item.get("reason") or item.get("staleReason") or item.get("latencyReason") or ""),
    }


def _static_capability(state: str, reason: str = "") -> Dict[str, object]:
    return {
        "state": state,
        "observedFields": [],
        "sourceAsOf": "",
        "fetchedAt": "",
        "provider": "",
        "reason": reason,
    }


def market_evidence_capabilities(position: Position, settings: Dict[str, object] = None) -> Dict[str, Dict[str, object]]:
    profile_key = market_evidence_profile_key(position, settings)
    quote_freshness = str(position.freshness_status or position.source_timestamp_state or "").lower()
    price_observed = bool(number(position.current_price) and any(number(value) for value in (position.ma5, position.ma20, position.ma60)))
    price_state = STALE if "stale" in quote_freshness else FRESH if price_observed else MISSING
    capabilities = {
        "pricePath": {
            **_static_capability(price_state),
            "observedFields": [
                field
                for field, value in [
                    ("currentPrice", position.current_price),
                    ("ma5", position.ma5),
                    ("ma20", position.ma20),
                    ("ma60", position.ma60),
                ]
                if number(value)
            ],
            "sourceAsOf": str(position.source_as_of or position.updated_at or ""),
            "fetchedAt": str(position.source_fetched_at or ""),
            "provider": str(position.quote_source or ""),
        },
        "volume": _stage_state(
            position,
            "ccnl",
            ["volume", "volumeRatio", "tradingValue"],
            fallback_observed=bool(number(position.volume)),
        ),
        "tradeFlow": _stage_state(
            position,
            "ccnl",
            ["tradeStrength", "buyVolume", "sellVolume"],
            fallback_observed=bool(number(position.trade_strength) or number(position.buy_volume) or number(position.sell_volume)),
        ),
        "orderBook": _stage_state(
            position,
            "orderbook",
            ["orderbookBidVolume", "orderbookAskVolume", "bidAskImbalance"],
            fallback_observed=bool(number(position.orderbook_bid_volume) or number(position.orderbook_ask_volume)),
        ),
        "investorFlow": _stage_state(
            position,
            "investor",
            [
                "foreignNetVolume", "foreignBuyVolume", "foreignSellVolume",
                "institutionNetVolume", "institutionBuyVolume", "institutionSellVolume",
                "individualNetVolume", "individualBuyVolume", "individualSellVolume",
            ],
        ),
    }
    lines = security_lines_for_symbol(position.symbol, settings)
    has_cross_listing = any(line.is_adr for line in lines) and any(line.is_local for line in lines)
    capabilities["crossListingIdentity"] = _static_capability(
        FRESH if has_cross_listing else NOT_APPLICABLE,
        "보안 라인 카탈로그의 본주·ADR 연결 정보" if has_cross_listing else "교차 상장 종목이 아닙니다.",
    )
    capabilities["crossListingQuote"] = _static_capability(
        MISSING if profile_key == "ADR" else NOT_APPLICABLE,
        "본주와 ADR의 동일 기준시각 시세가 함께 필요합니다." if profile_key == "ADR" else "교차 상장 비교 대상이 아닙니다.",
    )
    if profile_key in {"US_EQUITY", "ADR"}:
        for key in ("tradeFlow", "orderBook", "investorFlow"):
            if capabilities[key]["state"] not in {FRESH, STALE}:
                capabilities[key] = _static_capability(
                    PROVIDER_UNSUPPORTED,
                    "현재 미국 주식 공급 경로는 가격·거래량을 제공하지만 이 항목은 제공하지 않습니다.",
                )
    elif profile_key == "CRYPTO":
        for key in ("tradeFlow", "orderBook", "investorFlow", "crossListingIdentity", "crossListingQuote"):
            capabilities[key] = _static_capability(
                NOT_APPLICABLE,
                "현재 크립토 판단 프로필의 필수 증거가 아닙니다.",
            )
    return capabilities


def market_evidence_profile(position: Position, settings: Dict[str, object] = None) -> Dict[str, object]:
    key = market_evidence_profile_key(position, settings)
    definition = dict(PROFILE_DEFINITIONS[key])
    capabilities = market_evidence_capabilities(position, settings)
    required = list(definition["required"])
    confirmation_any = list(definition["confirmationAny"])
    required_ready = all(capabilities[item]["state"] == FRESH for item in required)
    confirmation_ready = any(capabilities[item]["state"] == FRESH for item in confirmation_any)
    eligible = required_ready and confirmation_ready
    unavailable = [
        {
            "capability": capability,
            "label": CAPABILITY_LABELS.get(capability, capability),
            **dict(payload),
        }
        for capability, payload in capabilities.items()
        if payload.get("state") != FRESH
    ]
    profile = {
        "version": MARKET_EVIDENCE_PROFILE_VERSION,
        "profileKey": key,
        "label": definition["label"],
        "tboxClass": definition["tboxClass"],
        "market": str(position.market or "").upper().strip(),
        "currency": str(position.currency or "").upper().strip(),
        "dataState": "sufficient" if eligible else "partial",
        "judgementEvidenceUsable": eligible,
        "requiredCapabilities": required,
        "confirmationCapabilities": confirmation_any,
        "capabilities": capabilities,
        "unavailableCapabilities": unavailable,
    }
    profile["observableFollowUpFields"] = sorted(observable_follow_up_fields(profile))
    return profile


def observable_follow_up_fields(profile: Dict[str, object]) -> set:
    """Return machine-observable fields for follow-up condition validation."""

    capabilities = profile.get("capabilities") if isinstance(profile.get("capabilities"), dict) else {}
    fields = set()
    if dict(capabilities.get("pricePath") or {}).get("state") == FRESH:
        fields.update({"currentPrice", "ma5Distance", "ma20Distance", "ma60Distance", "priceChangeRate"})
    mapping = {
        "volume": {"volume", "volumeRatio", "timeAdjustedVolumeRatio"},
        "tradeFlow": {"tradeStrength", "buyVolume", "sellVolume"},
        "orderBook": {"bidAskImbalance", "orderbookBidVolume", "orderbookAskVolume"},
        "investorFlow": {"foreignNetVolume", "institutionNetVolume", "individualNetVolume", "smartMoneyNetVolume"},
        "crossListingQuote": {"adrPremiumPct", "adrPriceUsd", "localEquivalentKrw"},
    }
    for capability, capability_fields in mapping.items():
        if dict(capabilities.get(capability) or {}).get("state") == FRESH:
            fields.update(capability_fields)
    return fields
