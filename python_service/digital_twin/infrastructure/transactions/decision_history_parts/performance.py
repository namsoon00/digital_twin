"""Outcome-led, bounded history and calibration read models."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Mapping
from digital_twin.modules.decisions.domain.investment_brain import canonical_investment_timestamp
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from .ports import ConnectionFactory


def performance(
    account_id: str = "",
    symbol: str = "",
    limit: int = 500,
    as_of: str = "",
    *,
    _outcome_coverage_population: Callable[..., Dict[str, int]],
    _performance_archive_episode_count: Callable[..., int],
    _performance_episodes: Callable[..., List[Dict[str, object]]],
    _runtime_settings: Mapping[str, object],
    evaluate_decision_performance: Callable[..., Dict[str, object]],
) -> Dict[str, object]:
    try:
        minimum_samples = int(
            float(str(_runtime_settings.get("investmentBrainPerformanceMinimumSamples") or "5"))
        )
    except ValueError:
        minimum_samples = 5
    episodes = _performance_episodes(
        account_id=account_id,
        symbol=symbol,
        limit=max(1, min(2000, int(limit or 500))),
        as_of=as_of,
    )
    result = evaluate_decision_performance(
        episodes,
        minimum_sample_count=max(2, min(100, minimum_samples)),
    )
    archive_count = _performance_archive_episode_count(account_id, symbol, as_of=as_of)
    evaluated_count = int(result.get("episodeCount") or 0)
    result["evaluatedEpisodeCount"] = evaluated_count
    result["episodeCount"] = archive_count
    coverage_population = _outcome_coverage_population(
        account_id=account_id,
        symbol=symbol,
        as_of=as_of,
    )
    calibration_eligible = int(result.get("calibrationEligibleEpisodeCount") or 0)
    observed_population = max(
        calibration_eligible,
        int(coverage_population.get("observedEpisodeCount") or 0),
    )
    due_unobserved = int(coverage_population.get("dueUnobservedEpisodeCount") or 0)
    coverage_denominator = observed_population + due_unobserved
    result["outcomeCoveragePct"] = (
        round(
            (calibration_eligible / coverage_denominator) * 100,
            2,
        )
        if coverage_denominator
        else 0.0
    )
    result["outcomeCoverageEligibleEpisodeCount"] = calibration_eligible
    result["outcomeCoverageObservedEpisodeCount"] = observed_population
    result["outcomeCoverageDueUnobservedEpisodeCount"] = due_unobserved
    result["outcomeCoveragePopulationEpisodeCount"] = coverage_denominator
    result["outcomeCoverageBasis"] = "eligible-due-outcome-targets-v1"
    result["historySelection"] = "outcome-led-bounded-history"
    return result


def outcome_coverage_population(
    account_id: str = "",
    symbol: str = "",
    as_of: str = "",
    *,
    _connect: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> Dict[str, int]:
    """Count only observable episodes whose outcome contract is in force."""

    cutoff = canonical_investment_timestamp(as_of) or utc_now_iso()

    def counts_for(
        connection,
        target_table: str,
        outcome_table: str,
        episode_column: str,
    ) -> Dict[str, int]:
        scope = []
        params: List[object] = []
        if account_id:
            scope.append("targets.account_id = %s")
            params.append(str(account_id))
        if symbol:
            scope.append("targets.symbol = %s")
            params.append(str(symbol).upper())
        scope_sql = (" AND " + " AND ".join(scope)) if scope else ""
        observed = (
            connection.execute(
                "SELECT COUNT(DISTINCT targets." + episode_column + ") AS count "
                "FROM " + target_table + " AS targets "
                "WHERE targets.status = 'observed' AND targets.observed_at <= %s" + scope_sql,
                tuple([cutoff] + params),
            ).fetchone()
            or {}
        )
        due = (
            connection.execute(
                "SELECT COUNT(DISTINCT targets." + episode_column + ") AS count "
                "FROM " + target_table + " AS targets "
                "WHERE targets.status = 'pending' AND targets.target_at <= %s"
                + scope_sql
                + " AND NOT EXISTS (SELECT 1 FROM "
                + outcome_table
                + " AS outcomes "
                "WHERE outcomes." + episode_column + " = targets." + episode_column + ")",
                tuple([cutoff] + params),
            ).fetchone()
            or {}
        )
        return {
            "observed": int(observed.get("count") or 0),
            "due": int(due.get("count") or 0),
        }

    with _connect() as connection:
        decision = counts_for(
            connection,
            "investment_decision_outcome_targets",
            "investment_decision_outcomes",
            "episode_id",
        )
        shadow = counts_for(
            connection,
            "investment_hypothesis_observation_targets",
            "investment_hypothesis_observation_outcomes",
            "observation_episode_id",
        )
    return {
        "observedEpisodeCount": decision["observed"] + shadow["observed"],
        "dueUnobservedEpisodeCount": decision["due"] + shadow["due"],
    }


def performance_archive_episode_count(
    account_id: str = "",
    symbol: str = "",
    as_of: str = "",
    *,
    _connect: ConnectionFactory,
) -> int:
    clauses = []
    params: List[object] = []
    if account_id:
        clauses.append("account_id = %s")
        params.append(str(account_id))
    if symbol:
        clauses.append("symbol = %s")
        params.append(str(symbol).upper())
    normalized_as_of = canonical_investment_timestamp(as_of)
    if normalized_as_of:
        clauses.append("decided_at <= %s")
        params.append(normalized_as_of)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect() as connection:
        row = (
            connection.execute(
                "SELECT COUNT(*) AS count FROM investment_decision_episodes" + where,
                tuple(params),
            ).fetchone()
            or {}
        )
        shadow_row = (
            connection.execute(
                "SELECT COUNT(*) AS count FROM investment_hypothesis_observation_episodes"
                + where.replace("decided_at", "observed_from_at"),
                tuple(params),
            ).fetchone()
            or {}
        )
    return max(
        0,
        int(row.get("count") or 0) + int(shadow_row.get("count") or 0),
    )


def performance_episodes(
    account_id: str = "",
    symbol: str = "",
    limit: int = 500,
    as_of: str = "",
    *,
    _connect: ConnectionFactory,
    _runtime_settings: Mapping[str, object],
    _shadow_performance_episodes: Callable[..., List[Dict[str, object]]],
) -> List[Dict[str, object]]:
    """Load a bounded outcome-led history for performance calibration.

    The decision table grows much faster than its delayed outcome table.
    Reading only the latest decision rows eventually hides every observed
    result and resets all hypothesis qualification to ``shadow``. Outcome
    metrics do not need pending decisions or mutable follow-up rows, so
    load only episodes that own an observation and obtain the archive
    denominator with a separate count query.
    """

    clauses = []
    params: List[object] = []
    if account_id:
        clauses.append("account_id = %s")
        params.append(str(account_id))
    if symbol:
        clauses.append("symbol = %s")
        params.append(str(symbol).upper())
    normalized_as_of = canonical_investment_timestamp(as_of)
    if normalized_as_of:
        clauses.append("observed_at <= %s")
        params.append(normalized_as_of)
    try:
        outcome_limit = int(
            float(
                str(
                    _runtime_settings.get("investmentBrainPerformanceOutcomeEpisodeLimit") or "2000"
                )
            )
        )
    except (TypeError, ValueError):
        outcome_limit = 2000
    requested_limit = max(1, min(5000, int(limit or 500)))
    outcome_limit = max(1, min(5000, outcome_limit, requested_limit))
    params.append(outcome_limit)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    outer_where = " WHERE outcomes.observed_at <= %s" if normalized_as_of else ""
    if normalized_as_of:
        params.append(normalized_as_of)
    with _connect() as connection:
        rows = connection.execute(
            "SELECT outcomes.episode_id, outcomes.observed_at, outcomes.payload_json AS outcome_json, "
            "episodes.account_id, episodes.symbol, episodes.subject_name, episodes.action, "
            "episodes.selected_hypothesis_id, episodes.decided_at, "
            "JSON_EXTRACT(episodes.payload_json, '$.hypothesisSet.hypotheses') AS hypotheses_json "
            "FROM investment_decision_outcomes AS outcomes JOIN ("
            "SELECT episode_id, MAX(observed_at) AS latest_observed_at "
            "FROM investment_decision_outcomes"
            + where
            + " GROUP BY episode_id ORDER BY latest_observed_at DESC LIMIT %s"
            ") AS selected ON selected.episode_id = outcomes.episode_id "
            "JOIN investment_decision_episodes AS episodes ON episodes.episode_id = outcomes.episode_id "
            + outer_where
            + " "
            "ORDER BY selected.latest_observed_at DESC, outcomes.observed_at ASC, outcomes.outcome_id ASC",
            tuple(params),
        ).fetchall()
    grouped: Dict[str, Dict[str, object]] = {}
    for row in rows or []:
        episode_id = str(row.get("episode_id") or "").strip()
        outcome = _json_loads(row.get("outcome_json"), {})
        if not episode_id or not outcome:
            continue
        if not outcome.get("observedAt"):
            outcome["observedAt"] = canonical_investment_timestamp(row.get("observed_at"))
        payload = outcome.get("payload") if isinstance(outcome.get("payload"), dict) else {}
        contract = (
            payload.get("hypothesisOutcomeContract")
            if isinstance(payload.get("hypothesisOutcomeContract"), dict)
            else {}
        )
        selected_hypothesis_id = str(
            payload.get("selectedHypothesisId") or row.get("selected_hypothesis_id") or ""
        )
        original_hypotheses = _json_loads(row.get("hypotheses_json"), [])
        original_hypothesis = (
            next(
                (
                    item
                    for item in original_hypotheses
                    if isinstance(item, dict)
                    and str(item.get("hypothesisId") or "") == selected_hypothesis_id
                ),
                {},
            )
            if isinstance(original_hypotheses, list)
            else {}
        )
        episode = grouped.setdefault(
            episode_id,
            {
                "episodeId": episode_id,
                "accountId": str(row.get("account_id") or ""),
                "symbol": str(row.get("symbol") or "").upper(),
                "subjectName": str(row.get("subject_name") or row.get("symbol") or ""),
                "action": str(row.get("action") or "HOLD").upper(),
                "selectedHypothesisId": selected_hypothesis_id,
                "decidedAt": canonical_investment_timestamp(row.get("decided_at")),
                "hypothesisSet": {
                    "hypotheses": [
                        {
                            "claimContract": dict(original_hypothesis.get("claimContract") or {}),
                            "hypothesisId": selected_hypothesis_id,
                            "templateId": str(payload.get("hypothesisTemplateId") or ""),
                            "templateLabel": str(payload.get("hypothesisTemplateLabel") or ""),
                            "familyId": str(payload.get("hypothesisFamilyId") or ""),
                            "stance": str(payload.get("selectedHypothesisStance") or "uncertain"),
                            "predictionTarget": str(
                                payload.get("predictionTarget")
                                or contract.get("predictionTarget")
                                or ""
                            ),
                            "expectedDirection": str(
                                payload.get("expectedDirection")
                                or contract.get("expectedDirection")
                                or ""
                            ),
                            "expectedOutcome": str(
                                payload.get("expectedOutcome")
                                or contract.get("expectedOutcome")
                                or ""
                            ),
                            "outcomeMetric": str(
                                payload.get("outcomeMetric") or contract.get("outcomeMetric") or ""
                            ),
                            "falsificationContract": str(
                                payload.get("falsificationContract")
                                or contract.get("falsificationContract")
                                or ""
                            ),
                            "supportingRuleIds": list(contract.get("sourceRuleIds") or []),
                        }
                    ],
                },
                "factsAtDecision": {"hypothesisOutcomeContract": contract},
                "outcomes": [],
            },
        )
        episode["outcomes"].append(outcome)
    combined = list(grouped.values())
    combined.extend(
        _shadow_performance_episodes(
            account_id=account_id,
            symbol=symbol,
            limit=outcome_limit,
            as_of=as_of,
        )
    )

    def latest_observation(item: Dict[str, object]) -> str:
        return max(
            (
                str(outcome.get("observedAt") or "")
                for outcome in item.get("outcomes") or []
                if isinstance(outcome, dict)
            ),
            default="",
        )

    return sorted(
        combined,
        key=lambda item: (
            latest_observation(item),
            str(item.get("episodeId") or ""),
        ),
        reverse=True,
    )[:outcome_limit]


def shadow_performance_episodes(
    account_id: str = "",
    symbol: str = "",
    limit: int = 500,
    as_of: str = "",
    *,
    _connect: ConnectionFactory,
) -> List[Dict[str, object]]:
    clauses = []
    params: List[object] = []
    if account_id:
        clauses.append("episodes.account_id = %s")
        params.append(str(account_id))
    if symbol:
        clauses.append("episodes.symbol = %s")
        params.append(str(symbol).upper())
    normalized_as_of = canonical_investment_timestamp(as_of)
    if normalized_as_of:
        clauses.append("outcomes.observed_at <= %s")
        params.append(normalized_as_of)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(max(1, min(5000, int(limit or 500))))
    with _connect() as connection:
        rows = connection.execute(
            "SELECT outcomes.observation_episode_id AS episode_id, "
            "outcomes.observed_at, outcomes.payload_json AS outcome_json, "
            "episodes.payload_json AS episode_json "
            "FROM investment_hypothesis_observation_outcomes AS outcomes "
            "JOIN investment_hypothesis_observation_episodes AS episodes "
            "ON episodes.episode_id = outcomes.observation_episode_id"
            + where
            + " ORDER BY outcomes.observed_at DESC, outcomes.outcome_id DESC LIMIT %s",
            tuple(params),
        ).fetchall()
    grouped: Dict[str, Dict[str, object]] = {}
    for row in rows or []:
        episode_payload = _json_loads(row.get("episode_json"), {})
        outcome = _json_loads(row.get("outcome_json"), {})
        episode_id = str(row.get("episode_id") or episode_payload.get("episodeId") or "").strip()
        if not episode_id or not episode_payload or not outcome:
            continue
        if not outcome.get("observedAt"):
            outcome["observedAt"] = canonical_investment_timestamp(row.get("observed_at"))
        hypothesis = dict(episode_payload.get("hypothesis") or {})
        contract = dict(episode_payload.get("outcomeContract") or {})
        item = grouped.setdefault(
            episode_id,
            {
                "episodeId": episode_id,
                "episodeKind": "shadow-hypothesis",
                "accountId": str(episode_payload.get("accountId") or ""),
                "symbol": str(episode_payload.get("symbol") or "").upper(),
                "subjectName": str(episode_payload.get("symbol") or ""),
                "action": str(episode_payload.get("candidateAction") or "HOLD").upper(),
                "selectedHypothesisId": str(episode_payload.get("hypothesisId") or ""),
                "decidedAt": canonical_investment_timestamp(episode_payload.get("observedFromAt")),
                "hypothesisSet": {"hypotheses": [hypothesis]},
                "factsAtDecision": {"hypothesisOutcomeContract": contract},
                "outcomes": [],
            },
        )
        item["outcomes"].append(outcome)
    return list(grouped.values())


def outcome_history_for_symbols(
    symbols: Iterable[str],
    account_id: str = "",
    as_of: str = "",
    limit_per_symbol: int = 120,
    maximum_episode_count: int = 600,
    *,
    _performance_episodes: Callable[..., List[Dict[str, object]]],
) -> List[Dict[str, object]]:
    """Load compact, point-in-time outcome history for ABox calibration.

    Recent decision memory and historical calibration have different
    retention needs. This read keeps enough independent result episodes
    for qualification while returning no prompts, research documents, or
    mutable follow-up state from the original decision payload.
    """

    clean_symbols = list(
        dict.fromkeys(
            str(item or "").upper().strip() for item in symbols or [] if str(item or "").strip()
        )
    )[:24]
    if not clean_symbols:
        return []
    per_symbol = max(3, min(500, int(limit_per_symbol or 120)))
    maximum = max(3, min(2000, int(maximum_episode_count or 600)))
    rows: List[Dict[str, object]] = []
    for clean_symbol in clean_symbols:
        rows.extend(
            _performance_episodes(
                account_id=account_id,
                symbol=clean_symbol,
                limit=per_symbol,
                as_of=as_of,
            )
        )

    def latest_observed_at(item: Dict[str, object]) -> str:
        return max(
            (
                str(outcome.get("observedAt") or "")
                for outcome in item.get("outcomes") or []
                if isinstance(outcome, dict)
            ),
            default="",
        )

    return sorted(
        rows,
        key=lambda item: (latest_observed_at(item), str(item.get("episodeId") or "")),
        reverse=True,
    )[:maximum]
