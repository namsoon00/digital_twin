from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..domain.investment_brain import (
    DecisionEpisode,
    LearningProposal,
    ObservedOutcome,
    stable_id,
    utc_now_iso,
)
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps


class MySQLInvestmentDecisionEpisodeStore(MySQLOperationalConnection):
    def save(self, episode: DecisionEpisode) -> DecisionEpisode:
        stamp = utc_now_iso()
        payload = episode.to_dict()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO investment_decision_episodes (
                    episode_id, account_id, symbol, subject_name, question_id,
                    hypothesis_set_id, selected_hypothesis_id, action, confidence,
                    inference_generation_id, status, decided_at, source,
                    payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE selected_hypothesis_id = VALUES(selected_hypothesis_id),
                    action = VALUES(action), confidence = VALUES(confidence), status = VALUES(status),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    episode.episode_id,
                    episode.account_id,
                    episode.symbol,
                    episode.subject_name,
                    episode.question.question_id,
                    episode.hypothesis_set.hypothesis_set_id,
                    episode.selected_hypothesis_id,
                    episode.action,
                    episode.confidence,
                    episode.inference_generation_id,
                    episode.status,
                    episode.decided_at,
                    episode.source,
                    json_dumps(payload),
                    stamp,
                    stamp,
                ),
            )
        return episode

    def get(self, episode_id: str) -> Optional[DecisionEpisode]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_decision_episodes WHERE episode_id = %s",
                (str(episode_id or ""),),
            ).fetchone()
        if not row:
            return None
        episode = DecisionEpisode.from_dict(_json_loads(row.get("payload_json"), {}))
        episode.outcomes = self.outcomes_for_episode(episode.episode_id)
        return episode

    def list(self, account_id: str = "", symbol: str = "", limit: int = 50) -> List[DecisionEpisode]:
        where = []
        params: List[object] = []
        if account_id:
            where.append("account_id = %s")
            params.append(str(account_id))
        if symbol:
            where.append("symbol = %s")
            params.append(str(symbol).upper())
        params.append(max(1, min(500, int(limit or 50))))
        sql = "SELECT payload_json FROM investment_decision_episodes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY decided_at DESC, episode_id DESC LIMIT %s"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        episodes = [
            DecisionEpisode.from_dict(_json_loads(row.get("payload_json"), {}))
            for row in rows or []
        ]
        for episode in episodes:
            episode.outcomes = self.outcomes_for_episode(episode.episode_id)
        return episodes

    def outcomes_for_episode(self, episode_id: str, limit: int = 30) -> List[ObservedOutcome]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM investment_decision_outcomes
                WHERE episode_id = %s
                ORDER BY observed_at ASC, outcome_id ASC
                LIMIT %s
                """,
                (str(episode_id or ""), max(1, min(200, int(limit or 30)))),
            ).fetchall()
        outcomes = []
        for row in rows or []:
            item = _json_loads(row.get("payload_json"), {})
            if not item:
                continue
            outcomes.append(ObservedOutcome(
                outcome_id=str(item.get("outcomeId") or ""),
                episode_id=str(item.get("episodeId") or episode_id),
                observed_at=str(item.get("observedAt") or ""),
                price=float(item.get("price") or 0),
                profit_loss_rate=float(item.get("profitLossRate") or 0),
                price_change_from_decision_pct=float(item.get("priceChangeFromDecisionPct") or 0),
                selected_hypothesis_status=str(item.get("selectedHypothesisStatus") or "pending"),
                contradicted_evidence_ids=list(item.get("contradictedEvidenceIds") or []),
                payload=dict(item.get("payload") or {}),
            ))
        return outcomes

    def record_observation(
        self,
        account_id: str,
        symbol: str,
        facts: Dict[str, object],
        observed_at: str = "",
    ) -> List[ObservedOutcome]:
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            return []
        observed_at = str(observed_at or facts.get("observedAt") or utc_now_iso())
        current_price = number(facts.get("currentPrice"))
        current_pnl = number(facts.get("profitLossRate"))
        episodes = self.list(account_id=account_id, symbol=symbol, limit=20)
        outcomes: List[ObservedOutcome] = []
        for episode in episodes:
            if not episode.episode_id or observed_at <= str(episode.decided_at or ""):
                continue
            outcome_horizon_minutes = due_outcome_horizon_minutes(
                episode,
                observed_at,
                self.runtime_settings.get("investmentBrainOutcomeObservationMinutes") or "60,1440,10080",
            )
            if not outcome_horizon_minutes:
                continue
            decision_price = number((episode.facts_at_decision or {}).get("currentPrice"))
            change_pct = round(((current_price / decision_price) - 1) * 100, 4) if current_price and decision_price else 0.0
            stance = selected_hypothesis_stance(episode)
            status = directional_hypothesis_status(stance, change_pct)
            outcome = ObservedOutcome(
                outcome_id=stable_id("decision-outcome", episode.episode_id, observed_at, current_price, current_pnl),
                episode_id=episode.episode_id,
                observed_at=observed_at,
                price=current_price,
                profit_loss_rate=current_pnl,
                price_change_from_decision_pct=change_pct,
                selected_hypothesis_status=status,
                payload={
                    "selectedHypothesisId": episode.selected_hypothesis_id,
                    "selectedHypothesisStance": stance,
                    "inferenceGenerationId": facts.get("inferenceGenerationId") or "",
                    "observationBasis": "subsequent-ontology-facts",
                    "horizonMinutes": outcome_horizon_minutes,
                },
            )
            self.save_outcome(episode, outcome)
            outcomes.append(outcome)
        if outcomes:
            self.propose_learning_from_outcomes(account_id, symbol)
        return outcomes

    def save_outcome(self, episode: DecisionEpisode, outcome: ObservedOutcome) -> ObservedOutcome:
        payload = outcome.to_dict()
        with self.transaction() as connection:
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
                "UPDATE investment_decision_episodes SET status = %s, updated_at = %s WHERE episode_id = %s",
                ("observed", utc_now_iso(), episode.episode_id),
            )
        return outcome

    def propose_learning_from_outcomes(self, account_id: str, symbol: str) -> Optional[LearningProposal]:
        try:
            minimum = int(float(str(self.runtime_settings.get("investmentBrainLearningMinContradictions") or "3")))
        except ValueError:
            minimum = 3
        minimum = max(2, min(20, minimum))
        with self.connect() as connection:
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
        distinct_rows = []
        seen_episode_ids = set()
        for row in rows or []:
            episode_payload = _json_loads(row.get("episode_json"), {})
            episode_id = str(episode_payload.get("episodeId") or "")
            if not episode_id or episode_id in seen_episode_ids:
                continue
            seen_episode_ids.add(episode_id)
            distinct_rows.append(row)
            if len(distinct_rows) >= minimum:
                break
        if len(distinct_rows) < minimum:
            return None
        episodes = [DecisionEpisode.from_dict(_json_loads(row.get("episode_json"), {})) for row in distinct_rows]
        episode_ids = [item.episode_id for item in episodes if item.episode_id]
        rule_ids: List[str] = []
        for episode in episodes:
            for hypothesis in episode.hypothesis_set.hypotheses:
                if hypothesis.hypothesis_id == episode.selected_hypothesis_id:
                    rule_ids.extend(hypothesis.supporting_rule_ids)
        rule_ids = list(dict.fromkeys(rule_ids))
        proposal = LearningProposal(
            proposal_id=stable_id("learning-proposal", account_id, symbol, ",".join(episode_ids)),
            title=str(symbol or "") + " 선택 가설 반복 반증 검토",
            reason="서로 다른 최근 판단 " + str(minimum) + "건에서 선택 가설이 이후 가격 방향 관측으로 반복 반증됐습니다. 원천 데이터와 가설 가중치를 재검토해야 합니다.",
            source_episode_ids=episode_ids,
            affected_rule_ids=rule_ids,
            proposed_change={
                "changeType": "review-hypothesis-prior-and-evidence-coverage",
                "automaticDeployment": False,
                "requiredValidation": ["historical-replay", "TypeDB-rule-preview", "human-approval"],
            },
        )
        return self.save_learning_proposal(proposal)

    def save_learning_proposal(self, proposal: LearningProposal) -> LearningProposal:
        stamp = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO investment_learning_proposals (
                    proposal_id, status, title, reason, affected_rule_ids_json,
                    source_episode_ids_json, payload_json, created_at, updated_at,
                    reviewed_at, review_note
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '', '')
                ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
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

    def list_learning_proposals(self, status: str = "", limit: int = 50) -> List[Dict[str, object]]:
        params: List[object] = []
        sql = "SELECT payload_json, status, reviewed_at, review_note FROM investment_learning_proposals"
        if status:
            sql += " WHERE status = %s"
            params.append(str(status))
        sql += " ORDER BY updated_at DESC, proposal_id DESC LIMIT %s"
        params.append(max(1, min(500, int(limit or 50))))
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        results = []
        for row in rows or []:
            payload = _json_loads(row.get("payload_json"), {})
            payload["status"] = row.get("status") or payload.get("status")
            payload["reviewedAt"] = row.get("reviewed_at") or ""
            payload["reviewNote"] = row.get("review_note") or ""
            results.append(payload)
        return results

    def review_learning_proposal(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        status = str(status or "").strip().lower()
        if status not in {"approved", "rejected", "review-required"}:
            raise ValueError("학습 제안 상태는 approved, rejected, review-required 중 하나여야 합니다.")
        stamp = utc_now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE investment_learning_proposals
                SET status = %s, reviewed_at = %s, review_note = %s, updated_at = %s
                WHERE proposal_id = %s
                """,
                (status, stamp if status != "review-required" else "", str(note or "")[:2000], stamp, str(proposal_id or "")),
            )
            if not cursor.rowcount:
                raise KeyError("학습 제안을 찾지 못했습니다.")
        rows = self.list_learning_proposals(status=status, limit=500)
        return next((item for item in rows if str(item.get("proposalId") or "") == str(proposal_id)), {})


def selected_hypothesis_stance(episode: DecisionEpisode) -> str:
    for item in episode.hypothesis_set.hypotheses:
        if item.hypothesis_id == episode.selected_hypothesis_id:
            return item.stance
    return "uncertain"


def directional_hypothesis_status(stance: str, price_change_pct: float) -> str:
    if not price_change_pct or stance not in {"risk", "support"}:
        return "inconclusive"
    if stance == "risk":
        return "directionally-corroborated" if price_change_pct < 0 else "directionally-contradicted"
    return "directionally-corroborated" if price_change_pct > 0 else "directionally-contradicted"


def due_outcome_horizon_minutes(episode: DecisionEpisode, observed_at: str, raw_horizons: object) -> int:
    decided = parse_datetime(episode.decided_at)
    observed = parse_datetime(observed_at)
    if not decided or not observed or observed <= decided:
        return 0
    elapsed_minutes = (observed - decided).total_seconds() / 60
    horizons = []
    for raw in str(raw_horizons or "").replace("\n", ",").split(","):
        try:
            value = int(float(raw.strip()))
        except ValueError:
            continue
        if value > 0 and value not in horizons:
            horizons.append(value)
    horizons = sorted(horizons) or [60, 1440, 10080]
    recorded = {
        int(float((item.payload or {}).get("horizonMinutes") or 0))
        for item in episode.outcomes or []
        if (item.payload or {}).get("horizonMinutes")
    }
    return next((value for value in horizons if elapsed_minutes >= value and value not in recorded), 0)


def parse_datetime(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
