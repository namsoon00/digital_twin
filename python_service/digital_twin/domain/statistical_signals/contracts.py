"""Immutable statistical-signal packets at the ontology input boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Dict, Iterable, Mapping, Optional, Tuple


MODEL_SIGNAL_CONTRACT_VERSION = "statistical-model-signal-v1"
SIGNAL_ELIGIBILITY_CONTRACT_VERSION = "statistical-signal-eligibility-v1"


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _float(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _bounded(value: object, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, _float(value)))


def _canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple, set)):
        rows = [_canonical(item) for item in value]
        return sorted(
            rows,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
    if isinstance(value, float):
        return round(value, 8)
    return value


def payload_hash(value: object) -> str:
    encoded = json.dumps(
        _canonical(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unique_text(values: Iterable[object], limit: int = 32) -> Tuple[str, ...]:
    result = []
    seen = set()
    for raw in values or []:
        value = _text(raw)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
        if len(result) >= max(1, int(limit or 1)):
            break
    return tuple(result)


@dataclass(frozen=True)
class SignalEligibility:
    status: str
    reasons: Tuple[str, ...] = ()
    data_quality: str = "unknown"
    validation_status: str = "replay-required"
    decision_eligibility: str = "reference-only"
    contract_version: str = SIGNAL_ELIGIBILITY_CONTRACT_VERSION

    @classmethod
    def create(
        cls,
        status: object,
        reasons: Iterable[object] = (),
        data_quality: object = "unknown",
        validation_status: object = "replay-required",
        decision_eligibility: object = "reference-only",
    ) -> "SignalEligibility":
        normalized = _text(status).lower() or "ineligible"
        if normalized not in {"eligible", "conditional", "ineligible", "reference-only"}:
            normalized = "ineligible"
        decision = _text(decision_eligibility).lower() or "reference-only"
        if decision not in {"eligible", "conditional", "reference-only"}:
            decision = "reference-only"
        return cls(
            status=normalized,
            reasons=_unique_text(reasons),
            data_quality=_text(data_quality).lower() or "unknown",
            validation_status=_text(validation_status).lower() or "replay-required",
            decision_eligibility=decision,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "contractVersion": self.contract_version,
            "status": self.status,
            "reasons": list(self.reasons),
            "dataQuality": self.data_quality,
            "validationStatus": self.validation_status,
            "decisionEligibility": self.decision_eligibility,
        }


@dataclass(frozen=True)
class ModelSignal:
    signal_id: str
    signal_type: str
    signal_family: str
    subject_id: str
    horizon: str
    polarity: str
    score: float
    strength_band: str
    confidence: float
    observed_at: str
    source_feature_snapshot_id: str
    feature_set_version: str
    model_release_id: str
    sample_count: int
    coverage_ratio: float
    eligibility: SignalEligibility
    input_features: Dict[str, object] = field(default_factory=dict)
    probability: Optional[float] = None
    material_hash: str = ""
    contract_version: str = MODEL_SIGNAL_CONTRACT_VERSION

    @classmethod
    def create(
        cls,
        signal_type: object,
        signal_family: object,
        subject_id: object,
        horizon: object,
        polarity: object,
        score: object,
        confidence: object,
        observed_at: object,
        source_feature_snapshot_id: object,
        feature_set_version: object,
        model_release_id: object,
        sample_count: object,
        coverage_ratio: object,
        eligibility: SignalEligibility,
        input_features: Mapping[str, object] = None,
        probability: object = None,
    ) -> "ModelSignal":
        normalized_probability = None
        if probability not in (None, ""):
            normalized_probability = _bounded(probability)
        material = {
            "contractVersion": MODEL_SIGNAL_CONTRACT_VERSION,
            "signalType": _text(signal_type).lower(),
            "signalFamily": _text(signal_family).lower(),
            "subjectId": _text(subject_id).upper(),
            "horizon": _text(horizon).upper(),
            "polarity": _text(polarity).lower() or "context",
            "score": round(_bounded(score), 8),
            "confidence": round(_bounded(confidence), 8),
            "observedAt": _text(observed_at),
            "featureSetVersion": _text(feature_set_version),
            "modelReleaseId": _text(model_release_id),
            "sampleCount": max(0, int(_float(sample_count))),
            "coverageRatio": round(_bounded(coverage_ratio), 8),
            "eligibility": eligibility.to_dict(),
            "inputFeatures": _canonical(dict(input_features or {})),
            "probability": normalized_probability,
        }
        digest = payload_hash(material)
        strength_band = (
            "strong" if float(material["score"]) >= 0.70
            else "moderate" if float(material["score"]) >= 0.40
            else "weak"
        )
        return cls(
            signal_id="model-signal:" + digest[:32],
            signal_type=str(material["signalType"]),
            signal_family=str(material["signalFamily"]),
            subject_id=str(material["subjectId"]),
            horizon=str(material["horizon"]),
            polarity=str(material["polarity"]),
            score=float(material["score"]),
            strength_band=strength_band,
            confidence=float(material["confidence"]),
            observed_at=str(material["observedAt"]),
            source_feature_snapshot_id=_text(source_feature_snapshot_id),
            feature_set_version=str(material["featureSetVersion"]),
            model_release_id=str(material["modelReleaseId"]),
            sample_count=int(material["sampleCount"]),
            coverage_ratio=float(material["coverageRatio"]),
            eligibility=eligibility,
            input_features=dict(material["inputFeatures"]),
            probability=normalized_probability,
            material_hash=digest,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "contractVersion": self.contract_version,
            "signalId": self.signal_id,
            "signalType": self.signal_type,
            "signalFamily": self.signal_family,
            "subjectId": self.subject_id,
            "horizon": self.horizon,
            "polarity": self.polarity,
            "score": self.score,
            "strengthBand": self.strength_band,
            "probability": self.probability,
            "confidence": self.confidence,
            "observedAt": self.observed_at,
            "sourceFeatureSnapshotId": self.source_feature_snapshot_id,
            "featureSetVersion": self.feature_set_version,
            "modelReleaseId": self.model_release_id,
            "sampleCount": self.sample_count,
            "coverageRatio": self.coverage_ratio,
            "eligibility": self.eligibility.to_dict(),
            "inputFeatures": dict(self.input_features),
            "materialHash": self.material_hash,
        }


@dataclass(frozen=True)
class ModelSignalSnapshot:
    snapshot_id: str
    account_id: str
    as_of: str
    source_feature_snapshot_id: str
    feature_set_version: str
    model_release_id: str
    subjects: Tuple[str, ...]
    signals: Tuple[ModelSignal, ...]
    material_hash: str
    shared_material_hash: str
    contract_version: str = MODEL_SIGNAL_CONTRACT_VERSION

    @classmethod
    def create(
        cls,
        account_id: object,
        as_of: object,
        source_feature_snapshot_id: object,
        feature_set_version: object,
        model_release_id: object,
        signals: Iterable[ModelSignal],
        subjects: Iterable[object] = (),
    ) -> "ModelSignalSnapshot":
        rows = tuple(sorted(
            [item for item in signals or [] if isinstance(item, ModelSignal)],
            key=lambda item: (item.subject_id, item.signal_type, item.horizon, item.signal_id),
        ))
        normalized_subjects = tuple(sorted({
            _text(item).upper()
            for item in [*list(subjects or []), *(signal.subject_id for signal in rows)]
            if _text(item)
        }))
        material = {
            "contractVersion": MODEL_SIGNAL_CONTRACT_VERSION,
            "modelReleaseId": _text(model_release_id),
            "featureSetVersion": _text(feature_set_version),
            "subjects": list(normalized_subjects),
            "signals": [item.material_hash for item in rows],
        }
        shared_digest = payload_hash(material)
        digest = payload_hash({
            "accountId": _text(account_id),
            "sharedMaterialHash": shared_digest,
        })
        return cls(
            snapshot_id="model-signal-snapshot:" + digest[:32],
            account_id=_text(account_id),
            as_of=_text(as_of),
            source_feature_snapshot_id=_text(source_feature_snapshot_id),
            feature_set_version=_text(feature_set_version),
            model_release_id=_text(model_release_id),
            subjects=normalized_subjects,
            signals=rows,
            material_hash=digest,
            shared_material_hash=shared_digest,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "contractVersion": self.contract_version,
            "snapshotId": self.snapshot_id,
            "accountId": self.account_id,
            "asOf": self.as_of,
            "sourceFeatureSnapshotId": self.source_feature_snapshot_id,
            "featureSetVersion": self.feature_set_version,
            "modelReleaseId": self.model_release_id,
            "signalCount": len(self.signals),
            "subjects": list(self.subjects),
            "signals": [item.to_dict() for item in self.signals],
            "materialHash": self.material_hash,
            "sharedMaterialHash": self.shared_material_hash,
        }
