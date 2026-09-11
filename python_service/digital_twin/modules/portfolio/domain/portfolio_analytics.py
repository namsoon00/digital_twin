"""Deterministic portfolio risk analytics derived from stored market history."""

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import math
from statistics import mean, pstdev
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


PORTFOLIO_ANALYTICS_VERSION = "portfolio-analytics-v1"
TRADING_DAYS_PER_YEAR = 252
RISK_EVENT_VOLATILITY_DELTA_PCT = 1.0
RISK_EVENT_DRAWDOWN_DELTA_PCT = 0.5
RISK_EVENT_CORRELATION_DELTA = 0.05
RISK_EVENT_POSITION_WEIGHT_DELTA_PCT = 0.5
RISK_EVENT_BETA_DELTA = 0.1


def numeric(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def price_points(rows: Iterable[Mapping[str, object]]) -> List[Tuple[str, float]]:
    points: Dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        stamp = str(row.get("marketSessionDate") or row.get("bucketAt") or row.get("generatedAt") or "")[:10]
        price = numeric(row.get("currentPrice") or row.get("close") or row.get("price"))
        if stamp and price > 0:
            points[stamp] = price
    return sorted(points.items())


def return_points(rows: Iterable[Mapping[str, object]]) -> Dict[str, float]:
    points = price_points(rows)
    result: Dict[str, float] = {}
    for index in range(1, len(points)):
        previous = points[index - 1][1]
        current = points[index][1]
        if previous > 0:
            result[points[index][0]] = current / previous - 1.0
    return result


def maximum_drawdown_pct(rows: Iterable[Mapping[str, object]]) -> float:
    peak = 0.0
    drawdown = 0.0
    for _stamp, price in price_points(rows):
        peak = max(peak, price)
        if peak > 0:
            drawdown = min(drawdown, (price / peak - 1.0) * 100.0)
    return round(drawdown, 6)


def period_return_pct(rows: Iterable[Mapping[str, object]]) -> float:
    points = price_points(rows)
    if len(points) < 2 or points[0][1] <= 0:
        return 0.0
    return round((points[-1][1] / points[0][1] - 1.0) * 100.0, 6)


def annualized_volatility_pct(returns: Iterable[float]) -> float:
    values = [numeric(item) for item in returns]
    if len(values) < 2:
        return 0.0
    return round(pstdev(values) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 6)


def aligned_values(left: Mapping[str, float], right: Mapping[str, float]) -> Tuple[List[float], List[float]]:
    keys = sorted(set(left) & set(right))
    return [left[key] for key in keys], [right[key] for key in keys]


def correlation(left: Mapping[str, float], right: Mapping[str, float]) -> Optional[float]:
    left_values, right_values = aligned_values(left, right)
    if len(left_values) < 10:
        return None
    left_mean = mean(left_values)
    right_mean = mean(right_values)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_values, right_values))
    left_variance = sum((item - left_mean) ** 2 for item in left_values)
    right_variance = sum((item - right_mean) ** 2 for item in right_values)
    denominator = math.sqrt(left_variance * right_variance)
    return round(numerator / denominator, 6) if denominator else None


def beta(left: Mapping[str, float], benchmark: Mapping[str, float]) -> Optional[float]:
    left_values, benchmark_values = aligned_values(left, benchmark)
    if len(left_values) < 10:
        return None
    left_mean = mean(left_values)
    benchmark_mean = mean(benchmark_values)
    covariance = sum(
        (item - left_mean) * (market - benchmark_mean)
        for item, market in zip(left_values, benchmark_values)
    ) / len(left_values)
    variance = sum((item - benchmark_mean) ** 2 for item in benchmark_values) / len(benchmark_values)
    return round(covariance / variance, 6) if variance else None


@dataclass(frozen=True)
class PositionRiskMetric:
    symbol: str
    weight_pct: float
    sample_count: int
    period_return_pct: float
    annualized_volatility_pct: float
    maximum_drawdown_pct: float
    benchmark_symbol: str = ""
    beta: Optional[float] = None
    benchmark_return_pct: Optional[float] = None
    active_return_pct: Optional[float] = None
    average_daily_trading_value: float = 0.0
    latest_observation_at: str = ""
    data_state: str = "partial"
    missing_data: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["version"] = PORTFOLIO_ANALYTICS_VERSION
        return payload


@dataclass(frozen=True)
class PairwiseCorrelation:
    left_symbol: str
    right_symbol: str
    correlation: float
    sample_count: int

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    risk_snapshot_id: str
    portfolio_id: str
    observed_at: str
    positions: List[PositionRiskMetric] = field(default_factory=list)
    correlations: List[PairwiseCorrelation] = field(default_factory=list)
    annualized_volatility_pct: float = 0.0
    maximum_drawdown_pct: float = 0.0
    maximum_pairwise_correlation: float = 0.0
    period_return_pct: float = 0.0
    benchmark_return_pct: Optional[float] = None
    active_return_pct: Optional[float] = None
    volatility_policy_delta_pct: float = 0.0
    drawdown_policy_delta_pct: float = 0.0
    correlation_policy_delta: float = 0.0
    sample_count: int = 0
    data_state: str = "partial"
    missing_data: List[str] = field(default_factory=list)
    provenance: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": PORTFOLIO_ANALYTICS_VERSION,
            "riskSnapshotId": self.risk_snapshot_id,
            "portfolioId": self.portfolio_id,
            "observedAt": self.observed_at,
            "annualizedVolatilityPct": self.annualized_volatility_pct,
            "maximumDrawdownPct": self.maximum_drawdown_pct,
            "maximumPairwiseCorrelation": self.maximum_pairwise_correlation,
            "periodReturnPct": self.period_return_pct,
            "benchmarkReturnPct": self.benchmark_return_pct,
            "activeReturnPct": self.active_return_pct,
            "volatilityPolicyDeltaPct": self.volatility_policy_delta_pct,
            "drawdownPolicyDeltaPct": self.drawdown_policy_delta_pct,
            "correlationPolicyDelta": self.correlation_policy_delta,
            "sampleCount": self.sample_count,
            "dataState": self.data_state,
            "missingData": list(self.missing_data),
            "provenance": dict(self.provenance),
            "positions": [item.to_dict() for item in self.positions],
            "correlations": [item.to_dict() for item in self.correlations],
        }


@dataclass(frozen=True)
class PortfolioRiskEventMateriality:
    material: bool
    reason_codes: List[str] = field(default_factory=list)
    metric_deltas: Dict[str, float] = field(default_factory=dict)
    thresholds: Dict[str, float] = field(default_factory=dict)
    baseline_risk_snapshot_id: str = ""
    current_risk_snapshot_id: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "material": self.material,
            "reasonCodes": list(self.reason_codes),
            "metricDeltas": dict(self.metric_deltas),
            "thresholds": dict(self.thresholds),
            "baselineRiskSnapshotId": self.baseline_risk_snapshot_id,
            "currentRiskSnapshotId": self.current_risk_snapshot_id,
        }


def _risk_value(payload: Mapping[str, object], camel: str, snake: str = "") -> float:
    return numeric(payload.get(camel) if payload.get(camel) is not None else payload.get(snake))


def _risk_position_rows(payload: Mapping[str, object]) -> Dict[str, Mapping[str, object]]:
    raw = payload.get("positionRiskSummary") or payload.get("positions") or []
    return {
        str(item.get("symbol") or "").upper().strip(): item
        for item in raw
        if isinstance(item, Mapping) and str(item.get("symbol") or "").strip()
    }


def portfolio_risk_event_materiality(
    baseline: Mapping[str, object],
    current: PortfolioRiskSnapshot,
) -> PortfolioRiskEventMateriality:
    """Decide whether a measured risk change warrants another graph run.

    Every measurement remains persistable. This gate only controls the domain
    event, comparing against the last emitted baseline so many small updates
    can accumulate into one meaningful change.
    """
    previous = dict(baseline or {})
    current_payload = current.to_dict()
    thresholds = {
        "annualizedVolatilityPct": RISK_EVENT_VOLATILITY_DELTA_PCT,
        "maximumDrawdownPct": RISK_EVENT_DRAWDOWN_DELTA_PCT,
        "maximumPairwiseCorrelation": RISK_EVENT_CORRELATION_DELTA,
        "positionWeightPct": RISK_EVENT_POSITION_WEIGHT_DELTA_PCT,
        "beta": RISK_EVENT_BETA_DELTA,
    }
    baseline_id = str(previous.get("riskSnapshotId") or "")
    if not previous or not baseline_id:
        return PortfolioRiskEventMateriality(
            material=True,
            reason_codes=["initial-risk-baseline"],
            thresholds=thresholds,
            baseline_risk_snapshot_id=baseline_id,
            current_risk_snapshot_id=current.risk_snapshot_id,
        )
    if baseline_id == current.risk_snapshot_id:
        return PortfolioRiskEventMateriality(
            material=False,
            thresholds=thresholds,
            baseline_risk_snapshot_id=baseline_id,
            current_risk_snapshot_id=current.risk_snapshot_id,
        )

    reasons: List[str] = []
    deltas = {
        "annualizedVolatilityPct": round(abs(
            current.annualized_volatility_pct
            - _risk_value(previous, "annualizedVolatilityPct", "annualized_volatility_pct")
        ), 6),
        "maximumDrawdownPct": round(abs(
            current.maximum_drawdown_pct
            - _risk_value(previous, "maximumDrawdownPct", "maximum_drawdown_pct")
        ), 6),
        "maximumPairwiseCorrelation": round(abs(
            current.maximum_pairwise_correlation
            - _risk_value(previous, "maximumPairwiseCorrelation", "maximum_pairwise_correlation")
        ), 6),
        "positionWeightPct": 0.0,
        "beta": 0.0,
    }
    for key in ["annualizedVolatilityPct", "maximumDrawdownPct", "maximumPairwiseCorrelation"]:
        if deltas[key] >= thresholds[key]:
            reasons.append(key + "-material-change")

    previous_positions = _risk_position_rows(previous)
    current_positions = _risk_position_rows(current_payload)
    if set(previous_positions) != set(current_positions):
        reasons.append("position-set-change")
    for symbol in sorted(set(previous_positions) & set(current_positions)):
        old = previous_positions[symbol]
        new = current_positions[symbol]
        weight_delta = abs(
            _risk_value(new, "weightPct", "weight_pct")
            - _risk_value(old, "weightPct", "weight_pct")
        )
        deltas["positionWeightPct"] = round(max(deltas["positionWeightPct"], weight_delta), 6)
        old_beta = old.get("beta")
        new_beta = new.get("beta")
        if (old_beta is None) != (new_beta is None):
            reasons.append("position-beta-availability-change")
        elif old_beta is not None and new_beta is not None:
            deltas["beta"] = round(max(
                deltas["beta"],
                abs(numeric(new_beta) - numeric(old_beta)),
            ), 6)
    if deltas["positionWeightPct"] >= thresholds["positionWeightPct"]:
        reasons.append("position-weight-material-change")
    if deltas["beta"] >= thresholds["beta"]:
        reasons.append("position-beta-material-change")

    detailed_breach_keys = {
        "volatilityPolicyDeltaPct", "volatility_policy_delta_pct",
        "drawdownPolicyDeltaPct", "drawdown_policy_delta_pct",
        "correlationPolicyDelta", "correlation_policy_delta",
    }
    previous_breaches = (
        _risk_value(previous, "volatilityPolicyDeltaPct", "volatility_policy_delta_pct") > 0,
        _risk_value(previous, "drawdownPolicyDeltaPct", "drawdown_policy_delta_pct") > 0,
        _risk_value(previous, "correlationPolicyDelta", "correlation_policy_delta") > 0,
    )
    current_breaches = (
        current.volatility_policy_delta_pct > 0,
        current.drawdown_policy_delta_pct > 0,
        current.correlation_policy_delta > 0,
    )
    breach_changed = (
        previous_breaches != current_breaches
        if detailed_breach_keys & set(previous)
        else bool(previous.get("policyBreach")) != any(current_breaches)
    )
    if breach_changed:
        reasons.append("risk-policy-breach-transition")
    previous_state = str(previous.get("dataState") or previous.get("data_state") or "")
    if previous_state and previous_state != current.data_state:
        reasons.append("risk-data-state-change")
    previous_missing = set(previous.get("missingData") or previous.get("missing_data") or [])
    if previous_missing and previous_missing != set(current.missing_data):
        reasons.append("risk-missing-data-change")
    previous_policy = str(dict(previous.get("provenance") or {}).get("policyVersion") or "")
    current_policy = str(dict(current.provenance or {}).get("policyVersion") or "")
    if previous_policy and previous_policy != current_policy:
        reasons.append("risk-policy-version-change")

    return PortfolioRiskEventMateriality(
        material=bool(reasons),
        reason_codes=list(dict.fromkeys(reasons)),
        metric_deltas=deltas,
        thresholds=thresholds,
        baseline_risk_snapshot_id=baseline_id,
        current_risk_snapshot_id=current.risk_snapshot_id,
    )


def with_policy_limits(
    snapshot: PortfolioRiskSnapshot,
    *,
    max_volatility_pct: float,
    max_drawdown_pct: float,
    max_correlation: float,
    policy_version: str,
) -> PortfolioRiskSnapshot:
    """Attach versioned policy deltas without mutating a measurement identity."""
    volatility_delta = round(max(0.0, snapshot.annualized_volatility_pct - numeric(max_volatility_pct)), 6)
    drawdown_delta = round(
        max(0.0, abs(min(0.0, snapshot.maximum_drawdown_pct)) - numeric(max_drawdown_pct)),
        6,
    )
    correlation_delta = round(max(0.0, snapshot.maximum_pairwise_correlation - numeric(max_correlation)), 6)
    material = {
        "measurementId": snapshot.risk_snapshot_id,
        "policyVersion": str(policy_version or ""),
        "volatilityPolicyDeltaPct": volatility_delta,
        "drawdownPolicyDeltaPct": drawdown_delta,
        "correlationPolicyDelta": correlation_delta,
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return replace(
        snapshot,
        risk_snapshot_id="portfolio-risk-snapshot:" + fingerprint,
        volatility_policy_delta_pct=volatility_delta,
        drawdown_policy_delta_pct=drawdown_delta,
        correlation_policy_delta=correlation_delta,
        provenance={
            **dict(snapshot.provenance or {}),
            "measurementSnapshotId": snapshot.risk_snapshot_id,
            "policyVersion": str(policy_version or ""),
        },
    )


def portfolio_risk_snapshot(
    portfolio_id: str,
    observed_at: str,
    series_by_symbol: Mapping[str, Iterable[Mapping[str, object]]],
    weights_by_symbol: Mapping[str, object],
    benchmark_by_symbol: Mapping[str, str] = None,
) -> PortfolioRiskSnapshot:
    benchmarks = {str(key).upper(): str(value).upper() for key, value in dict(benchmark_by_symbol or {}).items()}
    series = {str(key).upper(): list(value or []) for key, value in dict(series_by_symbol or {}).items()}
    returns = {symbol: return_points(rows) for symbol, rows in series.items()}
    position_symbols = sorted({str(key).upper() for key in weights_by_symbol if numeric(weights_by_symbol.get(key)) > 0})
    metrics: List[PositionRiskMetric] = []
    missing: List[str] = []
    for symbol in position_symbols:
        rows = series.get(symbol) or []
        symbol_returns = returns.get(symbol) or {}
        benchmark_symbol = benchmarks.get(symbol, "")
        benchmark_rows = (series.get(benchmark_symbol) or []) if benchmark_symbol else []
        benchmark_returns = returns.get(benchmark_symbol) or {}
        measured_beta = beta(symbol_returns, benchmark_returns) if benchmark_symbol else None
        benchmark_period_return = period_return_pct(benchmark_rows) if benchmark_rows else None
        active_return = (
            round(period_return_pct(rows) - benchmark_period_return, 6)
            if benchmark_period_return is not None and rows else None
        )
        item_missing = []
        if len(symbol_returns) < 20:
            item_missing.append("minimumReturnSamples")
        if benchmark_symbol and measured_beta is None:
            item_missing.append("benchmarkReturnSamples")
        trading_values = [numeric(item.get("tradingValue")) for item in rows if numeric(item.get("tradingValue")) > 0]
        metrics.append(PositionRiskMetric(
            symbol=symbol,
            weight_pct=round(numeric(weights_by_symbol.get(symbol)), 6),
            sample_count=len(symbol_returns),
            period_return_pct=period_return_pct(rows),
            annualized_volatility_pct=annualized_volatility_pct(symbol_returns.values()),
            maximum_drawdown_pct=maximum_drawdown_pct(rows),
            benchmark_symbol=benchmark_symbol,
            beta=measured_beta,
            benchmark_return_pct=benchmark_period_return,
            active_return_pct=active_return,
            average_daily_trading_value=round(mean(trading_values), 4) if trading_values else 0.0,
            latest_observation_at=str(rows[-1].get("bucketAt") or rows[-1].get("generatedAt") or "") if rows else "",
            data_state="complete" if not item_missing else "partial",
            missing_data=item_missing,
        ))
        missing.extend(symbol + ":" + item for item in item_missing)

    correlations: List[PairwiseCorrelation] = []
    for left_index, left_symbol in enumerate(position_symbols):
        for right_symbol in position_symbols[left_index + 1:]:
            value = correlation(returns.get(left_symbol) or {}, returns.get(right_symbol) or {})
            if value is None:
                continue
            sample_count = len(set(returns.get(left_symbol) or {}) & set(returns.get(right_symbol) or {}))
            correlations.append(PairwiseCorrelation(left_symbol, right_symbol, value, sample_count))

    return_date_sets = [set(returns.get(symbol) or {}) for symbol in position_symbols]
    dates = sorted(set.intersection(*return_date_sets)) if return_date_sets and all(return_date_sets) else []
    portfolio_returns = []
    portfolio_path = 1.0
    portfolio_prices = [{"bucketAt": "0000-00-00", "currentPrice": portfolio_path}]
    for date in dates:
        # Position weights are portfolio weights. The unallocated remainder is
        # cash with a zero return, so invested weights must not be renormalized.
        daily_return = sum(
            numeric(weights_by_symbol.get(symbol)) / 100.0 * (returns.get(symbol) or {}).get(date, 0.0)
            for symbol in position_symbols
        )
        portfolio_returns.append(daily_return)
        portfolio_path *= 1.0 + daily_return
        portfolio_prices.append({"bucketAt": date, "currentPrice": portfolio_path})
    portfolio_period_return = round((portfolio_path - 1.0) * 100.0, 6) if portfolio_returns else 0.0
    benchmark_values = [item.benchmark_return_pct for item in metrics if item.benchmark_return_pct is not None]
    weighted_benchmark_return = None
    if benchmark_values:
        benchmark_weight_total = sum(item.weight_pct for item in metrics if item.benchmark_return_pct is not None)
        if benchmark_weight_total > 0:
            weighted_benchmark_return = round(sum(
                item.weight_pct * float(item.benchmark_return_pct)
                for item in metrics if item.benchmark_return_pct is not None
            ) / 100.0, 6)
    active_return = (
        round(portfolio_period_return - weighted_benchmark_return, 6)
        if weighted_benchmark_return is not None else None
    )
    if not portfolio_returns:
        missing.append("portfolioReturnSamples")
    if len(position_symbols) > 1 and not correlations:
        missing.append("positionCorrelationMatrix")
    if metrics and weighted_benchmark_return is None:
        missing.append("portfolioBenchmarkReturn")
    max_correlation = max([item.correlation for item in correlations] or [0.0])
    material = {
        "portfolioId": portfolio_id,
        "positions": [item.to_dict() for item in metrics],
        "correlations": [item.to_dict() for item in correlations],
        "portfolioVolatility": annualized_volatility_pct(portfolio_returns),
        "portfolioDrawdown": maximum_drawdown_pct(portfolio_prices),
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    missing = list(dict.fromkeys(missing))
    return PortfolioRiskSnapshot(
        risk_snapshot_id="portfolio-risk-snapshot:" + fingerprint,
        portfolio_id=str(portfolio_id or ""),
        observed_at=str(observed_at or ""),
        positions=metrics,
        correlations=correlations,
        annualized_volatility_pct=annualized_volatility_pct(portfolio_returns),
        maximum_drawdown_pct=maximum_drawdown_pct(portfolio_prices),
        maximum_pairwise_correlation=round(max_correlation, 6),
        period_return_pct=portfolio_period_return,
        benchmark_return_pct=weighted_benchmark_return,
        active_return_pct=active_return,
        sample_count=len(portfolio_returns),
        data_state="complete" if not missing else "partial",
        missing_data=missing,
        provenance={
            "source": "mysql-market-time-series",
            "granularity": "1d",
            "annualizationTradingDays": TRADING_DAYS_PER_YEAR,
            "lookAheadPrevented": True,
        },
    )
