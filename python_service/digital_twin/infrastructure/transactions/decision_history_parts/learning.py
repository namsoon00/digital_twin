"""Review-only learning proposals from persisted outcome history."""

from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional
from digital_twin.modules.decisions.domain.investment_brain import LearningProposal, stable_id
from digital_twin.modules.outcomes.domain.decision_performance import (
    contradiction_learning_candidates,
)
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from .outcome_policy import outcome_is_calibration_eligible
from .ports import ConnectionFactory


def propose_learning_from_outcomes(
    account_id: str,
    symbol: str,
    *,
    _connect: ConnectionFactory,
    _runtime_settings: Mapping[str, object],
    _save_learning_proposal: Callable[..., LearningProposal],
) -> Optional[LearningProposal]:
    try:
        minimum = int(
            float(str(_runtime_settings.get("investmentBrainLearningMinContradictions") or "3"))
        )
    except ValueError:
        minimum = 3
    minimum = max(2, min(20, minimum))
    with _connect() as connection:
        rows = connection.execute(
            """
                SELECT e.payload_json AS episode_json, o.payload_json AS outcome_json
                FROM investment_decision_outcomes o
                JOIN investment_decision_episodes e ON e.episode_id = o.episode_id
                WHERE e.account_id = %s AND e.symbol = %s
                  AND o.selected_hypothesis_status = 'directionally-contradicted'
                ORDER BY o.observed_at DESC
                LIMIT %s
                """,
            (str(account_id or ""), str(symbol or "").upper(), min(200, minimum * 10)),
        ).fetchall()
    episode_rows = []
    for row in rows or []:
        outcome_payload = _json_loads(row.get("outcome_json"), {})
        if not outcome_is_calibration_eligible(outcome_payload):
            continue
        episode_payload = _json_loads(row.get("episode_json"), {})
        if not str(episode_payload.get("episodeId") or ""):
            continue
        episode_payload["outcomes"] = [outcome_payload]
        episode_rows.append(episode_payload)
    candidates = contradiction_learning_candidates(episode_rows, minimum)
    if not candidates:
        return None
    candidate = candidates[0]
    episode_ids = list(candidate.get("sourceEpisodeIds") or [])
    rule_ids = list(candidate.get("affectedRuleIds") or [])
    family_label = str(candidate.get("templateLabel") or candidate.get("familyId") or "선택 가설")
    horizon_minutes = int(candidate.get("horizonMinutes") or 0)
    proposal = LearningProposal(
        proposal_id=stable_id(
            "learning-proposal",
            account_id,
            symbol,
            str(candidate.get("groupKey") or ""),
            ",".join(episode_ids),
        ),
        title=str(symbol or "") + " " + family_label + " 반복 반증 검토",
        reason=(
            "동일 가설 가족군·동일 관찰 기간의 서로 독립된 최근 사건 "
            + str(candidate.get("contradictedCount") or minimum)
            + "건에서 계약 기반 사후 관측이 반복 반증됐습니다. 원천 데이터와 가설 기준을 재검토해야 합니다."
        ),
        source_episode_ids=episode_ids,
        affected_rule_ids=rule_ids,
        proposed_change={
            "changeType": "review-hypothesis-prior-and-evidence-coverage",
            "familyId": candidate.get("familyId"),
            "templateId": candidate.get("templateId"),
            "predictionTarget": candidate.get("predictionTarget"),
            "expectedDirection": candidate.get("expectedDirection"),
            "expectedOutcome": candidate.get("expectedOutcome"),
            "outcomeMetric": candidate.get("outcomeMetric"),
            "falsificationContract": candidate.get("falsificationContract"),
            "horizonMinutes": horizon_minutes,
            "contradictedCount": candidate.get("contradictedCount"),
            "automaticDeployment": False,
            "requiredValidation": ["historical-replay", "TypeDB-rule-preview", "human-approval"],
        },
    )
    return _save_learning_proposal(proposal)


def save_learning_proposal(
    proposal: LearningProposal,
    *,
    _connect: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> LearningProposal:
    stamp = utc_now_iso()
    with _connect() as connection:
        connection.execute(
            """
                INSERT INTO investment_learning_proposals (
                    proposal_id, status, title, reason, affected_rule_ids_json,
                    source_episode_ids_json, payload_json, created_at, updated_at,
                    reviewed_at, review_note
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '', '')
                ON DUPLICATE KEY UPDATE title = VALUES(title), reason = VALUES(reason),
                    affected_rule_ids_json = VALUES(affected_rule_ids_json),
                    source_episode_ids_json = VALUES(source_episode_ids_json),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
            (
                proposal.proposal_id,
                proposal.status,
                proposal.title,
                proposal.reason,
                json_dumps(proposal.affected_rule_ids),
                json_dumps(proposal.source_episode_ids),
                json_dumps(proposal.to_dict()),
                proposal.created_at,
                stamp,
            ),
        )
    return proposal


def list_learning_proposals(
    status: str = "",
    limit: int = 50,
    *,
    _connect: ConnectionFactory,
) -> List[Dict[str, object]]:
    params: List[object] = []
    sql = "SELECT payload_json, status, reviewed_at, review_note FROM investment_learning_proposals"
    if status:
        sql += " WHERE status = %s"
        params.append(str(status))
    sql += " ORDER BY updated_at DESC, proposal_id DESC LIMIT %s"
    params.append(max(1, min(500, int(limit or 50))))
    with _connect() as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
    results = []
    for row in rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        payload["status"] = row.get("status") or payload.get("status")
        payload["reviewedAt"] = row.get("reviewed_at") or ""
        payload["reviewNote"] = row.get("review_note") or ""
        results.append(payload)
    return results


def review_learning_proposal(
    proposal_id: str,
    status: str,
    note: str = "",
    *,
    _connect: ConnectionFactory,
    _list_learning_proposals: Callable[..., List[Dict[str, object]]],
    utc_now_iso: Callable[[], str],
) -> Dict[str, object]:
    status = str(status or "").strip().lower()
    if status not in {"approved", "rejected", "review-required"}:
        raise ValueError("학습 제안 상태는 approved, rejected, review-required 중 하나여야 합니다.")
    stamp = utc_now_iso()
    with _connect() as connection:
        cursor = connection.execute(
            """
                UPDATE investment_learning_proposals
                SET status = %s, reviewed_at = %s, review_note = %s, updated_at = %s
                WHERE proposal_id = %s
                """,
            (
                status,
                stamp if status != "review-required" else "",
                str(note or "")[:2000],
                stamp,
                str(proposal_id or ""),
            ),
        )
        if not cursor.rowcount:
            raise KeyError("학습 제안을 찾지 못했습니다.")
    rows = _list_learning_proposals(status=status, limit=500)
    return next(
        (item for item in rows if str(item.get("proposalId") or "") == str(proposal_id)), {}
    )
