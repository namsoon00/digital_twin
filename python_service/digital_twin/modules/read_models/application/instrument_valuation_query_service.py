"""Build a read-only valuation view from the latest monitor snapshot."""

from __future__ import annotations

import math
from typing import Dict, Iterable, Mapping, Optional

from digital_twin.modules.news_intelligence.contracts import company_prompt_context, company_valuation_context, latest_source_as_of
from digital_twin.modules.portfolio.contracts import InstrumentValuationQuery
from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state, utc_now_iso
from digital_twin.modules.portfolio.contracts import ValuationModelRequest, ValuationModelService


READ_MODEL_VERSION = "instrument-valuation-read-model-v1"


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _number(value: object, *, positive: bool = False, allow_zero: bool = True) -> Optional[float]:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if positive and parsed <= 0:
        return None
    if not allow_zero and parsed == 0:
        return None
    return parsed


def _first_number(values: Iterable[object], *, positive: bool = False, allow_zero: bool = True) -> Optional[float]:
    for value in values:
        parsed = _number(value, positive=positive, allow_zero=allow_zero)
        if parsed is not None:
            return parsed
    return None


def _unique_text(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(_text(value) for value in values if _text(value)))


class InstrumentValuationQueryService:
    """Expose valuation facts without creating an investment action."""

    def __init__(
        self,
        monitor_store,
        valuation_service: ValuationModelService = None,
        settings: Mapping[str, object] = None,
    ):
        self.monitor_store = monitor_store
        self.valuation_service = valuation_service or ValuationModelService()
        self.settings = dict(settings or {})

    def query(self, query: InstrumentValuationQuery) -> Dict[str, object]:
        request = query.normalized()
        if not request.symbol:
            raise ValueError("symbol is required")

        account_id, state = self._account_state(request.account_id)
        if not state:
            return self._not_found(request.symbol, request.account_id, "최근 계좌 스냅샷이 없습니다.")
        snapshot = account_snapshot_from_monitor_state(dict(state))
        if snapshot is None:
            return self._not_found(request.symbol, account_id, "계좌 스냅샷 형식을 읽을 수 없습니다.")
        position, scope = self._position(snapshot.positions, snapshot.watchlist, request.symbol)
        if position is None:
            return self._not_found(request.symbol, account_id, "보유·관심 종목에서 찾지 못했습니다.")

        external_signals = dict(snapshot.external_signals or {})
        result = self.valuation_service.evaluate(ValuationModelRequest(
            position=position,
            external_signals=external_signals,
            settings=self.settings,
        ))
        primary = dict(result.rows[0]) if result.rows else {}
        source_symbol = _text(primary.get("sourceSymbol") or position.symbol).upper()
        company = company_valuation_context(
            external_signals,
            source_symbol,
            price_as_of=position.updated_at or snapshot.generated_at,
            currency=position.currency,
        )
        company_detail = company_prompt_context(external_signals, source_symbol)
        company_metrics = company.get("metrics") if isinstance(company.get("metrics"), Mapping) else {}
        multiple_band = primary.get("multipleBand") if isinstance(primary.get("multipleBand"), Mapping) else {}
        eps_scenario = primary.get("epsScenario") if isinstance(primary.get("epsScenario"), Mapping) else {}

        per_status = _text(company.get("perStatus") or "missing")
        current_per = (
            _first_number(
                (company_metrics.get("peRatio"), primary.get("peRatio")),
                positive=True,
            )
            if per_status == "available"
            else None
        )
        forward_per = _first_number(
            (company_metrics.get("forwardPE"), primary.get("forwardPE")),
            positive=True,
        )
        pbr = _first_number((company_metrics.get("pbr"), primary.get("pbr")), positive=True)
        peg = _first_number((company_metrics.get("pegRatio"),), positive=True)
        trailing_eps = _first_number((company_metrics.get("trailingEPS"),), allow_zero=True)
        expected_eps = _first_number((primary.get("expectedEPS"),), allow_zero=False)
        fair_value = self._fair_value(primary, position.currency)
        missing = _unique_text([
            *(company.get("missing") or []),
            *(primary.get("missingInputs") or []),
            *(primary.get("modelExclusionReasons") or []),
        ])
        quality_issues = [
            dict(item)
            for item in primary.get("valuationQualityIssues") or []
            if isinstance(item, Mapping)
        ]
        sources = self._sources(company_detail, primary, company)

        return {
            "contract": READ_MODEL_VERSION,
            "status": "ok",
            "generatedAt": utc_now_iso(),
            "decisionRole": "reference-only",
            "instrument": {
                "symbol": _text(position.symbol).upper(),
                "sourceSymbol": source_symbol,
                "name": _text(position.name or company.get("companyName") or position.symbol),
                "market": _text(position.market),
                "currency": _text(position.currency),
                "scope": scope,
                "currentPrice": _number(position.current_price, positive=True),
                "priceAsOf": _text(position.updated_at or snapshot.generated_at),
            },
            "snapshot": {
                "accountId": account_id,
                "generatedAt": _text(snapshot.generated_at),
                "mode": _text(snapshot.mode),
                "status": _text(snapshot.status),
            },
            "marketMetrics": {
                "currentPER": current_per,
                "forwardPER": forward_per,
                "pbr": pbr,
                "pegRatio": peg,
                "trailingEPS": trailing_eps,
                "trailingEPSPeriod": _text(
                    primary.get("epsPeriod")
                    or eps_scenario.get("period")
                    or "ttm"
                ) if trailing_eps is not None else "",
                "expectedEPS": expected_eps,
                "returnOnEquityPct": _number(company_metrics.get("returnOnEquityPct")),
                "returnOnAssetsPct": _number(company_metrics.get("returnOnAssetsPct")),
                "dividendYieldPct": _number(company_metrics.get("dividendYieldPct")),
                "perStatus": per_status,
                "reportingBasis": dict(company.get("reportingBasis") or {}),
            },
            "valuation": {
                "status": result.status,
                "model": {
                    "id": _text(primary.get("valuationModelId")),
                    "family": _text(primary.get("valuationModelFamily")),
                    "method": _text(primary.get("valuationMethod")),
                    "formula": _text(primary.get("formula")),
                    "version": _text(primary.get("modelVersion")),
                    "serviceVersion": result.model_service_version,
                },
                "fairValue": fair_value,
                "safetyMargin": {
                    "conservativePct": _number(primary.get("conservativeMarginOfSafetyPct")) if fair_value["low"] is not None else None,
                    "basePct": _number(primary.get("marginOfSafetyPct")) if fair_value["base"] is not None else None,
                    "optimisticPct": _number(primary.get("optimisticMarginOfSafetyPct")) if fair_value["high"] is not None else None,
                    "requiredPct": _number(primary.get("minimumMarginOfSafetyPct")),
                },
                "earningsScenario": {
                    "low": _first_number((eps_scenario.get("low"), primary.get("expectedEPSLow")), allow_zero=False),
                    "base": _first_number((eps_scenario.get("base"), primary.get("expectedEPS")), allow_zero=False),
                    "high": _first_number((eps_scenario.get("high"), primary.get("expectedEPSHigh")), allow_zero=False),
                    "period": _text(eps_scenario.get("period") or primary.get("epsPeriod")),
                    "method": _text(eps_scenario.get("method")),
                    "sourceCount": int(_number(eps_scenario.get("sourceCount")) or 0),
                    "analystCount": int(_number(eps_scenario.get("analystCount")) or 0),
                    "providers": list(eps_scenario.get("providers") or []),
                    "confidence": _text(eps_scenario.get("confidence")),
                },
                "multipleBand": {
                    "low": _first_number((multiple_band.get("low"), primary.get("targetPERLow")), positive=True),
                    "base": _first_number((multiple_band.get("base"), primary.get("targetPER")), positive=True),
                    "high": _first_number((multiple_band.get("high"), primary.get("targetPERHigh")), positive=True),
                    "basis": _text(multiple_band.get("basis")),
                    "sampleCount": int(_number(multiple_band.get("sampleCount")) or 0),
                    "providerCount": int(_number(multiple_band.get("providerCount")) or 0),
                    "providers": list(multiple_band.get("providers") or []),
                    "evidenceBacked": bool(multiple_band.get("evidenceBacked")),
                    "confidence": _text(multiple_band.get("confidence")),
                },
                "quality": {
                    "status": _text(primary.get("valuationQualityStatus") or "unavailable"),
                    "dataState": _text(primary.get("valuationDataState")),
                    "reliabilityState": _text(primary.get("valuationReliabilityState")),
                    "freshnessStatus": _text(primary.get("valuationFreshnessStatus")),
                    "decisionEligible": bool(primary.get("valuationDecisionEligible")),
                    "issues": quality_issues,
                },
                "asOf": _text(primary.get("valuationAsOf") or company.get("sourceAsOf")),
                "sourceReason": _text(primary.get("sourceReason")),
                "preferredMetric": _text(primary.get("preferredValuationMetric")),
                "reviewStatus": _text(primary.get("approvalStatus")),
            },
            "companyData": {
                "state": _text(company.get("dataState") or "unavailable"),
                "officialSource": bool(company.get("officialSource")),
                "sourceAsOf": _text(company.get("sourceAsOf")),
                "sourceProviders": list(company.get("sourceProviders") or []),
            },
            "missingData": missing,
            "sources": sources,
        }

    def _account_state(self, requested_account_id: str):
        states = self.monitor_store.previous if self.monitor_store is not None else {}
        if not isinstance(states, Mapping) or not states:
            return requested_account_id, {}
        if requested_account_id:
            state = states.get(requested_account_id)
            return requested_account_id, dict(state) if isinstance(state, Mapping) else {}
        account_id = "default" if isinstance(states.get("default"), Mapping) else next(iter(states), "")
        state = states.get(account_id)
        return account_id, dict(state) if isinstance(state, Mapping) else {}

    @staticmethod
    def _position(positions, watchlist, symbol: str):
        for item in positions or []:
            if _text(getattr(item, "symbol", "")).upper() == symbol:
                return item, "holding"
        for item in watchlist or []:
            if _text(getattr(item, "symbol", "")).upper() == symbol:
                return item, "watchlist"
        return None, ""

    @staticmethod
    def _fair_value(row: Mapping[str, object], currency: str) -> Dict[str, object]:
        return {
            "low": _number(row.get("fairValueLow"), positive=True),
            "base": _first_number((row.get("fairValue"), row.get("fairValueBase")), positive=True),
            "high": _number(row.get("fairValueHigh"), positive=True),
            "currency": _text(row.get("valuationCurrency") or currency),
        }

    @staticmethod
    def _sources(company: Mapping[str, object], model: Mapping[str, object], context: Mapping[str, object]):
        rows = []
        for item in company.get("provenance") or []:
            if not isinstance(item, Mapping):
                continue
            provider = _text(item.get("provider"))
            if not provider:
                continue
            rows.append({
                "provider": provider,
                "scope": _text(item.get("scope")),
                "asOf": _text(item.get("asOf")),
            })
        model_provider = _text(model.get("sourceProvider"))
        model_as_of = _text(model.get("valuationAsOf"))
        if model_provider:
            rows.append({"provider": model_provider, "scope": "valuation-model-input", "asOf": model_as_of})
        if not rows:
            for provider in context.get("sourceProviders") or []:
                rows.append({"provider": _text(provider), "scope": "company-metrics", "asOf": _text(context.get("sourceAsOf"))})

        grouped = {}
        for row in rows:
            provider = _text(row.get("provider"))
            if not provider:
                continue
            item = grouped.setdefault(provider, {"provider": provider, "scopes": [], "asOfValues": []})
            scope = _text(row.get("scope"))
            as_of = _text(row.get("asOf"))
            if scope and scope not in item["scopes"]:
                item["scopes"].append(scope)
            if as_of:
                item["asOfValues"].append(as_of)
        return [
            {
                "provider": item["provider"],
                "scopes": item["scopes"],
                "asOf": latest_source_as_of(item["asOfValues"]),
            }
            for item in grouped.values()
        ][:6]

    @staticmethod
    def _not_found(symbol: str, account_id: str, reason: str) -> Dict[str, object]:
        return {
            "contract": READ_MODEL_VERSION,
            "status": "not-found",
            "generatedAt": utc_now_iso(),
            "decisionRole": "reference-only",
            "instrument": {"symbol": symbol},
            "snapshot": {"accountId": account_id},
            "reason": reason,
        }
