"""Bounded, synthetic multi-account rehearsal of the real MySQL storage path.

Run this file directly; it supervises its own child and exclusively owned schema.
It never runs TypeDB, inference, notification transports, or managed workers.
"""

import argparse
from array import array
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import gc
import json
import math
import os
from pathlib import Path
import re
import resource
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import traceback
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "orbit_alpha_test_integrated_load_"
REVISIONS = 3
PAST = "2000-01-01T00:00:00Z"
RELEASE = "f" * 64
SETUP_SECONDS = 90


class VerificationFailure(RuntimeError):
    pass


def require(condition, message):
    # Unlike assert, verification must remain active under python -O.
    if not condition:
        raise VerificationFailure(message)


@dataclass(frozen=True)
class LoadConfig:
    mode: str = "regression"
    accounts: int = 4
    concurrency: int = 2
    rounds: int = 2
    duration_seconds: float = 30
    recovery_seconds: float = 30
    max_cases: int = 2000
    wave_interval_seconds: float = 0.2

    def validate(self):
        require(self.mode in {"regression", "soak"}, "Unknown rehearsal mode")
        for name, lower, upper in (
            ("accounts", 2, 64), ("concurrency", 1, 16),
            ("rounds", 1, 1000), ("max_cases", 2, 10000),
        ):
            value = getattr(self, name)
            require(type(value) is int and lower <= value <= upper,
                    name + " is outside the bounded integer range")
        require(self.concurrency <= self.accounts, "Concurrency exceeds accounts")
        for name, lower, upper in (
            ("duration_seconds", 1, 3600), ("recovery_seconds", 5, 120),
            ("wave_interval_seconds", 0, 5),
        ):
            value = getattr(self, name)
            require(math.isfinite(value) and lower <= value <= upper,
                    name + " is outside the finite bounded range")
        required = self.accounts * (self.rounds if self.mode == "regression" else 1)
        require(required <= self.max_cases, "Case cap cannot accommodate requested waves")
        return self

    @property
    def load_budget(self):
        return self.duration_seconds if self.mode == "soak" else 60

    @property
    def hard_timeout(self):
        return SETUP_SECONDS + self.load_budget + self.recovery_seconds + 15


def isolated_environment(database, directory, source=None):
    """Never inherit URL overrides, account secrets, settings paths or runtime data."""
    source = os.environ if source is None else source
    require(bool(re.fullmatch(DATABASE_PREFIX + r"[0-9a-f]{24}", database)),
            "Refusing a schema not owned by this harness")
    host = source.get("MYSQL_HOST", "127.0.0.1")
    require(host in {"127.0.0.1", "localhost", "::1"}, "Only loopback MySQL is allowed")
    port = int(source.get("MYSQL_PORT", "3306"))
    require(1 <= port <= 65535, "Invalid local MySQL port")
    unix_socket = source.get("MYSQL_UNIX_SOCKET", "")
    require(not unix_socket or os.path.isabs(unix_socket), "MySQL socket must be absolute")
    environment = {key: source[key] for key in ("PATH", "SYSTEMROOT", "LANG", "LC_ALL") if key in source}
    environment.update({
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests")]),
        "PYTHONUNBUFFERED": "1",
        "MYSQL_HOST": host, "MYSQL_PORT": str(port),
        "MYSQL_USER": source.get("MYSQL_USER", "root"),
        "MYSQL_PASSWORD": source.get("MYSQL_PASSWORD", ""),
        "MYSQL_UNIX_SOCKET": unix_socket,
        "MYSQL_DATABASE": database, "MYSQL_TEST_DATABASE": database,
        "MYSQL_URL": "", "DATABASE_URL": "",
        "DIGITAL_TWIN_DATA_DIR": str(directory),
        "SETTINGS_PATH": str(Path(directory) / "unused-settings.json"),
        "ORBIT_RUNTIME_ENV": "test", "ORBIT_RUNTIME_REVISION": "integrated-load-fixture",
        "ORBIT_INFRASTRUCTURE_OVERRIDE_ENABLED": "1",
    })
    return environment


def admin_connection(environment):
    import pymysql

    kwargs = {
        "host": environment["MYSQL_HOST"], "port": int(environment["MYSQL_PORT"]),
        "user": environment["MYSQL_USER"], "password": environment["MYSQL_PASSWORD"],
        "charset": "utf8mb4", "autocommit": True,
        "connect_timeout": 5, "read_timeout": 5, "write_timeout": 5,
    }
    if environment["MYSQL_UNIX_SOCKET"]:
        kwargs["unix_socket"] = environment["MYSQL_UNIX_SOCKET"]
    return pymysql.connect(**kwargs)


@contextmanager
def local_mysql_only(environment):
    """Fail closed on unintended Python socket, subprocess or schema access."""
    import pymysql

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_mysql = pymysql.connect
    database = environment["MYSQL_TEST_DATABASE"]
    blocked = {"socket": 0, "schema": 0, "process": 0}

    def admit(condition, kind, message):
        if not condition:
            blocked[kind] += 1
            raise VerificationFailure(message)

    def allowed(address):
        if isinstance(address, tuple):
            return (address[0] in {"127.0.0.1", "::1"}
                    and address[1] == int(environment["MYSQL_PORT"]))
        return bool(environment["MYSQL_UNIX_SOCKET"]) and address == environment["MYSQL_UNIX_SOCKET"]

    def connect(instance, address):
        admit(allowed(address), "socket", "Non-MySQL socket blocked by load harness")
        return original_connect(instance, address)

    def connect_ex(instance, address):
        admit(allowed(address), "socket", "Non-MySQL socket blocked by load harness")
        return original_connect_ex(instance, address)

    def mysql_connect(*args, **kwargs):
        admit(not args, "schema", "Positional database connection blocked")
        target = kwargs.get("database", kwargs.get("db", ""))
        admit(target in {"", database}, "schema", "Foreign schema connection blocked")
        for timeout in ("connect_timeout", "read_timeout", "write_timeout"):
            kwargs[timeout] = min(5, kwargs.get(timeout) or 5)
        return original_mysql(**kwargs)

    def launch_blocked(*_args, **_kwargs):
        admit(False, "process", "Child process launch blocked")

    def getaddrinfo(host, *args, **kwargs):
        admit(host in {"127.0.0.1", "localhost", "::1"}, "socket", "Non-local DNS lookup blocked")
        return original_getaddrinfo(host, *args, **kwargs)

    def sendto_blocked(*_args, **_kwargs):
        admit(False, "socket", "Datagram send blocked")

    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", connect))
        stack.enter_context(patch.object(socket.socket, "connect_ex", connect_ex))
        stack.enter_context(patch.object(socket, "getaddrinfo", getaddrinfo))
        stack.enter_context(patch.object(socket.socket, "sendto", sendto_blocked))
        stack.enter_context(patch.object(pymysql, "connect", mysql_connect))
        for owner, name in ((subprocess, "Popen"), (os, "system")):
            stack.enter_context(patch.object(owner, name, launch_blocked))
        yield blocked


def percentile_summary(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50Ms": None, "p95Ms": None, "p99Ms": None, "maxMs": None}
    result = {"count": len(ordered), "maxMs": round(ordered[-1], 3)}
    for percentile in (50, 95, 99):
        result["p" + str(percentile) + "Ms"] = round(ordered[math.ceil(len(ordered) * percentile / 100) - 1], 3)
    return result


class Measurements:
    def __init__(self):
        self.samples = {}
        self.counters = {}
        self.lock = threading.Lock()

    def add(self, name, milliseconds):
        with self.lock:
            values = self.samples.setdefault(name, array("d"))
            require(len(values) < 100000, "Latency sample cap reached")
            values.append(milliseconds)

    def count(self, name, amount=1):
        with self.lock:
            self.counters[name] = self.counters.get(name, 0) + amount

    @contextmanager
    def measure(self, name):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, (time.perf_counter() - started) * 1000)


def claim_ai_with_retry(store, worker, measurements):
    # Measure the production policy once; a harness retry would multiply its budget.
    try:
        with measurements.measure("aiClaim"):
            return store.claim(worker, 1, 60)
    finally:
        receipt = dict(store.last_transaction_retry or {})
        measurements.count("aiClaimTransactionAttempts", int(receipt.get("attempts") or 1))
        measurements.count("aiClaimDeadlockRetries", int(receipt.get("retryCount") or 0))


def memory_sample():
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    high_water = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "pythonCurrentBytes": current, "pythonPeakBytes": peak,
        "processRssHighWaterBytes": int(high_water * (1 if sys.platform == "darwin" else 1024)),
    }


class Rehearsal:
    def __init__(self, config):
        # Reuse existing fixtures and production adapters after isolation is installed.
        import stabilization_database as fixtures
        from mysql_fixtures import mysql_test_settings
        from digital_twin.infrastructure.transactions import monitoring
        from digital_twin.modules.decisions.domain.ai_inference_queue import AIInferenceRequest, AIInferenceResult
        from digital_twin.modules.decisions.domain.investment_brain import ObservedOutcome
        from digital_twin.modules.notifications.domain.notifications import NotificationJob
        from digital_twin.modules.reasoning.domain.job_claim import ReasoningJobLeaseLost

        self.config = config
        self.fixtures = fixtures
        self.monitoring = monitoring
        self.AIRequest, self.AIResult = AIInferenceRequest, AIInferenceResult
        self.Outcome, self.Notification = ObservedOutcome, NotificationJob
        self.LeaseLost = ReasoningJobLeaseLost
        self.metrics = Measurements()
        self.settings = mysql_test_settings()
        self.settings.update({
            "mysqlConnectionPoolSize": str(config.concurrency),
            "mysqlOperationTimeoutSeconds": "5",
            "mysqlDeadlockRetryCount": "3",
            "_skipNotificationRuleDefaultsSeed": "1",
            "_skipOperationalHistoryRetention": "1",
        })
        self.jobs = fixtures.MySQLReasoningEngineJobStore(self.settings)
        self.settings["_skipOperationalSchemaBootstrap"] = "1"
        self.registry = fixtures.MySQLReasoningEngineRegistryStore(self.settings)
        self.events = fixtures.MySQLEventLog(self.settings)
        self.monitor = fixtures.MySQLMonitorStore(self.settings)
        self.anchors = monitoring.MySQLMarketObservationReasoningAnchorStore(self.settings)
        self.notifications = fixtures.MySQLNotificationJobStore(self.settings)
        self.ai = fixtures.MySQLAIInferenceQueueStore(self.settings)
        self.decisions = fixtures.MySQLInvestmentDecisionEpisodeStore(self.settings)
        self.deployment = "integrated-load-fixture"
        self.registry.upsert(fixtures.ReasoningEngineDescriptor(
            engine_family="fixture", engine_version="v2", deployment_id=self.deployment,
            status="active", graph_store_binding="no-typedb-fixture",
            time_series_backend_id="no-external-fixture",
            release_bundle=fixtures.EngineReleaseBundle("fixture-tbox", "fixture-rules", "fixture-prompt", "fixture-features"),
        ))
        self.registry.set_control(self.deployment, self.deployment)
        self.accounts = ["load-account-%04d" % index for index in range(config.accounts)]
        self.waves = 0
        self.backlogs = []
        self.consistency = {}
        self.deadline = float("inf")
        self.active_claims = set()
        self.peak_claims = 0
        self.claim_lock = threading.Lock()
        self.version = self.sql("SELECT VERSION() AS version")["version"]
        self.memory_before = memory_sample()
        self.memory_after = self.memory_before
        self.load_started = time.monotonic()

    def sql(self, statement, params=(), many=False):
        with self.jobs.connect() as connection:
            cursor = connection.execute(statement, params)
            if statement.lstrip().upper().startswith("SELECT"):
                return cursor.fetchall() if many else cursor.fetchone()
            return cursor.rowcount

    def counts(self, tables):
        return {table: self.sql("SELECT COUNT(*) AS n FROM " + table)["n"] for table in tables}

    def check_deadline(self):
        require(time.monotonic() < self.deadline, "Bounded workload/recovery deadline exceeded")

    def parallel(self, callback, values):
        with ThreadPoolExecutor(max_workers=self.config.concurrency) as pool:
            return list(pool.map(callback, values))

    def backlog(self, phase):
        states = self.sql("SELECT job_status, COUNT(*) AS n FROM reasoning_engine_jobs GROUP BY job_status", many=True)
        counts = {row["job_status"]: row["n"] for row in states}
        pending = sum(value for state, value in counts.items() if state not in {"completed", "superseded", "failed", "excluded"})
        ai_pending = self.sql("SELECT COUNT(*) AS n FROM ai_inference_requests WHERE status NOT IN ('completed', 'failed', 'superseded')")["n"]
        delivery_pending = self.sql("SELECT COUNT(*) AS n FROM notification_jobs WHERE status NOT IN ('done', 'failed', 'suppressed')")["n"]
        row = {"wave": self.waves, "phase": phase, "elapsedMs": round((time.monotonic() - self.load_started) * 1000, 3),
               "reasoningPending": pending, "states": counts,
               "aiPending": ai_pending, "deliveryPending": delivery_pending}
        self.backlogs.append(row)
        return pending

    def mark_anchor(self, account, event_id, stamp):
        with self.anchors.transaction() as connection:
            self.anchors.mark_pending_with_connection(connection, account, event_id, [
                {"symbol": "AAPL", "marketObservation": {"currentPrice": 101, "baselinePrice": 100}},
            ], stamp)

    def snapshot_rollback(self):
        tables = ["monitor_snapshots", "monitor_snapshot_history", "monitor_snapshot_reasoning_inputs", "verified_reasoning_source_snapshots", "domain_events", "reasoning_engine_jobs"]
        before = self.counts(tables)
        with patch.object(self.monitoring.reasoning_writes, "upsert_reasoning_snapshot_inputs", side_effect=VerificationFailure("injected-source-interruption")):
            try:
                self.monitor.save_snapshot(self.fixtures.fixture_snapshot("load-rollback"))
            except VerificationFailure as error:
                require(str(error) == "injected-source-interruption", "Unexpected source failure")
            else:
                raise VerificationFailure("Source interruption did not run")
        require(before == self.counts(tables), "Source-owner rollback changed durable counts")
        require("load-rollback" not in self.monitor.previous, "Failed source entered monitor cache")
        self.metrics.count("sourceRollbackVerified")

    def produce(self, account):
        item = {"account": account, "events": [], "started": time.perf_counter()}
        original_events = []
        for revision in range(REVISIONS):
            self.check_deadline()
            stamp = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self.waves * REVISIONS + revision)).isoformat().replace("+00:00", "Z")
            with self.metrics.measure("sourceSnapshotCommit"):
                self.monitor.save_snapshot(self.fixtures.fixture_snapshot(account, stamp))
            boundary = self.sql("SELECT snapshot_id, generated_at FROM verified_reasoning_source_snapshots WHERE account_id = %s AND generated_at = %s", (account, stamp))
            require(boundary is not None, "Exact synthetic source snapshot missing")
            event = self.fixtures.DomainEvent(
                name=self.fixtures.ONTOLOGY_REASONING_REQUESTED,
                aggregate_id="market-observation:AAPL", occurred_at=stamp,
                event_id="load-source:%s:%d:%d" % (account, self.waves, revision),
                correlation_id="integrated-load-fixture",
                payload={"accountIds": [account], "affectedSymbols": ["AAPL"],
                         "factTypes": ["PRICE_OBSERVATION"], "sourceObservedAt": stamp,
                         "workClass": "MARKET", "fixtureOnly": True,
                         "verifiedSourceSnapshot": {"accountId": account, "snapshotId": boundary["snapshot_id"], "generatedAt": stamp}},
            )
            with self.metrics.measure("sourceEventIngress"):
                self.events.handle(event)
            with self.metrics.measure("sourceEventReplay"):
                self.events.handle(event)
                require(not self.jobs.ingress_event(event)["saved"], "Duplicate source created another job")
            item["events"].append(event.event_id)
            original_events.append(event)
            item.update({"stamp": stamp, "snapshotId": boundary["snapshot_id"], "event": event})
            self.metrics.count("sourceEvents")
            self.metrics.count("sourceDuplicateReplays", 2)
        with self.metrics.measure("coalescedSourceReplay"):
            for event in original_events[:-1]:
                require(not self.jobs.ingress_event(event)["saved"], "Coalesced predecessor was recreated by ingress repair")
                self.metrics.count("coalescedSourceReplays")
        self.mark_anchor(account, item["events"][-1], item["stamp"])
        return item

    def claim(self, store, worker):
        self.check_deadline()
        with self.metrics.measure("reasoningClaim"):
            rows = store.claim(self.deployment, worker, 1, 60)
        if rows:
            with self.claim_lock:
                require(rows[0]["jobId"] not in self.active_claims, "Concurrent duplicate job ownership")
                self.active_claims.add(rows[0]["jobId"])
                self.peak_claims = max(self.peak_claims, len(self.active_claims))
            self.metrics.count("reasoningClaims")
        return rows

    def release_tracking(self, job):
        with self.claim_lock:
            self.active_claims.remove(job["jobId"])

    def reject_late(self, store, job, worker):
        before = self.sql("SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],))
        for name, args in {"complete": ({"fixture": "stale"},), "retry": ("stale callback",), "fail": ({}, "stale failure"),
                           "exclude": ({}, "stale exclude"), "supersede": ("stale",), "defer": ("stale",),
                           "await_world_projection": ({}, "stale"), "await_target_scope_repair": ({}, "stale")}.items():
            try:
                getattr(store, name)(job["jobId"], *args, worker_id=worker, claimed_at=job["claimedAt"])
            except self.LeaseLost:
                self.metrics.count("lateTransitionsRejected")
            else:
                raise VerificationFailure("Late transition was admitted: " + name)
        try:
            store.bind_release([job["jobId"]], {"releaseFingerprint": "0" * 64}, "REALTIME",
                               worker_id=worker, claimed_tokens={job["jobId"]: job["claimedAt"]})
        except self.LeaseLost:
            self.metrics.count("lateReleaseBindingsRejected")
        else:
            raise VerificationFailure("Late release binding was admitted")
        require(before == self.sql("SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],)), "Late worker mutated durable job")

    def complete_reasoning(self, store, job, worker, items):
        self.check_deadline()
        lineage = self.sql("SELECT source_event_id, account_id, symbol FROM reasoning_engine_job_sources WHERE survivor_job_id = %s", (job["jobId"],), many=True)
        account_ids = {row["account_id"] for row in lineage}
        require(len(account_ids) == 1, "Coalesced lineage crossed accounts")
        account = next(iter(account_ids))
        require(account in items, "Claim is outside this synthetic wave")
        item = items[account]
        require({row["source_event_id"] for row in lineage} == set(item["events"]), "Transitive source lineage missing or duplicated")
        require({row["symbol"] for row in lineage} == {"AAPL"}, "Lineage symbol changed")
        require(job["sourceSnapshotAt"] == item["stamp"] and job["sourceSnapshotId"] == item["snapshotId"], "Source boundary drifted across accounts/revisions")
        generation = "generation:" + job["jobId"]
        abox = "abox:" + job["jobId"]
        tokens = {job["jobId"]: job["claimedAt"]}
        require(store.heartbeat([job["jobId"]], worker, 60, claimed_tokens=tokens), "Current heartbeat rejected")
        store.bind_release([job["jobId"]], {"releaseFingerprint": RELEASE}, "REALTIME", worker_id=worker, claimed_tokens=tokens)
        result = {"fixtureOnly": True, "accountIds": [account], "evaluatedSymbols": ["AAPL"],
                  "projectionResults": {account: {"sourceAboxSnapshotId": abox, "inferenceGenerationId": generation}}}
        with self.metrics.measure("reasoningCompleteWithReceipt"):
            receipt = store.complete(job["jobId"], result, worker_id=worker, claimed_at=job["claimedAt"])
        require(receipt["completedCount"] == 1 and receipt["accountIds"] == [account], "Completion did not atomically settle its own anchor")
        saved = self.sql("SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],))
        self.reject_late(store, job, worker)
        self.mark_anchor(account, item["events"][-1], item["stamp"])
        with self.metrics.measure("completedReceiptRepair"):
            require(store.repair_completed_receipts(job["jobId"])["completedCount"] == 1, "Persisted-result repair missed anchor")
            require(store.repair_completed_receipts(job["jobId"])["completedCount"] == 0, "Receipt repair was not idempotent")
        require(saved == self.sql("SELECT * FROM reasoning_engine_jobs WHERE job_id = %s", (job["jobId"],)), "Receipt repair rewrote completed job")
        durable = self.sql("SELECT * FROM market_observation_reasoning_receipts WHERE survivor_job_id = %s", (job["jobId"],))
        require(durable["source_event_id"] == item["events"][-1] and durable["account_id"] == account
                and durable["inference_generation_id"] == generation and durable["source_abox_snapshot_id"] == abox
                and durable["release_fingerprint"] == RELEASE, "Receipt lineage identity drift")
        context = {"accountId": account, "rawSymbol": "AAPL", "messageType": "investmentInsight", "fixtureOnly": True,
                   "loadSourceEventId": item["events"][-1], "loadJobId": job["jobId"], "loadWave": self.waves,
                   "ontologyRelationContext": {"subject": {"symbol": "AAPL"}, "inferenceGenerationId": generation, "sourceAboxSnapshotId": abox}}
        notification = self.Notification.create("Synthetic storage receipt only; no transport", account_id=account,
                                                message_type="investmentInsight", source_event_id=item["events"][-1],
                                                dedupe_key="load-delivery:" + job["jobId"], context=context)
        with self.metrics.measure("aiOutboxEnqueue"):
            self.notifications.enqueue(notification)
            require(not self.notifications.enqueue(notification), "Duplicate notification admitted")
            request = self.AIRequest.create(notification, context, model="fixture-no-model", reasoning_effort="high")
            self.ai.enqueue(notification, request)
            self.ai.enqueue(notification, request)
        require(self.notifications.get(notification.job_id).status == "awaiting_ai", "Outbox was released before synthetic AI result")
        self.metrics.count("completedReasoning")
        self.metrics.count("receiptRepairs")

    def drain_reasoning(self, items, inject):
        held = []
        held_lock = threading.Lock()

        def worker(index):
            store = self.fixtures.MySQLReasoningEngineJobStore(self.settings)
            worker_id = "load-worker-%d" % index
            while True:
                rows = self.claim(store, worker_id)
                if not rows:
                    return
                job = rows[0]
                try:
                    source = job["sourceEventId"]
                    account = next((key for key, item in items.items() if source == item["events"][-1]), "")
                    require(bool(account), "Unknown source claimed")
                    if inject and account == self.accounts[0]:
                        with held_lock:
                            held.append((job, worker_id))
                        continue
                    if inject and account == self.accounts[1] and job["attemptCount"] == 0:
                        with self.metrics.measure("reasoningRetry"):
                            response = store.retry(job["jobId"], "injected transient storage work failure", 3,
                                                   worker_id=worker_id, claimed_at=job["claimedAt"])
                        require(not response["terminal"] and response["attemptCount"] == 1, "Retry budget changed")
                        persisted = store.get(job["jobId"])
                        require(persisted["status"] == "retry" and persisted["availableAt"] > datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "Retry lost its future backoff")
                        # Keep this fault deterministic even on a slow local host.
                        self.sql("UPDATE reasoning_engine_jobs SET available_at = %s WHERE job_id = %s AND job_status = 'retry'",
                                 ("2099-01-01T00:00:00Z", job["jobId"]))
                        self.metrics.count("retryScheduled")
                        continue
                    self.complete_reasoning(store, job, worker_id, items)
                finally:
                    self.release_tracking(job)

        self.parallel(worker, range(self.config.concurrency))
        return held

    def recover(self, held, items):
        require(len(held) == 1, "Expected exactly one abandoned claim per wave")
        job, worker = held[0]
        store = self.fixtures.MySQLReasoningEngineJobStore(self.settings)
        started = time.perf_counter()
        self.sql("UPDATE reasoning_engine_jobs SET lease_expires_at = %s WHERE job_id = %s AND job_status = 'processing'", (PAST, job["jobId"]))
        require(not store.heartbeat([job["jobId"]], worker, 60, claimed_tokens={job["jobId"]: job["claimedAt"]}), "Expired heartbeat revived a claim")
        self.reject_late(store, job, worker)
        replacement = self.claim(store, worker)
        require(len(replacement) == 1 and replacement[0]["jobId"] == job["jobId"], "Expired job was not reclaimed")
        current = replacement[0]
        try:
            require(current["claimedAt"] != job["claimedAt"], "Same-worker reclaim reused an attempt token")
            self.reject_late(store, job, worker)
            self.reject_late(store, current, "foreign-worker")
            require(not store.heartbeat([job["jobId"]], worker, 60, claimed_tokens={job["jobId"]: job["claimedAt"]}), "Old attempt heartbeat changed replacement")
            self.complete_reasoning(store, current, worker, items)
        finally:
            self.release_tracking(current)
        self.metrics.add("expiredClaimRecovery", (time.perf_counter() - started) * 1000)
        self.metrics.count("expiredClaimsReclaimed")
        retry = self.sql("SELECT job_id FROM reasoning_engine_jobs WHERE job_status = 'retry'", many=True)
        require(len(retry) == 1, "Expected one delayed retry, not missing or duplicate work")
        self.sql("UPDATE reasoning_engine_jobs SET available_at = %s WHERE job_id = %s AND job_status = 'retry'", (PAST, retry[0]["job_id"]))
        with self.metrics.measure("retryRecovery"):
            require(not self.drain_reasoning(items, inject=False), "Recovery abandoned another claim")
        require(store.get(retry[0]["job_id"])["attemptCount"] == 1, "Retry attempts lost after completion")
        require(self.backlog("reasoning-recovered") == 0, "Reasoning backlog did not drain")

    def publish_ai(self, request, worker, items, rollback=False):
        self.check_deadline()
        account = request.account_id
        item = items[account]
        job_id = request.context["loadJobId"]
        generation = "generation:" + job_id
        require(request.inference_generation_id == generation and request.context["loadSourceEventId"] == item["events"][-1], "AI handoff changed source/account/generation")
        episode = self.fixtures.fixture_episode(account, item["stamp"], "episode:" + request.request_id)
        episode.inference_generation_id = generation
        episode.source_abox_snapshot_id = "abox:" + job_id
        result = self.AIResult.create(request, {"action": "HOLD", "summary": "Synthetic persistence fixture, not an investment opinion."},
                                      source="storage-contract-fixture", validation_state="ready", latency_ms=0, prompt_bytes=0)
        context = {**request.context, "notificationAiExecutionAudit": {"status": "completed", "adoptionState": "executed-not-adopted", "fixtureOnly": True}}
        callback = lambda connection: self.decisions.save(episode, connection=connection)
        if rollback:
            tables = ["ai_inference_results", "ai_inference_execution_audits", "domain_events", "investment_decision_episodes",
                      "investment_flow_current", "investment_flow_heads", "investment_decision_follow_ups", "investment_decision_outcome_targets"]
            before = self.counts(tables)

            def interrupt(_connection, _outcome):
                raise VerificationFailure("injected-publication-interruption")

            try:
                with self.metrics.measure("publicationRollback"):
                    self.ai.complete(request, worker, result, context, before_complete=callback, after_complete=interrupt)
            except VerificationFailure as error:
                require(str(error) == "injected-publication-interruption", "Unexpected publication failure")
            else:
                raise VerificationFailure("Publication interruption did not run")
            require(before == self.counts(tables), "Publication rollback leaked an owner write or event")
            require(self.ai.get(request.request_id).status == "processing" and self.notifications.get(request.notification_job_id).status == "awaiting_ai", "Failed publication advanced request/delivery state")
            self.metrics.count("publicationRollbackVerified")
        before = self.sql("SELECT * FROM ai_inference_requests WHERE request_id = %s", (request.request_id,))
        require(not self.ai.complete(request, "foreign-ai-worker", result, context), "Foreign AI worker published")
        require(before == self.sql("SELECT * FROM ai_inference_requests WHERE request_id = %s", (request.request_id,)), "Foreign AI worker rewrote current claim")
        with self.metrics.measure("aiPublicationTransaction"):
            require(self.ai.complete(request, worker, result, context, before_complete=callback), "Current AI storage result rejected")
        require(not self.ai.complete(request, worker, result, context, before_complete=callback), "Terminal AI publication replay admitted")
        notification = self.notifications.get(request.notification_job_id)
        require(notification.status == "pending" and notification.account_id == account and notification.source_event_id == item["events"][-1], "Publication released wrong delivery")
        with self.metrics.measure("syntheticDeliveryReceipt"):
            attempt = self.notifications.start_delivery_attempt(notification, "fixture-no-transport", "synthetic-account", {"fixtureOnly": True})
            self.notifications.complete_delivery_attempt(notification, attempt, True, provider="fixture-no-external-delivery", metadata={"fixtureOnly": True})
            self.notifications.mark_done(notification)
        outcome = self.Outcome(outcome_id="outcome:" + request.request_id, episode_id=episode.episode_id,
                               observed_at=item["stamp"], price=102, selected_hypothesis_status="pending",
                               payload={"source": "storage-contract-fixture", "calibrationEligible": False})
        with self.metrics.measure("outcomeAndReplay"):
            self.decisions.save_outcome(episode, outcome)
            self.decisions.save_outcome(episode, outcome)
        observed = self.decisions.outcomes_for_episode(episode.episode_id)
        require([value.outcome_id for value in observed] == [outcome.outcome_id], "Outcome replay duplicated its anchor")
        self.metrics.add("sourceToOutcome", (time.perf_counter() - item["started"]) * 1000)
        self.metrics.count("completedCases")
        self.metrics.count("outcomeDuplicateReplays")

    def drain_ai(self, items):
        # The fault window is serial so unrelated commits cannot hide rollback leaks.
        first = claim_ai_with_retry(self.ai, "load-ai-probe", self.metrics)
        require(len(first) == 1, "AI queue lost all handoffs")
        self.publish_ai(first[0], "load-ai-probe", items, rollback=True)

        def worker(index):
            worker_id = "load-ai-%d" % index
            store = self.fixtures.MySQLAIInferenceQueueStore(self.settings)
            while True:
                self.check_deadline()
                claimed = claim_ai_with_retry(store, worker_id, self.metrics)
                if not claimed:
                    return
                self.publish_ai(claimed[0], worker_id, items)

        self.parallel(worker, range(self.config.concurrency))

    def terminal_probe(self):
        account = self.accounts[0]
        boundary = self.sql("SELECT snapshot_id, generated_at FROM verified_reasoning_source_snapshots WHERE account_id = %s ORDER BY generated_at DESC LIMIT 1", (account,))
        event = self.fixtures.DomainEvent(name=self.fixtures.ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="market-observation:FAILFIX", occurred_at="2026-01-01T00:00:00Z",
            event_id="load-terminal-probe", correlation_id="integrated-load-fixture",
            payload={"accountIds": [account], "affectedSymbols": ["FAILFIX"], "factTypes": ["PRICE_OBSERVATION"],
                     "workClass": "MARKET", "verifiedSourceSnapshot": {"accountId": account, "snapshotId": boundary["snapshot_id"], "generatedAt": boundary["generated_at"]}})
        self.events.handle(event)
        job = self.claim(self.jobs, "load-terminal")[0]
        try:
            require(job["sourceEventId"] == event.event_id, "Terminal probe claimed workload job")
            response = self.jobs.retry(job["jobId"], "injected exhausted work", 1, worker_id="load-terminal", claimed_at=job["claimedAt"])
            require(response["terminal"] and response["attemptCount"] == 1, "Retry exhaustion not terminal")
            self.reject_late(self.jobs, job, "load-terminal")
            require(self.jobs.get(job["jobId"])["status"] == "failed", "Terminal probe state changed")
            require(self.jobs.repair_completed_receipts(job["jobId"])["status"] == "not-completed", "Failed job repaired as completed")
            self.metrics.count("terminalExhaustionVerified")
        finally:
            self.release_tracking(job)

    def verify_counts(self):
        cases = self.waves * self.config.accounts
        expected = {
            "monitor_snapshots": self.config.accounts, "monitor_snapshot_history": cases * REVISIONS,
            "verified_reasoning_source_snapshots": cases * REVISIONS,
            "reasoning_engine_jobs": cases * REVISIONS + 1,
            "reasoning_engine_job_sources": cases * 6 + 1,
            "market_observation_reasoning_receipts": cases,
            "market_observation_reasoning_anchors": self.config.accounts + 1,
            "notification_jobs": cases, "notification_delivery_attempts": cases,
            "ai_inference_requests": cases, "ai_inference_results": cases,
            "ai_inference_execution_audits": cases, "investment_decision_episodes": cases,
            "investment_decision_outcomes": cases, "investment_decision_follow_ups": cases,
            "service_accounts": 0,
        }
        actual = self.counts(expected)
        self.consistency = {"expected": expected, "actual": actual, "matched": actual == expected}
        require(actual == expected, "Durable table counts disagree with completed synthetic work")
        source_count = self.sql("SELECT COUNT(*) AS n FROM domain_events WHERE name = %s", (self.fixtures.ONTOLOGY_REASONING_REQUESTED,))["n"]
        require(source_count == cases * REVISIONS + 1, "Source-event count/dedupe mismatch")
        self.consistency["sourceEventCount"] = source_count
        for table, column, state in (("notification_jobs", "status", "done"), ("ai_inference_requests", "status", "completed")):
            require(self.sql("SELECT COUNT(*) AS n FROM " + table + " WHERE " + column + " <> %s", (state,))["n"] == 0, "Unsettled queue: " + table)
        states = {row["job_status"]: row["n"] for row in self.sql("SELECT job_status, COUNT(*) AS n FROM reasoning_engine_jobs GROUP BY job_status", many=True)}
        require(states == {"completed": cases, "superseded": cases * 2, "failed": 1}, "Terminal reasoning state counts mismatch")
        violations = self.sql("""SELECT COUNT(*) AS n FROM investment_decision_outcomes o
            JOIN investment_decision_episodes e ON e.episode_id = o.episode_id
            JOIN ai_inference_requests a ON e.episode_id = CONCAT('episode:', a.request_id)
            JOIN notification_jobs n ON n.job_id = a.notification_job_id
            JOIN market_observation_reasoning_receipts r ON r.source_event_id = n.source_event_id AND r.account_id = n.account_id
            WHERE o.account_id <> e.account_id OR e.account_id <> a.account_id OR a.account_id <> n.account_id
               OR r.inference_generation_id <> a.inference_generation_id
               OR JSON_UNQUOTE(JSON_EXTRACT(e.payload_json, '$.inferenceGenerationId')) <> a.inference_generation_id
               OR JSON_UNQUOTE(JSON_EXTRACT(e.payload_json, '$.sourceAboxSnapshotId')) <> r.source_abox_snapshot_id""")["n"]
        joined = self.sql("""SELECT COUNT(*) AS n FROM investment_decision_outcomes o
            JOIN investment_decision_episodes e ON e.episode_id = o.episode_id
            JOIN ai_inference_requests a ON e.episode_id = CONCAT('episode:', a.request_id)
            JOIN notification_jobs n ON n.job_id = a.notification_job_id
            JOIN market_observation_reasoning_receipts r ON r.source_event_id = n.source_event_id AND r.account_id = n.account_id""")["n"]
        require(violations == 0 and joined == cases, "Publication/outcome account lineage is incomplete or crossed")
        require(self.metrics.counters.get("completedCases") == cases, "Worker completion count does not match durable outcomes")
        by_account = {row["account_id"]: row["n"] for row in self.sql("SELECT account_id, COUNT(*) AS n FROM investment_decision_outcomes GROUP BY account_id", many=True)}
        require(by_account == {account: self.waves for account in self.accounts}, "Outcome counts differ by account")
        event_counts = {row["name"]: row["n"] for row in self.sql("SELECT name, COUNT(*) AS n FROM domain_events GROUP BY name", many=True)}
        self.consistency.update({"reasoningStates": states, "joinedOutcomeAnchors": joined, "accountViolations": violations,
                                 "outcomesByAccount": by_account, "eventsByName": event_counts})

    def run(self):
        self.snapshot_rollback()
        self.mark_anchor("load-sentinel", "load-sentinel-event", PAST)
        sentinel = self.sql("SELECT * FROM market_observation_reasoning_anchors WHERE account_id = 'load-sentinel'")
        self.load_started = time.monotonic()
        end = self.load_started + self.config.load_budget
        self.deadline = end + self.config.recovery_seconds
        while (self.waves < self.config.rounds if self.config.mode == "regression" else time.monotonic() < end):
            require(time.monotonic() < end, "Admission budget elapsed before all regression waves")
            require((self.waves + 1) * self.config.accounts <= self.config.max_cases, "Case cap reached before requested duration; soak is incomplete")
            require(self.waves < 1000, "Wave cap reached before requested duration; soak is incomplete")
            self.check_deadline()
            started = time.perf_counter()
            items = {item["account"]: item for item in self.parallel(self.produce, self.accounts)}
            require(self.backlog("ingress-burst") == self.config.accounts, "Coalescing did not leave one survivor per account")
            held = self.drain_reasoning(items, inject=True)
            require(self.backlog("interrupted") == 2, "Injected interruption/retry backlog differs")
            with self.metrics.measure("waveRecoveryDrain"):
                self.recover(held, items)
                self.drain_ai(items)
            self.backlog("fully-drained")
            require(self.backlogs[-1]["aiPending"] == 0 and self.backlogs[-1]["deliveryPending"] == 0, "Publication/delivery backlog did not drain")
            require(sentinel == self.sql("SELECT * FROM market_observation_reasoning_anchors WHERE account_id = 'load-sentinel'"), "Foreign account anchor was modified")
            self.metrics.count("sentinelIsolationVerified")
            self.waves += 1
            self.metrics.add("wave", (time.perf_counter() - started) * 1000)
            progress_path = os.environ.get("INTEGRATED_LOAD_PROGRESS_PATH")
            if progress_path:
                with Path(progress_path).open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"status": "running", "workerPid": os.getpid(),
                                             "wavesCompleted": self.waves, "casesCompleted": self.metrics.counters["completedCases"],
                                             "elapsedWorkSeconds": round(time.monotonic() - self.load_started, 3),
                                             "aiClaimDeadlockRetries": self.metrics.counters.get("aiClaimDeadlockRetries", 0),
                                             "reasoningPending": 0, "aiPending": 0, "deliveryPending": 0}) + "\n")
            if self.config.mode == "soak":
                time.sleep(min(self.config.wave_interval_seconds, max(0, end - time.monotonic())))
        self.terminal_probe()
        require(self.backlog("final") == 0, "Unexpected final reasoning backlog")
        self.verify_counts()
        self.memory_after = memory_sample()

    def report(self):
        return {
            "schemaVersion": "integrated-load-verification-v1", "config": asdict(self.config),
            "workerPid": os.getpid(),
            "scope": "real-mysql-synthetic-storage-transactions-not-live-inference",
            "mysqlVersion": self.version, "pythonVersion": sys.version.split()[0],
            "managedProcessesTouched": False, "externalRequests": 0,
            "typeDBExecuted": False, "modelExecuted": False, "externalDeliveryExecuted": False,
            "wavesCompleted": self.waves, "elapsedWorkSeconds": round(time.monotonic() - self.load_started, 3),
            "peakConcurrentReasoningClaims": self.peak_claims,
            "aiClaimRetryPolicy": "production claim transaction retry; 1213 only, configured bounded budget; no harness retry",
            "counters": self.metrics.counters, "countConsistency": self.consistency,
            "latency": {key: percentile_summary(values) for key, values in sorted(self.metrics.samples.items())},
            "backlogSamples": self.backlogs,
            "memory": {"baselineAfterBootstrap": self.memory_before, "afterWorkload": self.memory_after,
                       "deltaBytes": {key: self.memory_after[key] - self.memory_before[key] for key in self.memory_before},
                       "scope": "child Python allocations and process RSS high-water; not MySQL server memory"},
        }


def worker_main(config):
    instance = None
    started = time.monotonic()
    tracemalloc.start()
    try:
        isolated_environment(os.environ.get("MYSQL_TEST_DATABASE", ""), os.environ.get("DIGITAL_TWIN_DATA_DIR", ""))
        require(os.environ.get("MYSQL_DATABASE") == os.environ.get("MYSQL_TEST_DATABASE")
                and not os.environ.get("MYSQL_URL") and not os.environ.get("DATABASE_URL"),
                "Worker database override is not isolated")
        with local_mysql_only(os.environ) as blocked:
            instance = Rehearsal(config)
            setup_seconds = time.monotonic() - started
            require(setup_seconds <= SETUP_SECONDS, "Schema/import bootstrap exceeded setup budget")
            instance.run()
            require(not any(blocked.values()), "An unintended external/schema/process access was attempted")
        report = instance.report()
        report["bootstrapSeconds"] = round(setup_seconds, 3)
        report["blockedAccessAttempts"] = blocked
        report["status"] = "passed"
    except Exception as error:
        if instance:
            instance.memory_after = memory_sample()
        report = instance.report() if instance else {}
        frame = traceback.extract_tb(error.__traceback__)[-1]
        report.update({"status": "failed", "errorType": type(error).__name__,
                       "failureLocation": {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name},
                       "failureStack": [{"file": Path(value.filename).name, "line": value.lineno, "function": value.name}
                                        for value in traceback.extract_tb(error.__traceback__) if "digital_twin" in value.filename][-8:],
                       "failure": str(error) if isinstance(error, VerificationFailure) else "Worker failed; no database credentials or payloads included"})
        if error.args and isinstance(error.args[0], int):
            report["databaseErrorCode"] = error.args[0]
        if getattr(error, "orbit_mysql_statement", ""):
            report["databaseStatement"] = error.orbit_mysql_statement
    finally:
        tracemalloc.stop()
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] == "passed" else 1


def run_rehearsal(config, environment=None, progress_path=None):
    """Only this supervisor creates/drops a schema or terminates its own child."""
    config.validate()
    started = time.monotonic()
    database = DATABASE_PREFIX + uuid.uuid4().hex[:24]
    report = {"status": "failed", "config": asdict(config), "isolatedDatabase": database,
              "supervisorPid": os.getpid(), "cleanupVerified": False}
    created = False
    with tempfile.TemporaryDirectory(prefix="orbit-integrated-load-") as directory:
        child_environment = isolated_environment(database, directory, environment)
        if progress_path:
            progress_path = Path(progress_path).resolve()
            progress_path.write_text(json.dumps({"status": "starting", "supervisorPid": os.getpid(),
                                               "isolatedDatabase": database, "config": asdict(config)}) + "\n", encoding="utf-8")
            child_environment["INTEGRATED_LOAD_PROGRESS_PATH"] = str(progress_path)
            report["progressPath"] = str(progress_path)
        try:
            connection = admin_connection(child_environment)
            try:
                with connection.cursor() as cursor:
                    # No IF NOT EXISTS: an existing schema is never taken over/reset.
                    cursor.execute("CREATE DATABASE `" + database + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                    created = True
            finally:
                connection.close()
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--_worker", json.dumps(asdict(config))],
                cwd=directory, env=child_environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=config.hard_timeout,
            )
            try:
                report.update(json.loads(completed.stdout))
            except (ValueError, TypeError):
                report.update({"status": "failed", "failure": "Worker did not return a valid report"})
            if completed.returncode:
                report["status"] = "failed"
            report["workerExitCode"] = completed.returncode
        except subprocess.TimeoutExpired:
            report.update({"status": "failed", "failure": "Hard timeout; only the harness-owned child was killed", "timedOut": True})
        except Exception as error:
            report.update({"status": "failed", "errorType": type(error).__name__, "failure": "Local isolated MySQL rehearsal failed; credentials omitted"})
        finally:
            if created:
                try:
                    connection = admin_connection(child_environment)
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute("DROP DATABASE IF EXISTS `" + database + "`")
                            cursor.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s", (database,))
                            report["cleanupVerified"] = cursor.fetchone()[0] == 0
                    finally:
                        connection.close()
                except Exception:
                    report["cleanupVerified"] = False
                if not report["cleanupVerified"]:
                    report.update({"status": "failed", "cleanupFailure": "Remove only the reported harness-owned schema after checking this run"})
    report["supervisorElapsedSeconds"] = round(time.monotonic() - started, 3)
    report["childHardTimeoutSeconds"] = config.hard_timeout
    if progress_path:
        with progress_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"status": report["status"], "cleanupVerified": report["cleanupVerified"],
                                     "supervisorElapsedSeconds": report["supervisorElapsedSeconds"]}) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("regression", "soak"), default="regression")
    parser.add_argument("--accounts", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--duration-seconds", type=float, default=30)
    parser.add_argument("--recovery-seconds", type=float, default=30)
    parser.add_argument("--max-cases", type=int, default=2000)
    parser.add_argument("--wave-interval-seconds", type=float, default=0.2)
    parser.add_argument("--output", type=Path, help="Optional synthetic JSON report; never contains DB credentials")
    parser.add_argument("--_worker", help=argparse.SUPPRESS)
    arguments = vars(parser.parse_args())
    internal = arguments.pop("_worker")
    output = arguments.pop("output")
    if internal:
        return worker_main(LoadConfig(**json.loads(internal)).validate())
    try:
        report = run_rehearsal(LoadConfig(**arguments), progress_path=str(output) + ".progress.jsonl" if output else None)
    except (VerificationFailure, ValueError) as error:
        parser.error(str(error))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output:
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
