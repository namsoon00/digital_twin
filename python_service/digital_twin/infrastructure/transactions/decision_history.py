"""Compatibility API and transaction wiring for decision history.

Private helpers receive individual capabilities, never this repository.
"""

from digital_twin.modules.decisions.infrastructure import (
    transaction_writes as decisions_writes,
)
from digital_twin.modules.outcomes.infrastructure import (
    transaction_writes as outcomes_writes,
)
from digital_twin.modules.portfolio.infrastructure import (
    transaction_writes as portfolio_writes,
)
from contextlib import nullcontext
from datetime import timedelta, timezone
from typing import Dict, Iterable, List, Optional

from digital_twin.modules.decisions.domain.investment_brain import (
    DecisionEpisode,
    LearningProposal,
    ObservedOutcome,
    canonical_investment_timestamp,
    parse_investment_timestamp,
    stable_id,
    scoped_decision_follow_ups,
    utc_now_iso,
)
from digital_twin.modules.decisions.domain.investment_decision_history import (
    compact_decision_episode_memory,
)
from digital_twin.modules.outcomes.domain.hypothesis_observation import (
    ShadowHypothesisObservationEpisode,
)
from digital_twin.modules.decisions.domain.investment_decision_actionability import (
    persisted_decision_authorization,
)
from digital_twin.modules.outcomes.domain.decision_follow_up import evaluate_follow_up_conditions
from digital_twin.modules.outcomes.domain.hypothesis_outcome_contract import (
    observation_domain_status,
    outcome_contract_completeness,
    resolved_outcome_contract,
)
from digital_twin.modules.outcomes.contracts import evaluate_hypothesis_outcome
from digital_twin.modules.market_data.domain.market_time_series import market_timezone
from digital_twin.modules.market_data.domain.market_hours import infer_market_from_context
from digital_twin.modules.outcomes.domain.decision_performance import (
    contradiction_learning_candidates,
    evaluate_decision_performance,
)
from digital_twin.modules.portfolio.domain.trade_execution import ActionPlan
from digital_twin.modules.decisions.domain.events import (
    investment_decision_changed_event,
    investment_validation_changed_event,
)
from digital_twin.modules.read_models.domain.investment_flow import investment_flow_id
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
from digital_twin.infrastructure.operational_common import json_dumps


from .decision_history_parts import (
    decision_write,
    legacy_repair,
    outcome_schedule,
    target_repair,
    outcome_policy,
    episode_queries,
    episode_hydration,
    replay_queries,
    performance,
    target_queries,
    follow_ups,
    observation_records,
    shadow_observations,
    learning,
)
from .decision_history_parts.outcome_policy import (
    selected_hypothesis_stance,
    selected_hypothesis_payload,
    directional_hypothesis_status,
    contract_benchmark_symbol,
    due_outcome_horizon_minutes,
    due_outcome_horizon_minutes_all,
    outcome_horizon_minutes,
    outcome_horizon_recorded,
    outcome_target_at,
    market_outcome_target_at,
    outcome_observation_is_usable,
    outcome_is_calibration_eligible,
    parse_datetime,
    number,
)


class MySQLInvestmentDecisionEpisodeStore(MySQLOperationalConnection):
    def save(self, episode: DecisionEpisode, connection=None) -> DecisionEpisode:
        prepared = decision_write.prepare_decision(episode, utc_now_iso=utc_now_iso)
        transaction = self.transaction() if connection is None else nullcontext(connection)
        with transaction as connection:
            decision_write.write_decision(
                connection,
                prepared,
                _supersede_prior_follow_ups_for_current=self.supersede_prior_follow_ups_for_current,
                _sync_outcome_targets=self.sync_outcome_targets,
                insert_domain_event_with_connection=insert_domain_event_with_connection,
            )
        return episode

    @staticmethod
    def supersede_prior_follow_ups_for_current(
        connection,
        account_id: str,
        symbol: str,
        episode_id: str,
        stamp: str,
    ) -> int:
        return outcome_schedule.supersede_prior_follow_ups_for_current(
            connection,
            account_id,
            symbol,
            episode_id,
            stamp,
        )

    def supersede_noncurrent_follow_ups(self, limit: int = 5000) -> Dict[str, object]:
        return legacy_repair.supersede_noncurrent_follow_ups(
            limit,
            _transaction=self.transaction,
            utc_now_iso=utc_now_iso,
        )

    def quarantine_invalid_legacy_outcomes(self, limit: int = 5000) -> Dict[str, object]:
        return legacy_repair.quarantine_invalid_legacy_outcomes(
            limit,
            _transaction=self.transaction,
            utc_now_iso=utc_now_iso,
        )

    def sync_outcome_targets(
        self, connection, episode: DecisionEpisode, stamp: str = ""
    ) -> Dict[str, object]:
        return outcome_schedule.sync_outcome_targets(
            connection,
            episode,
            stamp,
            _episode_outcome_contract=self.episode_outcome_contract,
            _episode_outcome_contract_completeness=self.episode_outcome_contract_completeness,
            _episode_outcome_horizons=self.episode_outcome_horizons,
            _upsert_outcome_target=self.upsert_outcome_target,
        )

    def save_shadow_hypothesis_observations(
        self,
        episodes: Iterable[ShadowHypothesisObservationEpisode],
    ) -> List[ShadowHypothesisObservationEpisode]:
        return shadow_observations.save_shadow_hypothesis_observations(
            episodes,
            _sync_shadow_hypothesis_observation_targets=self.sync_shadow_hypothesis_observation_targets,
            _transaction=self.transaction,
            utc_now_iso=utc_now_iso,
        )

    def ontology_evolution_comparison(self, plan, observed_after=""):
        from .decision_history_parts.evolution_comparison import read_comparison
        return read_comparison(plan, connect=self.connect, observed_after=observed_after)

    def sync_shadow_hypothesis_observation_targets(
        self,
        connection,
        episode: ShadowHypothesisObservationEpisode,
        stamp: str = "",
    ) -> Dict[str, object]:
        return outcome_schedule.sync_shadow_hypothesis_observation_targets(
            connection,
            episode,
            stamp,
            _outcome_max_delay_minutes=self.outcome_max_delay_minutes,
            utc_now_iso=utc_now_iso,
        )

    def shadow_observation_episodes_by_ids(
        self,
        episode_ids: Iterable[str],
    ) -> Dict[str, ShadowHypothesisObservationEpisode]:
        return shadow_observations.shadow_observation_episodes_by_ids(
            episode_ids,
            _connect=self.connect,
        )

    def shadow_observation_episodes_for_claims(
        self,
        account_id: str,
        symbol: str,
        claim_contract_ids: Iterable[str],
        limit: int = 100,
    ) -> List[ShadowHypothesisObservationEpisode]:
        return shadow_observations.shadow_observation_episodes_for_claims(
            account_id,
            symbol,
            claim_contract_ids,
            limit,
            _connect=self.connect,
        )

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
        return outcome_schedule.upsert_outcome_target(
            connection,
            target_id,
            episode,
            horizon_minutes,
            target_at,
            maximum_delay_minutes,
            contract_fingerprint,
            status,
            exclusion_reason,
            payload,
            stamp,
        )

    def backfill_outcome_targets(self, account_id: str, limit: int = 2000) -> Dict[str, object]:
        return target_repair.backfill_outcome_targets(
            account_id,
            limit,
            _episode_from_row=self.episode_from_row,
            _sync_outcome_targets=self.sync_outcome_targets,
            _transaction=self.transaction,
            utc_now_iso=utc_now_iso,
        )

    def repair_pending_outcome_target_schedules(
        self,
        account_id: str,
        limit: int = 1000,
    ) -> Dict[str, object]:
        return target_repair.repair_pending_outcome_target_schedules(
            account_id,
            limit,
            _episode_outcome_contract_completeness=self.episode_outcome_contract_completeness,
            _transaction=self.transaction,
            utc_now_iso=utc_now_iso,
        )

    def outcome_horizons(self) -> List[int]:
        return outcome_policy.outcome_horizons(
            _runtime_settings=self.runtime_settings,
        )

    def outcome_minimum_samples(self) -> int:
        return outcome_policy.outcome_minimum_samples(
            _runtime_settings=self.runtime_settings,
        )

    def episode_outcome_contract(self, episode: DecisionEpisode) -> Dict[str, object]:
        return outcome_policy.episode_outcome_contract(
            episode,
            _outcome_horizons=self.outcome_horizons,
            _outcome_max_delay_minutes=self.outcome_max_delay_minutes,
            _outcome_minimum_samples=self.outcome_minimum_samples,
        )

    @staticmethod
    def episode_outcome_contract_completeness(episode: DecisionEpisode) -> Dict[str, object]:
        return outcome_policy.episode_outcome_contract_completeness(
            episode,
        )

    def episode_outcome_horizons(self, episode: DecisionEpisode) -> List[int]:
        return outcome_policy.episode_outcome_horizons(
            episode,
            _episode_outcome_contract=self.episode_outcome_contract,
        )

    def episode_outcome_max_delay_minutes(self, episode: DecisionEpisode) -> int:
        return outcome_policy.episode_outcome_max_delay_minutes(
            episode,
            _episode_outcome_contract=self.episode_outcome_contract,
            _outcome_max_delay_minutes=self.outcome_max_delay_minutes,
        )

    def outcome_batch_size(self) -> int:
        return outcome_policy.outcome_batch_size(
            _runtime_settings=self.runtime_settings,
        )

    def episode_from_row(self, row: Dict[str, object]) -> DecisionEpisode:
        return episode_hydration.episode_from_row(
            row,
        )

    def outcomes_from_rows(
        self, rows: Iterable[Dict[str, object]], default_episode_id: str = ""
    ) -> List[ObservedOutcome]:
        return episode_hydration.outcomes_from_rows(
            rows,
            default_episode_id,
        )

    def hydrate_outcomes(
        self,
        episodes: Iterable[DecisionEpisode],
        as_of: str = "",
    ) -> List[DecisionEpisode]:
        return episode_hydration.hydrate_outcomes(
            episodes,
            as_of,
            _connect=self.connect,
            _hydrate_follow_ups=self.hydrate_follow_ups,
            _outcomes_from_rows=self.outcomes_from_rows,
        )

    def hydrate_follow_ups(
        self,
        episodes: Iterable[DecisionEpisode],
        as_of: str = "",
    ) -> List[DecisionEpisode]:
        return episode_hydration.hydrate_follow_ups(
            episodes,
            as_of,
            _connect=self.connect,
        )

    def episodes_by_ids(self, episode_ids: Iterable[str]) -> Dict[str, DecisionEpisode]:
        return episode_queries.episodes_by_ids(
            episode_ids,
            _connect=self.connect,
            _episode_from_row=self.episode_from_row,
            _hydrate_outcomes=self.hydrate_outcomes,
        )

    def get(self, episode_id: str) -> Optional[DecisionEpisode]:
        return episode_queries.get_episode(
            episode_id,
            _connect=self.connect,
            _episode_from_row=self.episode_from_row,
            _hydrate_outcomes=self.hydrate_outcomes,
        )

    def list(
        self, account_id: str = "", symbol: str = "", limit: int = 50
    ) -> List[DecisionEpisode]:
        return episode_queries.list_episodes(
            account_id,
            symbol,
            limit,
            _connect=self.connect,
            _episode_from_row=self.episode_from_row,
            _hydrate_outcomes=self.hydrate_outcomes,
        )

    def list_summaries(
        self, account_id: str = "", symbol: str = "", limit: int = 50
    ) -> List[Dict[str, object]]:
        return episode_queries.list_summaries(
            account_id,
            symbol,
            limit,
            _connect=self.connect,
        )

    def list_flow_heads(
        self,
        account_id: str = "",
        symbol: str = "",
        limit: int = 200,
    ) -> List[Dict[str, object]]:
        return episode_queries.list_flow_heads(
            account_id,
            symbol,
            limit,
            _connect=self.connect,
        )

    def list_replay_records(
        self,
        account_id: str = "",
        symbol: str = "",
        limit: int = 500,
    ) -> List[Dict[str, object]]:
        return replay_queries.list_replay_records(
            account_id,
            symbol,
            limit,
            _connect=self.connect,
        )

    def latest_decision_memory(
        self,
        account_id: str,
        symbol: str,
        exclude_episode_id: str = "",
    ) -> Dict[str, object]:
        return episode_queries.latest_decision_memory(
            account_id,
            symbol,
            exclude_episode_id,
            _connect=self.connect,
        )

    def list_for_symbols(
        self,
        symbols: Iterable[str],
        account_id: str = "",
        limit_per_symbol: int = 20,
        as_of: str = "",
    ) -> List[DecisionEpisode]:
        return episode_queries.list_for_symbols(
            symbols,
            account_id,
            limit_per_symbol,
            as_of,
            _connect=self.connect,
            _episode_from_row=self.episode_from_row,
            _hydrate_outcomes=self.hydrate_outcomes,
        )

    def performance(
        self,
        account_id: str = "",
        symbol: str = "",
        limit: int = 500,
        as_of: str = "",
    ) -> Dict[str, object]:
        return performance.performance(
            account_id,
            symbol,
            limit,
            as_of,
            _outcome_coverage_population=self.outcome_coverage_population,
            _performance_archive_episode_count=self.performance_archive_episode_count,
            _performance_episodes=self.performance_episodes,
            _runtime_settings=self.runtime_settings,
            evaluate_decision_performance=evaluate_decision_performance,
        )

    def outcome_coverage_population(
        self,
        account_id: str = "",
        symbol: str = "",
        as_of: str = "",
    ) -> Dict[str, int]:
        return performance.outcome_coverage_population(
            account_id,
            symbol,
            as_of,
            _connect=self.connect,
            utc_now_iso=utc_now_iso,
        )

    def performance_archive_episode_count(
        self,
        account_id: str = "",
        symbol: str = "",
        as_of: str = "",
    ) -> int:
        return performance.performance_archive_episode_count(
            account_id,
            symbol,
            as_of,
            _connect=self.connect,
        )

    def performance_episodes(
        self,
        account_id: str = "",
        symbol: str = "",
        limit: int = 500,
        as_of: str = "",
    ) -> List[Dict[str, object]]:
        return performance.performance_episodes(
            account_id,
            symbol,
            limit,
            as_of,
            _connect=self.connect,
            _runtime_settings=self.runtime_settings,
            _shadow_performance_episodes=self.shadow_performance_episodes,
        )

    def shadow_performance_episodes(
        self,
        account_id: str = "",
        symbol: str = "",
        limit: int = 500,
        as_of: str = "",
    ) -> List[Dict[str, object]]:
        return performance.shadow_performance_episodes(
            account_id,
            symbol,
            limit,
            as_of,
            _connect=self.connect,
        )

    def outcome_history_for_symbols(
        self,
        symbols: Iterable[str],
        account_id: str = "",
        as_of: str = "",
        limit_per_symbol: int = 120,
        maximum_episode_count: int = 600,
    ) -> List[Dict[str, object]]:
        return performance.outcome_history_for_symbols(
            symbols,
            account_id,
            as_of,
            limit_per_symbol,
            maximum_episode_count,
            _performance_episodes=self.performance_episodes,
        )

    def outcomes_for_episode(self, episode_id: str, limit: int = 30) -> List[ObservedOutcome]:
        return episode_hydration.outcomes_for_episode(
            episode_id,
            limit,
            _connect=self.connect,
            _outcomes_from_rows=self.outcomes_from_rows,
        )

    def pending_outcome_targets(
        self,
        account_id: str,
        observed_at: str = "",
        limit: int = 0,
        include_future: bool = False,
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
        repaired_accounts = getattr(
            self, "_outcome_target_schedule_repair_completed_accounts", set()
        )
        if normalized_account_id not in repaired_accounts:
            self.repair_pending_outcome_target_schedules(normalized_account_id, limit=5000)
            repaired_accounts.add(normalized_account_id)
            self._outcome_target_schedule_repair_completed_accounts = repaired_accounts
        return target_queries.pending_outcome_targets(
            target_queries.PendingTargetRead(normalized_account_id, observed_stamp, target_limit, include_future),
            _connect=self.connect,
        )

    def outcome_collection_targets(self, account_id: str, limit: int = 200) -> List[Dict[str, object]]:
        return self.pending_outcome_targets(account_id, limit=limit, include_future=True)

    def record_outcome_baselines(self, account_id: str, records: Iterable[Dict[str, object]]) -> int:
        with self.transaction() as connection:
            return outcomes_writes.record_outcome_baselines(connection, account_id, records)

    def outcome_target_summary(self, account_id: str = "", symbol: str = "") -> Dict[str, object]:
        return target_queries.outcome_target_summary(
            account_id,
            symbol,
            _connect=self.connect,
            utc_now_iso=utc_now_iso,
        )

    def record_observation(
        self,
        account_id: str,
        symbol: str,
        facts: Dict[str, object],
        observed_at: str = "",
    ) -> List[ObservedOutcome]:
        return observation_records.record_observation(
            account_id,
            symbol,
            facts,
            observed_at,
            _episode_outcome_contract_completeness=self.episode_outcome_contract_completeness,
            _episode_outcome_horizons=self.episode_outcome_horizons,
            _evaluate_follow_up_observation=self.evaluate_follow_up_observation,
            _list=self.list,
            _outcome_batch_size=self.outcome_batch_size,
            _record_outcome_observations=self.record_outcome_observations,
            utc_now_iso=utc_now_iso,
        )

    def evaluate_follow_up_observation(
        self,
        account_id: str,
        symbol: str,
        facts: Dict[str, object],
        observed_at: str,
    ) -> List[Dict[str, object]]:
        return follow_ups.evaluate_follow_up_observation(
            account_id,
            symbol,
            facts,
            observed_at,
            _connect=self.connect,
            utc_now_iso=utc_now_iso,
        )

    def quarantine_unverified_legacy_follow_up_transitions(
        self,
        limit: int = 5000,
    ) -> Dict[str, object]:
        return legacy_repair.quarantine_unverified_legacy_follow_up_transitions(
            limit,
            _transaction=self.transaction,
            utc_now_iso=utc_now_iso,
        )

    def record_outcome_observations(
        self,
        account_id: str,
        observations: Iterable[Dict[str, object]],
    ) -> List[ObservedOutcome]:
        return observation_records.record_outcome_observations(
            account_id,
            observations,
            _episode_outcome_contract=self.episode_outcome_contract,
            _episode_outcome_contract_completeness=self.episode_outcome_contract_completeness,
            _episode_outcome_horizons=self.episode_outcome_horizons,
            _episode_outcome_max_delay_minutes=self.episode_outcome_max_delay_minutes,
            _episodes_by_ids=self.episodes_by_ids,
            _propose_learning_from_outcomes=self.propose_learning_from_outcomes,
            _record_shadow_hypothesis_outcome_observations=self.record_shadow_hypothesis_outcome_observations,
            _save_outcome=self.save_outcome,
        )

    def record_shadow_hypothesis_outcome_observations(
        self,
        account_id: str,
        observations: Iterable[Dict[str, object]],
    ) -> List[ObservedOutcome]:
        return shadow_observations.record_shadow_hypothesis_outcome_observations(
            account_id,
            observations,
            _outcome_max_delay_minutes=self.outcome_max_delay_minutes,
            _save_shadow_hypothesis_outcome=self.save_shadow_hypothesis_outcome,
            _shadow_observation_episodes_by_ids=self.shadow_observation_episodes_by_ids,
        )

    def save_shadow_hypothesis_outcome(
        self,
        episode: ShadowHypothesisObservationEpisode,
        outcome: ObservedOutcome,
    ) -> ObservedOutcome:
        return shadow_observations.save_shadow_hypothesis_outcome(
            episode,
            outcome,
            _transaction=self.transaction,
            utc_now_iso=utc_now_iso,
        )

    def outcome_max_delay_minutes(self) -> int:
        return outcome_policy.outcome_max_delay_minutes(
            _runtime_settings=self.runtime_settings,
        )

    def save_outcome(self, episode: DecisionEpisode, outcome: ObservedOutcome) -> ObservedOutcome:
        return observation_records.save_outcome(
            episode,
            outcome,
            _transaction=self.transaction,
            utc_now_iso=utc_now_iso,
        )

    def propose_learning_from_outcomes(
        self, account_id: str, symbol: str
    ) -> Optional[LearningProposal]:
        return learning.propose_learning_from_outcomes(
            account_id,
            symbol,
            _connect=self.connect,
            _runtime_settings=self.runtime_settings,
            _save_learning_proposal=self.save_learning_proposal,
        )

    def save_learning_proposal(self, proposal: LearningProposal) -> LearningProposal:
        return learning.save_learning_proposal(
            proposal,
            _connect=self.connect,
            utc_now_iso=utc_now_iso,
        )

    def list_learning_proposals(self, status: str = "", limit: int = 50) -> List[Dict[str, object]]:
        return learning.list_learning_proposals(
            status,
            limit,
            _connect=self.connect,
        )

    def review_learning_proposal(
        self, proposal_id: str, status: str, note: str = ""
    ) -> Dict[str, object]:
        return learning.review_learning_proposal(
            proposal_id,
            status,
            note,
            _connect=self.connect,
            _list_learning_proposals=self.list_learning_proposals,
            utc_now_iso=utc_now_iso,
        )
