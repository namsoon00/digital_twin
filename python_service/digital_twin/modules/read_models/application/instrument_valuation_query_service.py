"""Build a read-only valuation view from the latest monitor snapshot."""

from __future__ import annotations

import math
from typing import Dict, Iterable, Mapping, Optional

from digital_twin.modules.news_intelligence.contracts import build_company_driver_map, company_prompt_context, company_valuation_context, evaluate_causal_attribution, latest_source_as_of
from digital_twin.modules.portfolio.contracts import InstrumentValuationQuery
from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state, utc_now_iso
from digital_twin.modules.portfolio.contracts import ValuationModelRequest, ValuationModelService, valuation_snapshot_delta


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
            valuation_at=position.updated_at or snapshot.generated_at,
            source_snapshot_id=position.valuation_snapshot_id or snapshot.portfolio.valuation_snapshot_id,
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
        stored_company = (
            (external_signals.get("companyKnowledge") or {}).get(source_symbol, {})
            if isinstance(external_signals.get("companyKnowledge"), Mapping)
            else {}
        )
        driver_map = build_company_driver_map(
            source_symbol,
            stored_company if isinstance(stored_company, Mapping) else {},
            macro_context=external_signals.get("macro") if isinstance(external_signals.get("macro"), Mapping) else {},
            fx_rates=external_signals.get("fxRates") if isinstance(external_signals.get("fxRates"), Mapping) else {},
        )
        causal_attribution = self._causal_attribution(external_signals, source_symbol, driver_map)
        previous_assessment = self._previous_assessment(external_signals, source_symbol)
        current_identity = {
            "valuationBundleId": _text(primary.get("valuationBundleId")),
            "valuationAssessmentId": _text(primary.get("valuationAssessmentId")),
            "auditFingerprint": _text(primary.get("valuationAuditFingerprint")),
            "materialFingerprint": _text(primary.get("valuationMaterialFingerprint")),
        }
        change = valuation_snapshot_delta(previous_assessment, primary) if previous_assessment else {
            "contractVersion": "valuation-snapshot-delta-v1",
            "state": "no-prior-assessment",
            "materialChange": False,
            "auditChange": False,
            "previousAssessmentId": "",
            "currentAssessmentId": current_identity["valuationAssessmentId"],
        }
        model_comparison = self._model_comparison(result.rows)
        implied_expectations = self._implied_expectations(result.rows, source_symbol)
        readiness_rows = external_signals.get("driverDcfReadiness")
        dcf_readiness = (
            dict(readiness_rows.get(source_symbol))
            if isinstance(readiness_rows, Mapping) and isinstance(readiness_rows.get(source_symbol), Mapping)
            else {}
        )
        current_verified = [
            {
                "driverId": _text(item.get("driverId")),
                "label": _text(item.get("label")),
                "value": _number(item.get("value")),
                "unit": _text(item.get("unit")),
                "period": _text(item.get("period")),
                "evidenceId": _text(item.get("observationId")),
            }
            for item in driver_map.get("drivers") or []
            if isinstance(item, Mapping)
        ]
        previous_driver_map = self._previous_driver_map(external_signals, source_symbol)
        driver_material_changed = bool(
            previous_driver_map
            and _text(previous_driver_map.get("materialFingerprint"))
            and _text(previous_driver_map.get("materialFingerprint")) != _text(driver_map.get("materialFingerprint"))
        )
        newly_confirmed = current_verified if driver_material_changed else []
        next_checks = _unique_text([
            *missing,
            *(item.get("reason") for item in driver_map.get("unresolved") or [] if isinstance(item, Mapping)),
            *(causal_attribution.get("blockingReasons") or []),
            *(dcf_readiness.get("missingInputs") or []),
            *((dcf_readiness.get("financialEvidence") or {}).get("blockingReasons") or []),
            *(["dcf-assumption-review-required"] if dcf_readiness.get("assumptionReviewState") == "required" else []),
        ])

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
                "trailingEPSPeriod": _text(company_metrics.get("trailingEPSPeriod") or "ttm") if trailing_eps is not None else "",
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
                    "analystCount": int(_number(eps_scenario.get("analystCount"))) if eps_scenario.get("analystCount") is not None else None,
                    "analystCountState": _text(eps_scenario.get("analystCountState") or ("reported" if eps_scenario.get("analystCount") is not None else "not-provided")),
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
                "identity": current_identity,
                "inputSnapshot": {
                    "contractVersion": _text((primary.get("valuationBundle") or {}).get("contractVersion")),
                    "valuationAt": _text((primary.get("valuationBundle") or {}).get("valuationAt")),
                    "knowledgeCutoffAt": _text((primary.get("valuationBundle") or {}).get("knowledgeCutoffAt")),
                    "reproducibilityState": _text(primary.get("valuationReproducibilityState")),
                    "reproducibilityGaps": list(primary.get("valuationReproducibilityGaps") or []),
                    "sourceRevisionCount": len((primary.get("valuationBundle") or {}).get("sourceRevisionVector") or []),
                },
                "models": model_comparison,
                "impliedExpectations": implied_expectations,
                "dcfReadiness": dcf_readiness,
            },
            "companyData": {
                "state": _text(company.get("dataState") or "unavailable"),
                "officialSource": bool(company.get("officialSource")),
                "sourceAsOf": _text(company.get("sourceAsOf")),
                "sourceProviders": list(company.get("sourceProviders") or []),
            },
            "missingData": missing,
            "sources": sources,
            "investmentAnalysis": {
                "contractVersion": "instrument-investment-analysis-v1",
                "currentVerifiedFacts": current_verified,
                "newlyConfirmedFacts": newly_confirmed,
                "companyDriverMaterialChanged": driver_material_changed,
                "changeFromPrevious": change,
                "companyDrivers": driver_map,
                "priceExplanation": causal_attribution,
                "valuationModels": model_comparison,
                "dcfReadiness": dcf_readiness,
                "impliedExpectations": implied_expectations,
                "nextChecks": next_checks,
                "customerMessageEligible": bool(
                    change.get("materialChange")
                    or causal_attribution.get("priceCauseClaimEligible")
                ),
            },
        }

    @staticmethod
    def _previous_assessment(external_signals: Mapping[str, object], symbol: str) -> Dict[str, object]:
        history = external_signals.get("valuationAssessmentHistory")
        row = history.get(symbol) if isinstance(history, Mapping) and isinstance(history.get(symbol), Mapping) else {}
        previous = row.get("previous") if isinstance(row.get("previous"), Mapping) else row
        return dict(previous) if isinstance(previous, Mapping) else {}

    @staticmethod
    def _previous_driver_map(external_signals: Mapping[str, object], symbol: str) -> Dict[str, object]:
        history = external_signals.get("companyDriverHistory")
        row = history.get(symbol) if isinstance(history, Mapping) and isinstance(history.get(symbol), Mapping) else {}
        previous = row.get("previous") if isinstance(row.get("previous"), Mapping) else row
        return dict(previous) if isinstance(previous, Mapping) else {}

    @staticmethod
    def _causal_attribution(external_signals: Mapping[str, object], symbol: str, driver_map: Mapping[str, object]) -> Dict[str, object]:
        events = external_signals.get("companyEvents")
        raw_events = events.get(symbol) if isinstance(events, Mapping) else []
        event_rows = raw_events if isinstance(raw_events, list) else [raw_events] if isinstance(raw_events, Mapping) else []
        if not event_rows:
            return {
                "contractVersion": "causal-attribution-v1",
                "claimStrength": "unresolved",
                "priceCauseClaimEligible": False,
                "blockingReasons": ["verified-event-missing"],
                "valuationImpactSeparateFromPriceCause": True,
            }
        reactions = external_signals.get("eventPriceReactions")
        reaction_rows = reactions.get(symbol) if isinstance(reactions, Mapping) else {}
        latest = next((dict(item) for item in event_rows if isinstance(item, Mapping)), {})
        contract = latest.get("companyEventContract") if isinstance(latest.get("companyEventContract"), Mapping) else latest
        event_id = _text(contract.get("eventId") or contract.get("observationId"))
        reaction = (
            reaction_rows.get(event_id)
            if isinstance(reaction_rows, Mapping) and isinstance(reaction_rows.get(event_id), Mapping)
            else reaction_rows if isinstance(reaction_rows, Mapping) and "windowStart" in reaction_rows
            else {}
        )
        alternatives = reaction.get("alternativeExplanations") if isinstance(reaction, Mapping) else []
        return evaluate_causal_attribution(
            latest,
            reaction if isinstance(reaction, Mapping) else {},
            driver_map=driver_map,
            alternative_explanations=alternatives if isinstance(alternatives, list) else [],
        )

    @staticmethod
    def _model_comparison(rows) -> list[Dict[str, object]]:
        result = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            assessment = row.get("valuationAssessment") if isinstance(row.get("valuationAssessment"), Mapping) else {}
            result.append({
                "modelId": _text(row.get("valuationModelId") or row.get("valuationMethod")),
                "family": _text(row.get("valuationModelFamily")),
                "assessmentId": _text(row.get("valuationAssessmentId")),
                "bundleId": _text(row.get("valuationBundleId")),
                "status": _text(assessment.get("calculationStatus") or ("calculated" if _number(row.get("fairValue"), positive=True) else "blocked")),
                "fairValue": _number(row.get("fairValue"), positive=True),
                "currency": _text(row.get("valuationCurrency")),
                "decisionEligible": bool(row.get("valuationDecisionEligible")),
                "referenceOnly": bool(row.get("valuationReferenceOnly")) or not bool(row.get("valuationDecisionEligible")),
                "reviewStatus": _text(row.get("approvalStatus")),
                "inputState": _text(row.get("valuationInputState")),
                "reliabilityState": _text(row.get("valuationReliabilityState")),
                "evidenceBacked": bool((row.get("multipleBand") or {}).get("evidenceBacked")),
                "sourceBacked": bool(row.get("sourceBacked") or row.get("sourceReferences")),
                "assumptionReviewState": _text(row.get("assumptionReviewState")),
                "assumptionReview": dict(row.get("assumptionReview")) if isinstance(row.get("assumptionReview"), Mapping) else {},
                "financialEvidence": dict(row.get("financialEvidence")) if isinstance(row.get("financialEvidence"), Mapping) else {},
                "exposureReadiness": dict(row.get("exposureReadiness")) if isinstance(row.get("exposureReadiness"), Mapping) else {},
                "officialFinancialsReady": bool(row.get("officialFinancialsReady")),
                "assumptions": [
                    {
                        "id": _text(item.get("id")),
                        "value": item.get("value"),
                        "unit": _text(item.get("unit")),
                        "status": _text(item.get("status")),
                        "reviewState": _text(item.get("reviewState")),
                        "evidenceClass": _text(item.get("evidenceClass")),
                        "materiality": _text(item.get("materiality")),
                    }
                    for item in row.get("assumptions") or []
                    if isinstance(item, Mapping)
                ],
                "sensitivity": dict(row.get("sensitivity")) if isinstance(row.get("sensitivity"), Mapping) else {},
                "warnings": list(row.get("modelWarnings") or []),
                "blockedReasons": list(assessment.get("blockedReasons") or row.get("modelExclusionReasons") or []),
                "comparisonPolicy": "do-not-average-model-values",
            })
        return result

    @staticmethod
    def _implied_expectations(rows, symbol: str) -> Dict[str, object]:
        for row in rows or []:
            if not isinstance(row, Mapping) or _text(row.get("valuationModelFamily")) != "driver-dcf":
                continue
            implied = row.get("impliedExpectations")
            if isinstance(implied, Mapping):
                return {
                    **dict(implied),
                    "valuationAssessmentId": _text(row.get("valuationAssessmentId")),
                    "symbol": symbol,
                }
        return {
            "contractVersion": "reverse-dcf-growth-solver-v1",
            "status": "unavailable",
            "blockedReasons": ["driver-dcf-assessment-missing"],
            "symbol": symbol,
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
