"""Frozen-contract interpretation, row clocks and market observation scheduling."""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Callable, Dict, List, Mapping
from digital_twin.modules.decisions.domain.investment_brain import (
    DecisionEpisode,
    parse_investment_timestamp,
)
from digital_twin.modules.outcomes.domain.hypothesis_outcome_contract import (
    outcome_contract_completeness,
    resolved_outcome_contract,
)
from digital_twin.modules.market_data.domain.market_time_series import market_timezone
from digital_twin.modules.market_data.domain.market_hours import infer_market_from_context


def outcome_horizons(
    *,
    _runtime_settings: Mapping[str, object],
) -> List[int]:
    return outcome_horizon_minutes(
        _runtime_settings.get("investmentBrainOutcomeObservationMinutes") or "60,1440,7200,28800",
    )


def outcome_minimum_samples(
    *,
    _runtime_settings: Mapping[str, object],
) -> int:
    try:
        value = int(
            float(
                str(
                    _runtime_settings.get("hypothesisOutcomeReviewMinimumSamples")
                    or _runtime_settings.get("investmentBrainOutcomeReviewMinimumSamples")
                    or "3"
                )
            )
        )
    except (TypeError, ValueError):
        value = 3
    return max(1, min(1000, value))


def episode_outcome_contract(
    episode: DecisionEpisode,
    *,
    _outcome_horizons: Callable[..., List[int]],
    _outcome_max_delay_minutes: Callable[..., int],
    _outcome_minimum_samples: Callable[..., int],
) -> Dict[str, object]:
    facts = episode.facts_at_decision if isinstance(episode.facts_at_decision, dict) else {}
    raw = (
        facts.get("hypothesisOutcomeContract")
        if isinstance(facts.get("hypothesisOutcomeContract"), dict)
        else {}
    )
    resolved = resolved_outcome_contract(
        raw,
        fallback_horizons=_outcome_horizons(),
        fallback_minimum_samples=_outcome_minimum_samples(),
        fallback_maximum_delay_minutes=_outcome_max_delay_minutes(),
    )
    for key in [
        "contractVersion",
        "contractFingerprint",
        "criteriaOrigin",
        "effectiveAt",
        "selectedHypothesisId",
        "sourceRuleIds",
        "marketHypothesisId",
        "accountHypothesisOverlayId",
        "inferenceGenerationId",
        "marketIndependenceKey",
        "accountIndependenceKey",
        "sourceFactIndependenceKey",
        "predictionTarget",
        "expectedDirection",
        "expectedOutcome",
        "outcomeMetric",
        "falsificationContract",
    ]:
        if raw.get(key) not in (None, "", [], {}):
            resolved[key] = raw.get(key)
    return resolved


def episode_outcome_contract_completeness(episode: DecisionEpisode) -> Dict[str, object]:
    facts = episode.facts_at_decision if isinstance(episode.facts_at_decision, dict) else {}
    raw = (
        facts.get("hypothesisOutcomeContract")
        if isinstance(facts.get("hypothesisOutcomeContract"), dict)
        else {}
    )
    return outcome_contract_completeness(raw)


def episode_outcome_horizons(
    episode: DecisionEpisode,
    *,
    _episode_outcome_contract: Callable[..., Dict[str, object]],
) -> List[int]:
    return outcome_horizon_minutes(_episode_outcome_contract(episode).get("outcomeHorizonMinutes"))


def episode_outcome_max_delay_minutes(
    episode: DecisionEpisode,
    *,
    _episode_outcome_contract: Callable[..., Dict[str, object]],
    _outcome_max_delay_minutes: Callable[..., int],
) -> int:
    return int(
        _episode_outcome_contract(episode).get("maximumObservationDelayMinutes")
        or _outcome_max_delay_minutes()
    )


def outcome_batch_size(
    *,
    _runtime_settings: Mapping[str, object],
) -> int:
    try:
        value = int(
            float(str(_runtime_settings.get("investmentBrainOutcomeEpisodeBatchSize") or "200"))
        )
    except (TypeError, ValueError):
        value = 200
    return max(10, min(1000, value))


def outcome_max_delay_minutes(
    *,
    _runtime_settings: Mapping[str, object],
) -> int:
    try:
        value = int(
            float(str(_runtime_settings.get("investmentBrainOutcomeMaxDelayMinutes") or "180"))
        )
    except (TypeError, ValueError):
        value = 180
    return max(1, min(60 * 24 * 14, value))


def selected_hypothesis_stance(episode: DecisionEpisode) -> str:
    return str(selected_hypothesis_payload(episode).get("stance") or "uncertain")


def selected_hypothesis_payload(episode: DecisionEpisode) -> Dict[str, object]:
    for item in episode.hypothesis_set.hypotheses:
        if item.hypothesis_id == episode.selected_hypothesis_id:
            return item.to_dict()
    return {}


def directional_hypothesis_status(stance: str, price_change_pct: float) -> str:
    if not price_change_pct or stance not in {"risk", "support"}:
        return "inconclusive"
    if stance == "risk":
        return (
            "directionally-corroborated" if price_change_pct < 0 else "directionally-contradicted"
        )
    return "directionally-corroborated" if price_change_pct > 0 else "directionally-contradicted"


def contract_benchmark_symbol(contract: Dict[str, object], facts: Dict[str, object] = None) -> str:
    source = dict(facts or {})
    explicit = str(source.get("benchmarkSymbol") or "").upper().strip()
    if explicit:
        return explicit
    for criterion in contract.get("criteria") or []:
        if not isinstance(criterion, dict):
            continue
        symbol = str(criterion.get("benchmarkSymbol") or "").upper().strip()
        if symbol:
            return symbol
    return ""


def due_outcome_horizon_minutes(
    episode: DecisionEpisode, observed_at: str, raw_horizons: object
) -> int:
    horizons = due_outcome_horizon_minutes_all(episode, observed_at, raw_horizons)
    return horizons[0] if horizons else 0


def due_outcome_horizon_minutes_all(
    episode: DecisionEpisode,
    observed_at: str,
    raw_horizons: object,
) -> List[int]:
    decided = parse_datetime(episode.decided_at)
    observed = parse_datetime(observed_at)
    if not decided or not observed or observed <= decided:
        return []
    due = []
    for value in outcome_horizon_minutes(raw_horizons):
        target = parse_datetime(outcome_target_at(episode, value))
        if target and observed >= target and not outcome_horizon_recorded(episode, value):
            due.append(value)
    return due


def outcome_horizon_minutes(raw_horizons: object) -> List[int]:
    if isinstance(raw_horizons, (list, tuple, set)):
        raw_values = raw_horizons
    else:
        raw_values = str(raw_horizons or "").replace("\n", ",").split(",")
    horizons: List[int] = []
    for raw in raw_values:
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in horizons:
            horizons.append(value)
    return sorted(horizons) or [60, 1440, 10080]


def outcome_horizon_recorded(episode: DecisionEpisode, horizon_minutes: int) -> bool:
    return int(horizon_minutes or 0) in {
        int(float((item.payload or {}).get("horizonMinutes") or 0))
        for item in episode.outcomes or []
        if (item.payload or {}).get("horizonMinutes")
    }


def outcome_target_at(episode: DecisionEpisode, horizon_minutes: int) -> str:
    facts = episode.facts_at_decision if isinstance(episode.facts_at_decision, dict) else {}
    return market_outcome_target_at(
        episode.decided_at,
        episode.symbol,
        str(facts.get("market") or ""),
        str(facts.get("currency") or ""),
        horizon_minutes,
    )


def market_outcome_target_at(
    decided_at: str,
    symbol: str,
    market: str,
    currency: str,
    horizon_minutes: int,
) -> str:
    decided = parse_datetime(decided_at)
    if not decided or int(horizon_minutes or 0) <= 0:
        return ""
    target = decided + timedelta(minutes=int(horizon_minutes))
    market = str(market or "").upper().strip()
    currency = str(currency or "").upper().strip()
    if not market:
        market = infer_market_from_context(
            "investmentInsight",
            {
                "symbol": symbol,
                "market": market,
                "currency": currency,
            },
        )
    is_crypto_market = market in {"CRYPTO", "COIN"} or currency in {"BTC", "ETH", "USDT", "USDC"}
    traditional_market = not is_crypto_market and (
        market
        in {
            "KR",
            "KOR",
            "KOREA",
            "KOSPI",
            "KOSDAQ",
            "KONEX",
            "KRX",
            "XKRX",
            "US",
            "USA",
            "NASDAQ",
            "NYSE",
            "AMEX",
            "ARCA",
            "BATS",
            "XNYS",
            "XNAS",
        }
        or currency in {"KRW", "USD"}
    )
    if traditional_market:
        local_target = target.astimezone(market_timezone(market, currency))
        while local_target.weekday() >= 5:
            local_target += timedelta(days=1)
        is_kr_market = (
            market
            in {
                "KR",
                "KOR",
                "KOREA",
                "KOSPI",
                "KOSDAQ",
                "KONEX",
                "KRX",
                "XKRX",
            }
            or currency == "KRW"
        )
        open_hour, open_minute = (9, 0) if is_kr_market else (9, 30)
        close_hour, close_minute = (15, 30) if is_kr_market else (16, 0)
        session_open = local_target.replace(
            hour=open_hour, minute=open_minute, second=0, microsecond=0
        )
        session_close = local_target.replace(
            hour=close_hour, minute=close_minute, second=0, microsecond=0
        )
        if local_target < session_open:
            local_target = session_open
        elif local_target > session_close:
            local_target = session_open + timedelta(days=1)
            while local_target.weekday() >= 5:
                local_target += timedelta(days=1)
        target = local_target.astimezone(timezone.utc)
    return target.isoformat().replace("+00:00", "Z")


def outcome_observation_is_usable(facts: Dict[str, object], observed_at: str) -> bool:
    if not number((facts or {}).get("currentPrice")) or not parse_datetime(observed_at):
        return False
    quality = str((facts or {}).get("dataQuality") or "").strip().lower()
    return quality not in {
        "stale",
        "cached",
        "invalid",
        "unavailable",
        "error",
        "mock",
        "estimated",
    }


def outcome_is_calibration_eligible(outcome_payload: Dict[str, object]) -> bool:
    payload = outcome_payload.get("payload") if isinstance(outcome_payload, dict) else {}
    eligibility = str((payload or {}).get("calibrationEligibility") or "").strip().lower()
    return eligibility == "eligible"


def parse_datetime(value: object):
    return parse_investment_timestamp(value)


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
