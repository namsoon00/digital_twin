"""Prepare one decision, then write its owners on the supplied connection.

The frozen packet transfers the existing mutable domain episode and payload;
it does not introduce a second decision snapshot or persistence clock.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Optional
from digital_twin.modules.decisions.domain.investment_brain import (
    DecisionEpisode,
    canonical_investment_timestamp,
    scoped_decision_follow_ups,
)
from digital_twin.modules.decisions.domain.investment_decision_actionability import (
    persisted_decision_authorization,
)
from digital_twin.modules.decisions.domain.events import (
    investment_decision_changed_event,
    investment_validation_changed_event,
)
from digital_twin.modules.decisions.infrastructure import transaction_writes as decisions_writes
from digital_twin.modules.outcomes.infrastructure import transaction_writes as outcomes_writes
from digital_twin.modules.portfolio.infrastructure import transaction_writes as portfolio_writes
from digital_twin.modules.portfolio.domain.trade_execution import ActionPlan
from digital_twin.modules.read_models.domain.investment_flow import investment_flow_id
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from .outcome_policy import number


@dataclass(frozen=True)
class PreparedDecision:
    episode: DecisionEpisode
    plan: Optional[ActionPlan]
    stamp: str
    payload: Dict[str, object]
    flow_id: str


def prepare_decision(
    episode: DecisionEpisode, *, utc_now_iso: Callable[[], str]
) -> PreparedDecision:
    action = str(episode.action or "").upper()
    if action == "NO_ACTION":
        raise ValueError("NO_ACTION is an operational disposition and cannot be a DecisionEpisode.")
    if (
        episode.source == "v2-reasoning-case"
        and not str(episode.selected_hypothesis_id or "").strip()
    ):
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
    episode.portfolio_id = episode.portfolio_id or "portfolio:" + str(
        episode.account_id or "default"
    )
    plan = None
    if action in {"BUY", "ADD", "TRIM", "SELL"}:
        authorization = persisted_decision_authorization(episode)
        if not authorization.get("authorized"):
            raise ValueError(
                "Executable DecisionEpisode requires a complete actionability contract."
            )
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
    return PreparedDecision(episode, plan, stamp, payload, flow_id)


def write_decision(
    connection: BoundWriteConnection,
    prepared: PreparedDecision,
    *,
    _supersede_prior_follow_ups_for_current: Callable[..., int],
    _sync_outcome_targets: Callable[..., Dict[str, object]],
    insert_domain_event_with_connection: Callable[..., None],
) -> None:
    episode = prepared.episode
    plan = prepared.plan
    stamp = prepared.stamp
    payload = prepared.payload
    flow_id = prepared.flow_id
    current_row = connection.execute(
        "SELECT payload_json FROM investment_decision_episodes WHERE episode_id = %s",
        (episode.episode_id,),
    ).fetchone()
    prior_row = (
        current_row
        or connection.execute(
            "SELECT payload_json FROM investment_decision_episodes "
            "WHERE account_id = %s AND symbol = %s ORDER BY decided_at DESC, episode_id DESC LIMIT 1",
            (episode.account_id, episode.symbol),
        ).fetchone()
    )
    previous_payload = _json_loads(prior_row.get("payload_json"), {}) if prior_row else {}
    decisions_writes.upsert_decision_episode(
        connection=connection,
        episode=episode,
        payload=payload,
        stamp=stamp,
    )
    decisions_writes.advance_current_decision_flow(
        connection=connection,
        episode=episode,
        flow_id=flow_id,
        stamp=stamp,
    )
    _supersede_prior_follow_ups_for_current(
        connection,
        episode.account_id,
        episode.symbol,
        episode.episode_id,
        stamp,
    )
    decisions_writes.upsert_decision_flow_head(
        connection=connection,
        episode=episode,
        flow_id=flow_id,
        stamp=stamp,
    )
    if plan is not None:
        portfolio_writes.upsert_decision_action_plan(
            connection=connection,
            plan=plan,
            stamp=stamp,
        )
    _sync_outcome_targets(connection, episode, stamp)
    for condition in list(episode.follow_up_conditions or []) + list(
        episode.unsupported_follow_ups or []
    ):
        if not isinstance(condition, dict) or not str(condition.get("conditionId") or "").strip():
            continue
        outcomes_writes.upsert_decision_followup(
            connection=connection,
            condition=condition,
            episode=episode,
            stamp=stamp,
            _bound_number=number,
        )
    decision_fields = (
        "action",
        "reviewLevel",
        "dataState",
        "validationState",
        "selectedHypothesisId",
    )
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
