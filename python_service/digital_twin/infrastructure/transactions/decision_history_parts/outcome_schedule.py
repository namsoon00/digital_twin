"""Bound-connection scheduling and follow-up ownership writes."""

from __future__ import annotations

from digital_twin.modules.outcomes.infrastructure import transaction_writes as outcomes_writes
from typing import Callable, Dict, List
from digital_twin.modules.decisions.domain.investment_brain import (
    DecisionEpisode,
    canonical_investment_timestamp,
    stable_id,
)
from digital_twin.modules.outcomes.domain.hypothesis_observation import (
    ShadowHypothesisObservationEpisode,
)
from digital_twin.modules.outcomes.domain.hypothesis_outcome_contract import (
    outcome_contract_completeness,
)
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from .outcome_policy import (
    contract_benchmark_symbol,
    market_outcome_target_at,
    number,
    outcome_horizon_minutes,
    outcome_target_at,
)


def supersede_prior_follow_ups_for_current(
    connection: BoundWriteConnection,
    account_id: str,
    symbol: str,
    episode_id: str,
    stamp: str,
) -> int:
    return outcomes_writes.supersede_prior_followups(
        connection=connection,
        account_id=account_id,
        symbol=symbol,
        episode_id=episode_id,
        stamp=stamp,
    )


def sync_outcome_targets(
    connection: BoundWriteConnection,
    episode: DecisionEpisode,
    stamp: str = "",
    *,
    _episode_outcome_contract: Callable[..., Dict[str, object]],
    _episode_outcome_contract_completeness: Callable[..., Dict[str, object]],
    _episode_outcome_horizons: Callable[..., List[int]],
    _upsert_outcome_target: Callable[..., None],
) -> Dict[str, object]:
    return outcomes_writes.schedule_decision_outcomes(
        connection=connection,
        episode=episode,
        stamp=stamp,
        _episode_outcome_contract=_episode_outcome_contract,
        _episode_outcome_contract_completeness=_episode_outcome_contract_completeness,
        _episode_outcome_horizons=_episode_outcome_horizons,
        _upsert_outcome_target=_upsert_outcome_target,
        _bound_contract_benchmark_symbol=contract_benchmark_symbol,
        _bound_number=number,
        _bound_outcome_target_at=outcome_target_at,
    )


def upsert_outcome_target(
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
) -> None:
    return outcomes_writes.upsert_decision_outcome_target(
        connection=connection,
        target_id=target_id,
        episode=episode,
        horizon_minutes=horizon_minutes,
        target_at=target_at,
        maximum_delay_minutes=maximum_delay_minutes,
        contract_fingerprint=contract_fingerprint,
        status=status,
        exclusion_reason=exclusion_reason,
        payload=payload,
        stamp=stamp,
    )


def sync_shadow_hypothesis_observation_targets(
    connection: BoundWriteConnection,
    episode: ShadowHypothesisObservationEpisode,
    stamp: str = "",
    *,
    _outcome_max_delay_minutes: Callable[..., int],
    utc_now_iso: Callable[[], str],
) -> Dict[str, object]:
    contract = dict(episode.outcome_contract or {})
    completeness = outcome_contract_completeness(contract)
    if not episode.observation_eligible or not completeness.get("complete"):
        return {
            "status": "excluded",
            "targetCount": 0,
            "reason": str((episode.readiness or {}).get("reason") or "outcome-contract-incomplete"),
        }
    stamp = canonical_investment_timestamp(stamp) or utc_now_iso()
    fingerprint = str(contract.get("contractFingerprint") or "")
    maximum_delay = int(
        contract.get("maximumObservationDelayMinutes") or _outcome_max_delay_minutes()
    )
    target_count = 0
    for horizon_minutes in outcome_horizon_minutes(contract.get("outcomeHorizonMinutes")):
        target_at = market_outcome_target_at(
            episode.observed_from_at,
            episode.symbol,
            episode.market,
            episode.currency,
            horizon_minutes,
        )
        if not target_at:
            continue
        target_id = stable_id(
            "shadow-hypothesis-observation-target",
            episode.episode_id,
            horizon_minutes,
            fingerprint,
        )
        payload = {
            "episodeKind": "shadow-hypothesis",
            "requestId": target_id,
            "episodeId": episode.episode_id,
            "shadowObservationEpisodeId": episode.episode_id,
            "symbol": episode.symbol,
            "market": episode.market,
            "currency": episode.currency,
            "horizonMinutes": horizon_minutes,
            "decidedAt": episode.observed_from_at,
            "baselineAt": episode.observed_from_at,
            "targetAt": target_at,
            "maximumObservationDelayMinutes": maximum_delay,
            "requiredObservationDomains": contract.get("requiredObservationDomains") or [],
            "hypothesisOutcomeContract": contract,
            "benchmarkSymbol": contract_benchmark_symbol(contract),
            "requiresInstrumentBaseline": True,
        }
        connection.execute(
            """
                INSERT INTO investment_hypothesis_observation_targets (
                    target_id, observation_episode_id, account_id, symbol,
                    horizon_minutes, target_at, maximum_delay_minutes,
                    contract_fingerprint, status, exclusion_reason, outcome_id,
                    payload_json, created_at, updated_at, observed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', '', '', %s, %s, %s, '')
                ON DUPLICATE KEY UPDATE
                    target_at = VALUES(target_at),
                    maximum_delay_minutes = VALUES(maximum_delay_minutes),
                    payload_json = VALUES(payload_json),
                    updated_at = VALUES(updated_at)
                """,
            (
                target_id,
                episode.episode_id,
                episode.account_id,
                episode.symbol,
                horizon_minutes,
                target_at,
                maximum_delay,
                fingerprint,
                json_dumps(payload),
                stamp,
                stamp,
            ),
        )
        target_count += 1
    return {"status": "scheduled", "targetCount": target_count}
