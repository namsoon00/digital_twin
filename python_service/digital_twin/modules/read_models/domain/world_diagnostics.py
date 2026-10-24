"""Observation guarantees, separate from investment data completeness."""

from datetime import datetime, timezone


def diagnostics_observation(payload, cache, *, now=None):
    now = now or datetime.now(timezone.utc)
    generated = str(payload.get("generatedAt") or "")
    try:
        stamp = datetime.fromisoformat(generated.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("missing timezone")
        age = (now - stamp).total_seconds()
    except (ValueError, TypeError):
        age = None
    if age is None or age < 0:
        state, label = "unknown", "진단 기준 시점 미확인"
    elif cache.get("stale") or age > 60:
        state, label = "stale", "과거 진단 · 현재 상태 미확인"
    else:
        state, label = "recent", "최근 진단 · 데이터 충족 여부는 별도 확인"
    return {
        "version": "world-diagnostic-observation-v1",
        "status": state,
        "label": label,
        "generatedAt": generated,
        "ageSeconds": int(age) if age is not None and age >= 0 else None,
        "refreshing": bool(cache.get("refreshing")),
        "refreshFailed": bool(cache.get("lastError")),
        "snapshotRecent": state == "recent",
        "coverageVerified": False,
    }


def world_inventory(worlds, metadata, *, incremental):
    rows = []
    for world in worlds:
        wid = str(world.get("worldId") or "")
        data = metadata.get(wid) or {}
        rows.append({
            "worldId": wid,
            "worldType": str(world.get("worldType") or "unknown"),
            "status": str(data.get("status") or "unknown"),
            "projectionAsOf": str(data.get("asOf") or ""),
            "activeScopeCount": data.get("activeScopeCount"),
            "scopeFamilyCounts": dict(data.get("scopeFamilyCounts") or {}),
            "sourceFreshness": "not-assessed",
            "coverage": "not-assessed",
        })
    observed = sum(row["status"] == "ok" for row in rows)
    return {
        "version": "world-inventory-observation-v1",
        "status": "observed" if rows and observed == len(rows) else "partial" if observed else "unknown",
        "worlds": rows,
        "sharedPremiseRole": "offline-reconciliation" if incremental else "configured-path-needs-verification",
        "note": "갱신 시각과 저장 범위는 관측 정보입니다. 원천 데이터의 최신성·정확성이나 세계 전체의 포괄성을 보증하지 않습니다.",
    }
