"""Bounded, ABox-backed outcome context for current hypothesis comparison.

This module does not rank actions or change a TypeDB rule result. It derives
qualification only from a materialized calibration for the exact claim revision.
Family and template resemblance never transfers empirical qualification.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from .decision_evidence_contract import hypothesis_set_evidence_summary
from .hypothesis_calibration_identity import claim_validation_fingerprint, claim_revision_identity
from .decision_performance import binomial_confidence_interval, number
from .rule_claim_contract import RuleClaimContract, hypothesis_qualification


HYPOTHESIS_CALIBRATION_CONTEXT_VERSION = "hypothesis-calibration-context-v2"
HYPOTHESIS_CALIBRATION_SOURCE = "typedb-abox-hypothesis-calibration"


def hypothesis_calibration_snapshot_from_abox_rows(
    rows: Iterable[Dict[str, object]],
    symbols: Iterable[str] = None,
    source_abox_snapshot_id: str = "",
    generation_aligned: bool = False,
    active_membership_verified: bool = False,
    source: str = HYPOTHESIS_CALIBRATION_SOURCE,
    limit: int = 40,
) -> Dict[str, object]:
    """Return only active, symbol-scoped calibration facts from the ABox."""
    clean_symbols = {
        str(value or "").upper().strip()
        for value in symbols or []
        if str(value or "").strip()
    }
    source_snapshot_id = str(source_abox_snapshot_id or "").strip()
    base = {
        "version": HYPOTHESIS_CALIBRATION_CONTEXT_VERSION,
        "source": str(source or HYPOTHESIS_CALIBRATION_SOURCE),
        "sourceAboxSnapshotId": source_snapshot_id,
        "generationAligned": bool(generation_aligned),
        "activeAboxMembershipValidated": bool(active_membership_verified),
        "scope": "same-account-symbol-claim-revision",
        "decisionEligibility": "historical-review-only",
        "automaticDeployment": False,
        "symbols": sorted(clean_symbols),
        "calibrations": [],
        "calibrationCount": 0,
    }
    if not generation_aligned:
        return {
            **base,
            "status": "not-aligned",
            "reason": "현재 InferenceBox 세대와 ABox 스냅샷이 일치하지 않아 과거 결과 보정을 사용하지 않습니다.",
        }
    if not source_snapshot_id:
        return {
            **base,
            "status": "source-unverified",
            "reason": "InferenceBox의 원본 ABox 세대가 없어 과거 결과 보정을 사용하지 않습니다.",
        }

    calibrations: List[Dict[str, object]] = []
    for row in rows or []:
        item = normalized_hypothesis_calibration_row(
            row,
            source_snapshot_id,
            source,
            active_membership_verified=active_membership_verified,
        )
        if not item:
            continue
        if clean_symbols and item["symbol"] not in clean_symbols:
            continue
        calibrations.append(item)
    calibrations.sort(key=lambda item: (str(item.get("symbol") or ""), str(item.get("templateId") or "")))
    bounded = calibrations[:max(1, min(100, int(limit or 40)))]
    return {
        **base,
        "status": "ok" if bounded else "empty",
        "reason": "" if bounded else "현재 종목의 검증 가능한 가설 결과 보정 기록이 없습니다.",
        "calibrations": bounded,
        "calibrationCount": len(bounded),
    }


def attach_abox_hypothesis_calibrations(
    brain: Dict[str, object],
    calibration_snapshot: Dict[str, object] = None,
    subject_symbol: str = "",
    inference_generation_id: str = "",
    inference_generation_at: str = "",
    source_abox_snapshot_id: str = "",
    generation_aligned: bool = False,
) -> Dict[str, object]:
    """Attach exact-revision history and derive its governed qualification."""
    enriched = dict(brain or {})
    hypothesis_set = enriched.get("hypothesisSet") if isinstance(enriched.get("hypothesisSet"), dict) else {}
    hypotheses = [dict(item) for item in hypothesis_set.get("hypotheses") or [] if isinstance(item, dict)]
    snapshot = dict(calibration_snapshot or {}) if isinstance(calibration_snapshot, dict) else {}
    symbol = str(subject_symbol or hypothesis_set.get("subjectSymbol") or "").upper().strip()
    expected_snapshot_id = str(source_abox_snapshot_id or snapshot.get("sourceAboxSnapshotId") or "").strip()
    actual_snapshot_id = str(snapshot.get("sourceAboxSnapshotId") or "").strip()
    aligned = bool(generation_aligned and snapshot.get("generationAligned"))
    context = {
        "version": HYPOTHESIS_CALIBRATION_CONTEXT_VERSION,
        "status": "not-applied",
        "source": str(snapshot.get("source") or HYPOTHESIS_CALIBRATION_SOURCE),
        "scope": "same-account-symbol-claim-revision",
        "decisionEligibility": "historical-review-only",
        "automaticDeployment": False,
        "generationAligned": aligned,
        "inferenceGenerationId": str(inference_generation_id or ""),
        "inferenceGenerationAt": str(inference_generation_at or ""),
        "sourceAboxSnapshotId": expected_snapshot_id,
        "subjectSymbol": symbol,
        "candidateCalibrationCount": 0,
        "candidateTemplateIds": [],
    }
    if not hypotheses:
        context.update({"status": "unavailable", "reason": "현재 경쟁 가설 집합이 없어 결과 보정을 연결하지 않았습니다."})
        enriched["hypothesisCalibration"] = context
        return enriched
    if not aligned:
        context.update({"reason": "현재 InferenceBox 세대와 ABox 스냅샷이 일치하지 않아 결과 보정을 연결하지 않았습니다."})
        enriched["hypothesisCalibration"] = context
        return enriched
    if not expected_snapshot_id or (actual_snapshot_id and actual_snapshot_id != expected_snapshot_id):
        context.update({"reason": "과거 결과 보정의 ABox 원본 세대를 검증하지 못해 연결하지 않았습니다."})
        enriched["hypothesisCalibration"] = context
        return enriched
    if str(snapshot.get("status") or "").lower() != "ok":
        context.update({
            "status": "unavailable" if str(snapshot.get("status") or "") == "error" else "no-exact-history",
            "reason": str(snapshot.get("reason") or "현재 종목의 검증 가능한 가설 결과 보정 기록이 없습니다."),
        })
        enriched["hypothesisCalibration"] = context
        return enriched
    if not timestamp_value(inference_generation_at):
        context.update({"reason": "현재 추론 세대의 기준시각이 없어 미래 결과 혼입을 막기 위해 보정을 연결하지 않았습니다."})
        enriched["hypothesisCalibration"] = context
        return enriched

    by_identity: Dict[tuple, List[Dict[str, object]]] = {}
    for raw in snapshot.get("calibrations") or []:
        item = dict(raw or {}) if isinstance(raw, dict) else {}
        if str(item.get("symbol") or "").upper().strip() != symbol:
            continue
        if not calibration_is_not_after(item, inference_generation_at):
            continue
        calibrated = {
            **item,
            "source": str(item.get("source") or context["source"]),
            "sourceAboxSnapshotId": expected_snapshot_id,
            "generationAligned": True,
            "decisionEligibility": "historical-review-only",
            "automaticDeployment": False,
        }
        for identity_key in calibration_identity_keys(calibrated):
            by_identity.setdefault(identity_key, []).append(calibrated)

    updated_hypotheses = []
    matched_template_ids: List[str] = []
    matched_identities: List[Dict[str, str]] = []
    matched_calibrations: List[Dict[str, object]] = []
    qualification_by_template: Dict[str, Dict[str, object]] = {}
    for hypothesis in hypotheses:
        template_id = str(hypothesis.get("templateId") or "").strip()
        calibration = None
        matched_identity = None
        for identity_key in hypothesis_identity_keys(hypothesis):
            candidates = by_identity.get(identity_key) or []
            if not candidates:
                continue
            calibration = sorted(
                candidates,
                key=lambda value: (
                    str(value.get("latestObservedAt") or ""),
                    str(value.get("calibrationId") or ""),
                ),
                reverse=True,
            )[0]
            matched_identity = identity_key
            break
        if calibration:
            historical_calibration = dict(calibration)
            historical_calibration["matchedIdentityType"] = matched_identity[0]
            historical_calibration["matchedIdentity"] = matched_identity[1]
            hypothesis["historicalCalibration"] = historical_calibration
            claim_contract = RuleClaimContract.from_dict(hypothesis.get("claimContract"))
            if claim_contract.is_predictive and claim_contract.claim_contract_id:
                qualification = hypothesis_qualification(claim_contract, calibration)
                hypothesis["qualification"] = qualification
                qualification_by_template[template_id] = qualification
            matched_template_ids.append(template_id)
            matched_identities.append({
                "type": matched_identity[0],
                "id": matched_identity[1],
            })
            if not any(
                str(item.get("calibrationId") or "") == str(calibration.get("calibrationId") or "")
                for item in matched_calibrations
            ):
                matched_calibrations.append(calibration)
        updated_hypotheses.append(hypothesis)
    context.update({
        "status": "applied" if matched_template_ids else "no-exact-history",
        "reason": "" if matched_template_ids else "현재 종목과 같은 가설 템플릿의 검증 가능한 결과 기록이 없습니다.",
        "candidateCalibrationCount": len(matched_template_ids),
        "candidateTemplateIds": matched_template_ids,
        "matchedIdentities": matched_identities,
        "calibrations": matched_calibrations,
    })
    updated_hypothesis_set = {
        **hypothesis_set,
        "hypotheses": updated_hypotheses,
    }
    updated_hypothesis_set["decisionEvidenceSummary"] = hypothesis_set_evidence_summary(
        updated_hypothesis_set
    )
    enriched["hypothesisSet"] = updated_hypothesis_set
    templates = [
        dict(item) for item in enriched.get("hypothesisTemplates") or []
        if isinstance(item, dict)
    ]
    if templates and qualification_by_template:
        enriched["hypothesisTemplates"] = [
            {
                **item,
                **({"qualification": qualification_by_template[str(item.get("templateId") or "")]}
                   if str(item.get("templateId") or "") in qualification_by_template else {}),
            }
            for item in templates
        ]
    enriched["hypothesisCalibration"] = context
    return enriched


def normalized_hypothesis_calibration_row(
    row: Dict[str, object],
    source_abox_snapshot_id: str,
    source: str,
    active_membership_verified: bool = False,
) -> Dict[str, object]:
    payload = flattened_row(row)
    if str(payload.get("tboxClass") or "") != "HypothesisCalibration" and str(payload.get("kind") or "") != "hypothesis-calibration":
        return {}
    symbol = str(payload.get("symbol") or "").upper().strip()
    template_id = str(payload.get("templateId") or "").strip()
    family_id = str(payload.get("familyId") or "").strip()
    claim_contract_id = str(payload.get("claimContractId") or "").strip()
    calibration_identity = str(
        payload.get("calibrationIdentity")
        or claim_contract_id
        or family_id
        or template_id
        or ""
    ).strip()
    calibration_identity_type = str(
        payload.get("calibrationIdentityType")
        or ("claim-contract" if claim_contract_id else "")
        or ("family" if family_id or template_id.startswith("hypothesis-family:") else "")
        or "template"
    ).strip()
    if not family_id and calibration_identity_type == "family":
        family_id = calibration_identity
    if not claim_contract_id and calibration_identity_type == "claim-contract":
        claim_contract_id = calibration_identity
    row_snapshot_id = str(payload.get("aboxSnapshotId") or payload.get("snapshotId") or "").strip()
    snapshot_matches = row_snapshot_id == source_abox_snapshot_id
    if not symbol or not calibration_identity or (not snapshot_matches and not active_membership_verified):
        return {}
    decisive_count = positive_int(payload.get("decisiveOutcomeCount"))
    corroborated_count = positive_int(payload.get("corroboratedCount"))
    confidence = payload.get("directionalHitRateConfidence95")
    if not isinstance(confidence, dict):
        confidence = binomial_confidence_interval(corroborated_count, decisive_count)
    return {
        "calibrationId": str(payload.get("id") or ""),
        "symbol": symbol,
        "templateId": template_id or calibration_identity,
        "familyId": family_id,
        "claimContractId": claim_contract_id,
        "claimContractFingerprint": str(payload.get("claimContractFingerprint") or ""),
        "calibrationIdentity": calibration_identity,
        "calibrationIdentityType": calibration_identity_type,
        "templateLabel": str(payload.get("templateLabel") or template_id or calibration_identity),
        "calibrationStatus": str(payload.get("calibrationStatus") or "insufficient-history"),
        "outcomeState": str(payload.get("outcomeState") or "insufficient-history"),
        "reviewRecommendation": str(payload.get("reviewRecommendation") or "continue-observation"),
        "minimumDecisiveOutcomes": positive_int(payload.get("minimumDecisiveOutcomes"), 3),
        "independentEpisodeCount": positive_int(payload.get("independentEpisodeCount")),
        "decisiveOutcomeCount": decisive_count,
        "corroboratedCount": corroborated_count,
        "contradictedCount": positive_int(payload.get("contradictedCount")),
        "inconclusiveCount": positive_int(payload.get("inconclusiveCount")),
        "directionalHitRate": number(
            payload.get("directionalHitRate")
            if payload.get("directionalHitRate") not in (None, "")
            else confidence.get("rate") or 0
        ),
        "directionalHitRateConfidence95": {
            "lower": number(confidence.get("lower")),
            "upper": number(confidence.get("upper")),
        },
        "averageActionAdjustedReturnPct": number(payload.get("averageActionAdjustedReturnPct")),
        "actionReturnState": str(payload.get("actionReturnState") or "unavailable"),
        "latestObservedAt": str(payload.get("latestObservedAt") or ""),
        "outcomeHorizonMinutes": positive_int_list(payload.get("outcomeHorizonMinutes")),
        "horizonSlices": normalized_horizon_slices(payload.get("horizonSlices") or payload.get("byHorizon") or []),
        "source": str(payload.get("source") or source),
        "sourceAboxSnapshotId": source_abox_snapshot_id,
        "storedAboxSnapshotId": row_snapshot_id,
        "generationAligned": True,
        "activeAboxMembershipValidated": bool(active_membership_verified),
        "decisionEligibility": "historical-review-only",
        "automaticDeployment": False,
    }


def calibration_identity_keys(calibration: Dict[str, object]) -> List[tuple]:
    """Return typed identities carried by one materialized calibration fact."""

    claim_id = str(calibration.get("claimContractId") or "").strip()
    if claim_id:
        revision = claim_revision_identity(claim_id, str(calibration.get("claimContractFingerprint") or ""))
        return [("claim-revision", revision)] if revision else []
    result: List[tuple] = []
    explicit_type = str(calibration.get("calibrationIdentityType") or "").strip()
    explicit_id = str(calibration.get("calibrationIdentity") or "").strip()
    if explicit_type and explicit_id:
        result.append((explicit_type, explicit_id))
    for identity_type, field in (
        ("claim-contract", "claimContractId"),
        ("family", "familyId"),
        ("template", "templateId"),
    ):
        identity = str(calibration.get(field) or "").strip()
        key = (identity_type, identity)
        if identity and key not in result:
            result.append(key)
    return result


def hypothesis_identity_keys(hypothesis: Dict[str, object]) -> List[tuple]:
    """Match only explicit, stable RuleBox or hypothesis-family identities."""

    claim_contract = (
        hypothesis.get("claimContract")
        if isinstance(hypothesis.get("claimContract"), dict)
        else {}
    )
    claim_id = str(claim_contract.get("claimContractId") or "").strip()
    if claim_id:
        revision = claim_revision_identity(claim_id, claim_validation_fingerprint(claim_contract))
        return [("claim-revision", revision)] if revision else []
    candidates = (
        ("claim-contract", claim_contract.get("claimContractId")),
        ("family", hypothesis.get("familyId")),
        ("template", hypothesis.get("templateId")),
    )
    result: List[tuple] = []
    for identity_type, raw_identity in candidates:
        identity = str(raw_identity or "").strip()
        key = (identity_type, identity)
        if identity and key not in result:
            result.append(key)
    return result


def flattened_row(row: Dict[str, object]) -> Dict[str, object]:
    source = dict(row or {}) if isinstance(row, dict) else {}
    nested = source.get("properties") if isinstance(source.get("properties"), dict) else {}
    if not nested and source.get("propertiesJson"):
        try:
            parsed = json.loads(str(source.get("propertiesJson") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
        nested = parsed if isinstance(parsed, dict) else {}
    return {**source, **nested}


def positive_int(value: object, fallback: int = 0) -> int:
    try:
        return max(0, int(float(str(value or fallback))))
    except (TypeError, ValueError):
        return max(0, int(fallback or 0))


def positive_int_list(value: object) -> List[int]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: List[int] = []
    for item in values:
        parsed = positive_int(item)
        if parsed and parsed not in result:
            result.append(parsed)
    return sorted(result)


def normalized_horizon_slices(value: object) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        result.append({
            "horizonMinutes": positive_int(item.get("horizonMinutes")),
            "independentEpisodeCount": positive_int(item.get("independentEpisodeCount")),
            "decisiveOutcomeCount": positive_int(item.get("decisiveOutcomeCount")),
            "corroboratedCount": positive_int(item.get("corroboratedCount")),
            "contradictedCount": positive_int(item.get("contradictedCount")),
            "inconclusiveCount": positive_int(item.get("inconclusiveCount")),
            "outcomeState": str(item.get("outcomeState") or "insufficient-history"),
            "calibrationStatus": str(item.get("calibrationStatus") or "insufficient-history"),
        })
    return sorted(result, key=lambda item: int(item.get("horizonMinutes") or 0))


def calibration_is_not_after(calibration: Dict[str, object], inference_generation_at: str) -> bool:
    observed_at = timestamp_value(calibration.get("latestObservedAt"))
    inference_at = timestamp_value(inference_generation_at)
    return bool(observed_at and inference_at and observed_at <= inference_at)


def timestamp_value(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    suffixes = {" KST": timezone(timedelta(hours=9)), " UTC": timezone.utc, " GMT": timezone.utc}
    zone = None
    upper = text.upper()
    for suffix, candidate in suffixes.items():
        if upper.endswith(suffix):
            text = text[:-len(suffix)].strip()
            zone = candidate
            break
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone or timezone.utc)
    return parsed.astimezone(timezone.utc)
