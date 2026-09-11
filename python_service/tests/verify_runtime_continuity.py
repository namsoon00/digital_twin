#!/usr/bin/env python3
"""Opt-in passive runtime evidence, without constructing or invoking the runtime."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import signal
import stat
import time
from urllib.parse import urlsplit

from runtime_continuity_reads import (
    CONTROL, DEPLOYMENT, LINEAGE, QUEUES, SOURCES, ReadOnlyDatabase,
    database_options, queue_read,
)


ROOT = Path(__file__).resolve().parents[2]
ENDPOINTS = ("/api/version", "/api/operations/performance")
MAX_BODY = 256 * 1024


class ObservationDeadline(BaseException):
    """Must escape individual HTTP/SQL error handlers so no further reads start."""


def timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def iso(value):
    parsed = timestamp(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def age(value, now):
    parsed = timestamp(value)
    return round((now - parsed).total_seconds(), 3) if parsed else None


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


class Redactor:
    def __init__(self):
        self.salt = secrets.token_bytes(32)

    def identity(self, value):
        if value in (None, ""):
            return None
        return hmac.new(self.salt, str(value).encode(), hashlib.sha256).hexdigest()[:24]


def error_code(error):
    # Exception text can contain SQL, hostnames, accounts, URLs or credentials.
    numeric = error.args[0] if error.args and type(error.args[0]) is int else None
    kind = "timeout" if isinstance(error, (TimeoutError, ObservationDeadline)) else "read-failed"
    return {"kind": kind, "databaseCode": numeric}


@contextmanager
def deadline(seconds):
    def expired(_signum, _frame):
        raise ObservationDeadline("Observation deadline")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def local_endpoint(base_url):
    url = urlsplit(base_url)
    host = url.hostname
    if host == "localhost":
        host = "127.0.0.1"
    if (url.scheme != "http" or url.username is not None or url.password is not None
            or url.path not in ("", "/") or url.query or url.fragment
            or not host or not ipaddress.ip_address(host).is_loopback):
        raise ValueError("Only a credential-free loopback HTTP origin is allowed")
    return host, url.port or 3000


def http_read(endpoint, path, timeout):
    if path not in ENDPOINTS:
        raise ValueError("Endpoint not allowlisted")
    connection = http.client.HTTPConnection(*endpoint, timeout=timeout)
    try:
        connection.request("GET", path, headers={"Accept": "application/json", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ValueError("Response byte cap")
        if response.status != 200:
            return response.status, None
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object")
        return response.status, payload
    finally:
        connection.close()


def http_evidence(path, payload, redactor, now, started_at, row_limit):
    if path == "/api/version":
        if payload.get("contract") != "orbit-runtime-identity-v1" or not timestamp(payload.get("startedAt")):
            raise ValueError("Missing version contract")
        return {"identity": redactor.identity(json.dumps(
            [payload.get("revision"), payload.get("version"), payload.get("startedAt")], sort_keys=True)),
            "processAgeSeconds": age(payload.get("startedAt"), now)}
    if payload.get("version") != "api-performance-v1" or not isinstance(payload.get("routes"), list):
        raise ValueError("Missing performance contract")
    rows = payload["routes"][:row_limit]
    return {
        "generatedAgeSeconds": age(payload.get("generatedAt"), now),
        "truncated": len(payload["routes"]) > row_limit,
        "rollingTelemetryNotCumulative": True,
        "routes": [{
            "routeHash": redactor.identity(str(row.get("method")) + " " + str(row.get("route"))),
            "sampleCount": number(row.get("sampleCount")), "errorCount": number(row.get("errorCount")),
            "p95Ms": number(row.get("p95Ms")), "lastStatus": number(row.get("lastStatus")),
            "lastObservedAt": iso(row.get("lastObservedAt")),
        } for row in rows if isinstance(row, dict)],
        "recentLastErrors": sum(
            1 for row in rows if isinstance(row, dict)
            and (timestamp(row.get("lastObservedAt")) or datetime.min.replace(tzinfo=timezone.utc)) >= started_at
            and (number(row.get("lastStatus")) or 0) >= 400),
    }


def supervisor_evidence(path, now, redactor):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Heartbeat must be a regular file")
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("Heartbeat byte cap")
    payload = json.loads(raw)
    state = payload.get("state")
    return {"state": state if state in {"running", "starting", "maintenance", "stopping"} else "unknown",
            "ageSeconds": age(payload.get("observedAt"), now),
            "identity": redactor.identity(payload.get("pid")),
            "scope": "supervisor-heartbeat-only-not-individual-worker-liveness"}


def queue_evidence(sample, state, now, stale_seconds):
    rows = sample["rows"]
    ages = [age(row.get("created_at"), now) for row in rows]
    return {"state": state, "sampledCount": len(rows), "truncated": sample["truncated"],
            "oldestAgeSeconds": max((value for value in ages if value is not None), default=None),
            "missingTimestamps": sum(value is None for value in ages),
            "errorRows": sum(bool(row.get("has_error")) for row in rows),
            "expiredLeases": sum(bool(timestamp(row.get("lease_expires_at")))
                                 and timestamp(row["lease_expires_at"]) <= now for row in rows),
            "unownedActiveLeases": sum(state in {"running", "processing"} and "lease" in row
                                       and not row.get("lease_owner") for row in rows),
            "missingLeaseExpiries": sum(state in {"running", "processing"} and "lease" in row
                                       and timestamp(row.get("lease_expires_at")) is None for row in rows),
            "staleHeartbeats": sum(bool(row.get("lease_owner")) and (
                age(row.get("heartbeat_at"), now) is None
                or age(row.get("heartbeat_at"), now) > stale_seconds) for row in rows)}


def lineage_evidence(row, now, started_at, redactor):
    missing = [key for key in ("stored_event_id", "stored_snapshot_id", "case_id", "subject_case_id",
                               "stored_ai_request_id", "result_id", "insight_id") if not row.get(key)]
    conflicts = []
    checks = ("account_symbol_match", "candidate_match", "generation_in_result", "abox_in_result",
              "insight_match", "batch_request_match", "source_scope_match", "source_boundary_match")
    for key in checks:
        if row.get(key) is None:
            missing.append(key)
        elif row[key] != 1:
            conflicts.append(key)
    if row.get("stored_snapshot_id") and row.get("executed_snapshot_id") != row["stored_snapshot_id"]:
        missing.append("exact-executed-snapshot")
    if row.get("executed_source_match") != 1:
        missing.append("represented-not-proven-executed-source")
    if not row.get("trace_complete"):
        missing.append("reasoning-trace-complete")
    if not row.get("inference_generation_id") or not row.get("candidate_fingerprint") or not row.get("source_abox_snapshot_id"):
        missing.append("subject-identity")
    ai_valid = (row.get("ai_status") == "completed" and row.get("ai_authored") == 1
                and row.get("publication_contract_passed") == 1 and row.get("publication_mode") == "ai-authored")
    source_at, reasoning_at, ai_at = (timestamp(row.get(key)) for key in ("source_at", "reasoning_at", "ai_at"))
    # AI may finish before the queue completion receipt; do not impose that ordering.
    times_valid = bool(source_at and reasoning_at and ai_at and source_at <= reasoning_at <= now
                       and source_at <= ai_at <= now)
    complete = bool(not missing and not conflicts and ai_valid and times_valid and row.get("source_mode") == "live")
    publication = bool(complete and row.get("publication_id"))
    if row.get("decision_id") and row.get("decision_match") != 1:
        conflicts.append("decision_match")
        publication = False
    if row.get("publication_outcome") == "FINAL_DECISION":
        publication = bool(publication and row.get("decision_id") and row.get("decision_match") == 1)
    if row.get("notification_id") and row.get("notification_match") != 1:
        conflicts.append("notification_match")
        publication = False
    delivery = bool(publication and row.get("notification_id") and row.get("attempt_id")
                    and row.get("notification_match") == 1 and row.get("is_mock") == 0
                    and row.get("data_quality") == "actual" and row.get("delivery_status") == "delivered"
                    and row.get("channel") == "telegram" and timestamp(row.get("delivery_at"))
                    and ai_at <= timestamp(row["delivery_at"]) <= now)
    in_window = bool(complete and ai_at >= started_at)
    states = {"queued", "pending", "retry", "processing", "completed", "failed", "superseded", "suppressed"}
    outcomes = {"FINAL_DECISION", "REVIEW_ONLY", "OBSERVATION", "ABSTAIN", "SUPPRESSED", "ARCHIVE"}
    return {"id": redactor.identity(row.get("result_id") or row.get("subject_case_id") or row.get("job_id")),
            "anchors": {key: redactor.identity(row.get(key)) for key in (
                "source_event_id", "source_snapshot_id", "job_id", "case_id", "subject_case_id",
                "result_id", "insight_id", "publication_id", "notification_id", "attempt_id")},
            "missing": missing, "conflicts": conflicts,
            "aiRequested": bool(row.get("ai_request_id") or row.get("stored_ai_request_id")),
            "subjectOutcomeKind": row.get("outcome_kind") if row.get("outcome_kind") in outcomes else "unknown",
            "publicationOutcomeKind": row.get("publication_outcome") if row.get("publication_outcome") in outcomes else "unknown",
            "hasStoredPublicationRow": bool(row.get("publication_id")),
            "aiStatus": row.get("ai_status") if row.get("ai_status") in states else "unknown",
            "aiAuthoredContractPassed": ai_valid, "storedLinkedAi": complete,
            "aiCompletedInWindow": in_window,
            "sourceToAiEntirelyInWindow": bool(in_window and source_at >= started_at),
            "storedPublication": publication, "recordedTelegramDelivery": delivery,
            "deliveryInWindow": bool(delivery and timestamp(row["delivery_at"]) >= started_at),
            "sourceAt": iso(source_at), "reasoningAt": iso(reasoning_at), "aiAt": iso(ai_at),
            "sourceToAiSeconds": round((ai_at - source_at).total_seconds(), 3) if complete else None}


def collect_database(options, args, redactor, now, started_at):
    result = {"queues": [], "lineage": [], "queries": 0}
    db = None
    try:
        db = ReadOnlyDatabase(options, args.row_limit, args.query_timeout_ms)
        cutoff = iso(now - timedelta(seconds=args.lookback_seconds))
        controls = db.read(CONTROL, limit=1)["rows"]
        if not controls:
            result["missingControl"] = True
            return result
        control = controls[0]
        result["controlIdentity"] = redactor.identity(json.dumps(control, sort_keys=True, default=str))
        deployments = sorted({control.get(key) for key in (
            "active_deployment_id", "delivery_deployment_id", "candidate_deployment_id") if control.get(key)})
        result["expectedDeploymentCount"] = len(deployments)
        result["expectedQueueReads"] = len(deployments) * len(QUEUES["reasoning"][3]) + 8
        roles = {deployment: [role for role in ("active", "delivery", "candidate")
                              if control.get(role + "_deployment_id") == deployment] for deployment in deployments}
        result["deployments"] = []
        for deployment in deployments:
            rows = db.read(DEPLOYMENT, (deployment,), limit=1)["rows"]
            for row in rows:
                result["deployments"].append({
                    "id": redactor.identity(deployment), "release": redactor.identity(row.get("release_hash")),
                    "roles": roles[deployment],
                    "lastRunAgeSeconds": age(row.get("last_run_at"), now),
                    "health": row.get("health") if row.get("health") in {
                        "ready", "healthy", "deferred", "degraded", "blocked", "failed", "error", "running"} else "unknown",
                    "hasError": bool(row.get("has_error"))})
        sources = db.read(SOURCES)
        dates = [timestamp(row.get("generated_at")) for row in sources["rows"] if row.get("mode") == "live"]
        latest = max((date for date in dates if date), default=None)
        result["sources"] = {"sampledCount": len(sources["rows"]), "truncated": sources["truncated"],
                             "liveSampledCount": len(dates), "latestAt": iso(latest),
                             "latestAgeSeconds": age(latest, now),
                             "missingLiveTimestamps": sum(date is None for date in dates)}
        for kind, (_table, _id, _status, states) in QUEUES.items():
            for deployment in deployments if kind == "reasoning" else [None]:
                for state in states:
                    sample = queue_read(db, kind, state, cutoff, deployment)
                    for row in sample["rows"]:
                        if kind != "notification":
                            row["lease"] = True
                    result["queues"].append(dict(queue_evidence(sample, state, now, args.stale_seconds),
                                                 kind=kind, deployment=redactor.identity(deployment),
                                                 roles=roles.get(deployment, [])))
        delivery = control.get("delivery_deployment_id")
        if delivery:
            sample = db.read(LINEAGE, (delivery, cutoff, args.row_limit + 1))
            result["lineageTruncated"] = sample["truncated"]
            result["lineageSampledRows"] = len(sample["rows"])
            result["lineage"] = [lineage_evidence(row, now, started_at, redactor) for row in sample["rows"]]
        else:
            result["missingDeliveryDeployment"] = True
    except Exception as error:
        result["error"] = error_code(error)
    finally:
        if db:
            result["queries"] = db.queries
            db.close()
    return result


def observe(args, options, redactor, started_at):
    now = datetime.now(timezone.utc)
    observation = {"observedAt": iso(now), "http": []}
    for path in ENDPOINTS:
        before = time.monotonic()
        item = {"path": path}
        try:
            status, payload = http_read(local_endpoint(args.base_url), path, args.timeout_seconds)
            item["statusCode"] = status
            if status == 200:
                item.update(http_evidence(path, payload, redactor, now, started_at, args.row_limit))
        except Exception as error:
            item["error"] = error_code(error)
        item["durationMs"] = round((time.monotonic() - before) * 1000, 3)
        observation["http"].append(item)
    try:
        observation["supervisor"] = supervisor_evidence(args.heartbeat_file, now, redactor)
    except Exception as error:
        observation["supervisor"] = {"error": error_code(error)}
    observation["database"] = collect_database(options, args, redactor, now, started_at)
    return observation


def verdicts(observations, args, missed, completed):
    issues, gaps = set(), set()
    candidate_issues = set()
    identities, source_dates, linked, full, publications, delivered = set(), [], set(), set(), set(), set()
    releases, historical, lineage_gaps, lineage_errors = {}, set(), set(), set()
    backlog, primary_backlog = [], []
    for observation in observations:
        if observation.get("error"):
            issues.add("observation-timeout-or-error")
        http = observation.get("http", [])
        if len(http) != len(ENDPOINTS):
            gaps.add("missing-endpoint-observation")
        for item in http:
            if item.get("statusCode") != 200 or item.get("error"):
                issues.add("http-error-or-invalid-contract")
            if (item.get("durationMs") or 0) > args.slow_ms:
                issues.add("slow-http-observation")
            if item.get("identity"):
                identities.add(item["identity"])
            if item.get("recentLastErrors"):
                issues.add("recent-http-error-in-rolling-telemetry")
            if item.get("path") == ENDPOINTS[1]:
                value = item.get("generatedAgeSeconds")
                if value is None:
                    gaps.add("missing-telemetry-freshness")
                elif value > args.stale_seconds or value < -5:
                    issues.add("stale-telemetry-or-clock-skew")
            if item.get("truncated"):
                gaps.add("telemetry-truncated")
        supervisor = observation.get("supervisor", {})
        value = supervisor.get("ageSeconds")
        if value is None:
            gaps.add("supervisor-heartbeat-unavailable")
        elif value > args.stale_seconds or value < -5 or supervisor.get("state") != "running":
            issues.add("supervisor-stale-or-not-running")
        db = observation.get("database", {})
        if db.get("error"):
            issues.add("database-read-failed")
        if not db.get("controlIdentity") or not db.get("deployments"):
            gaps.add("missing-deployment-state")
        if len(db.get("deployments", [])) != db.get("expectedDeploymentCount"):
            gaps.add("missing-deployment-state")
        if db.get("controlIdentity"):
            identities.add("control:" + db["controlIdentity"])
        for row in db.get("deployments", []):
            releases.setdefault(row["id"], set()).add(row["release"])
            health_issues = candidate_issues if row.get("roles") == ["candidate"] else issues
            if row.get("hasError") or row.get("health") in {"degraded", "blocked", "failed", "error"}:
                health_issues.add("deployment-error-state")
            if row.get("health") == "unknown":
                gaps.add("unknown-deployment-health")
        sources = db.get("sources", {})
        if sources.get("latestAt"):
            source_dates.append(timestamp(sources["latestAt"]))
            if sources["latestAgeSeconds"] > args.stale_seconds or sources["latestAgeSeconds"] < -5:
                issues.add("stale-source-or-clock-skew")
        else:
            gaps.add("no-live-source-timestamp")
        if sources.get("missingLiveTimestamps"):
            gaps.add("source-sample-incomplete")
        queues = db.get("queues", [])
        if not queues or len(queues) != db.get("expectedQueueReads"):
            gaps.add("queues-unavailable")
        backlog.append(sum(row["sampledCount"] for row in queues if row["state"] != "failed"))
        primary_backlog.append(sum(row["sampledCount"] for row in queues
                                   if row["state"] != "failed" and row.get("roles") != ["candidate"]))
        for row in queues:
            queue_issues = candidate_issues if row.get("roles") == ["candidate"] else issues
            if row["truncated"] or row["missingTimestamps"] or row["missingLeaseExpiries"]:
                gaps.add("queue-sample-incomplete")
            if row["state"] == "failed" and row["sampledCount"]:
                queue_issues.add("recent-failed-jobs")
            if row["expiredLeases"] or row["staleHeartbeats"] or row["unownedActiveLeases"]:
                queue_issues.add("expired-unowned-or-stale-worker-lease")
            if row["state"] != "failed" and (row["oldestAgeSeconds"] or 0) > args.backlog_stale_seconds:
                queue_issues.add("aged-backlog")
        for row in db.get("lineage", []):
            if row["missing"]:
                lineage_gaps.update(row["missing"])
            if row["conflicts"]:
                issues.add("lineage-identity-conflict")
                lineage_errors.add("lineage-identity-conflict")
            if row["aiStatus"] == "failed":
                lineage_errors.add("sampled-ai-failure")
            if row["storedLinkedAi"]:
                historical.add(row["id"])
            if row["aiCompletedInWindow"]:
                linked.add(row["id"])
                if row["storedPublication"]:
                    publications.add(row["id"])
            if row["sourceToAiEntirelyInWindow"]:
                full.add(row["id"])
            if row["deliveryInWindow"]:
                delivered.add(row["id"])
        if db.get("lineageTruncated"):
            lineage_gaps.add("lineage-sample-truncated")
        if db.get("error") or db.get("missingDeliveryDeployment"):
            lineage_gaps.add("lineage-read-unavailable")
    web_ids = {value for value in identities if not value.startswith("control:")}
    control_ids = identities - web_ids
    if len(web_ids) > 1 or len(control_ids) > 1:
        issues.add("runtime-or-control-drift")
    if any(len(values) > 1 for values in releases.values()):
        issues.add("deployment-release-drift")
    if missed or not completed:
        gaps.add("missing-scheduled-observations")
    progressed = bool(len(source_dates) > 1 and source_dates[-1] > source_dates[0])
    if not linked:
        lineage_gaps.add("no-new-live-linked-ai-completion")
    if len(source_dates) > 1 and any(right < left for left, right in zip(source_dates, source_dates[1:])):
        issues.add("source-time-regressed")
    continuity = "degraded" if issues else "inconclusive" if gaps else "pass"
    return {"sampledInfrastructure": continuity, "issues": sorted(issues), "gaps": sorted(gaps),
            "infrastructureScope": "web-supervisor-sources-active-delivery-ai-notification",
            "candidateOnlyHealth": {"status": "degraded" if candidate_issues else "inconclusive",
                                    "issues": sorted(candidate_issues), "notAssumedInactive": True},
            "sourceProgress": {"status": "pass" if progressed else "inconclusive",
                               "timestampAdvanced": progressed,
                               "distinctLatestTimestamps": len(set(source_dates)),
                               "firstLatestAt": iso(source_dates[0]) if source_dates else None,
                               "lastLatestAt": iso(source_dates[-1]) if source_dates else None},
            "liveAiLineage": {"status": "degraded" if lineage_errors else "pass" if linked else "inconclusive",
                              "aiCompletionsInWindow": len(linked), "entireSourceToAiInWindow": len(full),
                              "publicationsWithInWindowAi": len(publications),
                              "storedLinkedAiIncludingHistorical": len(historical),
                              "gaps": sorted(lineage_gaps), "issues": sorted(lineage_errors)},
            "notificationLineage": {"status": "pass" if delivered else "inconclusive",
                                    "recordedTelegramDeliveriesInWindow": len(delivered)},
            "sampledBacklog": {"first": backlog[0] if backlog else None,
                               "last": backlog[-1] if backlog else None,
                               "peak": max(backlog, default=None), "notFullTotals": True, "includesCandidateOnly": True,
                               "primaryFirst": primary_backlog[0] if primary_backlog else None,
                               "primaryLast": primary_backlog[-1] if primary_backlog else None,
                               "primaryPeak": max(primary_backlog, default=None)}}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live-read-only", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=900)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--timeout-seconds", type=int, default=3)
    parser.add_argument("--query-timeout-ms", type=int, default=1000)
    parser.add_argument("--row-limit", type=int, default=100)
    parser.add_argument("--lookback-seconds", type=int, default=3600)
    parser.add_argument("--stale-seconds", type=int, default=300)
    parser.add_argument("--backlog-stale-seconds", type=int, default=1800)
    parser.add_argument("--slow-ms", type=int, default=2000)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--heartbeat-file", type=Path, default=ROOT / "data/python-supervisor-heartbeat.json")
    args = parser.parse_args(argv)
    bounds = {"duration_seconds": (0, 86400), "interval_seconds": (30, 900),
              "timeout_seconds": (1, 5), "query_timeout_ms": (100, 3000), "row_limit": (1, 200),
              "lookback_seconds": (60, 86400), "stale_seconds": (30, 86400),
              "backlog_stale_seconds": (60, 86400), "slow_ms": (100, 30000)}
    for key, (lower, upper) in bounds.items():
        if not lower <= getattr(args, key) <= upper:
            parser.error(key.replace("_", "-") + " is out of bounds")
    if 0 < args.duration_seconds < args.interval_seconds:
        parser.error("Use duration 0 for a single smoke observation, otherwise at least one interval")
    try:
        local_endpoint(args.base_url)
    except (ValueError, TypeError):
        parser.error("Invalid loopback HTTP origin")
    if not args.output.is_absolute() or ROOT == args.output.resolve() or ROOT in args.output.resolve().parents:
        parser.error("Output must be an absolute new path outside the repository")
    return args


def new_report(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8")


def run(args, options):
    redactor = Redactor()
    fingerprint = hashlib.sha256(
        Path(__file__).read_bytes() + Path(__file__).with_name("runtime_continuity_reads.py").read_bytes()
    ).hexdigest()
    started_at = datetime.now(timezone.utc)
    start = time.monotonic()
    observations, missed = [], []
    targets = list(range(0, args.duration_seconds + 1, args.interval_seconds))
    completed = False
    with new_report(args.output) as output, new_report(Path(str(args.output) + ".progress.jsonl")) as progress:
        try:
            for target in targets:
                wait = start + target - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                elapsed = time.monotonic() - start
                if elapsed - target > 2:
                    missed.append(target)
                    continue
                before = time.monotonic()
                try:
                    with deadline(20):
                        observation = observe(args, options, redactor, started_at)
                except (Exception, ObservationDeadline) as error:
                    observation = {"observedAt": iso(datetime.now(timezone.utc)), "error": error_code(error)}
                observation.update(scheduledSeconds=target, elapsedSeconds=round(before - start, 3),
                                   durationSeconds=round(time.monotonic() - before, 3))
                observations.append(observation)
                progress.write(json.dumps(observation, sort_keys=True) + "\n")
                progress.flush()
            remaining = start + args.duration_seconds - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            completed = not missed and len(observations) == len(targets)
        except KeyboardInterrupt:
            missed.extend(target for target in targets if target not in {
                row["scheduledSeconds"] for row in observations} and target not in missed)
        report = {"version": "runtime-continuity-v1", "startedAt": iso(started_at),
                  "verifierFingerprint": fingerprint,
                  "finishedAt": iso(datetime.now(timezone.utc)), "readOnly": True,
                  "requestedDurationSeconds": args.duration_seconds,
                  "elapsedSeconds": round(time.monotonic() - start, 3),
                  "sampledSpanSeconds": round(observations[-1]["elapsedSeconds"] - observations[0]["elapsedSeconds"], 3) if observations else 0,
                  "expectedObservations": len(targets), "observedCount": len(observations),
                  "missedScheduledSeconds": missed, "durationCompleted": completed,
                  "limits": {key: getattr(args, key) for key in (
                      "interval_seconds", "timeout_seconds", "query_timeout_ms", "row_limit", "lookback_seconds",
                      "stale_seconds", "backlog_stale_seconds", "slow_ms")},
                  "observations": observations,
                  "verdicts": verdicts(observations, args, missed, completed),
                  "limitations": ["Passive samples cannot prove availability between polls or 24-hour stability from a shorter run.",
                                  "Stored lineage is not model correctness, live TypeDB revalidation, or recipient acknowledgement.",
                                  "No inference, notification, synthetic job, settings mutation, promotion, or process restart was requested.",
                                  "Queue and lineage windows are bounded samples, not full totals; retained/deleted rows can leave gaps.",
                                  "Supervisor heartbeat and task leases do not prove every idle worker is alive."]}
        output.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        result = report["verdicts"]
        print(json.dumps({"sampledInfrastructure": result["sampledInfrastructure"],
                          "liveAiLineage": result["liveAiLineage"]["status"],
                          "observedCount": len(observations), "durationCompleted": completed}))
        return 1 if "degraded" in (result["sampledInfrastructure"], result["liveAiLineage"]["status"]) else 0 if (
            result["sampledInfrastructure"] == "pass" and result["liveAiLineage"]["status"] == "pass"
            and result["sourceProgress"]["status"] == "pass") else 2


def main(argv=None):
    args = parse_args(argv)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        options = database_options(os.environ, args.timeout_seconds)
        return run(args, options)
    except Exception as error:
        print(json.dumps({"status": "inconclusive", "error": error_code(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
