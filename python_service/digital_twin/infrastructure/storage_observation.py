"""Passive operational samples. Never construct stores or invoke application work.

Filesystem inventories run in a separate, low-priority process. Their timestamps
remain independent of the cheap live sample; cached blocks are never current
free-space readings or evidence of uninterrupted service availability.
"""
from datetime import datetime, timezone
from pathlib import Path
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_time(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def read_json(path):
    try:
        result = json.loads(Path(path).read_text())
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, default=str))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


CAPACITY_FIELDS = (
    "state", "checkedAt", "mysqlLimitMb", "mysqlSizeMb", "mysqlUsagePercent",
    "mysqlCapacityStage", "nonEssentialWritesAllowed", "coreWritesOnly",
    "freeMb", "minimumFreeMb", "criticalFreeMb", "cleanupMode",
    "typedbLimitMb", "typedbSizeMb", "typedbTotalSizeMb", "mysqlReclaimableMb",
)

QUERIES = (
    ("sources", "SELECT generated_at,created_at,mode FROM verified_reasoning_source_snapshots "
     "ORDER BY created_at DESC,snapshot_id DESC LIMIT 8"),
    ("retention", "SELECT status,deleted_count,compacted_count,estimated_bytes,started_at,completed_at "
     "FROM mysql_retention_runs ORDER BY created_at DESC,run_id DESC LIMIT 4"),
    ("newErrorSample", "SELECT occurred_at FROM domain_events "
     "WHERE name='system.error_reported' ORDER BY occurred_at DESC LIMIT 32"),
    ("notificationQueueSample", "SELECT status,created_at FROM notification_jobs "
     "WHERE status IN ('pending','awaiting_ai','processing','failed') ORDER BY created_at LIMIT 64"),
    ("marketSample", "SELECT market,observed_at,source_as_of,data_quality,sample_count "
     "FROM market_time_series_observations WHERE granularity='3m' ORDER BY bucket_at DESC LIMIT 64"),
    ("capacityRow", "SELECT @@datadir AS mysqlDirectory,JSON_EXTRACT(payload_json,'$.operationalStorageCapacity') AS health "
     "FROM app_store WHERE store_id='operational_storage_capacity_state'"),
    ("archiveReceiptSample", "SELECT table_name,original_bytes,stored_bytes,archived_at "
     "FROM storage_archive_records WHERE evicted=1 ORDER BY table_name,key_hash,row_hash LIMIT 16"),
    ("tableSpaceEstimates", "SELECT table_name AS tableName,data_length AS dataBytes,"
     "index_length AS indexBytes,data_free AS allocatorFreeBytes,table_rows AS estimatedRows "
     "FROM information_schema.tables WHERE table_schema=DATABASE() "
     "ORDER BY data_length+index_length DESC LIMIT 16"),
)


def database_options(environ):
    host = environ.get("MYSQL_HOST", "127.0.0.1")
    host = "127.0.0.1" if host == "localhost" else host
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError("Observation requires loopback MySQL")
    if not environ.get("MYSQL_DATABASE") or not environ.get("MYSQL_USER"):
        raise ValueError("Explicit observation database credentials required")
    result = dict(host=host, port=int(environ.get("MYSQL_PORT", 3306)),
                  user=environ["MYSQL_USER"], password=environ.get("MYSQL_PASSWORD", ""),
                  database=environ["MYSQL_DATABASE"], charset="utf8mb4", autocommit=False,
                  connect_timeout=2, read_timeout=2, write_timeout=2, local_infile=False)
    if environ.get("MYSQL_UNIX_SOCKET"):
        result["unix_socket"] = environ["MYSQL_UNIX_SOCKET"]
    return result


def problem(section, error):
    return {"section": section, "type": type(error).__name__,
            "mysqlCode": error.args[0] if error.args and isinstance(error.args[0], int) else None}


def collect_database(result, connect, environ):
    connection = None
    started = time.monotonic()
    try:
        connection = connect(**database_options(environ))
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=1500")
            cursor.execute("SET SESSION lock_wait_timeout=1")
            cursor.execute("START TRANSACTION READ ONLY")
            for section, sql in QUERIES:
                try:
                    if time.monotonic() - started > 15:
                        raise TimeoutError("Observation database budget exhausted")
                    result["queryCount"] += 1
                    cursor.execute(sql)
                    result[section] = list(cursor.fetchall())
                except Exception as error:
                    result["collectorErrors"].append(problem(section, error))
    except Exception as error:
        result["collectorErrors"].append(problem("mysql", error))
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()
    rows = result.pop("capacityRow", [])
    if rows and rows[0].get("mysqlDirectory"):
        result["mysqlDataDirectory"] = rows[0]["mysqlDirectory"]
    if rows and rows[0].get("health"):
        try:
            health = json.loads(rows[0]["health"])
            result["capacity"] = {key: health.get(key) for key in CAPACITY_FIELDS}
        except (TypeError, ValueError, AttributeError) as error:
            result["collectorErrors"].append(problem("capacity", error))


def http_read(path):
    try:
        response = urllib.request.urlopen("http://127.0.0.1:3000" + path, timeout=3)
    except urllib.error.HTTPError as error:
        return error.code, {}
    with response:
        return response.status, json.loads(response.read(2 * 1024 * 1024))


def disk_inventory(root, previous=None, runner=subprocess.run, mysql_directory=None):
    """Called only by the dedicated filesystem process, never the live sampler."""
    result = dict(previous or {})
    for field, relative in (("mysqlActualBytes", "mysql-runtime"),
                            ("typedb-dataBytes", "typedb-data"),
                            ("history-archiveBytes", "history-archive")):
        old = dict(result.get(field) or {})
        old["lastAttemptAt"] = utc_now()
        if field == "mysqlActualBytes" and not mysql_directory:
            old["lastError"] = "database-directory-unavailable"
            result[field] = old
            continue
        try:
            path = Path(mysql_directory) if field == "mysqlActualBytes" else Path(root) / "data" / relative
            answer = runner(["/usr/bin/du", "-sk", str(path)],
                            capture_output=True, text=True, timeout=180, check=True)
            blocks = int(answer.stdout.split()[0])
            if blocks < 0:
                raise ValueError("Negative filesystem allocation")
            old.update(value=blocks * 1024, measuredAt=utc_now(), lastError="",
                       method="allocated-blocks-hard-links-counted-once")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            old["lastError"] = type(error).__name__
        result[field] = old
    return result


def attach_disk_metrics(result, metrics, now=None):
    now = now or datetime.now(timezone.utc)
    result["diskMeasurements"] = {}
    for field in ("mysqlActualBytes", "typedb-dataBytes", "history-archiveBytes"):
        item = dict(metrics.get(field) or {})
        try:
            age = (now - parse_time(item["measuredAt"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            age = None
        item.update(ageSeconds=round(age, 3) if age is not None else None,
                    stale=age is None or age < 0 or age > 1800,
                    status="unavailable" if item.get("value") is None else "measured")
        result["diskMeasurements"][field] = item
        # Legacy reports can still read the number, but the separate timestamp
        # and sample completeness prevent cached bytes being counted as fresh.
        if item.get("value") is not None:
            result[field] = item["value"]


def collect_sample(root, directory, connect=None, reader=http_read, metrics_directory=None):
    root, directory = Path(root), Path(directory)
    metrics_directory = Path(metrics_directory) if metrics_directory is not None else directory
    started = time.monotonic()
    result = {"observedAt": utc_now(), "collectorErrors": [], "queryCount": 0,
              "scope": "bounded-passive-sample-v2", "continuousUptimeProven": False,
              "diskFreeBytes": shutil.disk_usage(root).free, "serviceIssues": []}
    heartbeat = read_json(root / "data/python-supervisor-heartbeat.json")
    try:
        age = (parse_time(result["observedAt"]) - parse_time(heartbeat["observedAt"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        age = None
    result["supervisor"] = {"state": heartbeat.get("state", "unavailable"),
                            "ageSeconds": age, "scope": "heartbeat-only"}
    result["ports"] = {}
    for name, port in (("web", 3000), ("mysql", 3306), ("typedb", 1729), ("questdb", 9000)):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                result["ports"][name] = True
        except OSError:
            result["ports"][name] = False
    result["http"] = []
    for path in ("/api/version", "/api/operations/performance"):
        stamp = time.monotonic()
        try:
            status, body = reader(path)
            item = {"path": path, "status": status,
                    "durationMs": round((time.monotonic() - stamp) * 1000, 2)}
            if path.endswith("version"):
                item["revision"] = body.get("revision")
            else:
                item["rollingRoutes"] = [{key: row.get(key) for key in
                    ("sampleCount", "errorCount", "p95Ms", "lastStatus", "lastObservedAt")}
                    for row in (body.get("routes") or [])[:24]]
            result["http"].append(item)
            if status != 200:
                result["serviceIssues"].append({"component": "web", "path": path, "status": status})
        except (urllib.error.URLError, OSError) as error:
            result["http"].append({"path": path, "probeErrorType": type(error).__name__})
            result["serviceIssues"].append({"component": "web", "path": path,
                                           "probeErrorType": type(error).__name__})
        except Exception as error:
            result["collectorErrors"].append(problem(path, error))
    if connect is None:
        import pymysql
        connect = lambda **options: pymysql.connect(**options, cursorclass=pymysql.cursors.DictCursor)
    collect_database(result, connect, os.environ)
    try:
        age = (datetime.now(timezone.utc)-parse_time(result["capacity"]["checkedAt"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = None
    result["capacityFreshness"] = {"ageSeconds": round(age, 3) if age is not None else None,
                                    "stale": age is None or age < 0 or age > 600}
    if result.get("mysqlDataDirectory"):
        write_json(metrics_directory / "disk-paths.json", {"mysqlDirectory": result.pop("mysqlDataDirectory"),
                   "observedAt": result["observedAt"]})
    attach_disk_metrics(result, read_json(metrics_directory / "component-metrics.json"))
    result["coreCollectorStatus"] = "partial" if result["collectorErrors"] else "ok"
    result["collectorStatus"] = "partial" if result["collectorErrors"] or any(
        row["stale"] or row.get("lastError") for row in result["diskMeasurements"].values()) or result["capacityFreshness"]["stale"] else "ok"
    result["durationSeconds"] = round(time.monotonic() - started, 3)
    return result


def launch_agent(label, interpreter, script, root, directory):
    """Successful STOP/deadline exits must not be restarted by launchd."""
    return {"Label": label, "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 30, "WorkingDirectory": str(root),
            "ProgramArguments": [str(interpreter), str(script), "--root", str(root),
                                 "--directory", str(directory)],
            "StandardOutPath": str(Path(directory) / "observer-launchd.log"),
            "StandardErrorPath": str(Path(directory) / "observer-launchd-error.log")}


def summarize_observations(directory):
    """Audit actual records, never infer uptime across missing observations."""
    directory = Path(directory)
    window = read_json(directory / "observation-window.json")
    rows, invalid = [], 0
    started = parse_time(window["startedAt"])
    try:
        with (directory / "storage-samples.jsonl").open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if parse_time(row["observedAt"]) >= started:
                        rows.append(row)
                except (ValueError, KeyError, TypeError):
                    invalid += 1
    except FileNotFoundError:
        pass
    rows.sort(key=lambda row: parse_time(row["observedAt"]))
    def valid(row):
        return (not row.get("collectorErrors") and row.get("collectorStatus", "ok") == "ok"
                and int(row.get("queryCount") or 0) >= 8 and len(row.get("http") or []) == 2
                and bool(row.get("capacity")))
    seconds, gaps, clocks = 0.0, [], {}
    for left, right in zip(rows, rows[1:]):
        delta = (parse_time(right["observedAt"]) - parse_time(left["observedAt"])).total_seconds()
        if delta > 600:
            gaps.append({"from": left["observedAt"], "to": right["observedAt"], "seconds": delta})
        elif delta > 0 and valid(left) and valid(right):
            seconds += delta
    for row in rows:
        for quote in row.get("marketSample", []):
            if quote.get("data_quality") != "actual":
                continue
            market = str(quote.get("market", "")).upper()
            market = "KR" if market in {"KR", "KOSPI", "KOSDAQ", "KONEX"} else market
            if market not in {"KR", "US"}:
                continue
            try:
                source = parse_time(quote["source_as_of"])
                age = (parse_time(row["observedAt"]) - source).total_seconds()
                local = source.astimezone(ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York"))
            except (KeyError, TypeError, ValueError):
                continue
            minute = local.hour * 60 + local.minute
            lower, upper = (540, 930) if market == "KR" else (570, 960)
            if 0 <= age <= 600 and local.weekday() < 5 and lower <= minute <= upper:
                key = market + " " + str(local.date())
                clocks.setdefault(key, set()).add(source.isoformat())
    coverage = {key: {"uniqueClocks": len(values), "first": min(values), "last": max(values)}
                for key, values in clocks.items()}
    hours = seconds / 3600
    sufficient = {}
    for market, required_span in (("KR", 5 * 3600), ("US", 6 * 3600)):
        sufficient[market] = any(key.startswith(market + " ") and
            (parse_time(item["last"]) - parse_time(item["first"])).total_seconds() >= required_span
            and item["uniqueClocks"] >= 30 for key, item in coverage.items())
    last = parse_time(rows[-1]["observedAt"]) if rows else started
    return {"startedAt": window["startedAt"], "lastSampleAt": rows[-1]["observedAt"] if rows else None,
            "sampleCount": len(rows), "invalidLines": invalid,
            "collectorErrorFreeSamples": sum(not row.get("collectorErrors") for row in rows),
            "completeSamples": sum(valid(row) for row in rows), "gapsOver10Minutes": gaps,
            "sampledSpanHours": round((last-started).total_seconds()/3600, 3),
            "validAdjacentIntervalHours": round(hours, 3), "continuousUptimeProven": False,
            "actualMarketSourceClocks": coverage, "sufficientRegularSessionSamples": sufficient,
            "eligibleForFinalReview": hours >= float(window.get("minimumHours", 72)) and all(sufficient.values()),
            "limitation": "Adjacent valid sample coverage is an observation estimate, not proof of uninterrupted uptime. "
                          "Market evidence is bounded source-clock samples, not whole-market statistics."}
