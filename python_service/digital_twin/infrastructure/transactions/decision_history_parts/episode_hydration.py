"""Row decoding and bounded joins of mutable outcome and follow-up state."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List
from digital_twin.modules.decisions.domain.investment_brain import (
    DecisionEpisode,
    ObservedOutcome,
    canonical_investment_timestamp,
)
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from .outcome_policy import number
from .ports import ConnectionFactory


def episode_from_row(row: Dict[str, object]) -> DecisionEpisode:
    episode = DecisionEpisode.from_dict(_json_loads(row.get("payload_json"), {}))
    stored_status = str(row.get("status") or "").strip()
    stored_decided_at = canonical_investment_timestamp(row.get("decided_at"))
    if stored_status:
        episode.status = stored_status
    if stored_decided_at:
        episode.decided_at = stored_decided_at
    else:
        episode.decided_at = (
            canonical_investment_timestamp(episode.decided_at) or episode.decided_at
        )
    return episode


def outcomes_from_rows(
    rows: Iterable[Dict[str, object]], default_episode_id: str = ""
) -> List[ObservedOutcome]:
    outcomes: List[ObservedOutcome] = []
    for row in rows or []:
        item = _json_loads(row.get("payload_json"), {})
        if not item:
            continue
        outcomes.append(
            ObservedOutcome(
                outcome_id=str(item.get("outcomeId") or ""),
                episode_id=str(
                    item.get("episodeId") or row.get("episode_id") or default_episode_id
                ),
                observed_at=canonical_investment_timestamp(
                    item.get("observedAt") or row.get("observed_at")
                )
                or str(item.get("observedAt") or ""),
                price=number(item.get("price")),
                profit_loss_rate=number(item.get("profitLossRate")),
                price_change_from_decision_pct=number(item.get("priceChangeFromDecisionPct")),
                selected_hypothesis_status=str(item.get("selectedHypothesisStatus") or "pending"),
                contradicted_evidence_ids=list(item.get("contradictedEvidenceIds") or []),
                payload=dict(item.get("payload") or {}),
            )
        )
    return outcomes


def hydrate_outcomes(
    episodes: Iterable[DecisionEpisode],
    as_of: str = "",
    *,
    _connect: ConnectionFactory,
    _hydrate_follow_ups: Callable[..., List[DecisionEpisode]],
    _outcomes_from_rows: Callable[..., List[ObservedOutcome]],
) -> List[DecisionEpisode]:
    result = _hydrate_follow_ups(episodes, as_of=as_of)
    episode_ids = [item.episode_id for item in result if item.episode_id]
    if not episode_ids:
        return result
    placeholders = ",".join(["%s"] * len(episode_ids))
    normalized_as_of = canonical_investment_timestamp(as_of)
    params: List[object] = list(episode_ids)
    cutoff = " AND observed_at <= %s" if normalized_as_of else ""
    if normalized_as_of:
        params.append(normalized_as_of)
    with _connect() as connection:
        rows = connection.execute(
            "SELECT episode_id, observed_at, payload_json FROM investment_decision_outcomes "
            "WHERE episode_id IN (" + placeholders + ") " + cutoff + " "
            "ORDER BY observed_at ASC, outcome_id ASC",
            tuple(params),
        ).fetchall()
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for row in rows or []:
        grouped.setdefault(str(row.get("episode_id") or ""), []).append(row)
    for episode in result:
        episode.outcomes = _outcomes_from_rows(
            grouped.get(episode.episode_id, []), episode.episode_id
        )
    return result


def hydrate_follow_ups(
    episodes: Iterable[DecisionEpisode],
    as_of: str = "",
    *,
    _connect: ConnectionFactory,
) -> List[DecisionEpisode]:
    """Merge mutable follow-up state into immutable decision payloads.

    DecisionEpisode keeps the facts and AI answer at decision time. Follow-up
    status changes later, so the normalized table is authoritative for that
    small mutable slice and is joined only for the bounded episodes being
    projected or reviewed.
    """

    result = list(episodes or [])
    episode_ids = [item.episode_id for item in result if item.episode_id]
    if not episode_ids:
        return result
    placeholders = ",".join(["%s"] * len(episode_ids))
    normalized_as_of = canonical_investment_timestamp(as_of)
    params: List[object] = list(episode_ids)
    cutoff = " AND created_at <= %s" if normalized_as_of else ""
    if normalized_as_of:
        params.append(normalized_as_of)
    with _connect() as connection:
        rows = connection.execute(
            "SELECT episode_id, observable, payload_json "
            "FROM investment_decision_follow_ups WHERE episode_id IN ("
            + placeholders
            + ") "
            + cutoff
            + " "
            "ORDER BY created_at ASC, condition_id ASC",
            tuple(params),
        ).fetchall()
    grouped: Dict[str, Dict[str, List[Dict[str, object]]]] = {}
    for row in rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        if not payload:
            continue
        buckets = grouped.setdefault(
            str(row.get("episode_id") or ""),
            {"tracked": [], "unsupported": []},
        )
        key = "tracked" if bool(row.get("observable")) else "unsupported"
        if str(payload.get("status") or "") in {"satisfied", "invalidated", "expired"} and not bool(
            payload.get("transitionVerified")
        ):
            payload["legacyTransitionState"] = "unverified"
        buckets[key].append(payload)
    for episode in result:
        buckets = grouped.get(episode.episode_id)
        if not buckets:
            continue
        episode.follow_up_conditions = list(buckets["tracked"])
        episode.unsupported_follow_ups = list(buckets["unsupported"])
    return result


def outcomes_for_episode(
    episode_id: str,
    limit: int = 30,
    *,
    _connect: ConnectionFactory,
    _outcomes_from_rows: Callable[..., List[ObservedOutcome]],
) -> List[ObservedOutcome]:
    with _connect() as connection:
        rows = connection.execute(
            """
                SELECT payload_json FROM investment_decision_outcomes
                WHERE episode_id = %s
                ORDER BY observed_at ASC, outcome_id ASC
                LIMIT %s
                """,
            (str(episode_id or ""), max(1, min(200, int(limit or 30)))),
        ).fetchall()
    return _outcomes_from_rows(rows, str(episode_id or ""))
