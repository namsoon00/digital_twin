"""Composition root for the statistical-signal stage."""

from typing import Dict

from ..application.statistical_signals import StatisticalSignalPipelineService
from ..domain.statistical_signals import (
    DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
    DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
    DEFAULT_EVENT_SIGNAL_RELEASE_ID,
    DEFAULT_FLOW_SIGNAL_RELEASE_ID,
    DEFAULT_PRICE_SIGNAL_RELEASE_ID,
    DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
)
from .mysql_statistical_signals import MySQLStatisticalModelSignalStore
from .mysql_versioned_runtime import MySQLTemporalFeatureSnapshotStore
from .settings import runtime_settings


def build_statistical_signal_pipeline_service(settings: Dict[str, object] = None):
    configured = dict(settings or runtime_settings())
    price_release_id = str(
        configured.get("statisticalPriceSignalReleaseId")
        or DEFAULT_PRICE_SIGNAL_RELEASE_ID
    )
    flow_release_id = str(
        configured.get("statisticalFlowSignalReleaseId")
        or DEFAULT_FLOW_SIGNAL_RELEASE_ID
    )
    return StatisticalSignalPipelineService(
        feature_snapshot_store=MySQLTemporalFeatureSnapshotStore(configured),
        signal_store=MySQLStatisticalModelSignalStore(configured),
        model_release_id=price_release_id,
        model_release_ids=(
            price_release_id,
            flow_release_id,
            DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
            DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
            DEFAULT_EVENT_SIGNAL_RELEASE_ID,
            DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
        ),
        feature_set_version=str(
            configured.get("_reasoningFeatureSetVersion")
            or configured.get("temporalFeatureSetVersion")
            or "temporal-features-v1"
        ),
    )
