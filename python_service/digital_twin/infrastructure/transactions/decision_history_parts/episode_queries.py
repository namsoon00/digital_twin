"""Bounded decision-history reads; no write or runtime capabilities."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional
from digital_twin.modules.decisions.domain.investment_brain import (
    DecisionEpisode,
    canonical_investment_timestamp,
)
from digital_twin.modules.decisions.domain.investment_decision_history import (
    compact_decision_episode_memory,
)
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from .ports import ConnectionFactory


def episodes_by_ids(
    episode_ids: Iterable[str],
    *,
    _connect: ConnectionFactory,
    _episode_from_row: Callable[..., DecisionEpisode],
    _hydrate_outcomes: Callable[..., List[DecisionEpisode]],
) -> Dict[str, DecisionEpisode]:
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
            "SELECT payload_json, status, decided_at FROM investment_decision_episodes "
            "WHERE episode_id IN (" + placeholders + ")",
            clean_ids,
        ).fetchall()
    episodes = _hydrate_outcomes(_episode_from_row(row) for row in rows or [])
    return {item.episode_id: item for item in episodes if item.episode_id}


def get_episode(
    episode_id: str,
    *,
    _connect: ConnectionFactory,
    _episode_from_row: Callable[..., DecisionEpisode],
    _hydrate_outcomes: Callable[..., List[DecisionEpisode]],
) -> Optional[DecisionEpisode]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload_json, status, decided_at FROM investment_decision_episodes WHERE episode_id = %s",
            (str(episode_id or ""),),
        ).fetchone()
    if not row:
        return None
    return _hydrate_outcomes([_episode_from_row(row)])[0]


def list_episodes(
    account_id: str = "",
    symbol: str = "",
    limit: int = 50,
    *,
    _connect: ConnectionFactory,
    _episode_from_row: Callable[..., DecisionEpisode],
    _hydrate_outcomes: Callable[..., List[DecisionEpisode]],
) -> List[DecisionEpisode]:
    where = []
    params: List[object] = []
    if account_id:
        where.append("account_id = %s")
        params.append(str(account_id))
    if symbol:
        where.append("symbol = %s")
        params.append(str(symbol).upper())
    params.append(max(1, min(2000, int(limit or 50))))
    sql = "SELECT payload_json, status, decided_at FROM investment_decision_episodes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY decided_at DESC, episode_id DESC LIMIT %s"
    with _connect() as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
    return _hydrate_outcomes(_episode_from_row(row) for row in rows or [])


def list_summaries(
    account_id: str = "",
    symbol: str = "",
    limit: int = 50,
    *,
    _connect: ConnectionFactory,
) -> List[Dict[str, object]]:
    where = []
    params: List[object] = []
    if account_id:
        where.append("account_id = %s")
        params.append(str(account_id))
    if symbol:
        where.append("symbol = %s")
        params.append(str(symbol).upper())
    params.append(max(1, min(500, int(limit or 50))))
    sql = (
        "SELECT episode_id, account_id, symbol, subject_name, question_id, selected_hypothesis_id, "
        "action, review_level, data_state, validation_state, inference_generation_id, status, "
        "decided_at, source, updated_at FROM investment_decision_episodes"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY decided_at DESC, episode_id DESC LIMIT %s"
    with _connect() as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
    return [
        {
            "episodeId": str(row.get("episode_id") or ""),
            "accountId": str(row.get("account_id") or ""),
            "symbol": str(row.get("symbol") or "").upper(),
            "subjectName": str(row.get("subject_name") or row.get("symbol") or ""),
            "questionId": str(row.get("question_id") or ""),
            "selectedHypothesisId": str(row.get("selected_hypothesis_id") or ""),
            "action": str(row.get("action") or "HOLD"),
            "reviewLevel": str(row.get("review_level") or "check"),
            "dataState": str(row.get("data_state") or "partial"),
            "validationState": str(row.get("validation_state") or "conditional"),
            "inferenceGenerationId": str(row.get("inference_generation_id") or ""),
            "status": str(row.get("status") or "active"),
            "decidedAt": canonical_investment_timestamp(row.get("decided_at")),
            "source": str(row.get("source") or ""),
            "updatedAt": str(row.get("updated_at") or ""),
            "detailRequired": True,
        }
        for row in rows or []
    ]


def list_flow_heads(
    account_id: str = "",
    symbol: str = "",
    limit: int = 200,
    *,
    _connect: ConnectionFactory,
) -> List[Dict[str, object]]:
    """Return one compact current decision per account instrument."""

    clauses = []
    params: List[object] = []
    if account_id:
        clauses.append("current.account_id = %s")
        params.append(str(account_id))
    if symbol:
        clauses.append("current.symbol = %s")
        params.append(str(symbol).upper())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(max(1, min(500, int(limit or 200))))
    sql = (
        "SELECT current.flow_id, current.account_id, current.symbol, current.updated_at AS flow_updated_at, "
        "episodes.payload_json, episodes.status, episodes.decided_at "
        "FROM investment_flow_current AS current JOIN investment_decision_episodes AS episodes "
        "ON episodes.episode_id = current.decision_episode_id "
        + where
        + " ORDER BY current.updated_at DESC, current.flow_id DESC LIMIT %s"
    )
    with _connect() as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
    result = []
    for row in rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        if not isinstance(payload, dict):
            continue
        payload = dict(payload)
        payload["flowId"] = str(row.get("flow_id") or payload.get("flowId") or "")
        payload["accountId"] = str(row.get("account_id") or payload.get("accountId") or "")
        payload["symbol"] = str(row.get("symbol") or payload.get("symbol") or "").upper()
        payload["status"] = str(row.get("status") or payload.get("status") or "active")
        payload["decidedAt"] = canonical_investment_timestamp(row.get("decided_at")) or str(
            payload.get("decidedAt") or ""
        )
        payload["updatedAt"] = str(
            row.get("flow_updated_at") or payload.get("updatedAt") or payload.get("decidedAt") or ""
        )
        result.append(payload)
    return result


def latest_decision_memory(
    account_id: str,
    symbol: str,
    exclude_episode_id: str = "",
    *,
    _connect: ConnectionFactory,
) -> Dict[str, object]:
    """Read the compact prior decision used by the next notification AI run.

    Notification continuity needs one valid prior action and its audit
    identity, not outcome history. A bounded payload scan skips legacy
    executable opinions that cannot reproduce the current actionability
    contract without hydrating the heavier learning model.
    """

    where = [
        "account_id = %s",
        "symbol = %s",
        "action IN ('BUY', 'ADD', 'HOLD', 'TRIM', 'SELL', 'AVOID', 'WATCH')",
        "selected_hypothesis_id <> ''",
        "status NOT IN ('blocked', 'failed', 'expired', 'suppressed', 'superseded', 'reference-only', 'invalid-legacy-outcome')",
        "validation_state NOT IN ('blocked', 'invalid', 'failed', 'error')",
    ]
    params: List[object] = [str(account_id or ""), str(symbol or "").upper()]
    if str(exclude_episode_id or "").strip():
        where.append("episode_id <> %s")
        params.append(str(exclude_episode_id).strip())
    with _connect() as connection:
        rows = connection.execute(
            "SELECT episode_id, account_id, symbol, subject_name, selected_hypothesis_id, "
            "action, review_level, data_state, validation_state, inference_generation_id, "
            "status, decided_at, source, payload_json "
            "FROM investment_decision_episodes WHERE "
            + " AND ".join(where)
            + " ORDER BY decided_at DESC, episode_id DESC LIMIT 12",
            tuple(params),
        ).fetchall()
    for row in rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        payload.update(
            {
                "episodeId": str(row.get("episode_id") or ""),
                "accountId": str(row.get("account_id") or ""),
                "symbol": str(row.get("symbol") or "").upper(),
                "subjectName": str(row.get("subject_name") or ""),
                "selectedHypothesisId": str(row.get("selected_hypothesis_id") or ""),
                "action": str(row.get("action") or "").upper(),
                "reviewLevel": str(row.get("review_level") or ""),
                "dataState": str(row.get("data_state") or ""),
                "validationState": str(row.get("validation_state") or ""),
                "inferenceGenerationId": str(row.get("inference_generation_id") or ""),
                "status": str(row.get("status") or ""),
                "decidedAt": (
                    canonical_investment_timestamp(row.get("decided_at"))
                    or str(row.get("decided_at") or "")
                ),
                "source": str(row.get("source") or ""),
            }
        )
        memory = compact_decision_episode_memory(payload)
        if memory:
            return memory
    return {}


def list_for_symbols(
    symbols: Iterable[str],
    account_id: str = "",
    limit_per_symbol: int = 20,
    as_of: str = "",
    *,
    _connect: ConnectionFactory,
    _episode_from_row: Callable[..., DecisionEpisode],
    _hydrate_outcomes: Callable[..., List[DecisionEpisode]],
) -> List[DecisionEpisode]:
    """Fetch bounded episode history for many symbols without N+1 reads.

    A workspace can include many holdings and market hypotheses.  A union
    keeps the latest bounded history per symbol in a small number of
    database round trips instead of issuing one query for every card.
    """
    clean_symbols = list(
        dict.fromkeys(
            str(item or "").upper().strip() for item in symbols or [] if str(item or "").strip()
        )
    )[:120]
    if not clean_symbols:
        return []
    per_symbol = max(1, min(80, int(limit_per_symbol or 20)))
    normalized_as_of = canonical_investment_timestamp(as_of)
    rows: List[Dict[str, object]] = []
    # Keep a generated query below operational limits for large accounts.
    for offset in range(0, len(clean_symbols), 24):
        chunk = clean_symbols[offset : offset + 24]
        statements = []
        params: List[object] = []
        for symbol in chunk:
            where = "symbol = %s"
            statement_params: List[object] = [symbol]
            if account_id:
                where += " AND account_id = %s"
                statement_params.append(str(account_id))
            if normalized_as_of:
                where += " AND decided_at <= %s"
                statement_params.append(normalized_as_of)
            statements.append(
                "(SELECT payload_json, status, decided_at FROM investment_decision_episodes "
                "WHERE " + where + " ORDER BY decided_at DESC, episode_id DESC LIMIT %s)"
            )
            params.extend(statement_params)
            params.append(per_symbol)
        sql = " UNION ALL ".join(statements)
        with _connect() as connection:
            rows.extend(connection.execute(sql, tuple(params)).fetchall() or [])
    return _hydrate_outcomes(
        (_episode_from_row(row) for row in rows),
        as_of=normalized_as_of,
    )
