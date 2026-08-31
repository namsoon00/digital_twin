from contextlib import nullcontext
from datetime import timedelta, timezone
from typing import Dict, Iterable, List, Optional

from ..domain.investment_brain import (
    DecisionEpisode,
    LearningProposal,
    ObservedOutcome,
    canonical_investment_timestamp,
    parse_investment_timestamp,
    stable_id,
    scoped_decision_follow_ups,
    utc_now_iso,
)
from ..domain.decision_follow_up import evaluate_follow_up_conditions
from ..domain.hypothesis_outcome_contract import (
    observation_domain_status,
    outcome_contract_completeness,
    resolved_outcome_contract,
)
from ..domain.hypothesis_outcome_evaluation import evaluate_hypothesis_outcome
from ..domain.market_time_series import market_timezone
from ..domain.decision_performance import (
    contradiction_learning_candidates,
    evaluate_decision_performance,
)
from ..domain.trade_execution import ActionPlan
from ..domain.events import investment_decision_changed_event, investment_validation_changed_event
from ..domain.investment_flow import investment_flow_id
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .mysql_operational_events import insert_domain_event_with_connection
from .operational_common import json_dumps


class MySQLInvestmentDecisionEpisodeStore(MySQLOperationalConnection):
    def save(self, episode: DecisionEpisode, connection=None) -> DecisionEpisode:
        action = str(episode.action or "").upper()
        if action == "NO_ACTION":
            raise ValueError("NO_ACTION is an operational disposition and cannot be a DecisionEpisode.")
        if episode.source == "v2-reasoning-case" and not str(episode.selected_hypothesis_id or "").strip():
            raise ValueError("V2 DecisionEpisode requires a selected subject-scoped hypothesis.")
        episode.decided_at = canonical_investment_timestamp(episode.decided_at) or utc_now_iso()
        episode.status = str(episode.status or "active")
        episode.follow_up_conditions = scoped_decision_follow_ups(
            episode.episode_id,
            episode.follow_up_conditions,
        )
        episode.unsupported_follow_ups = scoped_decision_follow_ups(
            episode.episode_id,
            episode.unsupported_follow_ups,
        )
        episode.portfolio_id = episode.portfolio_id or "portfolio:" + str(episode.account_id or "default")
        plan = None
        if action in {"BUY", "ADD", "TRIM", "SELL"}:
            plan = ActionPlan.create(
                portfolio_id=episode.portfolio_id,
                decision_episode_id=episode.episode_id,
                action=episode.action,
                policy_version=episode.mandate_version,
                inference_generation_id=episode.inference_generation_id,
                created_at=episode.decided_at,
            )
            episode.action_plan_id = plan.plan_id
        else:
            episode.action_plan_id = ""
        stamp = utc_now_iso()
        payload = episode.to_dict()
        flow_id = investment_flow_id(episode.account_id, episode.symbol, episode.episode_id)
        payload["flowId"] = flow_id
        transaction = self.transaction() if connection is None else nullcontext(connection)
        with transaction as connection:
            current_row = connection.execute(
                "SELECT payload_json FROM investment_decision_episodes WHERE episode_id = %s",
                (episode.episode_id,),
            ).fetchone()
            prior_row = current_row or connection.execute(
                "SELECT payload_json FROM investment_decision_episodes "
                "WHERE account_id = %s AND symbol = %s ORDER BY decided_at DESC, episode_id DESC LIMIT 1",
                (episode.account_id, episode.symbol),
            ).fetchone()
            previous_payload = _json_loads(prior_row.get("payload_json"), {}) if prior_row else {}
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
                    inference_generation_id = VALUES(inference_generation_id),
                    status = VALUES(status), decided_at = VALUES(decided_at),
                    source = VALUES(source),
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
            connection.execute(
                """
                INSERT INTO investment_flow_current (
                    account_id, symbol, flow_id, decision_episode_id,
                    source_abox_snapshot_id, inference_generation_id,
                    selected_hypothesis_id, action, data_state,
                    validation_state, decided_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    flow_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(flow_id), flow_id),
                    decision_episode_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(decision_episode_id), decision_episode_id),
                    source_abox_snapshot_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(source_abox_snapshot_id), source_abox_snapshot_id),
                    inference_generation_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(inference_generation_id), inference_generation_id),
                    selected_hypothesis_id = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(selected_hypothesis_id), selected_hypothesis_id),
                    action = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(action), action),
                    data_state = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(data_state), data_state),
                    validation_state = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(validation_state), validation_state),
                    updated_at = IF(VALUES(decided_at) >= investment_flow_current.decided_at, VALUES(updated_at), updated_at),
                    decided_at = GREATEST(investment_flow_current.decided_at, VALUES(decided_at))
                """,
                (
                    episode.account_id,
                    episode.symbol,
                    flow_id,
                    episode.episode_id,
                    episode.source_abox_snapshot_id,
                    episode.inference_generation_id,
                    episode.selected_hypothesis_id,
                    episode.action,
                    episode.data_state,
                    episode.validation_state,
                    episode.decided_at,
                    stamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO investment_flow_heads (
                    flow_id, account_id, symbol, decision_episode_id,
                    source_abox_snapshot_id, inference_generation_id,
                    selected_hypothesis_id, action, data_state,
                    validation_state, decided_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE decision_episode_id = VALUES(decision_episode_id),
                    source_abox_snapshot_id = VALUES(source_abox_snapshot_id),
                    inference_generation_id = VALUES(inference_generation_id),
                    selected_hypothesis_id = VALUES(selected_hypothesis_id),
                    action = VALUES(action), data_state = VALUES(data_state),
                    validation_state = VALUES(validation_state),
                    decided_at = VALUES(decided_at), updated_at = VALUES(updated_at)
                """,
                (
                    flow_id,
                    episode.account_id,
                    episode.symbol,
                    episode.episode_id,
                    episode.source_abox_snapshot_id,
                    episode.inference_generation_id,
                    episode.selected_hypothesis_id,
                    episode.action,
                    episode.data_state,
                    episode.validation_state,
                    episode.decided_at,
                    stamp,
                ),
            )
            if plan is not None:
                connection.execute(
                    """
                    INSERT INTO investment_action_plans (
                        plan_id, portfolio_id, decision_episode_id, policy_version,
                        inference_generation_id, action, status, payload_json, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE status = VALUES(status),
                        payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                    """,
                    (
                        plan.plan_id,
                        plan.portfolio_id,
                        plan.decision_episode_id,
                        plan.policy_version,
                        plan.inference_generation_id,
                        plan.action,
                        plan.status,
                        json_dumps(plan.to_dict()),
                        plan.created_at or stamp,
                        stamp,
                    ),
                )
            self.sync_outcome_targets(connection, episode, stamp)
            for condition in list(episode.follow_up_conditions or []) + list(episode.unsupported_follow_ups or []):
                if not isinstance(condition, dict) or not str(condition.get("conditionId") or "").strip():
                    continue
                connection.execute(
                    """
                    INSERT INTO investment_decision_follow_ups (
                        condition_id, episode_id, account_id, symbol, field_name,
                        comparison_operator, threshold_value, purpose, status,
                        observable, payload_json, created_at, updated_at, transitioned_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE status = VALUES(status), observable = VALUES(observable),
                        payload_json = VALUES(payload_json), updated_at = VALUES(updated_at),
                        transitioned_at = VALUES(transitioned_at)
                    """,
                    (
                        str(condition.get("conditionId")),
                        episode.episode_id,
                        episode.account_id,
                        episode.symbol,
                        str(condition.get("field") or ""),
                        str(condition.get("operator") or ""),
                        number(condition.get("threshold")),
                        str(condition.get("purpose") or "switch"),
                        str(condition.get("status") or "pending"),
                        1 if condition.get("observable") is not False else 0,
                        json_dumps(condition),
                        stamp,
                        stamp,
                        str(condition.get("transitionAt") or ""),
                    ),
                )
            decision_fields = ("action", "reviewLevel", "dataState", "validationState", "selectedHypothesisId")
            decision_changed = not previous_payload or any(
                str(previous_payload.get(key) or "") != str(payload.get(key) or "")
                for key in decision_fields
            )
            validation_changed = not previous_payload or any(
                str(previous_payload.get(key) or "") != str(payload.get(key) or "")
                for key in ("dataState", "validationState")
            )
            if decision_changed:
                insert_domain_event_with_connection(
                    connection,
                    investment_decision_changed_event(previous_payload, payload),
                )
            if validation_changed:
                insert_domain_event_with_connection(
                    connection,
                    investment_validation_changed_event(previous_payload, payload),
                )
        return episode

    def quarantine_invalid_legacy_outcomes(self, limit: int = 5000) -> Dict[str, object]:
        """Remove operational observations from active decision continuity.

        Rows are retained for audit, but they can no longer become the current
        investment decision, produce follow-up work, or open an action plan.
        """

        maximum = max(1, min(50000, int(limit or 5000)))
        quarantine_status = "invalid-legacy-outcome"
        stamp = utc_now_iso()
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT episode_id, account_id, symbol, payload_json FROM investment_decision_episodes "
                "WHERE status <> %s AND (action = 'NO_ACTION' OR "
                "(source = 'v2-reasoning-case' AND selected_hypothesis_id = '')) "
                "ORDER BY decided_at, episode_id LIMIT %s",
                (quarantine_status, maximum),
            ).fetchall()
            episode_ids = []
            affected_scopes = set()
            for row in rows or []:
                episode_id = str(row.get("episode_id") or "")
                if not episode_id:
                    continue
                affected_scopes.add((
                    str(row.get("account_id") or ""),
                    str(row.get("symbol") or "").upper(),
                ))
                payload = _json_loads(row.get("payload_json"), {})
                payload["status"] = quarantine_status
                payload["validationState"] = "invalid"
                facts = payload.get("factsAtDecision")
                facts = dict(facts or {}) if isinstance(facts, dict) else {}
                facts["legacyOutcomeQuarantine"] = {
                    "reason": "Operational observation is not a final investment decision.",
                    "quarantinedAt": stamp,
                }
                payload["factsAtDecision"] = facts
                connection.execute(
                    "UPDATE investment_decision_episodes SET status = %s, "
                    "validation_state = 'invalid', payload_json = %s, updated_at = %s "
                    "WHERE episode_id = %s",
                    (quarantine_status, json_dumps(payload), stamp, episode_id),
                )
                episode_ids.append(episode_id)
            if not episode_ids:
                return {
                    "status": "unchanged",
                    "quarantinedCount": 0,
                    "currentPointersRemoved": 0,
                    "currentPointersRepaired": 0,
                    "followUpsCanceled": 0,
                    "outcomeTargetsExcluded": 0,
                    "actionPlansCanceled": 0,
                }
            placeholders = ", ".join(["%s"] * len(episode_ids))
            cursor = connection.execute(
                "DELETE FROM investment_flow_current WHERE decision_episode_id IN ("
                + placeholders + ")",
                tuple(episode_ids),
            )
            current_removed = max(0, int(getattr(cursor, "rowcount", 0) or 0))
            current_repaired = 0
            for account_id, symbol in sorted(affected_scopes):
                replacement = connection.execute(
                    "SELECT episode_id, selected_hypothesis_id, action, data_state, "
                    "validation_state, inference_generation_id, decided_at, payload_json, updated_at "
                    "FROM investment_decision_episodes WHERE account_id = %s AND symbol = %s "
                    "AND action IN ('BUY', 'ADD', 'HOLD', 'TRIM', 'SELL', 'AVOID', 'WATCH') "
                    "AND selected_hypothesis_id <> '' "
                    "AND status NOT IN ('blocked', 'failed', 'expired', 'suppressed', 'superseded', "
                    "'reference-only', 'invalid-legacy-outcome') "
                    "AND validation_state NOT IN ('blocked', 'invalid', 'failed', 'error') "
                    "ORDER BY decided_at DESC, episode_id DESC LIMIT 1",
                    (account_id, symbol),
                ).fetchone()
                if not replacement:
                    continue
                replacement_payload = _json_loads(replacement.get("payload_json"), {})
                replacement_id = str(replacement.get("episode_id") or "")
                connection.execute(
                    """
                    INSERT INTO investment_flow_current (
                        account_id, symbol, flow_id, decision_episode_id,
                        source_abox_snapshot_id, inference_generation_id,
                        selected_hypothesis_id, action, data_state,
                        validation_state, decided_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        flow_id = VALUES(flow_id), decision_episode_id = VALUES(decision_episode_id),
                        source_abox_snapshot_id = VALUES(source_abox_snapshot_id),
                        inference_generation_id = VALUES(inference_generation_id),
                        selected_hypothesis_id = VALUES(selected_hypothesis_id), action = VALUES(action),
                        data_state = VALUES(data_state), validation_state = VALUES(validation_state),
                        decided_at = VALUES(decided_at), updated_at = VALUES(updated_at)
                    """,
                    (
                        account_id,
                        symbol,
                        investment_flow_id(account_id, symbol, replacement_id),
                        replacement_id,
                        str(replacement_payload.get("sourceAboxSnapshotId") or ""),
                        str(replacement.get("inference_generation_id") or ""),
                        str(replacement.get("selected_hypothesis_id") or ""),
                        str(replacement.get("action") or ""),
                        str(replacement.get("data_state") or ""),
                        str(replacement.get("validation_state") or ""),
                        str(replacement.get("decided_at") or ""),
                        str(replacement.get("updated_at") or stamp),
                    ),
                )
                current_repaired += 1
            cursor = connection.execute(
                "UPDATE investment_decision_follow_ups SET status = 'canceled', "
                "updated_at = %s, transitioned_at = %s WHERE episode_id IN ("
                + placeholders + ") AND status IN ('pending', 'ready')",
                (stamp, stamp, *episode_ids),
            )
            follow_ups = max(0, int(getattr(cursor, "rowcount", 0) or 0))
            cursor = connection.execute(
                "UPDATE investment_decision_outcome_targets SET status = 'excluded', "
                "exclusion_reason = 'invalid-legacy-outcome', updated_at = %s "
                "WHERE episode_id IN (" + placeholders + ") AND status <> 'observed'",
                (stamp, *episode_ids),
            )
            targets = max(0, int(getattr(cursor, "rowcount", 0) or 0))
            cursor = connection.execute(
                "UPDATE investment_action_plans SET status = 'canceled', updated_at = %s "
                "WHERE decision_episode_id IN (" + placeholders + ") "
                "AND NOT EXISTS (SELECT 1 FROM trade_execution_episodes execution "
                "WHERE execution.action_plan_id = investment_action_plans.plan_id)",
                (stamp, *episode_ids),
            )
            plans = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        return {
            "status": "quarantined",
            "quarantinedCount": len(episode_ids),
            "currentPointersRemoved": current_removed,
            "currentPointersRepaired": current_repaired,
            "followUpsCanceled": follow_ups,
            "outcomeTargetsExcluded": targets,
            "actionPlansCanceled": plans,
            "episodeIds": episode_ids,
        }

    def sync_outcome_targets(self, connection, episode: DecisionEpisode, stamp: str = "") -> Dict[str, object]:
        """Persist the immutable observation schedule in the decision transaction."""

        stamp = canonical_investment_timestamp(stamp) or utc_now_iso()
        facts = episode.facts_at_decision if isinstance(episode.facts_at_decision, dict) else {}
        calibration = facts.get("calibrationPolicy") if isinstance(facts.get("calibrationPolicy"), dict) else {}
        completeness = self.episode_outcome_contract_completeness(episode)
        contract = self.episode_outcome_contract(episode) if completeness.get("complete") else {}
        eligible = bool(
            episode.selected_hypothesis_id
            and calibration.get("eligible") is True
            and completeness.get("complete")
        )
        if not eligible:
            reason = str(
                calibration.get("reason")
                or ("outcome-contract-incomplete" if not completeness.get("complete") else "calibration-ineligible")
            )[:191]
            target_id = stable_id("decision-outcome-target-excluded", episode.episode_id)
            payload = {
                "requestId": target_id,
                "episodeId": episode.episode_id,
                "symbol": episode.symbol,
                "horizonMinutes": 0,
                "decidedAt": episode.decided_at,
                "targetAt": episode.decided_at,
                "status": "excluded",
                "exclusionReason": reason,
                "predictionContractCompleteness": completeness,
            }
            self.upsert_outcome_target(
                connection,
                target_id,
                episode,
                0,
                episode.decided_at,
                0,
                "",
                "excluded",
                reason,
                payload,
                stamp,
            )
            return {"status": "excluded", "targetCount": 0, "reason": reason}

        connection.execute(
            "DELETE FROM investment_decision_outcome_targets "
            "WHERE episode_id = %s AND status = 'excluded'",
            (episode.episode_id,),
        )
        fingerprint = str(contract.get("contractFingerprint") or "")
        maximum_delay = int(contract.get("maximumObservationDelayMinutes") or 0)
        target_count = 0
        for horizon_minutes in self.episode_outcome_horizons(episode):
            target_at = outcome_target_at(episode, horizon_minutes)
            if not target_at:
                continue
            target_id = stable_id(
                "decision-outcome-target",
                episode.episode_id,
                horizon_minutes,
                fingerprint,
            )
            payload = {
                "requestId": target_id,
                "episodeId": episode.episode_id,
                "symbol": episode.symbol,
                "subjectName": episode.subject_name,
                "market": str(facts.get("market") or ""),
                "currency": str(facts.get("currency") or ""),
                "horizonMinutes": horizon_minutes,
                "decidedAt": episode.decided_at,
                "targetAt": target_at,
                "maximumObservationDelayMinutes": maximum_delay,
                "requiredObservationDomains": contract.get("requiredObservationDomains") or [],
                "hypothesisOutcomeContract": contract,
                "benchmarkSymbol": contract_benchmark_symbol(contract, facts),
            }
            self.upsert_outcome_target(
                connection,
                target_id,
                episode,
                horizon_minutes,
                target_at,
                maximum_delay,
                fingerprint,
                "pending",
                "",
                payload,
                stamp,
            )
            target_count += 1
        return {"status": "scheduled", "targetCount": target_count}

    @staticmethod
    def upsert_outcome_target(
        connection,
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
        connection.execute(
            """
            INSERT INTO investment_decision_outcome_targets (
                target_id, episode_id, account_id, symbol, horizon_minutes,
                target_at, maximum_delay_minutes, contract_fingerprint,
                status, exclusion_reason, outcome_id, payload_json,
                created_at, updated_at, observed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', %s, %s, %s, '')
            ON DUPLICATE KEY UPDATE
                target_at = VALUES(target_at),
                maximum_delay_minutes = VALUES(maximum_delay_minutes),
                exclusion_reason = IF(
                    investment_decision_outcome_targets.status = 'observed',
                    investment_decision_outcome_targets.exclusion_reason,
                    VALUES(exclusion_reason)
                ),
                status = IF(
                    investment_decision_outcome_targets.status = 'observed',
                    investment_decision_outcome_targets.status,
                    VALUES(status)
                ),
                payload_json = VALUES(payload_json),
                updated_at = VALUES(updated_at)
            """,
            (
                target_id,
                episode.episode_id,
                episode.account_id,
                episode.symbol,
                int(horizon_minutes or 0),
                str(target_at or ""),
                int(maximum_delay_minutes or 0),
                str(contract_fingerprint or ""),
                str(status or "pending"),
                str(exclusion_reason or "")[:191],
                json_dumps(payload),
                stamp,
                stamp,
            ),
        )

    def backfill_outcome_targets(self, account_id: str, limit: int = 2000) -> Dict[str, object]:
        """One-time safe migration using only contracts frozen in each episode."""

        account_id = str(account_id or "")
        with self.transaction() as connection:
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
                    "FROM investment_decision_episodes WHERE episode_id IN ("
                    + placeholders
                    + ")",
                    tuple(episode_ids),
                ).fetchall()
                by_id = {
                    str(row.get("episode_id") or ""): row
                    for row in payload_rows or []
                }
                rows = [by_id[episode_id] for episode_id in episode_ids if episode_id in by_id]
            stamp = utc_now_iso()
            scheduled = 0
            excluded = 0
            for row in rows or []:
                episode = self.episode_from_row(row)
                result = self.sync_outcome_targets(connection, episode, stamp)
                scheduled += int(result.get("targetCount") or 0)
                excluded += 1 if result.get("status") == "excluded" else 0
        return {
            "status": "backfilled" if rows else "already-initialized",
            "episodeCount": len(rows or []),
            "scheduledTargetCount": scheduled,
            "excludedEpisodeCount": excluded,
        }

    def outcome_horizons(self) -> List[int]:
        return outcome_horizon_minutes(
            self.runtime_settings.get("investmentBrainOutcomeObservationMinutes") or "60,1440,7200,28800",
        )

    def outcome_minimum_samples(self) -> int:
        try:
            value = int(float(str(
                self.runtime_settings.get("hypothesisOutcomeReviewMinimumSamples")
                or self.runtime_settings.get("investmentBrainOutcomeReviewMinimumSamples")
                or "3"
            )))
        except (TypeError, ValueError):
            value = 3
        return max(1, min(1000, value))

    def episode_outcome_contract(self, episode: DecisionEpisode) -> Dict[str, object]:
        facts = episode.facts_at_decision if isinstance(episode.facts_at_decision, dict) else {}
        raw = facts.get("hypothesisOutcomeContract") if isinstance(facts.get("hypothesisOutcomeContract"), dict) else {}
        resolved = resolved_outcome_contract(
            raw,
            fallback_horizons=self.outcome_horizons(),
            fallback_minimum_samples=self.outcome_minimum_samples(),
            fallback_maximum_delay_minutes=self.outcome_max_delay_minutes(),
        )
        for key in [
            "contractVersion",
            "contractFingerprint",
            "criteriaOrigin",
            "effectiveAt",
            "selectedHypothesisId",
            "sourceRuleIds",
            "marketHypothesisId",
            "accountHypothesisOverlayId",
            "inferenceGenerationId",
            "marketIndependenceKey",
            "accountIndependenceKey",
            "sourceFactIndependenceKey",
            "predictionTarget",
            "expectedDirection",
            "expectedOutcome",
            "outcomeMetric",
            "falsificationContract",
        ]:
            if raw.get(key) not in (None, "", [], {}):
                resolved[key] = raw.get(key)
        return resolved

    @staticmethod
    def episode_outcome_contract_completeness(episode: DecisionEpisode) -> Dict[str, object]:
        facts = episode.facts_at_decision if isinstance(episode.facts_at_decision, dict) else {}
        raw = facts.get("hypothesisOutcomeContract") if isinstance(facts.get("hypothesisOutcomeContract"), dict) else {}
        return outcome_contract_completeness(raw)

    def episode_outcome_horizons(self, episode: DecisionEpisode) -> List[int]:
        return outcome_horizon_minutes(self.episode_outcome_contract(episode).get("outcomeHorizonMinutes"))

    def episode_outcome_max_delay_minutes(self, episode: DecisionEpisode) -> int:
        return int(self.episode_outcome_contract(episode).get("maximumObservationDelayMinutes") or self.outcome_max_delay_minutes())

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
        result = self.hydrate_follow_ups(episodes)
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

    def hydrate_follow_ups(self, episodes: Iterable[DecisionEpisode]) -> List[DecisionEpisode]:
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
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT episode_id, observable, payload_json "
                "FROM investment_decision_follow_ups WHERE episode_id IN (" + placeholders + ") "
                "ORDER BY created_at ASC, condition_id ASC",
                episode_ids,
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
            if (
                str(payload.get("status") or "") in {"satisfied", "invalidated", "expired"}
                and not bool(payload.get("transitionVerified"))
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

    def list_summaries(self, account_id: str = "", symbol: str = "", limit: int = 50) -> List[Dict[str, object]]:
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
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [{
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
        } for row in rows or []]

    def list_flow_heads(
        self,
        account_id: str = "",
        symbol: str = "",
        limit: int = 200,
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
        with self.connect() as connection:
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
            payload["decidedAt"] = canonical_investment_timestamp(row.get("decided_at")) or str(payload.get("decidedAt") or "")
            payload["updatedAt"] = str(row.get("flow_updated_at") or payload.get("updatedAt") or payload.get("decidedAt") or "")
            result.append(payload)
        return result

    def list_replay_records(
        self,
        account_id: str = "",
        symbol: str = "",
        limit: int = 500,
    ) -> List[Dict[str, object]]:
        """Read immutable decision payloads separately from later observations.

        The normal list view intentionally hydrates current follow-up state and
        outcomes. Historical replay needs the original payload plus those
        mutable records as separate streams so an application service can apply
        an explicit point-in-time cutoff.
        """

        where = []
        params: List[object] = []
        if account_id:
            where.append("account_id = %s")
            params.append(str(account_id))
        if symbol:
            where.append("symbol = %s")
            params.append(str(symbol).upper())
        params.append(max(1, min(2000, int(limit or 500))))
        sql = "SELECT episode_id, payload_json, status, decided_at, created_at FROM investment_decision_episodes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY decided_at DESC, episode_id DESC LIMIT %s"
        with self.connect() as connection:
            episode_rows = connection.execute(sql, tuple(params)).fetchall()
            episode_ids = [
                str(row.get("episode_id") or "")
                for row in episode_rows or []
                if str(row.get("episode_id") or "")
            ]
            outcome_rows = []
            follow_up_rows = []
            if episode_ids:
                placeholders = ",".join(["%s"] * len(episode_ids))
                outcome_rows = connection.execute(
                    "SELECT episode_id, observed_at, payload_json FROM investment_decision_outcomes "
                    "WHERE episode_id IN (" + placeholders + ") "
                    "ORDER BY observed_at ASC, outcome_id ASC",
                    tuple(episode_ids),
                ).fetchall()
                follow_up_rows = connection.execute(
                    "SELECT episode_id, observable, transitioned_at, payload_json "
                    "FROM investment_decision_follow_ups WHERE episode_id IN (" + placeholders + ") "
                    "ORDER BY created_at ASC, condition_id ASC",
                    tuple(episode_ids),
                ).fetchall()

        outcomes: Dict[str, List[Dict[str, object]]] = {}
        for row in outcome_rows or []:
            payload = _json_loads(row.get("payload_json"), {})
            if not payload:
                continue
            if not payload.get("observedAt"):
                payload["observedAt"] = canonical_investment_timestamp(row.get("observed_at"))
            outcomes.setdefault(str(row.get("episode_id") or ""), []).append(payload)

        follow_ups: Dict[str, Dict[str, List[Dict[str, object]]]] = {}
        for row in follow_up_rows or []:
            payload = _json_loads(row.get("payload_json"), {})
            if not payload:
                continue
            if not payload.get("transitionAt") and row.get("transitioned_at"):
                payload["transitionAt"] = canonical_investment_timestamp(row.get("transitioned_at"))
            buckets = follow_ups.setdefault(
                str(row.get("episode_id") or ""),
                {"tracked": [], "unsupported": []},
            )
            buckets["tracked" if bool(row.get("observable")) else "unsupported"].append(payload)

        result = []
        for row in episode_rows or []:
            episode_id = str(row.get("episode_id") or "")
            snapshot = _json_loads(row.get("payload_json"), {})
            stored_decided_at = canonical_investment_timestamp(row.get("decided_at"))
            if stored_decided_at:
                snapshot["decidedAt"] = stored_decided_at
            snapshot["recordedAt"] = canonical_investment_timestamp(row.get("created_at"))
            buckets = follow_ups.get(episode_id, {"tracked": [], "unsupported": []})
            result.append({
                "episodeSnapshot": snapshot,
                "persistedStatus": str(row.get("status") or ""),
                "outcomes": list(outcomes.get(episode_id, [])),
                "followUps": list(buckets["tracked"]),
                "unsupportedFollowUps": list(buckets["unsupported"]),
            })
        return result

    def latest_decision_memory(
        self,
        account_id: str,
        symbol: str,
        exclude_episode_id: str = "",
    ) -> Dict[str, object]:
        """Read the compact prior decision used by the next notification AI run.

        Notification continuity needs one action and its audit identity, not the
        episode's full hypothesis payload or outcome history. Keeping this read
        on indexed columns prevents one live alert from hydrating the heavier
        learning model.
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
        with self.connect() as connection:
            row = connection.execute(
                "SELECT episode_id, account_id, symbol, subject_name, selected_hypothesis_id, "
                "action, review_level, data_state, validation_state, inference_generation_id, "
                "status, decided_at, source "
                "FROM investment_decision_episodes WHERE " + " AND ".join(where)
                + " ORDER BY decided_at DESC, episode_id DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        if not row:
            return {}
        return {
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
            "decidedAt": canonical_investment_timestamp(row.get("decided_at")) or str(row.get("decided_at") or ""),
            "source": str(row.get("source") or ""),
        }

    def list_for_symbols(
        self,
        symbols: Iterable[str],
        account_id: str = "",
        limit_per_symbol: int = 20,
    ) -> List[DecisionEpisode]:
        """Fetch bounded episode history for many symbols without N+1 reads.

        A workspace can include many holdings and market hypotheses.  A union
        keeps the latest bounded history per symbol in a small number of
        database round trips instead of issuing one query for every card.
        """
        clean_symbols = list(dict.fromkeys(
            str(item or "").upper().strip()
            for item in symbols or []
            if str(item or "").strip()
        ))[:120]
        if not clean_symbols:
            return []
        per_symbol = max(1, min(80, int(limit_per_symbol or 20)))
        rows: List[Dict[str, object]] = []
        # Keep a generated query below operational limits for large accounts.
        for offset in range(0, len(clean_symbols), 24):
            chunk = clean_symbols[offset:offset + 24]
            statements = []
            params: List[object] = []
            for symbol in chunk:
                where = "symbol = %s"
                statement_params: List[object] = [symbol]
                if account_id:
                    where += " AND account_id = %s"
                    statement_params.append(str(account_id))
                statements.append(
                    "(SELECT payload_json, status, decided_at FROM investment_decision_episodes "
                    "WHERE " + where + " ORDER BY decided_at DESC, episode_id DESC LIMIT %s)"
                )
                params.extend(statement_params)
                params.append(per_symbol)
            sql = " UNION ALL ".join(statements)
            with self.connect() as connection:
                rows.extend(connection.execute(sql, tuple(params)).fetchall() or [])
        return self.hydrate_outcomes(self.episode_from_row(row) for row in rows)

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
        normalized_account_id = str(account_id or "")
        observed_stamp = canonical_investment_timestamp(observed_at) or utc_now_iso()
        target_limit = max(1, min(1000, int(limit or self.outcome_batch_size())))
        completed_accounts = getattr(self, "_outcome_target_backfill_completed_accounts", set())
        if normalized_account_id not in completed_accounts:
            backfill = self.backfill_outcome_targets(normalized_account_id)
            if str(backfill.get("status") or "") == "already-initialized":
                completed_accounts.add(normalized_account_id)
                self._outcome_target_backfill_completed_accounts = completed_accounts
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM investment_decision_outcome_targets
                WHERE account_id = %s AND status = 'pending' AND target_at <= %s
                ORDER BY target_at ASC, target_id ASC
                LIMIT %s
                """,
                (normalized_account_id, observed_stamp, target_limit),
            ).fetchall()
        targets: List[Dict[str, object]] = []
        for row in rows or []:
            payload = _json_loads(row.get("payload_json"), {})
            if payload:
                targets.append(payload)
        return targets

    def outcome_target_summary(self, account_id: str = "", symbol: str = "") -> Dict[str, object]:
        clauses = []
        params: List[object] = []
        if account_id:
            clauses.append("account_id = %s")
            params.append(str(account_id))
        if symbol:
            clauses.append("symbol = %s")
            params.append(str(symbol).upper())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        due_clause = (" AND " + " AND ".join(clauses)) if clauses else ""
        now = utc_now_iso()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count, MIN(target_at) AS oldest_target_at, "
                "MAX(updated_at) AS latest_updated_at "
                "FROM investment_decision_outcome_targets" + where + " GROUP BY status",
                tuple(params),
            ).fetchall()
            due = connection.execute(
                "SELECT COUNT(*) AS count, MIN(target_at) AS oldest_target_at "
                "FROM investment_decision_outcome_targets WHERE status = 'pending' "
                "AND target_at <= %s" + due_clause,
                tuple([now] + params),
            ).fetchone()
            latest = connection.execute(
                "SELECT MAX(observed_at) AS latest_observed_at FROM investment_decision_outcomes"
                + where,
                tuple(params),
            ).fetchone()
        states = {
            str(row.get("status") or "unknown"): {
                "count": int(row.get("count") or 0),
                "oldestTargetAt": str(row.get("oldest_target_at") or ""),
                "latestUpdatedAt": str(row.get("latest_updated_at") or ""),
            }
            for row in rows or []
        }
        due_count = int((due or {}).get("count") or 0)
        return {
            "status": "warning" if due_count else "ok",
            "checkedAt": now,
            "accountId": str(account_id or ""),
            "symbol": str(symbol or "").upper(),
            "pendingTargetCount": int((states.get("pending") or {}).get("count") or 0),
            "dueTargetCount": due_count,
            "oldestDueTargetAt": str((due or {}).get("oldest_target_at") or ""),
            "observedTargetCount": int((states.get("observed") or {}).get("count") or 0),
            "excludedTargetCount": int((states.get("excluded") or {}).get("count") or 0),
            "latestOutcomeObservedAt": str((latest or {}).get("latest_observed_at") or ""),
            "states": states,
            "contract": "durable-decision-outcome-target-v1",
        }

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
        transitions = self.evaluate_follow_up_observation(account_id, symbol, facts, observed_at)
        if transitions:
            facts["followUpTransitions"] = transitions
            facts["followUpTransitionCount"] = len(transitions)
        if not outcome_observation_is_usable(facts, observed_at):
            return []
        episodes = self.list(account_id=account_id, symbol=symbol, limit=self.outcome_batch_size())
        requests: List[Dict[str, object]] = []
        for episode in episodes:
            if not self.episode_outcome_contract_completeness(episode).get("complete"):
                continue
            outcome_horizon_minutes = due_outcome_horizon_minutes(
                episode,
                observed_at,
                self.episode_outcome_horizons(episode),
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

    def evaluate_follow_up_observation(
        self,
        account_id: str,
        symbol: str,
        facts: Dict[str, object],
        observed_at: str,
    ) -> List[Dict[str, object]]:
        """Advance only pending, observable conditions for one scoped subject."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT condition_id, payload_json
                FROM investment_decision_follow_ups
                WHERE account_id = %s AND symbol = %s
                  AND status = 'pending' AND observable = 1
                ORDER BY updated_at ASC, condition_id ASC
                LIMIT 80
                """,
                (str(account_id or ""), str(symbol or "").upper()),
            ).fetchall()
        transitions: List[Dict[str, object]] = []
        for row in rows or []:
            payload = _json_loads(row.get("payload_json"), {})
            updated, material = evaluate_follow_up_conditions([payload], facts, observed_at)
            if not updated or updated[0] == payload:
                continue
            condition = updated[0]
            stamp = utc_now_iso()
            transition_at = str(condition.get("transitionAt") or "") if material else ""
            with self.connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE investment_decision_follow_ups
                    SET status = %s, payload_json = %s, updated_at = %s,
                        transitioned_at = CASE WHEN %s <> '' THEN %s ELSE transitioned_at END
                    WHERE condition_id = %s AND status = 'pending'
                    """,
                    (
                        str(condition.get("status") or "pending"),
                        json_dumps(condition),
                        stamp,
                        transition_at,
                        transition_at,
                        str(row.get("condition_id") or ""),
                    ),
                )
            if material and int(getattr(cursor, "rowcount", 0) or 0) > 0:
                transitions.append(condition)
        return transitions

    def quarantine_unverified_legacy_follow_up_transitions(
        self,
        limit: int = 5000,
    ) -> Dict[str, object]:
        """Keep legacy terminal rows for audit without treating them as edges."""

        maximum = max(1, min(50000, int(limit or 5000)))
        stamp = utc_now_iso()
        quarantined = []
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT condition_id, payload_json FROM investment_decision_follow_ups "
                "WHERE status IN ('satisfied', 'invalidated', 'expired') "
                "ORDER BY updated_at, condition_id LIMIT %s",
                (maximum,),
            ).fetchall()
            for row in rows or []:
                payload = _json_loads(row.get("payload_json"), {})
                if bool(payload.get("transitionVerified")):
                    continue
                condition_id = str(row.get("condition_id") or "")
                payload.update({
                    "status": "legacy-unverified",
                    "transitionVerified": False,
                    "legacyTransitionState": "unverified",
                    "legacyTransitionQuarantinedAt": stamp,
                })
                cursor = connection.execute(
                    "UPDATE investment_decision_follow_ups SET status = 'legacy-unverified', "
                    "payload_json = %s, updated_at = %s WHERE condition_id = %s "
                    "AND status IN ('satisfied', 'invalidated', 'expired')",
                    (json_dumps(payload), stamp, condition_id),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) > 0:
                    quarantined.append(condition_id)
        return {
            "status": "quarantined" if quarantined else "unchanged",
            "quarantinedCount": len(quarantined),
            "conditionIds": quarantined,
        }

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
            contract = self.episode_outcome_contract(episode)
            contract_completeness = self.episode_outcome_contract_completeness(episode)
            if horizon_minutes not in self.episode_outcome_horizons(episode) or outcome_horizon_recorded(episode, horizon_minutes):
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
                and delay_minutes <= self.episode_outcome_max_delay_minutes(episode)
                and not list(contract_observation.get("missingObservationDomains") or [])
                and not missing_criterion_metrics
            )
            eligibility = (
                "eligible" if calibration_eligible
                else "excluded-incomplete-prediction-contract" if not contract_completeness.get("complete")
                else "excluded-contract-data-gap" if contract_observation.get("missingObservationDomains")
                else "excluded-criterion-data-gap" if missing_criterion_metrics
                else "excluded-delayed-observation"
            )
            outcome = ObservedOutcome(
                outcome_id=stable_id("decision-outcome", episode.episode_id, horizon_minutes),
                episode_id=episode.episode_id,
                observed_at=observed_at,
                price=current_price,
                profit_loss_rate=number(facts.get("profitLossRate")),
                price_change_from_decision_pct=change_pct,
                selected_hypothesis_status=str(evaluation.get("selectedHypothesisStatus") or "inconclusive"),
                payload={
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
                    "observationBasis": str(facts.get("observationBasis") or "subsequent-market-observation"),
                    "observationSource": str(facts.get("observationSource") or facts.get("provider") or ""),
                    "sourceAsOf": canonical_investment_timestamp(facts.get("sourceAsOf")) or observed_at,
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
                    "actualElapsedMinutes": round((observed_time - parse_datetime(episode.decided_at)).total_seconds() / 60.0, 2),
                    "observationDelayMinutes": round(delay_minutes, 2),
                    "observationTiming": "on-time" if delay_minutes <= self.episode_outcome_max_delay_minutes(episode) else "delayed",
                    "calibrationEligibility": eligibility,
                    "predictionContractCompleteness": contract_completeness,
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
            horizon_minutes = int((outcome.payload or {}).get("horizonMinutes") or 0)
            fingerprint = str((outcome.payload or {}).get("contractFingerprint") or "")
            connection.execute(
                "UPDATE investment_decision_outcome_targets "
                "SET status = 'observed', outcome_id = %s, observed_at = %s, updated_at = %s "
                "WHERE episode_id = %s AND horizon_minutes = %s AND contract_fingerprint = %s",
                (
                    outcome.outcome_id,
                    outcome.observed_at,
                    utc_now_iso(),
                    episode.episode_id,
                    horizon_minutes,
                    fingerprint,
                ),
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
            proposal_id=stable_id("learning-proposal", account_id, symbol, str(candidate.get("groupKey") or ""), ",".join(episode_ids)),
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
    return str(selected_hypothesis_payload(episode).get("stance") or "uncertain")


def selected_hypothesis_payload(episode: DecisionEpisode) -> Dict[str, object]:
    for item in episode.hypothesis_set.hypotheses:
        if item.hypothesis_id == episode.selected_hypothesis_id:
            return item.to_dict()
    return {}


def directional_hypothesis_status(stance: str, price_change_pct: float) -> str:
    if not price_change_pct or stance not in {"risk", "support"}:
        return "inconclusive"
    if stance == "risk":
        return "directionally-corroborated" if price_change_pct < 0 else "directionally-contradicted"
    return "directionally-corroborated" if price_change_pct > 0 else "directionally-contradicted"


def contract_benchmark_symbol(contract: Dict[str, object], facts: Dict[str, object] = None) -> str:
    source = dict(facts or {})
    explicit = str(source.get("benchmarkSymbol") or "").upper().strip()
    if explicit:
        return explicit
    for criterion in contract.get("criteria") or []:
        if not isinstance(criterion, dict):
            continue
        symbol = str(criterion.get("benchmarkSymbol") or "").upper().strip()
        if symbol:
            return symbol
    return ""


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
    due = []
    for value in outcome_horizon_minutes(raw_horizons):
        target = parse_datetime(outcome_target_at(episode, value))
        if target and observed >= target and not outcome_horizon_recorded(episode, value):
            due.append(value)
    return due


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
    target = decided + timedelta(minutes=int(horizon_minutes))
    facts = episode.facts_at_decision if isinstance(episode.facts_at_decision, dict) else {}
    market = str(facts.get("market") or "").upper().strip()
    currency = str(facts.get("currency") or "").upper().strip()
    is_crypto_market = market in {"CRYPTO", "COIN"} or currency in {"BTC", "ETH", "USDT", "USDC"}
    traditional_market = not is_crypto_market and (market in {
        "KR", "KOR", "KOREA", "KOSPI", "KOSDAQ", "KONEX", "KRX", "XKRX",
        "US", "USA", "NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "XNYS", "XNAS",
    } or currency in {"KRW", "USD"})
    if traditional_market:
        local_target = target.astimezone(market_timezone(market, currency))
        while local_target.weekday() >= 5:
            local_target += timedelta(days=1)
        is_kr_market = market in {
            "KR", "KOR", "KOREA", "KOSPI", "KOSDAQ", "KONEX", "KRX", "XKRX",
        } or currency == "KRW"
        open_hour, open_minute = (9, 0) if is_kr_market else (9, 30)
        close_hour, close_minute = (15, 30) if is_kr_market else (16, 0)
        session_open = local_target.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
        session_close = local_target.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
        if local_target < session_open:
            local_target = session_open
        elif local_target > session_close:
            local_target = session_open + timedelta(days=1)
            while local_target.weekday() >= 5:
                local_target += timedelta(days=1)
        target = local_target.astimezone(timezone.utc)
    return target.isoformat().replace("+00:00", "Z")


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
