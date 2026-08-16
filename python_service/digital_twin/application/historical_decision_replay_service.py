"""Read-only point-in-time audit for persisted investment decisions."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Mapping

from ..domain.point_in_time_replay import (
    DecisionReplayEnvelope,
    STRICT_REPLAY_MODE,
    observations_as_of,
)


TRANSITIONED_FOLLOW_UP_STATES = {"satisfied", "invalidated", "expired"}


def _mapping(value: object) -> Dict[str, object]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        payload = value.to_dict()
        return dict(payload or {}) if isinstance(payload, Mapping) else {}
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _record(value: object) -> Dict[str, object]:
    row = _mapping(value)
    if "episodeSnapshot" in row:
        return {
            "episodeSnapshot": _mapping(row.get("episodeSnapshot")),
            "outcomes": [_mapping(item) for item in row.get("outcomes") or []],
            "followUps": [_mapping(item) for item in row.get("followUps") or []],
            "unsupportedFollowUps": [_mapping(item) for item in row.get("unsupportedFollowUps") or []],
        }
    episode = row
    return {
        "episodeSnapshot": episode,
        "outcomes": [_mapping(item) for item in episode.get("outcomes") or []],
        "followUps": [_mapping(item) for item in episode.get("followUpConditions") or []],
        "unsupportedFollowUps": [_mapping(item) for item in episode.get("unsupportedFollowUps") or []],
    }


def _transition_rows(rows: Iterable[object]) -> List[Dict[str, object]]:
    return [
        row
        for row in (_mapping(item) for item in rows or [])
        if str(row.get("status") or "").lower() in TRANSITIONED_FOLLOW_UP_STATES
        or row.get("transitionAt") not in (None, "")
    ]


class HistoricalDecisionReplayService:
    """Build replay envelopes and prove that future observations stay outside inputs."""

    def __init__(self, decision_episode_store=None):
        self.decision_episode_store = decision_episode_store

    def load_records(self, account_id: str, symbol: str, limit: int) -> List[Dict[str, object]]:
        store = self.decision_episode_store
        if not store:
            return []
        if hasattr(store, "list_replay_records"):
            rows = store.list_replay_records(account_id=account_id, symbol=symbol, limit=limit)
        elif hasattr(store, "list"):
            rows = store.list(account_id=account_id, symbol=symbol, limit=limit)
        else:
            return []
        return [_record(item) for item in rows or []]

    def run(
        self,
        *,
        account_id: str = "",
        symbol: str = "",
        limit: int = 500,
        include_cases: bool = False,
        case_limit: int = 30,
        replay_mode: str = STRICT_REPLAY_MODE,
    ) -> Dict[str, object]:
        bounded_limit = max(1, min(2000, int(limit or 500)))
        if not self.decision_episode_store:
            return self.unavailable("결정 에피소드 저장소가 구성되지 않았습니다.")
        records = self.load_records(account_id, str(symbol or "").upper(), bounded_limit)
        if not records:
            return {
                **self.base_result(replay_mode),
                "status": "no-history",
                "summary": "시점 고정으로 재생할 과거 판단이 없습니다.",
                "episodeCount": 0,
            }

        envelopes: Dict[str, DecisionReplayEnvelope] = {}
        records_by_id: Dict[str, Dict[str, object]] = {}
        replay_classes = Counter()
        engine_versions = Counter()
        contract_versions = Counter()
        missing_exact_input_fields = Counter()
        missing_engine_comparison_fields = Counter()
        temporal_future_count = 0
        temporal_invalid_count = 0
        temporal_coarse_count = 0
        future_fact_samples: List[Dict[str, object]] = []
        for record in records:
            snapshot = record["episodeSnapshot"]
            envelope = DecisionReplayEnvelope.create(snapshot, replay_mode=replay_mode)
            if envelope.episode_id:
                envelopes[envelope.episode_id] = envelope
                records_by_id[envelope.episode_id] = record
            replay_classes[envelope.replay_class()] += 1
            engine_versions[str(envelope.engine_manifest.get("engineVersion") or "missing")] += 1
            contract_versions[str(envelope.engine_manifest.get("outcomeContractVersion") or "legacy-or-missing")] += 1
            missing_exact_input_fields.update(envelope.engine_manifest.get("missingExactInputFields") or [])
            missing_engine_comparison_fields.update(envelope.engine_manifest.get("missingEngineComparisonFields") or [])
            temporal_future_count += int(envelope.temporal_assessment.get("futureTimestampCount") or 0)
            temporal_invalid_count += int(envelope.temporal_assessment.get("invalidTimestampCount") or 0)
            temporal_coarse_count += int(envelope.temporal_assessment.get("coarseTimestampCount") or 0)
            for item in envelope.temporal_assessment.get("futureTimestampSamples") or []:
                if len(future_fact_samples) >= 40:
                    break
                future_fact_samples.append({
                    "episodeId": envelope.episode_id,
                    "symbol": envelope.symbol,
                    "decisionAt": envelope.decision_at,
                    "knowledgeCutoffAt": envelope.knowledge_cutoff_at,
                    "path": str(item.get("path") or ""),
                    "value": item.get("value"),
                    "reason": str(item.get("reason") or ""),
                })

        grouped: Dict[tuple, List[DecisionReplayEnvelope]] = defaultdict(list)
        for envelope in envelopes.values():
            grouped[(envelope.account_id, envelope.symbol)].append(envelope)

        pair_count = 0
        pairs_with_known_outcome = 0
        outcomes_known = 0
        outcomes_future = 0
        outcomes_invalid = 0
        follow_up_transitions_known = 0
        follow_up_transitions_future = 0
        follow_up_transitions_invalid = 0
        case_by_episode: Dict[str, Dict[str, object]] = {}
        for series in grouped.values():
            ordered = sorted(series, key=lambda item: (item.decision_at, item.episode_id))
            for previous, current in zip(ordered, ordered[1:]):
                pair_count += 1
                previous_record = records_by_id.get(previous.episode_id, {})
                outcome_partition = observations_as_of(
                    previous_record.get("outcomes") or [],
                    current.knowledge_cutoff_at,
                    timestamp_fields=("observedAt",),
                )
                transition_partition = observations_as_of(
                    _transition_rows(previous_record.get("followUps") or []),
                    current.knowledge_cutoff_at,
                    timestamp_fields=("transitionAt", "observedAt"),
                )
                known = int(outcome_partition["includedCount"])
                outcomes_known += known
                outcomes_future += int(outcome_partition["futureExcludedCount"])
                outcomes_invalid += int(outcome_partition["invalidExcludedCount"]) + int(
                    outcome_partition["missingTimestampExcludedCount"]
                )
                if known:
                    pairs_with_known_outcome += 1
                follow_up_transitions_known += int(transition_partition["includedCount"])
                follow_up_transitions_future += int(transition_partition["futureExcludedCount"])
                follow_up_transitions_invalid += int(transition_partition["invalidExcludedCount"]) + int(
                    transition_partition["missingTimestampExcludedCount"]
                )
                case_by_episode[previous.episode_id] = {
                    "nextDecisionAt": current.decision_at,
                    "nextKnowledgeCutoffAt": current.knowledge_cutoff_at,
                    "knownOutcomeCount": known,
                    "futureOutcomeExcludedCount": int(outcome_partition["futureExcludedCount"]),
                    "knownFollowUpTransitionCount": int(transition_partition["includedCount"]),
                    "futureFollowUpTransitionExcludedCount": int(transition_partition["futureExcludedCount"]),
                }

        duplicate_outcome_keys = self.duplicate_outcome_keys(records)
        exact_input_count = int(replay_classes.get("exact-input-replay") or 0)
        engine_comparison_ready_count = sum(
            1
            for envelope in envelopes.values()
            if envelope.replay_class() == "exact-input-replay"
            and not envelope.engine_manifest.get("missingEngineComparisonFields")
        )
        result = {
            **self.base_result(replay_mode),
            "status": "completed",
            "episodeCount": len(records),
            "accountSymbolSeriesCount": len(grouped),
            "replayClassCounts": dict(sorted(replay_classes.items())),
            "engineVersionCounts": dict(sorted(engine_versions.items())),
            "outcomeContractVersionCounts": dict(sorted(contract_versions.items())),
            "missingExactInputFieldCounts": dict(sorted(missing_exact_input_fields.items())),
            "missingEngineComparisonFieldCounts": dict(sorted(missing_engine_comparison_fields.items())),
            "exactInputReplayCount": exact_input_count,
            "engineComparisonReadyCount": engine_comparison_ready_count,
            "pointInTimeIntegrity": {
                "passed": temporal_future_count == 0 and temporal_invalid_count == 0 and not duplicate_outcome_keys,
                "futureFactTimestampCount": temporal_future_count,
                "invalidFactTimestampCount": temporal_invalid_count,
                "coarseFactTimestampCount": temporal_coarse_count,
                "futureFactSamples": future_fact_samples,
                "duplicateOutcomeKeys": duplicate_outcome_keys[:100],
            },
            "historicalContinuity": {
                "adjacentDecisionPairs": pair_count,
                "pairsWithOutcomeKnownByNextDecision": pairs_with_known_outcome,
                "outcomesKnownByNextDecision": outcomes_known,
                "futureOutcomesExcluded": outcomes_future,
                "invalidOrUntimedOutcomesExcluded": outcomes_invalid,
                "followUpTransitionsKnownByNextDecision": follow_up_transitions_known,
                "futureFollowUpTransitionsExcluded": follow_up_transitions_future,
                "invalidOrUntimedFollowUpTransitionsExcluded": follow_up_transitions_invalid,
                "futureInformationAccepted": False,
                "preventedLookAheadObservationCount": outcomes_future + follow_up_transitions_future,
            },
            "engineExecution": {
                "status": "not-run",
                "reason": "시점 고정 입력 감사 단계이며 V1/V2 엔진은 아직 호출하지 않았습니다.",
                "notificationDeliveryEnabled": False,
                "operationalAboxWriteEnabled": False,
            },
            "summary": self.summary(
                len(records), exact_input_count, engine_comparison_ready_count,
                temporal_future_count, temporal_invalid_count, outcomes_future,
            ),
        }
        if include_cases:
            bounded_case_limit = max(1, min(100, int(case_limit or 30)))
            ordered_envelopes = sorted(
                envelopes.values(),
                key=lambda item: (item.decision_at, item.episode_id),
                reverse=True,
            )[:bounded_case_limit]
            result["cases"] = [
                {
                    **envelope.to_dict(include_facts=False),
                    "continuityToNext": case_by_episode.get(envelope.episode_id) or {},
                }
                for envelope in ordered_envelopes
            ]
        return result

    def duplicate_outcome_keys(self, records: Iterable[Mapping[str, object]]) -> List[str]:
        seen = set()
        duplicates = []
        for record in records or []:
            episode_id = str(_mapping(record.get("episodeSnapshot")).get("episodeId") or "")
            for outcome in record.get("outcomes") or []:
                row = _mapping(outcome)
                payload = _mapping(row.get("payload"))
                horizon = str(payload.get("horizonMinutes") or "")
                key = episode_id + "|" + horizon
                if episode_id and horizon and key in seen and key not in duplicates:
                    duplicates.append(key)
                seen.add(key)
        return duplicates

    def base_result(self, replay_mode: str) -> Dict[str, object]:
        return {
            "source": "persisted-decision-snapshot+point-in-time-observations",
            "replayMode": str(replay_mode or STRICT_REPLAY_MODE),
            "mutated": False,
            "automaticDeployment": False,
            "decisionEligibility": "historical-replay-only",
            "notificationDeliveryEnabled": False,
            "operationalAboxWriteEnabled": False,
        }

    def unavailable(self, reason: str) -> Dict[str, object]:
        return {
            **self.base_result(STRICT_REPLAY_MODE),
            "status": "unavailable",
            "reason": str(reason or ""),
            "episodeCount": 0,
        }

    def summary(
        self,
        episode_count: int,
        exact_input_count: int,
        engine_comparison_ready_count: int,
        future_fact_count: int,
        invalid_fact_count: int,
        future_outcome_count: int,
    ) -> str:
        if future_fact_count or invalid_fact_count:
            return (
                "과거 판단 " + str(episode_count) + "건을 읽었지만 판단 입력에서 시점 위반을 발견해 "
                "엔진 비교를 차단했습니다."
            )
        return (
            "과거 판단 " + str(episode_count) + "건 중 원본 입력 재생 가능 " + str(exact_input_count)
            + "건을 확인했고, 판단 뒤 생긴 관측 " + str(future_outcome_count)
            + "건은 입력에서 제외했습니다. 완전한 V1/V2 비교 매니페스트를 갖춘 판단은 "
            + str(engine_comparison_ready_count) + "건입니다."
        )
