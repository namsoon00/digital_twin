"""Calendar read projection. No changes to events, hypotheses or decisions."""

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


def _time(value):
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def calendar_release_indicator(event):
    payload = event.get("payload") or {}
    if not isinstance(payload, dict) or payload.get("officialSource") is not True or payload.get("country") != "US":
        return ""
    provider = str(payload.get("sourceProvider") or "").upper()
    if provider == "FED" and payload.get("meetingType") == "fomcPolicyDecision":
        return "fomc"
    indicator = str(payload.get("indicator") or "").lower()
    return indicator if provider in {"BLS", "FRED"} and indicator in {"cpi", "employment"} else ""


def _verified_release(fact, indicator, event_date, now):
    result = (fact.get("payload") or {}).get("officialRelease") or {}
    dataset = "official.fomc-release" if indicator == "fomc" else "official.bls-release"
    expected_host = "www.federalreserve.gov" if indicator == "fomc" else "www.bls.gov"
    if fact.get("datasetId") != dataset or fact.get("subjectKey") != "release:" + indicator:
        return False
    try:
        url = urlsplit(result.get("sourceUrl") or "")
    except ValueError:
        return False
    body = str(result.get("sourceText") or "")
    released = _time(result.get("releasedAt") or result.get("releasedDate"))
    fetched = _time(fact.get("fetchedAt"))
    return bool(
        result.get("version") == "official-release-v1" and result.get("country") == "US"
        and result.get("indicator") == indicator and result.get("releasedDate") == event_date
        and url.scheme == "https" and url.hostname == expected_host and not url.username and not url.password
        and released and released <= now and fetched and fetched <= now
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
    event_date = start.astimezone(ZoneInfo("America/New_York")).date().isoformat() if start else ""
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
