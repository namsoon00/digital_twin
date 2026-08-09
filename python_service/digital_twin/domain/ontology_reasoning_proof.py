"""Pure evidence rules for ontology reasoning bottleneck diagnosis.

The proof contract compares durable production timings with a read-only replay
against one unchanged ABox generation.  It classifies operational performance;
it never evaluates an investment rule or changes an investment decision.
"""

from __future__ import annotations

from statistics import median
from typing import Dict, Iterable, List, Mapping


ONTOLOGY_REASONING_PROOF_VERSION = "ontology-reasoning-bottleneck-proof-v1"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _number(value: object) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _median(values: Iterable[object]) -> float:
    clean = [_number(value) for value in values if _number(value) > 0]
    return round(float(median(clean)), 1) if clean else 0.0


def summarize_production_stage_evidence(runs: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    """Summarize non-overlapping top-level and nested native stage timings."""

    rows: List[Dict[str, object]] = []
    for raw in runs or []:
        run = _mapping(raw)
        runtime = _mapping(run.get("runtimeStages"))
        native = _mapping(run.get("nativeStageTimings"))
        total_ms = _number(runtime.get("totalMs") or run.get("durationMs"))
        native_ms = _number(runtime.get("nativeInferenceMs"))
        abox_ms = _number(runtime.get("aboxPersistenceMs") or runtime.get("persistenceMs"))
        stage_values = [total_ms, native_ms, abox_ms]
        stage_values.extend(_number(value) for value in native.values())
        if not any(stage_values):
            continue
        rows.append({
            "runId": str(run.get("runId") or ""),
            "observedAt": str(run.get("observedAt") or ""),
            "targetSymbols": list(run.get("targetSymbols") or []),
            "totalMs": total_ms,
            "nativeInferenceMs": native_ms,
            "aboxPersistenceMs": abox_ms,
            "nativeStageTimings": native,
        })

    native_keys = sorted({
        str(key)
        for row in rows
        for key in _mapping(row.get("nativeStageTimings")).keys()
        if str(key)
    })
    medians = {
        "totalMs": _median(row.get("totalMs") for row in rows),
        "nativeInferenceMs": _median(row.get("nativeInferenceMs") for row in rows),
        "aboxPersistenceMs": _median(row.get("aboxPersistenceMs") for row in rows),
        "nativeStageTimings": {
            key: _median(_mapping(row.get("nativeStageTimings")).get(key) for row in rows)
            for key in native_keys
        },
    }
    native_ms = _number(medians["nativeInferenceMs"])
    abox_ms = _number(medians["aboxPersistenceMs"])
    total_ms = _number(medians["totalMs"])
    medians["nativeInferenceSharePct"] = round((native_ms / total_ms) * 100, 1) if total_ms else 0.0
    medians["aboxPersistenceSharePct"] = round((abox_ms / total_ms) * 100, 1) if total_ms else 0.0
    return {
        "sampleCount": len(rows),
        "medians": medians,
        "runs": rows,
    }


def summarize_read_only_replay(samples: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    rows = [dict(item or {}) for item in samples or [] if isinstance(item, Mapping)]
    valid = [item for item in rows if bool(item.get("validForComparison"))]
    stage_keys = sorted({
        str(key)
        for item in valid
        for key in _mapping(item.get("stageTimings")).keys()
        if str(key)
    })
    medians = {
        key: _median(_mapping(item.get("stageTimings")).get(key) for item in valid)
        for key in stage_keys
    }
    wall_values = [_number(item.get("wallClockMs")) for item in valid if _number(item.get("wallClockMs")) > 0]
    spread_pct = 0.0
    if len(wall_values) > 1 and _median(wall_values):
        spread_pct = round(((max(wall_values) - min(wall_values)) / _median(wall_values)) * 100, 1)
    return {
        "sampleCount": len(rows),
        "validSampleCount": len(valid),
        "invalidSampleCount": len(rows) - len(valid),
        "medianWallClockMs": _median(wall_values),
        "wallClockSpreadPct": spread_pct,
        "medianStageTimings": medians,
        "allSamplesUsedSameGeneration": bool(valid) and len({
            str(item.get("generationFingerprint") or "") for item in valid
        }) == 1,
    }


def classify_reasoning_bottleneck(
    production: Mapping[str, object],
    replay: Mapping[str, object],
    slow_rule_overlap: Iterable[str] = None,
) -> Dict[str, object]:
    """Classify only when independent evidence satisfies fixed thresholds."""

    production_values = _mapping(production)
    medians = _mapping(production_values.get("medians"))
    native_stages = _mapping(medians.get("nativeStageTimings"))
    replay_values = _mapping(replay)
    replay_stages = _mapping(replay_values.get("medianStageTimings"))
    native_ms = _number(medians.get("nativeInferenceMs"))
    abox_ms = _number(medians.get("aboxPersistenceMs"))
    total_ms = _number(medians.get("totalMs"))
    valid_replays = int(_number(replay_values.get("validSampleCount")))
    stable_generation = bool(replay_values.get("allSamplesUsedSameGeneration"))
    overlap = sorted({str(item) for item in slow_rule_overlap or [] if str(item)})

    native_rule_query_ms = _number(native_stages.get("nativeRuleQueriesMs"))
    matched_graph_read_ms = _number(native_stages.get("matchedGraphReadMs"))
    native_read_path_ms = native_rule_query_ms + matched_graph_read_ms
    inner_candidates = {
        # These stages belong to one read boundary. Treating them as competing
        # causes understates the TypeDB read path whenever query execution and
        # evidence hydration are both expensive.
        "native-read-path": native_read_path_ms,
        "inferencebox-write": _number(native_stages.get("inferenceBoxWriteMs")),
        "inference-graph-build": _number(native_stages.get("inferenceGraphBuildMs")),
    }
    inner_stage, inner_ms = max(inner_candidates.items(), key=lambda item: item[1], default=("", 0.0))
    inner_share = round((inner_ms / native_ms) * 100, 1) if native_ms else 0.0
    native_is_top_level = bool(native_ms and native_ms >= abox_ms and native_ms >= total_ms * 0.4)
    abox_is_top_level = bool(abox_ms and abox_ms > native_ms and abox_ms >= total_ms * 0.4)

    replay_native_rule_query_ms = _number(replay_stages.get("nativeRuleQueriesMs"))
    replay_matched_graph_read_ms = _number(replay_stages.get("matchedGraphReadMs"))
    replay_native_read_path_ms = replay_native_rule_query_ms + replay_matched_graph_read_ms
    replay_inner_ms = {
        "native-read-path": replay_native_read_path_ms,
        "inference-graph-build": _number(replay_stages.get("inferenceGraphBuildMs")),
    }.get(inner_stage, 0.0)
    replay_candidates = {
        "native-read-path": replay_native_read_path_ms,
        "inference-graph-build": _number(replay_stages.get("inferenceGraphBuildMs")),
    }
    replay_dominant_stage, replay_dominant_ms = max(
        replay_candidates.items(),
        key=lambda item: item[1],
        default=("", 0.0),
    )
    replay_wall_ms = _number(replay_values.get("medianWallClockMs"))
    replay_dominant_share = (
        round((replay_dominant_ms / replay_wall_ms) * 100, 1)
        if replay_wall_ms
        else 0.0
    )
    replay_reproduced = bool(
        valid_replays >= 2
        and stable_generation
        and replay_inner_ms >= 1000
        and (not inner_ms or replay_inner_ms >= inner_ms * 0.35)
    )

    status = "inconclusive"
    confidence = "low"
    cause = "unproven"
    reason = "운영 실행과 동일 세대의 반복 재현 표본이 충분하지 않습니다."
    independently_reproduced = False

    if abox_is_top_level:
        status = "supported"
        confidence = "medium" if int(_number(production_values.get("sampleCount"))) >= 2 else "low"
        cause = "abox-persistence-dominant"
        reason = "운영 표본에서 ABox 저장이 전체 실행의 가장 큰 상위 단계입니다. 읽기 전용 재현은 저장 원인을 독립 재현하지 않습니다."
    elif native_is_top_level and inner_stage:
        cause = inner_stage + "-dominant"
        if inner_stage == "inferencebox-write":
            status = "supported"
            confidence = "medium" if inner_share >= 50 else "low"
            reason = "운영 표본에서 InferenceBox 저장이 native inference 내부의 가장 큰 단계입니다. 무기록 측정 원칙 때문에 쓰기는 재실행하지 않았습니다."
        elif inner_share >= 50 and replay_reproduced:
            status = "confirmed"
            confidence = "high" if overlap or inner_stage != "native-read-path" else "medium"
            independently_reproduced = True
            reason = "운영 단계 계측의 지배 구간이 변경 없는 동일 ABox 세대의 읽기 전용 반복 실행에서도 재현됐습니다."
        elif inner_share >= 35:
            status = "supported"
            confidence = "medium" if valid_replays else "low"
            reason = "운영 계측은 이 단계를 주요 병목으로 지지하지만 동일 세대 반복 재현 기준을 모두 충족하지 못했습니다."
        elif (
            inner_ms <= 0
            and valid_replays >= 2
            and stable_generation
            and replay_dominant_ms >= 1000
            and replay_dominant_share >= 50
        ):
            status = "confirmed"
            confidence = "medium"
            cause = replay_dominant_stage + "-dominant"
            independently_reproduced = True
            replay_inner_ms = replay_dominant_ms
            reason = "운영 이력은 native inference를 상위 병목으로 기록했고, 변경 없는 동일 ABox 세대의 무기록 재현이 그 내부 지배 단계를 분리했습니다."
    elif total_ms:
        cause = "orchestration-or-unattributed-dominant"
        status = "supported"
        confidence = "low"
        reason = "ABox 저장과 native inference 외 시간이 커서 현재 세부 계측만으로 단일 원인을 확정할 수 없습니다."

    return {
        "contract": ONTOLOGY_REASONING_PROOF_VERSION,
        "status": status,
        "confidence": confidence,
        "cause": cause,
        "reason": reason,
        "independentlyReproduced": independently_reproduced,
        "productionNativeInferenceSharePct": round((native_ms / total_ms) * 100, 1) if total_ms else 0.0,
        "productionDominantNativeStageSharePct": inner_share,
        "productionDominantNativeStageMs": round(inner_ms, 1),
        "productionDominantReadSubstage": (
            "native-rule-query"
            if native_rule_query_ms >= matched_graph_read_ms
            else "matched-graph-read"
        ) if native_read_path_ms else "",
        "productionNativeRuleQueryMs": round(native_rule_query_ms, 1),
        "productionMatchedGraphReadMs": round(matched_graph_read_ms, 1),
        "replayDominantStageMs": round(replay_inner_ms, 1),
        "replayDominantStageSharePct": replay_dominant_share,
        "replayNativeRuleQueryMs": round(replay_native_rule_query_ms, 1),
        "replayMatchedGraphReadMs": round(replay_matched_graph_read_ms, 1),
        "validReplaySampleCount": valid_replays,
        "stableGeneration": stable_generation,
        "slowRuleOverlap": overlap,
        "proofCriteria": {
            "minimumValidReplaySamples": 2,
            "minimumDominantNativeStageSharePct": 50,
            "minimumReplayStageMs": 1000,
            "minimumReplayToProductionRatio": 0.35,
        },
    }
