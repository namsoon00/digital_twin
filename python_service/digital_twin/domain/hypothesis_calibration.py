"""Bounded, ABox-backed outcome context for current hypothesis comparison.

This module deliberately does not rank actions or change a TypeDB rule result.
It only makes an already materialized ``HypothesisCalibration`` fact available
to the AI comparison for the same account, symbol, and hypothesis template.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List


HYPOTHESIS_CALIBRATION_CONTEXT_VERSION = "hypothesis-calibration-context-v1"
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
        "scope": "same-account-symbol-template",
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
    """Attach exact historical context without changing the candidate set.

    The graph-derived candidates remain immutable in meaning: this function
    only adds audit context to candidates whose template and symbol exactly
    match an active ABox calibration. A missing, stale, or future result is
    excluded instead of being treated as evidence.
    """
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
        "scope": "same-account-symbol-template",
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

    by_template: Dict[str, Dict[str, object]] = {}
    for raw in snapshot.get("calibrations") or []:
        item = dict(raw or {}) if isinstance(raw, dict) else {}
        if str(item.get("symbol") or "").upper().strip() != symbol:
            continue
        template_id = str(item.get("templateId") or "").strip()
        if not template_id or not calibration_is_not_after(item, inference_generation_at):
            continue
        by_template[template_id] = {
            **item,
            "source": str(item.get("source") or context["source"]),
            "sourceAboxSnapshotId": expected_snapshot_id,
            "generationAligned": True,
            "decisionEligibility": "historical-review-only",
            "automaticDeployment": False,
        }

    updated_hypotheses = []
    matched_template_ids: List[str] = []
    for hypothesis in hypotheses:
        template_id = str(hypothesis.get("templateId") or "").strip()
        calibration = by_template.get(template_id)
        if calibration:
            hypothesis["historicalCalibration"] = calibration
            matched_template_ids.append(template_id)
        updated_hypotheses.append(hypothesis)
    context.update({
        "status": "applied" if matched_template_ids else "no-exact-history",
        "reason": "" if matched_template_ids else "현재 종목과 같은 가설 템플릿의 검증 가능한 결과 기록이 없습니다.",
        "candidateCalibrationCount": len(matched_template_ids),
        "candidateTemplateIds": matched_template_ids,
        "calibrations": [by_template[key] for key in matched_template_ids],
    })
    enriched["hypothesisSet"] = {
        **hypothesis_set,
        "hypotheses": updated_hypotheses,
    }
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
    row_snapshot_id = str(payload.get("aboxSnapshotId") or payload.get("snapshotId") or "").strip()
    snapshot_matches = row_snapshot_id == source_abox_snapshot_id
    if not symbol or not template_id or (not snapshot_matches and not active_membership_verified):
        return {}
    return {
        "calibrationId": str(payload.get("id") or ""),
        "symbol": symbol,
        "templateId": template_id,
        "templateLabel": str(payload.get("templateLabel") or template_id),
        "calibrationStatus": str(payload.get("calibrationStatus") or "insufficient-history"),
        "outcomeState": str(payload.get("outcomeState") or "insufficient-history"),
        "reviewRecommendation": str(payload.get("reviewRecommendation") or "continue-observation"),
        "minimumDecisiveOutcomes": positive_int(payload.get("minimumDecisiveOutcomes"), 3),
        "independentEpisodeCount": positive_int(payload.get("independentEpisodeCount")),
        "decisiveOutcomeCount": positive_int(payload.get("decisiveOutcomeCount")),
        "corroboratedCount": positive_int(payload.get("corroboratedCount")),
        "contradictedCount": positive_int(payload.get("contradictedCount")),
        "inconclusiveCount": positive_int(payload.get("inconclusiveCount")),
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
