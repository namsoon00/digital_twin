"""Composition root for the statistical-signal stage."""

from typing import Dict

from ..application.statistical_signals import StatisticalSignalPipelineService
from ..domain.statistical_signals import DEFAULT_PRICE_SIGNAL_RELEASE_ID
from .mysql_statistical_signals import MySQLStatisticalModelSignalStore
from .mysql_versioned_runtime import MySQLTemporalFeatureSnapshotStore
from .settings import runtime_settings


def build_statistical_signal_pipeline_service(settings: Dict[str, object] = None):
    configured = dict(settings or runtime_settings())
    return StatisticalSignalPipelineService(
        feature_snapshot_store=MySQLTemporalFeatureSnapshotStore(configured),
        signal_store=MySQLStatisticalModelSignalStore(configured),
        model_release_id=str(
            configured.get("statisticalPriceSignalReleaseId")
            or DEFAULT_PRICE_SIGNAL_RELEASE_ID
        ),
        feature_set_version=str(
            configured.get("_reasoningFeatureSetVersion")
            or configured.get("temporalFeatureSetVersion")
            or "temporal-features-v1"
        ),
    )
