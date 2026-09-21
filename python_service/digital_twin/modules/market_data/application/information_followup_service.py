"""Durable informational follow-ups, independent of investment decisions."""

import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..domain.information_observation import exact_time
from digital_twin.shared_kernel.events import DomainEvent


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class InformationFollowupService:
    def __init__(self, repository, sources, observer, settings, now=None, validate_source=None):
        self.repository = repository
        self.sources = sources
        self.observer = observer
        self.settings = settings
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.validate_source = validate_source or (lambda source: True)

    def status(self):
        return {"enabled": str(self.settings.get("informationFollowupEnabled", "1")).lower() not in {"0", "false", "off", "no", "disabled"}, **self.repository.summary()}

    def run_once(self):
        if str(self.settings.get("informationFollowupEnabled", "1")).lower() in {"0", "false", "off", "no", "disabled"}:
            return {"status": "disabled"}
        now = self.now()
        registered = updated = 0
        errors = []
        for source in self.sources(now):
            published = exact_time(source.get("eventAt"))
            if not published and source.get("releaseMetrics"):
                published = exact_time(str(source.get("publicationDate") or "") + "T00:00:00+00:00")
            if not source.get("sourceHash") or not source.get("verified") or not published or published > now or published < now - timedelta(hours=72):
                continue
            identity = "information:" + hashlib.sha256((source["sourceKind"] + ":" + source["sourceId"] + ":" + source["sourceHash"]).encode()).hexdigest()[:40]
            if self.repository.get(identity):
                continue
            try:
                baselines = self.observer.capture_baselines(source["eventAt"], source.get("symbols") or [])
            except Exception:
                errors.append({"sourceId": source["sourceId"], "reason": "baseline-read-error"})
                continue
            row = {"trackingId": identity, "sourceKind": source["sourceKind"], "sourceId": source["sourceId"],
                   "sourceHash": source["sourceHash"], "source": source, "baselineSnapshots": baselines,
                   "status": "active", "createdAt": stamp(now), "updatedAt": stamp(now),
                   "nextCheckAt": stamp(now), "completedHorizons": [], "notifiedPhases": [], "decisionAuthority": False}
            registered += int(self.repository.register(row))
        for row in self.repository.due(stamp(now), limit=25):
            source = row["source"]
            try:
                valid = self.validate_source(source)
                if valid:
                    baselines = row.get("baselineSnapshots") or {}
                    missing = [symbol for symbol in source.get("symbols") or []
                        if not isinstance(baselines.get(symbol), dict)
                        or baselines[symbol].get("observationGranularity") not in {"3m", "15m", "1h"}]
                    if missing:
                        # Retry only historical, pre-publication observations; never today's quote.
                        baselines.update(self.observer.capture_baselines(source["eventAt"], missing))
                        row["baselineSnapshots"] = baselines
                reaction = self.observer.observe(source["eventAt"], source.get("symbols") or [], baselines=row.get("baselineSnapshots") or {}) if valid else {}
            except Exception:
                valid = True
                reaction = {"status": "error", "label": "원문·관측 기록 재확인 대기", "observations": []}
            if not valid or reaction.get("status") == "error":
                expired = now > exact_time(row["createdAt"]) + timedelta(days=4)
                row.update(status="expired" if valid and expired else "active" if valid else "canceled", updatedAt=stamp(now), nextCheckAt=stamp(now + timedelta(minutes=5)),
                    lastError="observation-read-error" if valid else "source-no-longer-eligible")
                if not valid:
                    row["marketReaction"] = {"version": "information-price-observation-v1", "label": "원문 상태 변경으로 관찰 중단", "monitoringMode": "background", "notificationRegistered": False, "trackingStatus": "canceled", "observations": [], "decisionAuthority": False}
                else:
                    errors.append({"sourceId": source["sourceId"], "reason": row["lastError"]})
                updated += int(self.repository.complete(row, None, []))
                continue
            row["lastError"] = ""
            retained = {(item["symbol"], item["horizonMinutes"]): item for item in (row.get("marketReaction") or {}).get("observations", []) if item.get("status") == "observed"}
            reaction["observations"] = [retained.get((item["symbol"], item["horizonMinutes"]), item) for item in reaction.get("observations", [])]
            changed = []
            completed = set(row.get("completedHorizons") or [])
            observed_horizons = set(row.get("observedHorizons") or [])
            expired_horizons = set(row.get("expiredHorizons") or [])
            event_at = exact_time(source["eventAt"])
            if not event_at:
                completed.update((60, 1440))
            for minutes in (60, 1440):
                if not event_at:
                    break
                observations = [item for item in reaction.get("observations") or [] if item.get("horizonMinutes") == minutes]
                done = all(item.get("status") == "observed" for item in observations) and bool(observations)
                expired = now >= event_at + timedelta(minutes=minutes + 180)
                if done:
                    observed_horizons.add(minutes)
                    expired_horizons.discard(minutes)
                elif minutes in completed and minutes not in observed_horizons:
                    expired_horizons.add(minutes)
                if minutes not in completed and (done or expired):
                    completed.add(minutes)
                    (observed_horizons if done else expired_horizons).add(minutes)
                    if any(item.get("status") == "observed" for item in observations) and event_at + timedelta(minutes=minutes) >= exact_time(row["createdAt"]):
                        changed.append(str(minutes))
            released = event_at or exact_time(str(source.get("publicationDate") or "") + "T00:00:00+00:00")
            recent_release = bool(event_at and released and 0 <= (now - released).total_seconds() <= 10800)
            if not event_at:
                recent_release = source.get("publicationDate") == now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
            if source.get("releaseMetrics") and "release" not in row.get("notifiedPhases", []) and recent_release:
                changed.insert(0, "release")
            reaction.update(monitoringMode="background", notificationRegistered=True, trackingId=row["trackingId"])
            row.update(marketReaction=reaction, completedHorizons=sorted(completed), updatedAt=stamp(now),
                       observedHorizons=sorted(observed_horizons), expiredHorizons=sorted(expired_horizons),
                       completionBasis="measured" if observed_horizons == {60, 1440} else "insufficient-observations",
                       status=("completed" if observed_horizons == {60, 1440} else "expired") if len(completed) == 2 else "active",
                       nextCheckAt=stamp(now + timedelta(minutes=5)))
            row["notifiedPhases"] = sorted(set(row.get("notifiedPhases", [])) | set(changed))
            reaction["trackingStatus"] = row["status"]
            reaction["nextCheckAt"] = row["nextCheckAt"] if row["status"] == "active" else ""
            event = DomainEvent(name="information.observation.updated", aggregate_id=row["trackingId"],
                payload={"trackingId": row["trackingId"], "sourceKind": row["sourceKind"], "sourceId": row["sourceId"],
                         "sourceHash": row["sourceHash"], "phases": changed, "decisionAuthority": False})
            updated += int(self.repository.complete(row, event, changed))
        return {"status": "degraded" if errors else "ok", "registeredCount": registered, "updatedCount": updated, "errors": errors[:25]}
