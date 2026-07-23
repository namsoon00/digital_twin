"""Persist and expose lifecycle changes for TypeDB-materialized hypotheses."""

from dataclasses import replace
from typing import Dict, Iterable, List

from ..domain.events import hypothesis_lifecycle_transitioned_event
from ..domain.hypothesis_lifecycle import (
    TERMINAL_HYPOTHESIS_LIFECYCLE_STATES,
    HypothesisLifecycleRecord,
    HypothesisLifecycleSnapshot,
    lifecycle_context_summary,
    lifecycle_snapshots_from_relation_context,
    record_for_absent_snapshot,
    record_for_snapshot,
    snapshot_expiry_reason,
)
from ..domain.ontology_inference_context import (
    inferencebox_from_snapshot,
    relation_contexts_from_snapshot,
)
from ..domain.ontology_observation_quality import position_observation_profiles
from ..domain.portfolio import AccountSnapshot


class HypothesisLifecycleService:
    """Audit active TypeDB paths after a successful graph projection.

    A lifecycle update is intentionally downstream of InferenceBox. It cannot
    create an investment opinion or resurrect a failed TypeDB generation.
    """

    def __init__(self, store, event_publisher=None, settings: Dict[str, object] = None):
        self.store = store
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})

    def observe_snapshot(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        inferencebox = inferencebox_from_snapshot(snapshot)
        observed_at = str(snapshot.generated_at or inferencebox.get("inferenceGenerationAt") or "")
        if not self.inference_is_reconcilable(inferencebox):
            payload = {
                "version": "typedb-hypothesis-lifecycle-v1",
                "status": "skipped-unhealthy-inference",
                "reason": self.inference_health_reason(inferencebox),
                "observedAt": observed_at,
                "bySymbol": {},
            }
            snapshot.metadata["hypothesisLifecycle"] = payload
            return payload

        contexts = relation_contexts_from_snapshot(snapshot, self.settings)
        current_snapshots = [
            item
            for context in contexts.values()
            for item in lifecycle_snapshots_from_relation_context(context, observed_at=observed_at)
        ]
        symbols = self.subject_symbols(snapshot)
        previous_by_key = self.current_for_subjects(snapshot.account_id, symbols)
        next_by_key: Dict[str, HypothesisLifecycleRecord] = dict(previous_by_key)
        transitions = []
        active_keys = set()
        for lifecycle_snapshot in current_snapshots:
            active_keys.add(lifecycle_snapshot.lifecycle_key)
            previous = previous_by_key.get(lifecycle_snapshot.lifecycle_key)
            record, transition = record_for_snapshot(previous, lifecycle_snapshot, observed_at)
            if transition is None:
                if record != previous:
                    self.store.save(record)
                next_by_key[record.lifecycle_key] = record
                continue
            self.store.save(record, transition)
            next_by_key[record.lifecycle_key] = record
            if transition.previous_state != transition.current_state or transition.material_change:
                transitions.append((record, transition))
                self.publish_transition(record, transition)

        if self.generation_covers_subjects(inferencebox, symbols):
            profiles_by_symbol = self.observation_profiles_by_symbol(snapshot, inferencebox)
            for key, previous in previous_by_key.items():
                if key in active_keys or previous.state in TERMINAL_HYPOTHESIS_LIFECYCLE_STATES:
                    continue
                expiry_reason = self.absent_record_expiry_reason(previous, profiles_by_symbol.get(previous.symbol) or {}, observed_at)
                record, transition = record_for_absent_snapshot(previous, observed_at, expiry_reason)
                if transition is None:
                    continue
                self.store.save(record, transition)
                next_by_key[key] = record
                transitions.append((record, transition))
                self.publish_transition(record, transition)

        by_symbol: Dict[str, List[HypothesisLifecycleRecord]] = {}
        for record in next_by_key.values():
            if record.symbol in symbols:
                by_symbol.setdefault(record.symbol, []).append(record)
        payload = {
            "version": "typedb-hypothesis-lifecycle-v1",
            "status": "ok",
            "observedAt": observed_at,
            "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
            "bySymbol": {
                symbol: lifecycle_context_summary(records)
                for symbol, records in sorted(by_symbol.items())
            },
            "transitionCount": len(transitions),
        }
        snapshot.metadata["hypothesisLifecycle"] = payload
        return payload

    def inference_is_reconcilable(self, inferencebox: Dict[str, object]) -> bool:
        if not isinstance(inferencebox, dict):
            return False
        if str(inferencebox.get("status") or "").lower() != "ok":
            return False
        if not self.enabled_value(inferencebox.get("nativeTypeDbReasoningUsed")):
            return False
        if not self.enabled_value(inferencebox.get("generationAligned")):
            return False
        return bool(str(inferencebox.get("inferenceGenerationId") or "").strip())

    @staticmethod
    def enabled_value(value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def inference_health_reason(self, inferencebox: Dict[str, object]) -> str:
        if not isinstance(inferencebox, dict) or not inferencebox:
            return "InferenceBox가 없습니다."
        if str(inferencebox.get("status") or "").lower() != "ok":
            return "InferenceBox 상태가 정상(ok)이 아닙니다."
        if not self.enabled_value(inferencebox.get("nativeTypeDbReasoningUsed")):
            return "TypeDB native rule 물질화 결과가 없어 가설 수명주기를 갱신하지 않았습니다."
        if not self.enabled_value(inferencebox.get("generationAligned")):
            return "InferenceBox가 현재 ABox 세대와 정렬되지 않아 이전 가설을 무효화하지 않았습니다."
        return "InferenceBox 세대 식별자가 없습니다."

    def subject_symbols(self, snapshot: AccountSnapshot) -> set:
        return {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(getattr(position, "symbol", "") or "").strip() and not position.is_cash()
        }

    def current_for_subjects(self, account_id: str, symbols: Iterable[str]) -> Dict[str, HypothesisLifecycleRecord]:
        if hasattr(self.store, "current_for_subjects"):
            return dict(self.store.current_for_subjects(account_id, symbols) or {})
        rows = self.store.list_current(account_id=account_id, limit=1000) if hasattr(self.store, "list_current") else []
        allowed = {str(item or "").upper().strip() for item in symbols or [] if str(item or "").strip()}
        return {
            item.lifecycle_key: item
            for item in rows or []
            if isinstance(item, HypothesisLifecycleRecord) and item.symbol in allowed
        }

    def generation_covers_subjects(self, inferencebox: Dict[str, object], symbols: Iterable[str]) -> bool:
        targets = {
            str(item or "").upper().strip()
            for item in (
                inferencebox.get("targetSymbols")
                or inferencebox.get("symbols")
                or []
            )
            if str(item or "").strip()
        }
        required = {str(item or "").upper().strip() for item in symbols or [] if str(item or "").strip()}
        # A healthy inference status alone is not proof that every current
        # symbol was evaluated. Without an explicit target manifest we retain
        # prior paths rather than converting an incomplete read into a false
        # invalidation.
        return bool(targets) and required.issubset(targets)

    def observation_profiles_by_symbol(
        self,
        snapshot: AccountSnapshot,
        inferencebox: Dict[str, object],
    ) -> Dict[str, Dict[str, Dict[str, object]]]:
        result = {}
        for position in list(snapshot.positions or []) + list(snapshot.watchlist or []):
            symbol = str(getattr(position, "symbol", "") or "").upper().strip()
            if not symbol or position.is_cash() or symbol in result:
                continue
            result[symbol] = position_observation_profiles(position, {
                "settings": self.settings,
                "asOf": str(inferencebox.get("inferenceGenerationAt") or snapshot.generated_at or ""),
            })
        return result

    def absent_record_expiry_reason(
        self,
        record: HypothesisLifecycleRecord,
        profiles: Dict[str, Dict[str, object]],
        observed_at: str,
    ) -> str:
        previous_snapshot = HypothesisLifecycleSnapshot.from_dict(record.snapshot)
        refreshed = replace(
            previous_snapshot,
            observation_profiles={
                str(key): dict(value)
                for key, value in dict(profiles or {}).items()
                if isinstance(value, dict)
            },
            observed_at=observed_at or previous_snapshot.observed_at,
        )
        return snapshot_expiry_reason(refreshed, observed_at)

    def publish_transition(self, record: HypothesisLifecycleRecord, transition) -> None:
        if not self.event_publisher:
            return
        payload = {
            **transition.to_dict(),
            "accountId": record.account_id,
            "marketId": record.market_id,
            "symbol": record.symbol,
        }
        self.event_publisher.publish(hypothesis_lifecycle_transitioned_event(payload))
