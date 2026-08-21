"""Build immutable feature and model-signal packets inside one durable job."""

from __future__ import annotations

import time
from typing import Dict, Iterable, Mapping

from ...domain.statistical_signals import (
    DEFAULT_PRICE_SIGNAL_RELEASE_ID,
    score_temporal_feature_snapshot,
)
from ...domain.time_series_storage import (
    TEMPORAL_FEATURE_SET_VERSION,
    TemporalFeatureSnapshot,
    TimeSeriesWatermark,
)


def _row_timestamp(row: Mapping[str, object]) -> str:
    for key in ("bucketAt", "bucket_at", "generatedAt", "observed_at", "observedAt"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _latest_observed_at(windows: Mapping[str, object], fallback: object = "") -> str:
    values = [str(fallback or "").strip()]
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
    return max((value for value in values if value), default="")


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
        feature_set_version: str = TEMPORAL_FEATURE_SET_VERSION,
    ):
        self.feature_snapshot_store = feature_snapshot_store
        self.signal_store = signal_store
        self.model_release_id = str(model_release_id or DEFAULT_PRICE_SIGNAL_RELEASE_ID)
        self.feature_set_version = str(feature_set_version or TEMPORAL_FEATURE_SET_VERSION)

    def run(
        self,
        account_id: object,
        backend_id: object,
        windows: Mapping[str, object],
        as_of: object = "",
        source_event_id: object = "",
    ) -> Dict[str, object]:
        started = time.perf_counter()
        latest_observed_at = _latest_observed_at(windows, as_of)
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
            as_of=latest_observed_at,
            windows=windows,
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
        signal_snapshot = score_temporal_feature_snapshot(
            feature_snapshot,
            release_id=self.model_release_id,
        )
        signal_scored_at = time.perf_counter()
        signal_receipt = {
            "status": "not-configured",
            "changedSignalCount": 0,
            "unchangedSignalCount": 0,
        }
        signal_error = ""
        if self.signal_store:
            try:
                signal_receipt = dict(self.signal_store.save(signal_snapshot) or {})
            except Exception as error:  # noqa: BLE001 - graph still receives the immutable packet.
                signal_error = str(error)[:300]
                signal_receipt = {
                    "status": "persistence-error",
                    "changedSignalCount": 0,
                    "unchangedSignalCount": 0,
                }
        finished = time.perf_counter()
        return {
            "status": "ready" if signal_snapshot.signals else "empty",
            "featureSnapshot": feature_snapshot,
            "signalSnapshot": signal_snapshot,
            "persistence": {
                "featureSnapshotInserted": feature_persisted,
                "featureSnapshotError": feature_error,
                "signalSnapshot": signal_receipt,
                "signalSnapshotError": signal_error,
            },
            "timings": {
                "featureAssemblyMs": int((feature_assembled_at - started) * 1000),
                "featurePersistenceMs": int((feature_persisted_at - feature_assembled_at) * 1000),
                "signalScoringMs": int((signal_scored_at - feature_persisted_at) * 1000),
                "signalPersistenceMs": int((finished - signal_scored_at) * 1000),
                "totalMs": int((finished - started) * 1000),
            },
        }
