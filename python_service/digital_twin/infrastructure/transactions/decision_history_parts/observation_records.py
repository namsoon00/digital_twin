"""Decision observation assembly and per-outcome transactional persistence."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional
from digital_twin.modules.decisions.domain.investment_brain import (
    DecisionEpisode,
    LearningProposal,
    ObservedOutcome,
    canonical_investment_timestamp,
    stable_id,
)
from digital_twin.modules.outcomes.domain.hypothesis_outcome_contract import (
    observation_domain_status,
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
    due_outcome_horizon_minutes,
    number,
    outcome_horizon_minutes,
    outcome_observation_is_usable,
    outcome_target_at,
    parse_datetime,
    selected_hypothesis_payload,
)
from .ports import ConnectionFactory


def record_observation(
    account_id: str,
    symbol: str,
    facts: Dict[str, object],
    observed_at: str = "",
    *,
    _episode_outcome_contract_completeness: Callable[..., Dict[str, object]],
    _episode_outcome_horizons: Callable[..., List[int]],
    _evaluate_follow_up_observation: Callable[..., List[Dict[str, object]]],
    _list: Callable[..., List[DecisionEpisode]],
    _outcome_batch_size: Callable[..., int],
    _record_outcome_observations: Callable[..., List[ObservedOutcome]],
    utc_now_iso: Callable[[], str],
) -> List[ObservedOutcome]:
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return []
    observed_at = (
        canonical_investment_timestamp(observed_at or facts.get("observedAt")) or utc_now_iso()
    )
    transitions = _evaluate_follow_up_observation(account_id, symbol, facts, observed_at)
    if transitions:
        facts["followUpTransitions"] = transitions
        facts["followUpTransitionCount"] = len(transitions)
    if not outcome_observation_is_usable(facts, observed_at):
        return []
    episodes = _list(account_id=account_id, symbol=symbol, limit=_outcome_batch_size())
    requests: List[Dict[str, object]] = []
    for episode in episodes:
        if not _episode_outcome_contract_completeness(episode).get("complete"):
            continue
        outcome_horizon_minutes = due_outcome_horizon_minutes(
            episode,
            observed_at,
            _episode_outcome_horizons(episode),
        )
        if not outcome_horizon_minutes:
            continue
        requests.append(
            {
                "episodeId": episode.episode_id,
                "horizonMinutes": outcome_horizon_minutes,
                "facts": dict(facts or {}),
                "observedAt": observed_at,
            }
        )
    return _record_outcome_observations(account_id, requests)


def record_outcome_observations(
    account_id: str,
    observations: Iterable[Dict[str, object]],
    *,
    _episode_outcome_contract: Callable[..., Dict[str, object]],
    _episode_outcome_contract_completeness: Callable[..., Dict[str, object]],
    _episode_outcome_horizons: Callable[..., List[int]],
    _episode_outcome_max_delay_minutes: Callable[..., int],
    _episodes_by_ids: Callable[..., Dict[str, DecisionEpisode]],
    _propose_learning_from_outcomes: Callable[..., Optional[LearningProposal]],
    _record_shadow_hypothesis_outcome_observations: Callable[..., List[ObservedOutcome]],
    _save_outcome: Callable[..., ObservedOutcome],
) -> List[ObservedOutcome]:
    normalized: List[Dict[str, object]] = []
    for raw in observations or []:
        item = dict(raw or {}) if isinstance(raw, dict) else {}
        episode_id = str(item.get("episodeId") or "").strip()
        try:
            horizon_minutes = int(float(item.get("horizonMinutes") or 0))
        except (TypeError, ValueError):
            horizon_minutes = 0
        facts = dict(item.get("facts") or {})
        observed_at = canonical_investment_timestamp(
            item.get("observedAt") or facts.get("observedAt")
        )
        if (
            not episode_id
            or horizon_minutes <= 0
            or not observed_at
            or not outcome_observation_is_usable(facts, observed_at)
        ):
            continue
        normalized.append(
            {
                "episodeId": episode_id,
                "episodeKind": str(item.get("episodeKind") or "decision"),
                "horizonMinutes": horizon_minutes,
                "facts": facts,
                "observedAt": observed_at,
            }
        )
    if not normalized:
        return []
    decision_observations = [
        item for item in normalized if item.get("episodeKind") != "shadow-hypothesis"
    ]
    shadow_observations = [
        item for item in normalized if item.get("episodeKind") == "shadow-hypothesis"
    ]
    episodes = _episodes_by_ids(item["episodeId"] for item in decision_observations)
    outcomes: List[ObservedOutcome] = []
    changed_symbols = set()
    for item in decision_observations:
        episode = episodes.get(item["episodeId"])
        if not episode or str(episode.account_id or "") != str(account_id or ""):
            continue
        horizon_minutes = int(item["horizonMinutes"])
        contract = _episode_outcome_contract(episode)
        contract_completeness = _episode_outcome_contract_completeness(episode)
        existing = next((outcome for outcome in episode.outcomes if int((outcome.payload or {}).get("horizonMinutes") or 0) == horizon_minutes), None)
        if horizon_minutes not in _episode_outcome_horizons(episode) or (existing and not outcome_needs_data(existing.to_dict())):
            continue
        target_at = outcome_target_at(episode, horizon_minutes)
        observed_at = str(item["observedAt"])
        target_time = parse_datetime(target_at)
        observed_time = parse_datetime(observed_at)
        if not target_time or not observed_time or observed_time < target_time:
            continue
        facts = dict(item["facts"] or {})
        current_price = number(facts.get("currentPrice"))
        decision_price = number(
            facts.get("decisionPrice") or (episode.facts_at_decision or {}).get("currentPrice")
        )
        change_pct = (
            round(((current_price / decision_price) - 1) * 100, 4)
            if current_price and decision_price
            else 0.0
        )
        selected_hypothesis = selected_hypothesis_payload(episode)
        stance = str(selected_hypothesis.get("stance") or "uncertain")
        delay_minutes = max(0.0, (observed_time - target_time).total_seconds() / 60.0)
        contract_observation = observation_domain_status(facts, contract)
        evaluation = evaluate_hypothesis_outcome(
            contract,
            stance,
            facts,
            change_pct,
            horizon_minutes,
        )
        missing_criterion_metrics = list(evaluation.get("missingRequiredMetricIds") or [])
        calibration_eligible = (
            bool(contract_completeness.get("complete"))
            and delay_minutes <= _episode_outcome_max_delay_minutes(episode)
            and not list(contract_observation.get("missingObservationDomains") or [])
            and not missing_criterion_metrics
        )
        eligibility = (
            "eligible"
            if calibration_eligible
            else (
                "excluded-incomplete-prediction-contract"
                if not contract_completeness.get("complete")
                else (
                    "excluded-contract-data-gap"
                    if contract_observation.get("missingObservationDomains")
                    else (
                        "excluded-criterion-data-gap"
                        if missing_criterion_metrics
                        else "excluded-delayed-observation"
                    )
                )
            )
        )
        outcome = ObservedOutcome(
            outcome_id=stable_id("decision-outcome", episode.episode_id, horizon_minutes),
            episode_id=episode.episode_id,
            observed_at=observed_at,
            price=current_price,
            profit_loss_rate=number(facts.get("profitLossRate")),
            price_change_from_decision_pct=change_pct,
            selected_hypothesis_status=str(
                evaluation.get("selectedHypothesisStatus") or "inconclusive"
            ),
            payload={
                "observationFacts": observation_facts(facts),
                "evaluationAttemptCount": int((existing.payload or {}).get("evaluationAttemptCount") or 1) + 1 if existing else 1,
                "selectedHypothesisId": episode.selected_hypothesis_id,
                "selectedHypothesisStance": stance,
                "hypothesisFamilyId": selected_hypothesis.get("familyId") or "",
                "hypothesisTemplateId": selected_hypothesis.get("templateId") or "",
                "predictionTarget": selected_hypothesis.get("predictionTarget") or "",
                "expectedDirection": selected_hypothesis.get("expectedDirection") or "",
                "expectedOutcome": selected_hypothesis.get("expectedOutcome") or "",
                "outcomeMetric": selected_hypothesis.get("outcomeMetric") or "",
                "falsificationContract": selected_hypothesis.get("falsificationContract") or "",
                "inferenceGenerationId": facts.get("inferenceGenerationId") or "",
                "decisionPrice": decision_price,
                "decisionPriceSourceAsOf": facts.get("decisionPriceSourceAsOf") or "",
                "observationBasis": str(
                    facts.get("observationBasis") or "subsequent-market-observation"
                ),
                "observationSource": str(
                    facts.get("observationSource") or facts.get("provider") or ""
                ),
                "sourceAsOf": canonical_investment_timestamp(facts.get("sourceAsOf"))
                or observed_at,
                "dataQuality": str(facts.get("dataQuality") or "unknown"),
                "hypothesisOutcomeContract": contract,
                "contractFingerprint": contract.get("contractFingerprint") or "",
                "marketIndependenceKey": contract.get("marketIndependenceKey") or "",
                "accountIndependenceKey": contract.get("accountIndependenceKey") or "",
                **contract_observation,
                **evaluation,
                "missingRequiredMetricIds": missing_criterion_metrics,
                "benchmarkSymbol": contract_benchmark_symbol(contract, episode.facts_at_decision),
                "benchmarkReturnPct": facts.get("benchmarkReturnPct"),
                "excessReturnPct": (
                    round(change_pct - number(facts.get("benchmarkReturnPct")), 6)
                    if facts.get("benchmarkReturnPct") not in (None, "")
                    else None
                ),
                "benchmarkObservationSource": facts.get("benchmarkObservationSource") or "",
                "benchmarkStartAsOf": facts.get("benchmarkStartAsOf") or "",
                "benchmarkEndAsOf": facts.get("benchmarkEndAsOf") or "",
                "horizonMinutes": horizon_minutes,
                "targetAt": target_at,
                "actualElapsedMinutes": round(
                    (observed_time - parse_datetime(episode.decided_at)).total_seconds() / 60.0, 2
                ),
                "observationDelayMinutes": round(delay_minutes, 2),
                "observationTiming": (
                    "on-time"
                    if delay_minutes <= _episode_outcome_max_delay_minutes(episode)
                    else "delayed"
                ),
                "calibrationEligibility": eligibility,
                "predictionContractCompleteness": contract_completeness,
            },
        )
        saved = _save_outcome(episode, outcome)
        outcomes.append(saved or outcome)
        changed_symbols.add(episode.symbol)
    outcomes.extend(
        _record_shadow_hypothesis_outcome_observations(
            account_id,
            shadow_observations,
        )
    )
    for symbol in sorted(changed_symbols):
        _propose_learning_from_outcomes(account_id, symbol)
    return outcomes


def save_outcome(
    episode: DecisionEpisode,
    outcome: ObservedOutcome,
    *,
    _transaction: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> ObservedOutcome:
    outcome.observed_at = canonical_investment_timestamp(outcome.observed_at) or utc_now_iso()
    payload = outcome.to_dict()
    with _transaction() as connection:
        row = connection.execute(
            "SELECT payload_json FROM investment_decision_outcomes WHERE outcome_id = %s FOR UPDATE",
            (outcome.outcome_id,),
        ).fetchone()
        previous = _json_loads((row or {}).get("payload_json"), {})
        if previous:
            if not outcome_needs_data(previous):
                return ObservedOutcome.from_dict(previous)
            validate_outcome_repair(previous, payload)
            outcome.payload["evaluationHistory"] = outcome_evaluation_history(previous, utc_now_iso())
            payload = outcome.to_dict()
        episode.status = "observed"
        episode.outcomes = [
            item for item in episode.outcomes if item.outcome_id != outcome.outcome_id
        ] + [outcome]
        episode_payload = episode.to_dict()
        connection.execute(
            """
                INSERT INTO investment_decision_outcomes (
                    outcome_id, episode_id, account_id, symbol, observed_at,
                    selected_hypothesis_status, price, profit_loss_rate,
                    price_change_from_decision_pct, payload_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE selected_hypothesis_status = VALUES(selected_hypothesis_status),
                    price = VALUES(price), profit_loss_rate = VALUES(profit_loss_rate),
                    price_change_from_decision_pct = VALUES(price_change_from_decision_pct),
                    payload_json = VALUES(payload_json)
                """,
            (
                outcome.outcome_id,
                outcome.episode_id,
                episode.account_id,
                episode.symbol,
                outcome.observed_at,
                outcome.selected_hypothesis_status,
                outcome.price,
                outcome.profit_loss_rate,
                outcome.price_change_from_decision_pct,
                json_dumps(payload),
                utc_now_iso(),
            ),
        )
        connection.execute(
            "UPDATE investment_decision_episodes SET status = %s, decided_at = %s, payload_json = %s, updated_at = %s WHERE episode_id = %s",
            (
                "observed",
                episode.decided_at,
                json_dumps(episode_payload),
                utc_now_iso(),
                episode.episode_id,
            ),
        )
        horizon_minutes = int((outcome.payload or {}).get("horizonMinutes") or 0)
        fingerprint = str((outcome.payload or {}).get("contractFingerprint") or "")
        connection.execute(
            "UPDATE investment_decision_outcome_targets "
            "SET status = %s, outcome_id = %s, observed_at = %s, updated_at = %s "
            "WHERE episode_id = %s AND horizon_minutes = %s AND contract_fingerprint = %s",
            (
                "needs-data" if outcome_needs_data(payload) else "observed",
                outcome.outcome_id,
                outcome.observed_at,
                utc_now_iso(),
                episode.episode_id,
                horizon_minutes,
                fingerprint,
            ),
        )
    return outcome
