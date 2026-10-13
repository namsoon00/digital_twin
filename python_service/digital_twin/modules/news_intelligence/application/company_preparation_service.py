"""Bounded reconciliation on the existing research worker, without LLM calls."""
from datetime import datetime, timedelta, timezone
from ..domain.company_preparation import assess_preparation


class CompanyPreparationService:
    def __init__(self, subjects, store, reports, relationships, ai, refresh, settings=None, clock=None):
        self.subjects, self.store, self.reports, self.relationships = subjects, store, reports, relationships
        self.ai, self.refresh, self.settings = ai, refresh, dict(settings or {})
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self):
        now = self.clock()
        stamp = now.isoformat().replace("+00:00", "Z")
        subjects = sorted(self.subjects(), key=lambda row: (row["accountId"], row["symbol"]))
        checked, errors = 0, []
        limit = max(1, min(5, int(self.settings.get("companyPreparationBatchSize") or 2)))
        with self.store.subject("", "rotation") as cursor:
            if cursor is None:
                return {"status": "busy", "checked": 0}
            position = cursor.load().get("after", "")
            key = lambda row: row["accountId"] + ":" + row["symbol"]
            ordered = [row for row in subjects if key(row) > position] + [row for row in subjects if key(row) <= position]
            for subject in ordered:
                if checked >= limit:
                    break
                # The rotation lock already serializes every writer. A second
                # connection-held lock would exhaust the normal two-slot pool
                # before the bounded report/evidence reads can begin.
                state = self.store.state(subject["accountId"], subject["symbol"])
                previous = state.load()
                if previous.get("nextCheckAt", "") > stamp and previous.get("worldId", "") == subject.get("worldId", "") and previous.get("roles") == subject["roles"]:
                    continue
                checked += 1
                try:
                    if not subject.get("worldId"):
                        self.refresh(subject["accountId"])
                        payload, relationships, ai = {}, {}, {"status": "waiting"}
                        ai["detail"] = "모니터 자료 반영 후 중앙 AI가 분석 대상을 발견합니다."
                    else:
                        payload = self.reports(subject)
                        relationships = self.relationships(subject)
                        ai = self.ai.progress(subject)
                    result = assess_preparation(subject, payload, relationships, ai, previous, stamp)
                    if subject.get("worldId") and result["gaps"]:
                        result["researchRequest"] = self.ai.request_preparation(subject, result["gaps"], result["researchBasis"])
                    # Existing source collectors and central AI retain their
                    # own leases, cooldowns, evidence and publication checks.
                    delay = 60 if result["status"] == "complete" else 15
                    result["nextCheckAt"] = (now + timedelta(minutes=delay)).isoformat().replace("+00:00", "Z")
                    state.replace(result)
                except Exception as error:
                    failed = {**previous, "version": "company-preparation-v1", "accountId": subject["accountId"], "symbol": subject["symbol"],
                              "roles": subject["roles"], "worldId": subject.get("worldId", ""), "status": "failed", "label": "재처리 대기",
                              "checkedAt": stamp, "lastErrorKind": type(error).__name__,
                              "nextCheckAt": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")}
                    state.replace(failed)
                    errors.append({"symbol": subject["symbol"], "errorKind": type(error).__name__})
                cursor.replace({"after": key(subject), "updatedAt": stamp})
        return {"status": "partial" if errors else "ok", "checked": checked, "universeCount": len(subjects), "errors": errors}
