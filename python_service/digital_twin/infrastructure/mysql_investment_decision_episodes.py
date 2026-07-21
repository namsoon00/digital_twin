from datetime import timedelta
from typing import Dict, Iterable, List, Optional

from ..domain.investment_brain import (
    DecisionEpisode,
    LearningProposal,
    ObservedOutcome,
    canonical_investment_timestamp,
    parse_investment_timestamp,
    stable_id,
    utc_now_iso,
)
from ..domain.decision_performance import evaluate_decision_performance
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps


class MySQLInvestmentDecisionEpisodeStore(MySQLOperationalConnection):
    def save(self, episode: DecisionEpisode) -> DecisionEpisode:
        episode.decided_at = canonical_investment_timestamp(episode.decided_at) or utc_now_iso()
        episode.status = str(episode.status or "active")
        stamp = utc_now_iso()
        payload = episode.to_dict()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO investment_decision_episodes (
                    episode_id, account_id, symbol, subject_name, question_id,
                    hypothesis_set_id, selected_hypothesis_id, action,
                    review_level, data_state, validation_state,
                    inference_generation_id, status, decided_at, source,
                    payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE selected_hypothesis_id = VALUES(selected_hypothesis_id),
                    action = VALUES(action), review_level = VALUES(review_level),
                    data_state = VALUES(data_state), validation_state = VALUES(validation_state),
                    status = VALUES(status),
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
                    episode.review_level,
                    episode.data_state,
                    episode.validation_state,
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

    def outcome_horizons(self) -> List[int]:
        return outcome_horizon_minutes(
            self.runtime_settings.get("investmentBrainOutcomeObservationMinutes") or "60,1440,10080",
        )

    def outcome_batch_size(self) -> int:
        try:
            value = int(float(str(self.runtime_settings.get("investmentBrainOutcomeEpisodeBatchSize") or "200")))
        except (TypeError, ValueError):
            value = 200
        return max(10, min(1000, value))

    def episode_from_row(self, row: Dict[str, object]) -> DecisionEpisode:
        episode = DecisionEpisode.from_dict(_json_loads(row.get("payload_json"), {}))
        stored_status = str(row.get("status") or "").strip()
        stored_decided_at = canonical_investment_timestamp(row.get("decided_at"))
        if stored_status:
            episode.status = stored_status
        if stored_decided_at:
            episode.decided_at = stored_decided_at
        else:
            episode.decided_at = canonical_investment_timestamp(episode.decided_at) or episode.decided_at
        return episode

    def outcomes_from_rows(self, rows: Iterable[Dict[str, object]], default_episode_id: str = "") -> List[ObservedOutcome]:
        outcomes: List[ObservedOutcome] = []
        for row in rows or []:
            item = _json_loads(row.get("payload_json"), {})
            if not item:
                continue
            outcomes.append(ObservedOutcome(
                outcome_id=str(item.get("outcomeId") or ""),
                episode_id=str(item.get("episodeId") or row.get("episode_id") or default_episode_id),
                observed_at=canonical_investment_timestamp(item.get("observedAt") or row.get("observed_at")) or str(item.get("observedAt") or ""),
                price=number(item.get("price")),
                profit_loss_rate=number(item.get("profitLossRate")),
                price_change_from_decision_pct=number(item.get("priceChangeFromDecisionPct")),
                selected_hypothesis_status=str(item.get("selectedHypothesisStatus") or "pending"),
                contradicted_evidence_ids=list(item.get("contradictedEvidenceIds") or []),
                payload=dict(item.get("payload") or {}),
            ))
        return outcomes

    def hydrate_outcomes(self, episodes: Iterable[DecisionEpisode]) -> List[DecisionEpisode]:
        result = list(episodes or [])
        episode_ids = [item.episode_id for item in result if item.episode_id]
        if not episode_ids:
            return result
        placeholders = ",".join(["%s"] * len(episode_ids))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT episode_id, observed_at, payload_json FROM investment_decision_outcomes "
                "WHERE episode_id IN (" + placeholders + ") "
                "ORDER BY observed_at ASC, outcome_id ASC",
                episode_ids,
            ).fetchall()
        grouped: Dict[str, List[Dict[str, object]]] = {}
        for row in rows or []:
            grouped.setdefault(str(row.get("episode_id") or ""), []).append(row)
        for episode in result:
            episode.outcomes = self.outcomes_from_rows(grouped.get(episode.episode_id, []), episode.episode_id)
        return result

    def episodes_by_ids(self, episode_ids: Iterable[str]) -> Dict[str, DecisionEpisode]:
        clean_ids = list(dict.fromkeys(str(item or "").strip() for item in episode_ids or [] if str(item or "").strip()))
        if not clean_ids:
            return {}
        placeholders = ",".join(["%s"] * len(clean_ids))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json, status, decided_at FROM investment_decision_episodes "
                "WHERE episode_id IN (" + placeholders + ")",
                clean_ids,
            ).fetchall()
        episodes = self.hydrate_outcomes(self.episode_from_row(row) for row in rows or [])
        return {item.episode_id: item for item in episodes if item.episode_id}

    def get(self, episode_id: str) -> Optional[DecisionEpisode]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json, status, decided_at FROM investment_decision_episodes WHERE episode_id = %s",
                (str(episode_id or ""),),
            ).fetchone()
        if not row:
            return None
        return self.hydrate_outcomes([self.episode_from_row(row)])[0]

    def list(self, account_id: str = "", symbol: str = "", limit: int = 50) -> List[DecisionEpisode]:
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
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return self.hydrate_outcomes(self.episode_from_row(row) for row in rows or [])

    def performance(self, account_id: str = "", symbol: str = "", limit: int = 500) -> Dict[str, object]:
        try:
            minimum_samples = int(float(str(self.runtime_settings.get("investmentBrainPerformanceMinimumSamples") or "5")))
        except ValueError:
            minimum_samples = 5
        episodes = self.list(account_id=account_id, symbol=symbol, limit=max(1, min(2000, int(limit or 500))))
        return evaluate_decision_performance(
            episodes,
            minimum_sample_count=max(2, min(100, minimum_samples)),
        )

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
        return self.outcomes_from_rows(rows, str(episode_id or ""))

    def pending_outcome_targets(
        self,
        account_id: str,
        observed_at: str = "",
        limit: int = 0,
    ) -> List[Dict[str, object]]:
        observed_stamp = canonical_investment_timestamp(observed_at) or utc_now_iso()
        target_limit = max(1, min(1000, int(limit or self.outcome_batch_size())))
        episode_limit = max(target_limit, min(2000, target_limit * max(1, len(self.outcome_horizons()))))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json, status, decided_at
                FROM investment_decision_episodes
                WHERE account_id = %s AND status IN ('active', 'observed')
                ORDER BY decided_at ASC, episode_id ASC
                LIMIT %s
                """,
                (str(account_id or ""), episode_limit),
            ).fetchall()
        episodes = self.hydrate_outcomes(self.episode_from_row(row) for row in rows or [])
        targets: List[Dict[str, object]] = []
        for episode in episodes:
            for horizon_minutes in due_outcome_horizon_minutes_all(episode, observed_stamp, self.outcome_horizons()):
                target_at = outcome_target_at(episode, horizon_minutes)
                if not target_at:
                    continue
                targets.append({
                    "requestId": stable_id("decision-outcome-target", episode.episode_id, horizon_minutes),
                    "episodeId": episode.episode_id,
                    "symbol": episode.symbol,
                    "subjectName": episode.subject_name,
                    "market": str((episode.facts_at_decision or {}).get("market") or ""),
                    "currency": str((episode.facts_at_decision or {}).get("currency") or ""),
                    "horizonMinutes": horizon_minutes,
                    "decidedAt": episode.decided_at,
                    "targetAt": target_at,
                })
                if len(targets) >= target_limit:
                    return targets
        return targets

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
        observed_at = canonical_investment_timestamp(observed_at or facts.get("observedAt")) or utc_now_iso()
        if not outcome_observation_is_usable(facts, observed_at):
            return []
        episodes = self.list(account_id=account_id, symbol=symbol, limit=self.outcome_batch_size())
        requests: List[Dict[str, object]] = []
        for episode in episodes:
            outcome_horizon_minutes = due_outcome_horizon_minutes(
                episode,
                observed_at,
                self.outcome_horizons(),
            )
            if not outcome_horizon_minutes:
                continue
            requests.append({
                "episodeId": episode.episode_id,
                "horizonMinutes": outcome_horizon_minutes,
                "facts": dict(facts or {}),
                "observedAt": observed_at,
            })
        return self.record_outcome_observations(account_id, requests)

    def record_outcome_observations(
        self,
        account_id: str,
        observations: Iterable[Dict[str, object]],
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
            observed_at = canonical_investment_timestamp(item.get("observedAt") or facts.get("observedAt"))
            if not episode_id or horizon_minutes <= 0 or not observed_at or not outcome_observation_is_usable(facts, observed_at):
                continue
            normalized.append({
                "episodeId": episode_id,
                "horizonMinutes": horizon_minutes,
                "facts": facts,
                "observedAt": observed_at,
            })
        if not normalized:
            return []
        episodes = self.episodes_by_ids(item["episodeId"] for item in normalized)
        outcomes: List[ObservedOutcome] = []
        changed_symbols = set()
        for item in normalized:
            episode = episodes.get(item["episodeId"])
            if not episode or str(episode.account_id or "") != str(account_id or ""):
                continue
            horizon_minutes = int(item["horizonMinutes"])
            if horizon_minutes not in self.outcome_horizons() or outcome_horizon_recorded(episode, horizon_minutes):
                continue
            target_at = outcome_target_at(episode, horizon_minutes)
            observed_at = str(item["observedAt"])
            target_time = parse_datetime(target_at)
            observed_time = parse_datetime(observed_at)
            if not target_time or not observed_time or observed_time < target_time:
                continue
            facts = dict(item["facts"] or {})
            current_price = number(facts.get("currentPrice"))
            decision_price = number((episode.facts_at_decision or {}).get("currentPrice"))
            change_pct = round(((current_price / decision_price) - 1) * 100, 4) if current_price and decision_price else 0.0
            stance = selected_hypothesis_stance(episode)
            delay_minutes = max(0.0, (observed_time - target_time).total_seconds() / 60.0)
            calibration_eligible = delay_minutes <= self.outcome_max_delay_minutes()
            outcome = ObservedOutcome(
                outcome_id=stable_id("decision-outcome", episode.episode_id, horizon_minutes),
                episode_id=episode.episode_id,
                observed_at=observed_at,
                price=current_price,
                profit_loss_rate=number(facts.get("profitLossRate")),
                price_change_from_decision_pct=change_pct,
                selected_hypothesis_status=directional_hypothesis_status(stance, change_pct),
                payload={
                    "selectedHypothesisId": episode.selected_hypothesis_id,
                    "selectedHypothesisStance": stance,
                    "inferenceGenerationId": facts.get("inferenceGenerationId") or "",
                    "observationBasis": str(facts.get("observationBasis") or "subsequent-market-observation"),
                    "observationSource": str(facts.get("observationSource") or facts.get("provider") or ""),
                    "sourceAsOf": canonical_investment_timestamp(facts.get("sourceAsOf")) or observed_at,
                    "dataQuality": str(facts.get("dataQuality") or "unknown"),
                    "horizonMinutes": horizon_minutes,
                    "targetAt": target_at,
                    "actualElapsedMinutes": round((observed_time - parse_datetime(episode.decided_at)).total_seconds() / 60.0, 2),
                    "observationDelayMinutes": round(delay_minutes, 2),
                    "observationTiming": "on-time" if calibration_eligible else "delayed",
                    "calibrationEligibility": "eligible" if calibration_eligible else "excluded-delayed-observation",
                },
            )
            self.save_outcome(episode, outcome)
            outcomes.append(outcome)
            changed_symbols.add(episode.symbol)
        for symbol in sorted(changed_symbols):
            self.propose_learning_from_outcomes(account_id, symbol)
        return outcomes

    def outcome_max_delay_minutes(self) -> int:
        try:
            value = int(float(str(self.runtime_settings.get("investmentBrainOutcomeMaxDelayMinutes") or "180")))
        except (TypeError, ValueError):
            value = 180
        return max(1, min(60 * 24 * 14, value))

    def save_outcome(self, episode: DecisionEpisode, outcome: ObservedOutcome) -> ObservedOutcome:
        outcome.observed_at = canonical_investment_timestamp(outcome.observed_at) or utc_now_iso()
        episode.status = "observed"
        episode.outcomes = [
            item for item in episode.outcomes
            if item.outcome_id != outcome.outcome_id
        ] + [outcome]
        payload = outcome.to_dict()
        episode_payload = episode.to_dict()
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
                "UPDATE investment_decision_episodes SET status = %s, decided_at = %s, payload_json = %s, updated_at = %s WHERE episode_id = %s",
                ("observed", episode.decided_at, json_dumps(episode_payload), utc_now_iso(), episode.episode_id),
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
            outcome_payload = _json_loads(row.get("outcome_json"), {})
            if not outcome_is_calibration_eligible(outcome_payload):
                continue
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
    horizons = due_outcome_horizon_minutes_all(episode, observed_at, raw_horizons)
    return horizons[0] if horizons else 0


def due_outcome_horizon_minutes_all(
    episode: DecisionEpisode,
    observed_at: str,
    raw_horizons: object,
) -> List[int]:
    decided = parse_datetime(episode.decided_at)
    observed = parse_datetime(observed_at)
    if not decided or not observed or observed <= decided:
        return []
    elapsed_minutes = (observed - decided).total_seconds() / 60
    return [
        value for value in outcome_horizon_minutes(raw_horizons)
        if elapsed_minutes >= value and not outcome_horizon_recorded(episode, value)
    ]


def outcome_horizon_minutes(raw_horizons: object) -> List[int]:
    if isinstance(raw_horizons, (list, tuple, set)):
        raw_values = raw_horizons
    else:
        raw_values = str(raw_horizons or "").replace("\n", ",").split(",")
    horizons: List[int] = []
    for raw in raw_values:
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in horizons:
            horizons.append(value)
    return sorted(horizons) or [60, 1440, 10080]


def outcome_horizon_recorded(episode: DecisionEpisode, horizon_minutes: int) -> bool:
    return int(horizon_minutes or 0) in {
        int(float((item.payload or {}).get("horizonMinutes") or 0))
        for item in episode.outcomes or []
        if (item.payload or {}).get("horizonMinutes")
    }


def outcome_target_at(episode: DecisionEpisode, horizon_minutes: int) -> str:
    decided = parse_datetime(episode.decided_at)
    if not decided or int(horizon_minutes or 0) <= 0:
        return ""
    return (decided + timedelta(minutes=int(horizon_minutes))).isoformat().replace("+00:00", "Z")


def outcome_observation_is_usable(facts: Dict[str, object], observed_at: str) -> bool:
    if not number((facts or {}).get("currentPrice")) or not parse_datetime(observed_at):
        return False
    quality = str((facts or {}).get("dataQuality") or "").strip().lower()
    return quality not in {"stale", "cached", "invalid", "unavailable", "error", "mock", "estimated"}


def outcome_is_calibration_eligible(outcome_payload: Dict[str, object]) -> bool:
    payload = outcome_payload.get("payload") if isinstance(outcome_payload, dict) else {}
    eligibility = str((payload or {}).get("calibrationEligibility") or "").strip().lower()
    return eligibility == "eligible"


def parse_datetime(value: object):
    return parse_investment_timestamp(value)


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
