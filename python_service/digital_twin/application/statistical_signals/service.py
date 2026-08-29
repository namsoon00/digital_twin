"""Build immutable feature and model-signal packets inside one durable job."""

from __future__ import annotations

import time
from typing import Dict, Iterable, Mapping

from ...domain.market_time_series import parse_timestamp
from ...domain.statistical_signals import (
    CAPITAL_FLOW_SHADOW_RELEASE_ID,
    DEFAULT_FLOW_SIGNAL_RELEASE_ID,
    DEFAULT_PRICE_SIGNAL_RELEASE_ID,
    ModelSignalBundle,
    score_graph_hypothesis_contracts,
    score_flow_feature_snapshot,
    score_temporal_feature_snapshot,
)
from ...domain.time_series_storage import (
    TEMPORAL_FEATURE_SET_VERSION,
    TemporalFeatureSnapshot,
    TimeSeriesWatermark,
)


def _row_timestamp(row: Mapping[str, object]) -> str:
    for key in (
        "knownAt", "known_at", "receivedAt", "received_at",
        "generatedAt", "observed_at", "observedAt", "updatedAt",
        "bucketAt", "bucket_at",
    ):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _event_timestamp(row: Mapping[str, object]) -> str:
    for key in ("bucketAt", "bucket_at", "generatedAt", "observed_at", "observedAt"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _point_in_time_windows(
    windows: Mapping[str, object],
    cutoff_at: object,
) -> tuple[Dict[str, object], Dict[str, object]]:
    """Exclude observations that were not knowable at the decision cutoff."""

    cutoff_text = str(cutoff_at or "").strip()
    cutoff = parse_timestamp(cutoff_text)
    if not cutoff:
        return dict(windows or {}), {
            "status": "unbounded",
            "knowledgeCutoffAt": cutoff_text,
            "keptRowCount": 0,
            "removedFutureRowCount": 0,
            "removedUndatedRowCount": 0,
            "affectedWindows": [],
        }
    result: Dict[str, object] = {}
    kept_count = 0
    future_count = 0
    undated_count = 0
    affected = []
    for symbol, by_window in dict(windows or {}).items():
        if not isinstance(by_window, Mapping):
            continue
        filtered_windows = {}
        for window, rows in by_window.items():
            if not isinstance(rows, list):
                continue
            filtered_rows = []
            removed_here = 0
            for raw in rows:
                if not isinstance(raw, Mapping):
                    undated_count += 1
                    removed_here += 1
                    continue
                row = dict(raw)
                knowledge_at = parse_timestamp(_row_timestamp(row))
                event_at = parse_timestamp(_event_timestamp(row))
                if not knowledge_at or not event_at:
                    undated_count += 1
                    removed_here += 1
                    continue
                if knowledge_at > cutoff or event_at > cutoff:
                    future_count += 1
                    removed_here += 1
                    continue
                filtered_rows.append(row)
                kept_count += 1
            filtered_windows[str(window)] = filtered_rows
            if removed_here:
                affected.append(str(symbol).upper() + ":" + str(window).upper())
        result[str(symbol)] = filtered_windows
    return result, {
        "status": "filtered" if future_count or undated_count else "verified",
        "knowledgeCutoffAt": cutoff_text,
        "keptRowCount": kept_count,
        "removedFutureRowCount": future_count,
        "removedUndatedRowCount": undated_count,
        "affectedWindows": sorted(set(affected)),
    }


def _latest_observed_at(windows: Mapping[str, object], fallback: object = "") -> str:
    values = []
    for by_window in dict(windows or {}).values():
        if not isinstance(by_window, Mapping):
            continue
        for rows in by_window.values():
            if not isinstance(rows, list):
                continue
            values.extend(
                _row_timestamp(row)
                for row in rows
                if isinstance(row, Mapping)
            )
    observed = max((value for value in values if value), default="")
    return observed or str(fallback or "").strip()


class StatisticalSignalPipelineService:
    """Pure scoring plus optional idempotent persistence.

    The parent reasoning work item is already durable and latest-wins. Keeping
    this bounded CPU stage inside that work item avoids another queue hop while
    preserving an explicit input/output contract and independent persistence.
    """

    def __init__(
        self,
        feature_snapshot_store=None,
        signal_store=None,
        model_release_id: str = DEFAULT_PRICE_SIGNAL_RELEASE_ID,
        model_release_ids: Iterable[str] = None,
        feature_set_version: str = TEMPORAL_FEATURE_SET_VERSION,
    ):
        self.feature_snapshot_store = feature_snapshot_store
        self.signal_store = signal_store
        self.model_release_id = str(model_release_id or DEFAULT_PRICE_SIGNAL_RELEASE_ID)
        requested_releases = tuple(
            str(value or "").strip()
            for value in (model_release_ids or (
                self.model_release_id,
                DEFAULT_FLOW_SIGNAL_RELEASE_ID,
                CAPITAL_FLOW_SHADOW_RELEASE_ID,
            ))
            if str(value or "").strip()
        )
        self.model_release_ids = tuple(dict.fromkeys(requested_releases))
        self.feature_set_version = str(feature_set_version or TEMPORAL_FEATURE_SET_VERSION)

    def run(
        self,
        account_id: object,
        backend_id: object,
        windows: Mapping[str, object],
        as_of: object = "",
        source_event_id: object = "",
        graph=None,
        rules: Iterable[object] = (),
    ) -> Dict[str, object]:
        started = time.perf_counter()
        point_in_time_windows, point_in_time = _point_in_time_windows(windows, as_of)
        latest_observed_at = _latest_observed_at(point_in_time_windows, as_of)
        knowledge_cutoff_at = str(as_of or latest_observed_at).strip()
        watermark = TimeSeriesWatermark(
            backend_id=str(backend_id or ""),
            observed_through=latest_observed_at,
            source_event_id=str(source_event_id or ""),
            sequence=0,
            status="ready" if windows else "empty",
        )
        feature_snapshot = TemporalFeatureSnapshot.create(
            backend_id=backend_id,
            account_id=account_id,
            as_of=knowledge_cutoff_at,
            windows=point_in_time_windows,
            watermark=watermark,
            feature_set_version=self.feature_set_version,
        )
        feature_assembled_at = time.perf_counter()
        feature_persisted = False
        feature_error = ""
        if self.feature_snapshot_store:
            try:
                feature_persisted = bool(self.feature_snapshot_store.upsert(feature_snapshot))
            except Exception as error:  # noqa: BLE001 - reference signals must not block TypeDB.
                feature_error = str(error)[:300]
        feature_persisted_at = time.perf_counter()
        scorers = {
            DEFAULT_PRICE_SIGNAL_RELEASE_ID: score_temporal_feature_snapshot,
            DEFAULT_FLOW_SIGNAL_RELEASE_ID: score_flow_feature_snapshot,
            CAPITAL_FLOW_SHADOW_RELEASE_ID: score_flow_feature_snapshot,
        }
        baseline_snapshots = []
        skipped_releases = []
        baseline_release_ids = tuple(dict.fromkeys((
            DEFAULT_PRICE_SIGNAL_RELEASE_ID,
            DEFAULT_FLOW_SIGNAL_RELEASE_ID,
            CAPITAL_FLOW_SHADOW_RELEASE_ID,
        )))
        for release_id in baseline_release_ids:
            scorer = scorers.get(release_id)
            if not scorer:
                continue
            baseline_snapshots.append(scorer(feature_snapshot, release_id=release_id))
        if graph is not None and rules:
            scored = score_graph_hypothesis_contracts(
                graph,
                feature_snapshot,
                rules,
                baseline_snapshots=baseline_snapshots,
            )
            requested = set(self.model_release_ids)
            signal_snapshots = [
                item for item in scored
                if item.model_release_id in requested
            ]
            skipped_releases = sorted(requested - {item.model_release_id for item in signal_snapshots})
        else:
            signal_snapshots = [
                item for item in baseline_snapshots
                if item.model_release_id in set(self.model_release_ids)
            ]
            skipped_releases = sorted(
                set(self.model_release_ids) - {item.model_release_id for item in signal_snapshots}
            )
        signal_bundle = ModelSignalBundle.create(
            account_id=feature_snapshot.account_id,
            as_of=knowledge_cutoff_at,
            source_feature_snapshot_id=feature_snapshot.snapshot_id,
            feature_set_version=feature_snapshot.feature_set_version,
            snapshots=signal_snapshots,
        )
        signal_snapshot = next(
            (
                item for item in signal_snapshots
                if item.model_release_id == self.model_release_id
            ),
            signal_snapshots[0] if signal_snapshots else None,
        )
        signal_scored_at = time.perf_counter()
        signal_receipts = {}
        signal_errors = {}
        for item in signal_snapshots:
            receipt = {
                "status": "not-configured",
                "changedSignalCount": 0,
                "unchangedSignalCount": 0,
            }
            if self.signal_store:
                try:
                    receipt = dict(self.signal_store.save(item) or {})
                except Exception as error:  # noqa: BLE001 - graph still receives the immutable packet.
                    signal_errors[item.model_release_id] = str(error)[:300]
                    receipt = {
                        "status": "persistence-error",
                        "changedSignalCount": 0,
                        "unchangedSignalCount": 0,
                    }
            signal_receipts[item.model_release_id] = receipt
        signal_receipt = signal_receipts.get(
            self.model_release_id,
            next(iter(signal_receipts.values()), {
                "status": "not-configured",
                "changedSignalCount": 0,
                "unchangedSignalCount": 0,
            }),
        )
        production_evidence_requested = bool(graph is not None and rules)
        signal_persistence_ready = bool(
            self.signal_store
            and not signal_errors
            and all(
                str(receipt.get("status") or "") in {"changed", "unchanged"}
                for receipt in signal_receipts.values()
            )
            and len(signal_receipts) == len(signal_snapshots)
        )
        durable_evidence_ready = bool(
            not production_evidence_requested
            or (
                self.feature_snapshot_store
                and not feature_error
                and signal_persistence_ready
            )
        )
        supported_assessment_count = sum(
            1 for item in signal_bundle.assessments
            if item.status == "supported"
        )
        decision_eligible_assessment_count = sum(
            1 for item in signal_bundle.assessments
            if item.status == "supported"
            and item.decision_eligibility in {"eligible", "conditional"}
        )
        diagnostic_ready = bool(signal_bundle.assessments and durable_evidence_ready)
        decision_blockers = []
        if production_evidence_requested and not self.feature_snapshot_store:
            decision_blockers.append("feature-snapshot-store-not-configured")
        if production_evidence_requested and feature_error:
            decision_blockers.append("feature-snapshot-persistence-failed")
        if production_evidence_requested and not self.signal_store:
            decision_blockers.append("model-signal-store-not-configured")
        if production_evidence_requested and signal_errors:
            decision_blockers.append("model-signal-persistence-failed")
        if production_evidence_requested and self.signal_store and not signal_persistence_ready:
            decision_blockers.append("model-signal-persistence-unverified")
        finished = time.perf_counter()
        return {
            "status": (
                "ready"
                if (signal_bundle.signals or signal_bundle.assessments) and durable_evidence_ready
                else "evidence-not-durable"
                if signal_bundle.signals or signal_bundle.assessments
                else "empty"
            ),
            "decisionEligible": bool(
                decision_eligible_assessment_count and durable_evidence_ready
            ),
            "diagnosticReady": diagnostic_ready,
            "assessmentCount": len(signal_bundle.assessments),
            "supportedAssessmentCount": supported_assessment_count,
            "decisionEligibleAssessmentCount": decision_eligible_assessment_count,
            "decisionBlockers": sorted(set(decision_blockers)),
            "featureSnapshot": feature_snapshot,
            "signalSnapshot": signal_snapshot,
            "signalSnapshots": tuple(signal_snapshots),
            "signalBundle": signal_bundle,
            "skippedModelReleaseIds": skipped_releases,
            "pointInTime": point_in_time,
            "persistence": {
                "featureSnapshotInserted": feature_persisted,
                "featureSnapshotError": feature_error,
                "signalSnapshot": signal_receipt,
                "signalSnapshots": signal_receipts,
                "signalSnapshotErrors": signal_errors,
            },
            "timings": {
                "featureAssemblyMs": int((feature_assembled_at - started) * 1000),
                "featurePersistenceMs": int((feature_persisted_at - feature_assembled_at) * 1000),
                "signalScoringMs": int((signal_scored_at - feature_persisted_at) * 1000),
                "signalPersistenceMs": int((finished - signal_scored_at) * 1000),
                "totalMs": int((finished - started) * 1000),
            },
        }
