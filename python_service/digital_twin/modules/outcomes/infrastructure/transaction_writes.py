"""Connection-bound writes; the caller owns commit, rollback and ordering."""

from __future__ import annotations
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from typing import Any, Callable, Dict
from digital_twin.domain.investment_outcomes import (
    DecisionReview,
    PerformanceAttribution,
)
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.domain.investment_brain import (
    DecisionEpisode,
    canonical_investment_timestamp,
    stable_id,
    utc_now_iso,
)


def save_decision_review(
    connection: BoundWriteConnection,
    review: DecisionReview,
    stamp: str,
):
    connection.execute(
        """
            INSERT INTO investment_decision_reviews (
                review_id, decision_episode_id, selected_hypothesis_status,
                policy_compliant, execution_compliant, evidence_still_valid,
                payload_json, reviewed_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE selected_hypothesis_status = VALUES(selected_hypothesis_status),
                policy_compliant = VALUES(policy_compliant),
                execution_compliant = VALUES(execution_compliant),
                evidence_still_valid = VALUES(evidence_still_valid),
                payload_json = VALUES(payload_json), reviewed_at = VALUES(reviewed_at),
                updated_at = VALUES(updated_at)
            """,
        (
            review.review_id,
            review.decision_episode_id,
            review.selected_hypothesis_status,
            1 if review.policy_compliant else 0,
            1 if review.execution_compliant else 0,
            1 if review.evidence_still_valid else 0,
            json_dumps(review.to_dict()),
            review.reviewed_at,
            stamp,
            stamp,
        ),
    )


def save_performance_attribution(
    connection: BoundWriteConnection,
    attribution: PerformanceAttribution,
    stamp: str,
):
    connection.execute(
        """
            INSERT INTO investment_performance_attributions (
                attribution_id, decision_episode_id, action_plan_id,
                execution_episode_id, market_return_pct, instrument_return_pct,
                active_return_pct, execution_cost, realized_profit_loss,
                currency_effect_pct, payload_json, observed_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE market_return_pct = VALUES(market_return_pct),
                instrument_return_pct = VALUES(instrument_return_pct),
                active_return_pct = VALUES(active_return_pct),
                execution_cost = VALUES(execution_cost),
                realized_profit_loss = VALUES(realized_profit_loss),
                currency_effect_pct = VALUES(currency_effect_pct),
                payload_json = VALUES(payload_json), observed_at = VALUES(observed_at),
                updated_at = VALUES(updated_at)
            """,
        (
            attribution.attribution_id,
            attribution.decision_episode_id,
            attribution.action_plan_id,
            attribution.execution_episode_id,
            attribution.market_return_pct,
            attribution.instrument_return_pct,
            attribution.active_return_pct,
            attribution.execution_cost,
            attribution.realized_profit_loss,
            attribution.currency_effect_pct,
            json_dumps(attribution.to_dict()),
            attribution.observed_at,
            stamp,
            stamp,
        ),
    )


def upsert_decision_followup(
    connection: BoundWriteConnection,
    condition: Any,
    episode: Any,
    stamp: Any,
    *,
    _bound_number: Callable[..., Any],
):
    return connection.execute(
        """
                    INSERT INTO investment_decision_follow_ups (
                        condition_id, episode_id, account_id, symbol, field_name,
                        comparison_operator, threshold_value, purpose, status,
                        observable, payload_json, created_at, updated_at, transitioned_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE status = VALUES(status), observable = VALUES(observable),
                        payload_json = VALUES(payload_json), updated_at = VALUES(updated_at),
                        transitioned_at = VALUES(transitioned_at)
                    """,
        (
            str(condition.get("conditionId")),
            episode.episode_id,
            episode.account_id,
            episode.symbol,
            str(condition.get("field") or ""),
            str(condition.get("operator") or ""),
            _bound_number(condition.get("threshold")),
            str(condition.get("purpose") or "switch"),
            str(condition.get("status") or "pending"),
            1 if condition.get("observable") is not False else 0,
            json_dumps(condition),
            stamp,
            stamp,
            str(condition.get("transitionAt") or ""),
        ),
    )


def supersede_prior_followups(
    connection: BoundWriteConnection,
    account_id: str,
    symbol: str,
    episode_id: str,
    stamp: str,
):
    """Keep history immutable while giving one decision ownership of tracking."""

    current = connection.execute(
        "SELECT decision_episode_id FROM investment_flow_current "
        "WHERE account_id = %s AND symbol = %s LIMIT 1",
        (str(account_id or ""), str(symbol or "").upper()),
    ).fetchone()
    if str((current or {}).get("decision_episode_id") or "") != str(episode_id or ""):
        return 0
    rows = connection.execute(
        "SELECT condition_id, payload_json FROM investment_decision_follow_ups "
        "WHERE account_id = %s AND symbol = %s AND episode_id <> %s "
        "AND status = 'pending' ORDER BY updated_at, condition_id LIMIT 500",
        (str(account_id or ""), str(symbol or "").upper(), str(episode_id or "")),
    ).fetchall()
    changed = 0
    for row in rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        payload.update(
            {
                "status": "superseded",
                "trackingStatus": "stopped-newer-decision",
                "supersededByEpisodeId": str(episode_id or ""),
                "supersededAt": str(stamp or ""),
            }
        )
        cursor = connection.execute(
            "UPDATE investment_decision_follow_ups SET status = 'superseded', "
            "payload_json = %s, updated_at = %s WHERE condition_id = %s "
            "AND status = 'pending'",
            (json_dumps(payload), stamp, str(row.get("condition_id") or "")),
        )
        changed += max(0, int(getattr(cursor, "rowcount", 0) or 0))
    return changed


def schedule_decision_outcomes(
    connection: BoundWriteConnection,
    episode: DecisionEpisode,
    stamp: str,
    *,
    _episode_outcome_contract: Callable[..., Any],
    _episode_outcome_contract_completeness: Callable[..., Any],
    _episode_outcome_horizons: Callable[..., Any],
    _upsert_outcome_target: Callable[..., Any],
    _bound_contract_benchmark_symbol: Callable[..., Any],
    _bound_number: Callable[..., Any],
    _bound_outcome_target_at: Callable[..., Any],
):
    """Persist the immutable observation schedule in the decision transaction."""

    stamp = canonical_investment_timestamp(stamp) or utc_now_iso()
    facts = (
        episode.facts_at_decision if isinstance(episode.facts_at_decision, dict) else {}
    )
    calibration = (
        facts.get("calibrationPolicy")
        if isinstance(facts.get("calibrationPolicy"), dict)
        else {}
    )
    completeness = _episode_outcome_contract_completeness(episode)
    contract = (
        _episode_outcome_contract(episode) if completeness.get("complete") else {}
    )
    eligible = bool(
        episode.selected_hypothesis_id
        and calibration.get("eligible") is True
        and completeness.get("complete")
    )
    if not eligible:
        reason = str(
            "no-selected-hypothesis"
            if not episode.selected_hypothesis_id
            else (
                "outcome-contract-incomplete"
                if not completeness.get("complete")
                else calibration.get("reason") or "calibration-ineligible"
            )
        )[:191]
        target_id = stable_id("decision-outcome-target-excluded", episode.episode_id)
        payload = {
            "episodeKind": "decision",
            "requestId": target_id,
            "episodeId": episode.episode_id,
            "symbol": episode.symbol,
            "horizonMinutes": 0,
            "decidedAt": episode.decided_at,
            "targetAt": episode.decided_at,
            "status": "excluded",
            "exclusionReason": reason,
            "predictionContractCompleteness": completeness,
        }
        _upsert_outcome_target(
            connection,
            target_id,
            episode,
            0,
            episode.decided_at,
            0,
            "",
            "excluded",
            reason,
            payload,
            stamp,
        )
        return {"status": "excluded", "targetCount": 0, "reason": reason}

    connection.execute(
        "DELETE FROM investment_decision_outcome_targets "
        "WHERE episode_id = %s AND status = 'excluded'",
        (episode.episode_id,),
    )
    fingerprint = str(contract.get("contractFingerprint") or "")
    maximum_delay = int(contract.get("maximumObservationDelayMinutes") or 0)
    fact_delta = (
        facts.get("factDelta") if isinstance(facts.get("factDelta"), dict) else {}
    )
    baseline_at = (
        canonical_investment_timestamp(
            fact_delta.get("source_observed_at")
            or fact_delta.get("sourceObservedAt")
            or facts.get("sourceAsOf")
        )
        or episode.decided_at
    )
    target_count = 0
    for horizon_minutes in _episode_outcome_horizons(episode):
        target_at = _bound_outcome_target_at(episode, horizon_minutes)
        if not target_at:
            continue
        target_id = stable_id(
            "decision-outcome-target",
            episode.episode_id,
            horizon_minutes,
            fingerprint,
        )
        payload = {
            "episodeKind": "decision",
            "requestId": target_id,
            "episodeId": episode.episode_id,
            "symbol": episode.symbol,
            "subjectName": episode.subject_name,
            "market": str(facts.get("market") or ""),
            "currency": str(facts.get("currency") or ""),
            "horizonMinutes": horizon_minutes,
            "decidedAt": episode.decided_at,
            "baselineAt": baseline_at,
            "targetAt": target_at,
            "maximumObservationDelayMinutes": maximum_delay,
            "requiredObservationDomains": contract.get("requiredObservationDomains")
            or [],
            "hypothesisOutcomeContract": contract,
            "benchmarkSymbol": _bound_contract_benchmark_symbol(contract, facts),
            "requiresInstrumentBaseline": True,
            **(
                {"decisionPrice": _bound_number(facts.get("currentPrice"))}
                if _bound_number(facts.get("currentPrice")) > 0
                else {}
            ),
            **(
                {"decisionPriceSourceAsOf": facts.get("sourceAsOf")}
                if facts.get("sourceAsOf")
                else {}
            ),
        }
        _upsert_outcome_target(
            connection,
            target_id,
            episode,
            horizon_minutes,
            target_at,
            maximum_delay,
            fingerprint,
            "pending",
            "",
            payload,
            stamp,
        )
        target_count += 1
    return {"status": "scheduled", "targetCount": target_count}


def upsert_decision_outcome_target(
    connection: BoundWriteConnection,
    target_id: str,
    episode: DecisionEpisode,
    horizon_minutes: int,
    target_at: str,
    maximum_delay_minutes: int,
    contract_fingerprint: str,
    status: str,
    exclusion_reason: str,
    payload: Dict[str, object],
    stamp: str,
):
    connection.execute(
        """
            INSERT INTO investment_decision_outcome_targets (
                target_id, episode_id, account_id, symbol, horizon_minutes,
                target_at, maximum_delay_minutes, contract_fingerprint,
                status, exclusion_reason, outcome_id, payload_json,
                created_at, updated_at, observed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', %s, %s, %s, '')
            ON DUPLICATE KEY UPDATE
                target_at = VALUES(target_at),
                maximum_delay_minutes = VALUES(maximum_delay_minutes),
                exclusion_reason = IF(
                    investment_decision_outcome_targets.status = 'observed',
                    investment_decision_outcome_targets.exclusion_reason,
                    VALUES(exclusion_reason)
                ),
                status = IF(
                    investment_decision_outcome_targets.status = 'observed',
                    investment_decision_outcome_targets.status,
                    VALUES(status)
                ),
                payload_json = VALUES(payload_json),
                updated_at = VALUES(updated_at)
            """,
        (
            target_id,
            episode.episode_id,
            episode.account_id,
            episode.symbol,
            int(horizon_minutes or 0),
            str(target_at or ""),
            int(maximum_delay_minutes or 0),
            str(contract_fingerprint or ""),
            str(status or "pending"),
            str(exclusion_reason or "")[:191],
            json_dumps(payload),
            stamp,
            stamp,
        ),
    )
