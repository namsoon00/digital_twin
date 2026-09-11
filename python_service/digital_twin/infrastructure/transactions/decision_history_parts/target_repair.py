"""Pending-only outcome schedule migration in its original transaction."""

from __future__ import annotations

from typing import Callable, Dict
from digital_twin.modules.decisions.domain.investment_brain import (
    DecisionEpisode,
    canonical_investment_timestamp,
)
from digital_twin.modules.outcomes.domain.hypothesis_observation import (
    ShadowHypothesisObservationEpisode,
)
from digital_twin.modules.outcomes.domain.hypothesis_outcome_contract import (
    outcome_contract_completeness,
)
from digital_twin.modules.market_data.domain.market_hours import infer_market_from_context
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from .outcome_policy import market_outcome_target_at, number, outcome_target_at
from .ports import ConnectionFactory


def backfill_outcome_targets(
    account_id: str,
    limit: int = 2000,
    *,
    _episode_from_row: Callable[..., DecisionEpisode],
    _sync_outcome_targets: Callable[..., Dict[str, object]],
    _transaction: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> Dict[str, object]:
    """One-time safe migration using only contracts frozen in each episode."""

    account_id = str(account_id or "")
    with _transaction() as connection:
        # This migration check runs from the market-data cycle. Keep its
        # steady-state query index-only: selecting the large episode JSON
        # before proving a target is missing repeatedly read the complete
        # decision archive even after migration had finished.
        candidates = connection.execute(
            "SELECT episodes.episode_id "
            "FROM investment_decision_episodes AS episodes "
            "WHERE episodes.account_id = %s "
            "AND NOT EXISTS ("
            "SELECT 1 FROM investment_decision_outcome_targets AS targets "
            "WHERE targets.episode_id = episodes.episode_id"
            ") ORDER BY episodes.decided_at ASC, episodes.episode_id ASC LIMIT %s",
            (account_id, max(1, min(10000, int(limit or 2000)))),
        ).fetchall()
        episode_ids = [
            str(row.get("episode_id") or "").strip()
            for row in candidates or []
            if str(row.get("episode_id") or "").strip()
        ]
        rows = []
        if episode_ids:
            placeholders = ", ".join(["%s"] * len(episode_ids))
            payload_rows = connection.execute(
                "SELECT episode_id, payload_json, status, decided_at "
                "FROM investment_decision_episodes WHERE episode_id IN (" + placeholders + ")",
                tuple(episode_ids),
            ).fetchall()
            by_id = {str(row.get("episode_id") or ""): row for row in payload_rows or []}
            rows = [by_id[episode_id] for episode_id in episode_ids if episode_id in by_id]
        stamp = utc_now_iso()
        scheduled = 0
        excluded = 0
        for row in rows or []:
            episode = _episode_from_row(row)
            result = _sync_outcome_targets(connection, episode, stamp)
            scheduled += int(result.get("targetCount") or 0)
            excluded += 1 if result.get("status") == "excluded" else 0
    return {
        "status": "backfilled" if rows else "already-initialized",
        "episodeCount": len(rows or []),
        "scheduledTargetCount": scheduled,
        "excludedEpisodeCount": excluded,
    }


def repair_pending_outcome_target_schedules(
    account_id: str,
    limit: int = 1000,
    *,
    _episode_outcome_contract_completeness: Callable[..., Dict[str, object]],
    _transaction: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> Dict[str, object]:
    """Realign valid targets and retire targets with invalid contracts.

    Older decision payloads sometimes omitted market and currency. Their
    short horizon was therefore scheduled during closed hours and a later
    ingestion of the unchanged close could be mistaken for a new quote.
    Older prediction contracts can also lack a criterion for one of their
    scheduled horizons. Only pending targets are mutable; observed and
    excluded audit rows are deliberately left untouched.
    """

    maximum = max(1, min(5000, int(limit or 1000)))
    repaired = []
    excluded = []
    stamp = utc_now_iso()
    with _transaction() as connection:
        rows = connection.execute(
            "SELECT targets.target_id, targets.horizon_minutes, targets.target_at, "
            "targets.payload_json AS target_json, episodes.payload_json AS episode_json, "
            "episodes.status AS episode_status, episodes.decided_at "
            "FROM investment_decision_outcome_targets AS targets "
            "JOIN investment_decision_episodes AS episodes ON episodes.episode_id = targets.episode_id "
            "WHERE targets.account_id = %s AND targets.status = 'pending' "
            "ORDER BY targets.target_at ASC, targets.target_id ASC LIMIT %s",
            (str(account_id or ""), maximum),
        ).fetchall()
        for row in rows or []:
            episode_payload = _json_loads(row.get("episode_json"), {})
            if not episode_payload:
                continue
            episode = DecisionEpisode.from_dict(episode_payload)
            stored_decided_at = canonical_investment_timestamp(row.get("decided_at"))
            if stored_decided_at:
                episode.decided_at = stored_decided_at
            try:
                horizon_minutes = int(float(row.get("horizon_minutes") or 0))
            except (TypeError, ValueError):
                horizon_minutes = 0
            target_payload = _json_loads(row.get("target_json"), {})
            original_payload = dict(target_payload)
            completeness = _episode_outcome_contract_completeness(episode)
            facts = episode.facts_at_decision if isinstance(episode.facts_at_decision, dict) else {}
            calibration = (
                facts.get("calibrationPolicy")
                if isinstance(facts.get("calibrationPolicy"), dict)
                else {}
            )
            if not completeness.get("complete") or calibration.get("eligible") is not True:
                reason = str(
                    "no-selected-hypothesis"
                    if not episode.selected_hypothesis_id
                    else (
                        "outcome-contract-incomplete"
                        if not completeness.get("complete")
                        else calibration.get("reason") or "calibration-ineligible"
                    )
                )[:191]
                target_payload.update(
                    {
                        "status": "excluded",
                        "exclusionReason": reason,
                        "predictionContractCompleteness": completeness,
                    }
                )
                cursor = connection.execute(
                    "UPDATE investment_decision_outcome_targets "
                    "SET status = 'excluded', exclusion_reason = %s, "
                    "payload_json = %s, updated_at = %s "
                    "WHERE target_id = %s AND status = 'pending'",
                    (
                        reason,
                        json_dumps(target_payload),
                        stamp,
                        str(row.get("target_id") or ""),
                    ),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) > 0:
                    excluded.append(str(row.get("target_id") or ""))
                continue
            target_at = outcome_target_at(episode, horizon_minutes)
            if not target_at:
                continue
            target_payload.setdefault("episodeKind", "decision")
            target_payload.setdefault("requiresInstrumentBaseline", True)
            target_payload["targetAt"] = target_at
            fact_delta = facts.get("factDelta") if isinstance(facts.get("factDelta"), dict) else {}
            baseline_at = canonical_investment_timestamp(
                fact_delta.get("source_observed_at")
                or fact_delta.get("sourceObservedAt")
                or facts.get("sourceAsOf")
            )
            if baseline_at:
                target_payload.setdefault("baselineAt", baseline_at)
            decision_price = number(facts.get("currentPrice"))
            if decision_price > 0:
                target_payload.setdefault("decisionPrice", decision_price)
                if facts.get("sourceAsOf"):
                    target_payload.setdefault(
                        "decisionPriceSourceAsOf",
                        facts.get("sourceAsOf"),
                    )
            inferred_market = infer_market_from_context(
                "investmentInsight",
                {
                    "symbol": episode.symbol,
                    "market": target_payload.get("market"),
                    "currency": target_payload.get("currency"),
                },
            )
            if inferred_market and not str(target_payload.get("market") or "").strip():
                target_payload["market"] = inferred_market
            if not str(target_payload.get("currency") or "").strip():
                target_payload["currency"] = (
                    "KRW" if inferred_market == "KR" else "USD" if inferred_market == "US" else ""
                )
            target_changed = target_at != canonical_investment_timestamp(row.get("target_at"))
            if not target_changed and target_payload == original_payload:
                continue
            cursor = connection.execute(
                "UPDATE investment_decision_outcome_targets SET target_at = %s, "
                "payload_json = %s, updated_at = %s "
                "WHERE target_id = %s AND status = 'pending'",
                (
                    target_at,
                    json_dumps(target_payload),
                    stamp,
                    str(row.get("target_id") or ""),
                ),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) > 0:
                repaired.append(str(row.get("target_id") or ""))

        shadow_rows = connection.execute(
            "SELECT targets.target_id, targets.horizon_minutes, targets.target_at, "
            "targets.payload_json AS target_json, episodes.payload_json AS episode_json "
            "FROM investment_hypothesis_observation_targets AS targets "
            "JOIN investment_hypothesis_observation_episodes AS episodes "
            "ON episodes.episode_id = targets.observation_episode_id "
            "WHERE targets.account_id = %s AND targets.status = 'pending' "
            "ORDER BY targets.target_at ASC, targets.target_id ASC LIMIT %s",
            (str(account_id or ""), maximum),
        ).fetchall()
        for row in shadow_rows or []:
            episode_payload = _json_loads(row.get("episode_json"), {})
            episode = ShadowHypothesisObservationEpisode.from_dict(episode_payload)
            target_payload = _json_loads(row.get("target_json"), {})
            original_payload = dict(target_payload)
            completeness = outcome_contract_completeness(episode.outcome_contract)
            if not episode.observation_eligible or not completeness.get("complete"):
                reason = str(
                    "outcome-contract-incomplete"
                    if not completeness.get("complete")
                    else (episode.readiness or {}).get("reason") or "observation-ineligible"
                )[:191]
                target_payload.update(
                    {
                        "status": "excluded",
                        "exclusionReason": reason,
                        "predictionContractCompleteness": completeness,
                    }
                )
                cursor = connection.execute(
                    "UPDATE investment_hypothesis_observation_targets "
                    "SET status = 'excluded', exclusion_reason = %s, "
                    "payload_json = %s, updated_at = %s "
                    "WHERE target_id = %s AND status = 'pending'",
                    (
                        reason,
                        json_dumps(target_payload),
                        stamp,
                        str(row.get("target_id") or ""),
                    ),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) > 0:
                    excluded.append(str(row.get("target_id") or ""))
                continue
            try:
                horizon_minutes = int(float(row.get("horizon_minutes") or 0))
            except (TypeError, ValueError):
                horizon_minutes = 0
            target_at = market_outcome_target_at(
                episode.observed_from_at,
                episode.symbol,
                episode.market,
                episode.currency,
                horizon_minutes,
            )
            if not target_at:
                continue
            target_payload.setdefault("episodeKind", "shadow-hypothesis")
            target_payload.setdefault("requiresInstrumentBaseline", True)
            target_payload.setdefault("baselineAt", episode.observed_from_at)
            target_payload["targetAt"] = target_at
            target_changed = target_at != canonical_investment_timestamp(row.get("target_at"))
            if not target_changed and target_payload == original_payload:
                continue
            cursor = connection.execute(
                "UPDATE investment_hypothesis_observation_targets "
                "SET target_at = %s, payload_json = %s, updated_at = %s "
                "WHERE target_id = %s AND status = 'pending'",
                (
                    target_at,
                    json_dumps(target_payload),
                    stamp,
                    str(row.get("target_id") or ""),
                ),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) > 0:
                repaired.append(str(row.get("target_id") or ""))
    return {
        "status": "repaired" if repaired or excluded else "unchanged",
        "checkedCount": len(rows or []) + len(shadow_rows or []),
        "repairedCount": len(repaired),
        "excludedCount": len(excluded),
        "targetIds": repaired,
        "excludedTargetIds": excluded,
    }
