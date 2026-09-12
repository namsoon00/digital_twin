"""Research-only shadow samples, observations and outcome persistence."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List
from digital_twin.modules.decisions.domain.investment_brain import (
    ObservedOutcome,
    canonical_investment_timestamp,
    stable_id,
)
from digital_twin.modules.outcomes.domain.hypothesis_observation import (
    ShadowHypothesisObservationEpisode,
)
from digital_twin.modules.outcomes.domain.hypothesis_outcome_contract import (
    observation_domain_status,
    outcome_contract_completeness,
)
from digital_twin.modules.outcomes.contracts import (
    evaluate_hypothesis_outcome,
    observation_facts,
    outcome_evaluation_history,
    outcome_needs_data,
    validate_outcome_repair,
)
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from .outcome_policy import (
    contract_benchmark_symbol,
    market_outcome_target_at,
    number,
    outcome_horizon_minutes,
    parse_datetime,
)
from .ports import ConnectionFactory


def save_shadow_hypothesis_observations(
    episodes: Iterable[ShadowHypothesisObservationEpisode],
    *,
    _sync_shadow_hypothesis_observation_targets: Callable[..., Dict[str, object]],
    _transaction: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> List[ShadowHypothesisObservationEpisode]:
    """Persist research-only predictions without creating decision authority."""

    rows = [
        item
        for item in episodes or []
        if isinstance(item, ShadowHypothesisObservationEpisode) and item.episode_id
    ]
    if not rows:
        return []
    stamp = utc_now_iso()
    saved_rows = []
    with _transaction() as connection:
        for episode in rows:
            existing = connection.execute(
                "SELECT payload_json FROM investment_hypothesis_observation_episodes WHERE episode_id = %s FOR UPDATE",
                (episode.episode_id,),
            ).fetchone() or {}
            frozen = _json_loads(existing.get("payload_json"), {})
            if (frozen.get("readiness") or {}).get("eligible") is True:
                # A later poll must not move a prediction's start price or evidence.
                saved_rows.append(ShadowHypothesisObservationEpisode.from_dict(frozen))
                continue
            payload = episode.to_dict()
            connection.execute(
                """
                    INSERT INTO investment_hypothesis_observation_episodes (
                        episode_id, candidate_set_id, account_id, symbol,
                        hypothesis_id, claim_identity, family_id, claim_contract_id,
                        source_abox_snapshot_id, inference_generation_id,
                        independence_bucket, market_independence_key,
                        account_independence_key, status, observed_from_at,
                        payload_json, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        candidate_set_id = VALUES(candidate_set_id),
                        hypothesis_id = VALUES(hypothesis_id),
                        source_abox_snapshot_id = VALUES(source_abox_snapshot_id),
                        inference_generation_id = VALUES(inference_generation_id),
                        status = IF(
                            investment_hypothesis_observation_episodes.status = 'observed',
                            investment_hypothesis_observation_episodes.status,
                            VALUES(status)
                        ),
                        payload_json = VALUES(payload_json),
                        updated_at = VALUES(updated_at)
                    """,
                (
                    episode.episode_id,
                    episode.candidate_set_id,
                    episode.account_id,
                    episode.symbol,
                    episode.hypothesis_id,
                    episode.claim_identity,
                    episode.family_id,
                    episode.claim_contract_id,
                    episode.source_abox_snapshot_id,
                    episode.inference_generation_id,
                    episode.independence_bucket,
                    episode.market_independence_key,
                    episode.account_independence_key,
                    episode.status,
                    episode.observed_from_at,
                    json_dumps(payload),
                    stamp,
                    stamp,
                ),
            )
            if episode.observation_eligible:
                _sync_shadow_hypothesis_observation_targets(
                    connection,
                    episode,
                    stamp,
                )
            saved_rows.append(episode)
    return saved_rows


def shadow_observation_episodes_by_ids(
    episode_ids: Iterable[str],
    *,
    _connect: ConnectionFactory,
) -> Dict[str, ShadowHypothesisObservationEpisode]:
    clean_ids = list(
        dict.fromkeys(
            str(item or "").strip() for item in episode_ids or [] if str(item or "").strip()
        )
    )
    if not clean_ids:
        return {}
    placeholders = ",".join(["%s"] * len(clean_ids))
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload_json, status, observed_from_at "
            "FROM investment_hypothesis_observation_episodes "
            "WHERE episode_id IN (" + placeholders + ")",
            tuple(clean_ids),
        ).fetchall()
    result = {}
    for row in rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        if not payload:
            continue
        payload["status"] = str(row.get("status") or payload.get("status") or "")
        payload["observedFromAt"] = canonical_investment_timestamp(
            row.get("observed_from_at") or payload.get("observedFromAt")
        )
        episode = ShadowHypothesisObservationEpisode.from_dict(payload)
        if episode.episode_id:
            result[episode.episode_id] = episode
    return result


def shadow_observation_episodes_for_claims(
    account_id: str,
    symbol: str,
    claim_contract_ids: Iterable[str],
    limit: int = 100,
    *,
    _connect: ConnectionFactory,
) -> List[ShadowHypothesisObservationEpisode]:
    """Read reused shadow samples by stable claim identity.

    A shadow episode is intentionally deduplicated across candidate sets in
    one independence bucket. Querying only by the latest candidate-set id
    therefore hides valid observation history from the current hypothesis.
    """

    claim_ids = list(
        dict.fromkeys(
            str(item or "").strip() for item in claim_contract_ids or [] if str(item or "").strip()
        )
    )
    normalized_account = str(account_id or "").strip() or "default"
    normalized_symbol = str(symbol or "").upper().strip()
    if not normalized_symbol or not claim_ids:
        return []
    placeholders = ",".join(["%s"] * len(claim_ids))
    bounded_limit = max(1, min(500, int(limit or 100)))
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload_json, status, observed_from_at "
            "FROM investment_hypothesis_observation_episodes "
            "WHERE account_id = %s AND symbol = %s "
            "AND claim_contract_id IN (" + placeholders + ") "
            "ORDER BY observed_from_at DESC, episode_id DESC LIMIT %s",
            (normalized_account, normalized_symbol, *claim_ids, bounded_limit),
        ).fetchall()
    result = []
    for row in rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        if not payload:
            continue
        payload["status"] = str(row.get("status") or payload.get("status") or "")
        payload["observedFromAt"] = canonical_investment_timestamp(
            row.get("observed_from_at") or payload.get("observedFromAt")
        )
        episode = ShadowHypothesisObservationEpisode.from_dict(payload)
        if episode.episode_id:
            result.append(episode)
    return result


def record_shadow_hypothesis_outcome_observations(
    account_id: str,
    observations: Iterable[Dict[str, object]],
    *,
    _outcome_max_delay_minutes: Callable[..., int],
    _save_shadow_hypothesis_outcome: Callable[..., ObservedOutcome],
    _shadow_observation_episodes_by_ids: Callable[
        ..., Dict[str, ShadowHypothesisObservationEpisode]
    ],
) -> List[ObservedOutcome]:
    rows = [dict(item or {}) for item in observations or []]
    episodes = _shadow_observation_episodes_by_ids(item.get("episodeId") for item in rows)
    outcomes = []
    for item in rows:
        episode = episodes.get(str(item.get("episodeId") or ""))
        if not episode or episode.account_id != str(account_id or ""):
            continue
        horizon_minutes = int(item.get("horizonMinutes") or 0)
        contract = dict(episode.outcome_contract or {})
        completeness = outcome_contract_completeness(contract)
        if horizon_minutes not in outcome_horizon_minutes(
            contract.get("outcomeHorizonMinutes")
        ) or not completeness.get("complete"):
            continue
        target_at = market_outcome_target_at(
            episode.observed_from_at,
            episode.symbol,
            episode.market,
            episode.currency,
            horizon_minutes,
        )
        observed_at = str(item.get("observedAt") or "")
        target_time = parse_datetime(target_at)
        observed_time = parse_datetime(observed_at)
        if not target_time or not observed_time or observed_time < target_time:
            continue
        facts = dict(item.get("facts") or {})
        current_price = number(facts.get("currentPrice"))
        decision_price = number(facts.get("decisionPrice"))
        if not current_price or not decision_price:
            continue
        change_pct = round(((current_price / decision_price) - 1) * 100, 4)
        contract_observation = observation_domain_status(facts, contract)
        evaluation = evaluate_hypothesis_outcome(
            contract,
            episode.stance,
            facts,
            change_pct,
            horizon_minutes,
        )
        missing_metrics = list(evaluation.get("missingRequiredMetricIds") or [])
        delay_minutes = max(
            0.0,
            (observed_time - target_time).total_seconds() / 60.0,
        )
        maximum_delay = int(
            contract.get("maximumObservationDelayMinutes") or _outcome_max_delay_minutes()
        )
        calibration_eligible = bool(
            delay_minutes <= maximum_delay
            and not list(contract_observation.get("missingObservationDomains") or [])
            and not missing_metrics
        )
        eligibility = (
            "eligible"
            if calibration_eligible
            else (
                "excluded-contract-data-gap"
                if contract_observation.get("missingObservationDomains")
                else (
                    "excluded-criterion-data-gap"
                    if missing_metrics
                    else "excluded-delayed-observation"
                )
            )
        )
        hypothesis = dict(episode.hypothesis or {})
        outcome = ObservedOutcome(
            outcome_id=stable_id(
                "shadow-hypothesis-outcome",
                episode.episode_id,
                horizon_minutes,
            ),
            episode_id=episode.episode_id,
            observed_at=observed_at,
            price=current_price,
            profit_loss_rate=0.0,
            price_change_from_decision_pct=change_pct,
            selected_hypothesis_status=str(
                evaluation.get("selectedHypothesisStatus") or "inconclusive"
            ),
            payload={
                "observationFacts": observation_facts(facts),
                "episodeKind": "shadow-hypothesis",
                "selectedHypothesisId": episode.hypothesis_id,
                "selectedHypothesisStance": episode.stance,
                "hypothesisFamilyId": episode.family_id,
                "hypothesisTemplateId": hypothesis.get("templateId") or episode.family_id,
                "hypothesisTemplateLabel": hypothesis.get("templateLabel")
                or hypothesis.get("claim")
                or "",
                "predictionTarget": hypothesis.get("predictionTarget")
                or contract.get("predictionTarget")
                or "",
                "expectedDirection": hypothesis.get("expectedDirection")
                or contract.get("expectedDirection")
                or "",
                "expectedOutcome": hypothesis.get("expectedOutcome")
                or contract.get("expectedOutcome")
                or "",
                "outcomeMetric": hypothesis.get("outcomeMetric")
                or contract.get("outcomeMetric")
                or "",
                "falsificationContract": hypothesis.get("falsificationContract")
                or contract.get("falsificationContract")
                or "",
                "hypothesisOutcomeContract": contract,
                "contractFingerprint": contract.get("contractFingerprint") or "",
                "marketIndependenceKey": episode.market_independence_key,
                "accountIndependenceKey": episode.account_independence_key,
                "independenceBucket": episode.independence_bucket,
                "sourceAboxSnapshotId": episode.source_abox_snapshot_id,
                "inferenceGenerationId": episode.inference_generation_id,
                "decisionPrice": decision_price,
                "decisionPriceSourceAsOf": facts.get("decisionPriceSourceAsOf") or "",
                "observationBasis": str(
                    facts.get("observationBasis") or "historical-market-time-series"
                ),
                "observationSource": str(
                    facts.get("observationSource") or facts.get("provider") or ""
                ),
                "sourceAsOf": canonical_investment_timestamp(facts.get("sourceAsOf"))
                or observed_at,
                "dataQuality": str(facts.get("dataQuality") or "unknown"),
                **contract_observation,
                **evaluation,
                "missingRequiredMetricIds": missing_metrics,
                "benchmarkSymbol": contract_benchmark_symbol(contract),
                "benchmarkReturnPct": facts.get("benchmarkReturnPct"),
                "benchmarkStartAsOf": facts.get("benchmarkStartAsOf") or "",
                "benchmarkEndAsOf": facts.get("benchmarkEndAsOf") or "",
                "benchmarkObservationSource": facts.get("benchmarkObservationSource") or "",
                "excessReturnPct": (
                    round(change_pct - number(facts.get("benchmarkReturnPct")), 6)
                    if facts.get("benchmarkReturnPct") not in (None, "")
                    else None
                ),
                "horizonMinutes": horizon_minutes,
                "targetAt": target_at,
                "actualElapsedMinutes": round(
                    (observed_time - parse_datetime(episode.observed_from_at)).total_seconds()
                    / 60.0,
                    2,
                ),
                "observationDelayMinutes": round(delay_minutes, 2),
                "observationTiming": ("on-time" if delay_minutes <= maximum_delay else "delayed"),
                "calibrationEligibility": eligibility,
                "predictionContractCompleteness": completeness,
            },
        )
        saved = _save_shadow_hypothesis_outcome(episode, outcome)
        outcomes.append(saved or outcome)
    return outcomes


def save_shadow_hypothesis_outcome(
    episode: ShadowHypothesisObservationEpisode,
    outcome: ObservedOutcome,
    *,
    _transaction: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> ObservedOutcome:
    payload = outcome.to_dict()
    stamp = utc_now_iso()
    horizon_minutes = int((outcome.payload or {}).get("horizonMinutes") or 0)
    fingerprint = str((outcome.payload or {}).get("contractFingerprint") or "")
    with _transaction() as connection:
        row = connection.execute(
            "SELECT payload_json FROM investment_hypothesis_observation_outcomes WHERE outcome_id = %s FOR UPDATE",
            (outcome.outcome_id,),
        ).fetchone()
        previous = _json_loads((row or {}).get("payload_json"), {})
        if previous:
            if not outcome_needs_data(previous):
                return ObservedOutcome.from_dict(previous)
            validate_outcome_repair(previous, payload)
            outcome.payload["evaluationHistory"] = outcome_evaluation_history(previous, stamp)
            outcome.payload["evaluationAttemptCount"] = int((previous.get("payload") or {}).get("evaluationAttemptCount") or 1) + 1
            payload = outcome.to_dict()
        connection.execute(
            """
                INSERT INTO investment_hypothesis_observation_outcomes (
                    outcome_id, observation_episode_id, account_id, symbol,
                    observed_at, selected_hypothesis_status, price,
                    price_change_from_decision_pct, payload_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    selected_hypothesis_status = VALUES(selected_hypothesis_status),
                    price = VALUES(price),
                    price_change_from_decision_pct = VALUES(price_change_from_decision_pct),
                    payload_json = VALUES(payload_json)
                """,
            (
                outcome.outcome_id,
                episode.episode_id,
                episode.account_id,
                episode.symbol,
                outcome.observed_at,
                outcome.selected_hypothesis_status,
                outcome.price,
                outcome.price_change_from_decision_pct,
                json_dumps(payload),
                stamp,
            ),
        )
        connection.execute(
            "UPDATE investment_hypothesis_observation_episodes "
            "SET status = 'observed', updated_at = %s WHERE episode_id = %s",
            (stamp, episode.episode_id),
        )
        connection.execute(
            "UPDATE investment_hypothesis_observation_targets "
            "SET status = %s, outcome_id = %s, observed_at = %s, updated_at = %s "
            "WHERE observation_episode_id = %s AND horizon_minutes = %s "
            "AND contract_fingerprint = %s",
            (
                "needs-data" if outcome_needs_data(payload) else "observed",
                outcome.outcome_id,
                outcome.observed_at,
                stamp,
                episode.episode_id,
                horizon_minutes,
                fingerprint,
            ),
        )
    return outcome
