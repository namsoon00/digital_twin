import hashlib
from typing import Dict, Iterable, List

from ..domain.investment_brain import canonical_investment_timestamp
from ..domain.investment_outcomes import DecisionReview, PerformanceAttribution
from ..domain.portfolio import AccountSnapshot


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


class InvestmentOutcomeObservationService:
    """Orchestrates later outcome observation without changing investment judgement.

    Decision episodes are the durable judgement record. This service only links
    them to the first eligible later market observation at each configured
    horizon; TypeDB receives those ObservedOutcome facts in the next ABox
    projection and learning remains review-only.
    """

    def __init__(
        self,
        decision_episode_store=None,
        market_time_series_store=None,
        settings: Dict[str, object] = None,
        investment_domain_store=None,
    ):
        self.decision_episode_store = decision_episode_store
        self.market_time_series_store = market_time_series_store
        self.settings = dict(settings or {})
        self.investment_domain_store = investment_domain_store

    def enabled(self) -> bool:
        return bool(
            self.decision_episode_store
            and self.market_time_series_store
            and hasattr(self.decision_episode_store, "pending_outcome_targets")
            and hasattr(self.decision_episode_store, "record_outcome_observations")
            and hasattr(self.market_time_series_store, "load_outcome_observations")
        )

    def batch_size(self) -> int:
        return int_setting(self.settings, "investmentBrainOutcomeEpisodeBatchSize", 200, 10, 1000)

    def max_delay_minutes(self) -> int:
        return int_setting(self.settings, "investmentBrainOutcomeMaxDelayMinutes", 180, 1, 60 * 24 * 14)

    def observe_snapshot(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "unavailable", "reason": "결과 관측 저장소 또는 시세 이력 저장소가 연결되지 않았습니다."}
        if not snapshot or not snapshot.has_live_account_data():
            return {"status": "skipped-non-live-snapshot", "reason": "정상 live 계좌 스냅샷에서만 결과를 기록합니다."}
        observed_at = canonical_investment_timestamp(snapshot.generated_at)
        if not observed_at:
            return {"status": "skipped-invalid-snapshot-time", "reason": "스냅샷 시각을 표준 UTC 시각으로 읽지 못했습니다."}
        targets = self.decision_episode_store.pending_outcome_targets(
            snapshot.account_id,
            observed_at,
            limit=self.batch_size(),
        )
        if not targets:
            return {
                "status": "no-due-targets",
                "observedAt": observed_at,
                "targetCount": 0,
                "savedOutcomeCount": 0,
            }
        historical = self.market_time_series_store.load_outcome_observations(
            snapshot.account_id,
            targets,
            max_delay_minutes=self.max_delay_minutes(),
        )
        snapshot_observations = self.snapshot_observations(snapshot.positions or [], observed_at)
        records = []
        historical_count = 0
        snapshot_fallback_count = 0
        missing_count = 0
        for target in targets:
            request_id = str(target.get("requestId") or "")
            symbol = str(target.get("symbol") or "").upper().strip()
            facts = dict(historical.get(request_id) or {})
            if facts:
                historical_count += 1
            else:
                facts = dict(snapshot_observations.get(symbol) or {})
                if facts:
                    snapshot_fallback_count += 1
            if not facts:
                missing_count += 1
                continue
            records.append({
                "episodeId": target.get("episodeId"),
                "horizonMinutes": target.get("horizonMinutes"),
                "observedAt": facts.get("generatedAt") or facts.get("updatedAt") or observed_at,
                "facts": facts,
            })
        outcomes = self.decision_episode_store.record_outcome_observations(snapshot.account_id, records)
        review_result = self.review_outcomes(outcomes)
        contract_data_gap_count = sum(
            1
            for item in outcomes
            if str((getattr(item, "payload", {}) or {}).get("calibrationEligibility") or "") == "excluded-contract-data-gap"
        )
        return {
            "status": "observed" if outcomes else ("waiting-market-observation" if missing_count else "no-new-outcomes"),
            "observedAt": observed_at,
            "targetCount": len(targets),
            "historicalObservationCount": historical_count,
            "snapshotFallbackCount": snapshot_fallback_count,
            "missingObservationCount": missing_count,
            "savedOutcomeCount": len(outcomes),
            "contractDataGapCount": contract_data_gap_count,
            "outcomeIds": [item.outcome_id for item in outcomes],
            "symbols": sorted({str(item.get("symbol") or "").upper() for item in targets if str(item.get("symbol") or "").strip()}),
            "maximumDelayMinutes": self.max_delay_minutes(),
            "performanceAttributionCount": review_result.get("attributionCount", 0),
            "decisionReviewCount": review_result.get("reviewCount", 0),
        }

    def review_outcomes(self, outcomes: Iterable[object]) -> Dict[str, int]:
        if not self.investment_domain_store or not outcomes:
            return {"attributionCount": 0, "reviewCount": 0}
        episode_ids = [str(getattr(item, "episode_id", "") or "") for item in outcomes]
        episodes = self.decision_episode_store.episodes_by_ids(episode_ids)
        execution_loader = getattr(self.investment_domain_store, "execution_feedback_for_decisions", None)
        execution_feedback = execution_loader(episode_ids) if callable(execution_loader) else {}
        attributions = []
        reviews = []
        for outcome in outcomes:
            episode = episodes.get(str(getattr(outcome, "episode_id", "") or ""))
            if not episode:
                continue
            payload = dict(getattr(outcome, "payload", {}) or {})
            lifecycle = dict(execution_feedback.get(episode.episode_id) or {})
            if not lifecycle:
                lifecycle = self.investment_domain_store.lifecycle_trace(episode.episode_id)
            executions = list(lifecycle.get("executionEpisodes") or [])
            fills = list(lifecycle.get("fills") or [])
            market_return = self.optional_number(
                payload.get("marketReturnPct"),
                (episode.facts_at_decision or {}).get("benchmarkReturnPct"),
            )
            missing = []
            if market_return is None:
                missing.append("benchmarkReturnPct")
                market_return = 0.0
            if not executions and str(episode.action or "").upper() in {"BUY", "ADD", "TRIM", "SELL"}:
                missing.append("executionEpisode")
            execution_cost = sum(self.optional_number(item.get("fee")) or 0.0 for item in fills if isinstance(item, dict))
            horizon = int(float(payload.get("horizonMinutes") or 0))
            attribution_id = "performance-attribution:" + hashlib.sha256(
                (str(outcome.outcome_id) + "|" + str(horizon)).encode("utf-8")
            ).hexdigest()[:24]
            attribution = PerformanceAttribution(
                attribution_id=attribution_id,
                decision_episode_id=episode.episode_id,
                action_plan_id=str(episode.action_plan_id or ""),
                execution_episode_id=str((executions[0] or {}).get("executionEpisodeId") or "") if executions else "",
                market_return_pct=market_return,
                instrument_return_pct=float(getattr(outcome, "price_change_from_decision_pct", 0) or 0),
                execution_cost=execution_cost,
                observed_at=str(getattr(outcome, "observed_at", "") or ""),
                horizon_minutes=horizon,
                benchmark_symbol=str(payload.get("benchmarkSymbol") or ""),
                data_state="complete" if not missing else "partial",
                missing_data=missing,
            )
            attributions.append(attribution)
            selected_status = str(getattr(outcome, "selected_hypothesis_status", "") or "pending")
            executable = str(episode.action or "").upper() in {"BUY", "ADD", "TRIM", "SELL"}
            review = DecisionReview(
                review_id="decision-review:" + hashlib.sha256(
                    (str(outcome.outcome_id) + "|" + attribution_id).encode("utf-8")
                ).hexdigest()[:24],
                decision_episode_id=episode.episode_id,
                selected_hypothesis_status=selected_status,
                policy_compliant=bool(episode.mandate_version),
                execution_compliant=(not executable) or bool(executions),
                evidence_still_valid=selected_status not in {"contradicted", "invalidated"},
                attribution_ids=[attribution_id],
                observations=[
                    "outcome-horizon-minutes:" + str(horizon),
                    "calibration-eligibility:" + str(payload.get("calibrationEligibility") or "unknown"),
                    *(missing or []),
                ],
                reviewed_at=str(getattr(outcome, "observed_at", "") or ""),
            )
            reviews.append(review)
        batch_saver = getattr(self.investment_domain_store, "save_outcome_reviews", None)
        if callable(batch_saver):
            batch_saver(attributions, reviews)
        else:
            for attribution in attributions:
                self.investment_domain_store.save_performance_attribution(attribution)
            for review in reviews:
                self.investment_domain_store.save_decision_review(review)
        return {"attributionCount": len(attributions), "reviewCount": len(reviews)}

    def optional_number(self, *values) -> object:
        for value in values:
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def snapshot_observations(self, positions: Iterable[object], observed_at: str) -> Dict[str, Dict[str, object]]:
        observations: Dict[str, Dict[str, object]] = {}
        for position in positions or []:
            if not position or position.is_cash():
                continue
            symbol = str(getattr(position, "symbol", "") or "").upper().strip()
            try:
                current_price = float(getattr(position, "current_price", 0) or 0)
            except (TypeError, ValueError):
                current_price = 0.0
            if not symbol or current_price <= 0:
                continue
            quality = str(getattr(position, "data_quality", "") or "actual")
            observations[symbol] = {
                "currentPrice": current_price,
                "profitLossRate": getattr(position, "profit_loss_rate", 0),
                "priceChangeRate": getattr(position, "change_rate", 0),
                "observedAt": observed_at,
                "sourceAsOf": getattr(position, "source_as_of", "") or getattr(position, "updated_at", "") or observed_at,
                "provider": getattr(position, "quote_source", "") or getattr(position, "source", "") or "account-snapshot",
                "observationSource": "live-account-snapshot",
                "observationBasis": "live-account-snapshot-fallback",
                "dataQuality": quality,
            }
        return observations
