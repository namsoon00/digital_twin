"""Calendar read projection. No changes to events, hypotheses or decisions."""

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from digital_twin.modules.market_data.public import verified_bls_statistics


def _time(value):
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def calendar_release_indicator(event):
    payload = event.get("payload") or {}
    if not isinstance(payload, dict) or payload.get("officialSource") is not True:
        return ""
    provider = str(payload.get("sourceProvider") or "").upper()
    if provider == "BOK" and payload.get("country") == "KR" and payload.get("meetingType") == "monetaryPolicyDecision":
        return "bok"
    if payload.get("country") != "US":
        return ""
    if provider == "FED" and payload.get("meetingType") == "fomcPolicyDecision":
        return "fomc"
    indicator = str(payload.get("indicator") or "").lower()
    return indicator if provider in {"BLS", "FRED"} and indicator in {"cpi", "employment"} else ""


def _verified_release(fact, indicator, event_date, now):
    result = (fact.get("payload") or {}).get("officialRelease") or {}
    dataset = "official." + indicator + "-release" if indicator in {"fomc", "bok"} else "official.bls-release"
    expected_host = {"fomc": "www.federalreserve.gov", "bok": "www.bok.or.kr"}.get(indicator, "www.bls.gov")
    if fact.get("datasetId") != dataset or fact.get("subjectKey") != "release:" + indicator:
        return False
    try:
        url = urlsplit(result.get("sourceUrl") or "")
    except ValueError:
        return False
    body = str(result.get("sourceText") or "")
    released = _time(result.get("releasedAt") or result.get("releasedDate"))
    fetched = _time(fact.get("fetchedAt"))
    released_by_now = bool(released and released <= now)
    if indicator == "bok" and result.get("timePrecision") == "date" and not result.get("releasedAt"):
        released_by_now = bool(released and released.date() <= now.astimezone(ZoneInfo("Asia/Seoul")).date())
    return bool(
        result.get("version") == "official-release-v1" and result.get("country") == ("KR" if indicator == "bok" else "US")
        and result.get("indicator") == indicator and result.get("releasedDate") == event_date
        and url.scheme == "https" and url.hostname == expected_host and not url.username and not url.password
        and released_by_now and fetched and fetched <= now
        and body and result.get("sourceHash") == hashlib.sha256(body.encode("utf-8")).hexdigest()
        and result.get("metrics") and all(isinstance(row, dict) and row.get("excerpt") and row["excerpt"] in body
                                         and row.get("actual") is not None for row in result["metrics"])
    )


def calendar_release_information(event, snapshot=None, now=None, enabled=True):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    snapshot = snapshot or {}
    payload = event.get("payload") or {}
    indicator = calendar_release_indicator(event)
    start = _time(event.get("startsAt"))
    event_zone = ZoneInfo("Asia/Seoul" if indicator == "bok" else "America/New_York")
    event_date = start.astimezone(event_zone).date().isoformat() if start else ""
    collection = dict((snapshot.get("collection") or {}).get(indicator) or {})
    collection_enabled = bool(enabled and collection.get("active"))
    result = {
        "version": "calendar-information-v1", "indicator": indicator,
        "scheduleVerified": payload.get("officialSource") is True and payload.get("scheduleState") == "confirmed",
        "timeRole": "reminder-default" if payload.get("timeState") in {"operationalDefault", "estimatedDefault"} else "source-provided" if payload.get("timeState") in {"official", "sourceProvided"} else "registered",
        "status": "unsupported", "statusLabel": "발표 결과 수집 미지원", "release": None,
        "collection": {**collection, "enabled": collection_enabled,
                       "state": "error" if collection.get("error") or snapshot.get("error") else "scheduled" if collection_enabled else "disabled" if not enabled else "not-registered"},
        "comparison": {"status": "unavailable", "reason": "발표 전 시장 예상치를 수집하지 않아 예상 대비 평가는 하지 않습니다."},
        "marketReaction": {"status": "not-observed", "label": "발표 후 시장 반응 관측 미연결"},
        "decisionAuthority": False,
    }
    if indicator in {"cpi", "employment"}:
        result["latestStatistics"] = latest_statistics(snapshot, indicator, now)
    if not indicator:
        return result
    matches = [fact for fact in snapshot.get("facts") or [] if _verified_release(fact, indicator, event_date, now)]
    if not matches:
        state = "scheduled" if start and start > now else "awaiting-release"
        if snapshot.get("error") or collection.get("error"):
            state = "collection-error"
        result.update(status=state, statusLabel={"scheduled": "발표 전", "awaiting-release": "이 일정의 공식 결과 미확보", "collection-error": "결과 수집 오류"}[state])
        return result
    matches.sort(key=lambda fact: _time(fact.get("fetchedAt")))
    selected = matches[-1]
    release = {key: value for key, value in selected["payload"]["officialRelease"].items() if key != "sourceText"}
    hashes = {fact["payload"]["officialRelease"]["sourceHash"] for fact in matches}
    revisions = {}
    for fact in matches:
        data = fact["payload"]["officialRelease"]
        revisions.setdefault(data["sourceHash"], {"sourceHash": data["sourceHash"], "firstCollectedAt": fact["fetchedAt"], "metrics": data["metrics"]})
    release.update(firstCollectedAt=matches[0]["fetchedAt"], lastCollectedAt=selected["fetchedAt"],
                   revisionCount=len(hashes), revisions=list(revisions.values())[-8:])
    result.update(status="released", statusLabel="공식 발표 결과 확보", release=release)
    return result


def latest_statistics(snapshot, indicator, now):
    candidates = []
    for fact in snapshot.get("facts") or []:
        data = (fact.get("payload") or {}).get("officialStatistics") or {}
        fetched = _time(fact.get("fetchedAt"))
        if fact.get("datasetId") != "official.bls-statistics" or fact.get("subjectKey") != "release:bls-statistics" or not fetched or fetched > now:
            continue
        if data.get("version") != "bls-statistical-vintage-v1" or data.get("sourceUrl") != "https://api.bls.gov/publicAPI/v1/timeseries/data/" or data.get("originalReleaseVintage") is not False:
            continue
        data = verified_bls_statistics(data, now)
        group = (data.get("indicators") or {}).get(indicator) if data else None
        if not isinstance(group, dict) or not group.get("metrics"):
            continue
        candidates.append((fetched, {**group, "source": data["source"], "sourceUrl": data["sourceUrl"], "sourceHash": data["sourceHash"],
            "fetchedAt": fact["fetchedAt"], "ageHours": round((now - fetched).total_seconds() / 3600, 1),
            "freshnessState": "fresh" if (now - fetched).total_seconds() <= 43200 else "stale",
            "label": "최근 공표 통계 · 보관된 조회본", "originalReleaseVintage": False,
            "note": "수정치가 포함될 수 있으며, 이 일정의 최초 발표값이나 발표 전 예상치가 아닙니다."}))
    return max(candidates, key=lambda row: row[0])[1] if candidates else None
