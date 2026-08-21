"""Versioned statistical-signal contracts used by ontology reasoning."""

from .contracts import (
    MODEL_SIGNAL_CONTRACT_VERSION,
    ModelSignal,
    ModelSignalSnapshot,
    SignalEligibility,
)
from .registry import (
    DEFAULT_PRICE_SIGNAL_RELEASE_ID,
    StatisticalModelRelease,
    default_statistical_model_registry,
)
from .scoring import score_temporal_feature_snapshot
from .rule_contracts import (
    RULE_SIGNAL_CONTRACT_VERSION,
    rule_statistical_signal_contract,
    statistical_signal_reverse_index,
)
from .evaluation import (
    MODEL_SIGNAL_EVALUATION_VERSION,
    ModelSignalOutcome,
    model_signal_evaluation_report,
)
from .candidate_release import (
    STATISTICAL_RULE_CANDIDATE_RELEASE_VERSION,
    compile_price_signal_rule_candidate,
    price_signal_rule_candidates,
    statistical_rule_candidate_release,
)


__all__ = [
    "DEFAULT_PRICE_SIGNAL_RELEASE_ID",
    "MODEL_SIGNAL_CONTRACT_VERSION",
    "ModelSignal",
    "ModelSignalSnapshot",
    "ModelSignalOutcome",
    "MODEL_SIGNAL_EVALUATION_VERSION",
    "SignalEligibility",
    "StatisticalModelRelease",
    "STATISTICAL_RULE_CANDIDATE_RELEASE_VERSION",
    "RULE_SIGNAL_CONTRACT_VERSION",
    "default_statistical_model_registry",
    "compile_price_signal_rule_candidate",
    "model_signal_evaluation_report",
    "price_signal_rule_candidates",
    "rule_statistical_signal_contract",
    "score_temporal_feature_snapshot",
    "statistical_signal_reverse_index",
    "statistical_rule_candidate_release",
]
