import os
import signal
import shutil
import socket
import subprocess
import sys
import time
import json
import calendar
import plistlib
from pathlib import Path
from typing import Dict, List

from .infrastructure.mysql_monitoring import mysql_settings
from .infrastructure.mysql_monitoring import MySQLMonitorAccountJobStore
from .infrastructure.mysql_operational import MySQLOntologyRuleboxPrewarmStateStore
from .infrastructure.mysql_operational_connection import MySQLOperationalConnection
from .infrastructure.settings import ROOT_DIR, data_dir, runtime_settings
from .infrastructure.typedb_storage_guard import typedb_storage_health, typedb_storage_inventory
from .infrastructure.operational_storage_guard import storage_directory_physical_size_bytes


BASE_WORKERS = {
    "monitor": {
        "label": "Python realtime monitor",
        "pid": data_dir() / "python-monitor.pid",
        "log": data_dir() / "python-monitor.log",
        "command": [sys.executable, "-u", "python_service/service.py", "monitor", "watch"],
        "needle": "python_service/service.py monitor watch",
    },
    "market-data": {
        "label": "Python market data collector",
        "pid": data_dir() / "python-market-data.pid",
        "log": data_dir() / "python-market-data.log",
        "command": [sys.executable, "-u", "python_service/service.py", "market-data", "watch"],
        "needle": "python_service/service.py market-data watch",
    },
    "kis-realtime": {
        "label": "Python KIS realtime WebSocket worker",
        "pid": data_dir() / "python-kis-realtime.pid",
        "log": data_dir() / "python-kis-realtime.log",
        "command": [sys.executable, "-u", "python_service/service.py", "kis-realtime", "watch"],
        "needle": "python_service/service.py kis-realtime watch",
    },
    "news": {
        "label": "Python news collector",
        "pid": data_dir() / "python-news.pid",
        "log": data_dir() / "python-news.log",
        "command": [sys.executable, "-u", "python_service/service.py", "news", "watch"],
        "needle": "python_service/service.py news watch",
    },
    "news-analysis": {
        "label": "Python news analysis worker",
        "pid": data_dir() / "python-news-analysis.pid",
        "log": data_dir() / "python-news-analysis.log",
        "command": [sys.executable, "-u", "python_service/service.py", "news-analysis", "watch"],
        "needle": "python_service/service.py news-analysis watch",
    },
    "investment-research": {
        "label": "Python investment research worker",
        "pid": data_dir() / "python-investment-research.pid",
        "log": data_dir() / "python-investment-research.log",
        "command": [sys.executable, "-u", "python_service/service.py", "investment-research", "watch"],
        "needle": "python_service/service.py investment-research watch",
    },
    "investment-calendar": {
        "label": "Python investment calendar worker",
        "pid": data_dir() / "python-investment-calendar.pid",
        "log": data_dir() / "python-investment-calendar.log",
        "command": [sys.executable, "-u", "python_service/service.py", "investment-calendar", "watch"],
        "needle": "python_service/service.py investment-calendar watch",
    },
    "model-review": {
        "label": "Python model review worker",
        "pid": data_dir() / "python-model-review.pid",
        "log": data_dir() / "python-model-review.log",
        "command": [sys.executable, "-u", "python_service/service.py", "model-review", "watch"],
        "needle": "python_service/service.py model-review watch",
    },
    "ontology-reasoning": {
        "label": "Python ontology reasoning worker",
        "pid": data_dir() / "python-ontology-reasoning.pid",
        "log": data_dir() / "python-ontology-reasoning.log",
        "command": [sys.executable, "-u", "python_service/service.py", "ontology-reasoning", "watch"],
        "needle": "python_service/service.py ontology-reasoning watch",
    },
    "ontology-world-projection": {
        "label": "Python shared ontology world projection worker",
        "pid": data_dir() / "python-ontology-world-projection.pid",
        "log": data_dir() / "python-ontology-world-projection.log",
        "command": [sys.executable, "-u", "python_service/service.py", "ontology-world-projection", "watch"],
        "needle": "python_service/service.py ontology-world-projection watch",
    },
    "ontology-inference-detail": {
        "label": "Python deferred ontology inference detail worker",
        "pid": data_dir() / "python-ontology-inference-detail.pid",
        "log": data_dir() / "python-ontology-inference-detail.log",
        "command": [sys.executable, "-u", "python_service/service.py", "ontology-inference-detail", "watch"],
        "needle": "python_service/service.py ontology-inference-detail watch",
    },
    "ontology-rulebox-prewarm": {
        "label": "Python TypeDB RuleBox schema-function prewarm worker",
        "pid": data_dir() / "python-ontology-rulebox-prewarm.pid",
        "log": data_dir() / "python-ontology-rulebox-prewarm.log",
        "command": [sys.executable, "-u", "python_service/service.py", "ontology-rulebox-prewarm", "watch"],
        "needle": "python_service/service.py ontology-rulebox-prewarm watch",
    },
    "ontology-maintenance": {
        "label": "Python ontology ABox maintenance worker",
        "pid": data_dir() / "python-ontology-maintenance.pid",
        "log": data_dir() / "python-ontology-maintenance.log",
        "command": [sys.executable, "-u", "python_service/service.py", "ontology-maintenance", "watch"],
        "needle": "python_service/service.py ontology-maintenance watch",
    },
    "ontology-lab": {
        "label": "Python ontology lab worker",
        "pid": data_dir() / "python-ontology-lab.pid",
        "log": data_dir() / "python-ontology-lab.log",
        "command": [sys.executable, "-u", "python_service/service.py", "ontology-lab", "watch"],
        "needle": "python_service/service.py ontology-lab watch",
    },
    "notifications": {
        "label": "Python notification worker",
        "pid": data_dir() / "python-notifications.pid",
        "log": data_dir() / "python-notifications.log",
        "command": [sys.executable, "-u", "python_service/service.py", "notifications", "watch"],
        "needle": "python_service/service.py notifications watch",
    },
    "operational-maintenance": {
        "label": "Python operational history maintenance worker",
        "pid": data_dir() / "python-operational-maintenance.pid",
        "log": data_dir() / "python-operational-maintenance.log",
        "command": [sys.executable, "-u", "python_service/service.py", "maintenance", "watch"],
        "needle": "python_service/service.py maintenance watch",
    },
}


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


def typedb_requested(settings: Dict[str, object]) -> bool:
    return truthy((settings or {}).get("ontologyTypeDbEnabled"))


def typedb_executable() -> str:
    explicit = str(os.environ.get("TYPEDB_COMMAND") or "").strip()
    if explicit:
        return explicit
    found = shutil.which("typedb")
    if found:
        return found
    home_install = Path.home() / ".typedb" / "typedb"
    return str(home_install) if home_install.exists() else ""


def typedb_worker_spec(settings: Dict[str, object]) -> Dict[str, object]:
    executable = typedb_executable()
    address = str((settings or {}).get("typedbAddress") or "127.0.0.1:1729").strip() or "127.0.0.1:1729"
    data_path = data_dir() / "typedb-data"
    log_dir = data_dir() / "typedb-logs"
    password = str((settings or {}).get("typedbPassword") or os.environ.get("TYPEDB_PASSWORD") or "").strip()
    allow_weak_password = truthy(
        os.environ.get("TYPEDB_ALLOW_DEFAULT_PASSWORD")
        or (settings or {}).get("typedbAllowDefaultPassword")
    )
    weak_password = password.lower() in {"", "admin", "password", "typedb"}
    command = [
        executable,
        "server",
        "--server.listen-address",
        address,
        "--server.advertise-address",
        address,
        "--server.http.listen-address",
        "127.0.0.1:8000",
        "--diagnostics.monitoring.enabled",
        "false",
        "--diagnostics.reporting.metrics",
        "false",
        "--diagnostics.reporting.errors",
        "false",
        "--storage.data-directory",
        str(data_path),
        "--logging.directory",
        str(log_dir),
    ] if executable else []
    return {
        "label": "TypeDB ontology graph store",
        "pid": data_dir() / "typedb.pid",
        "log": data_dir() / "typedb.log",
        "command": command,
        "needle": "typedb_server_bin",
        "role": "typedb",
        "dataPath": data_path,
        "retentionHours": str((settings or {}).get("typedbDataRetentionHours") or "24"),
        "maxSizeMb": str((settings or {}).get("typedbDataMaxSizeMb") or "2048"),
        "minimumFreeSpaceMb": str(max(
            int_value((settings or {}).get("typedbMinimumFreeSpaceMb"), 4096, 1),
            int_value((settings or {}).get("operationalMinimumFreeSpaceMb"), 12288, 1),
        )),
        # TypeDB is the durable ontology store. Capacity pressure must surface
        # as an explicit, source-verified rotation rather than an arbitrary
        # worker restart. The active graph is rebuildable from MySQL.
        "autoResetEnabled": str((settings or {}).get("typedbAutoResetEnabled") or "0"),
        "autoRotationEnabled": str((settings or {}).get("typedbCapacityAutoRotateEnabled") or "1"),
        "autoRotationPercent": str((settings or {}).get("typedbCapacityAutoRotatePercent") or "90"),
        "autoRotationCooldownMinutes": str(
            (settings or {}).get("typedbCapacityAutoRotateCooldownMinutes") or "60"
        ),
        "ageResetEnabled": str((settings or {}).get("typedbAgeResetEnabled") or "0"),
        "healthAddress": address,
        "typedbUser": str((settings or {}).get("typedbUser") or os.environ.get("TYPEDB_USER") or "admin"),
        "typedbPassword": password,
        "typedbDatabase": str((settings or {}).get("typedbDatabase") or os.environ.get("TYPEDB_DATABASE") or "orbit_alpha_ontology"),
        "typedbTlsEnabled": str((settings or {}).get("typedbTlsEnabled") or os.environ.get("TYPEDB_TLS_ENABLED") or "0"),
        # A durable TypeDB may need several minutes to replay its WAL and
        # rebuild the type cache after a clean server restart.  Treating that
        # normal recovery as a 60-second failure leaves every dependent worker
        # down and turns a recoverable restart into a reasoning backlog.
        "startupWaitSeconds": str((settings or {}).get("typedbStartupWaitSeconds") or "1800"),
        "seedOnStart": str((settings or {}).get("typedbSeedOnStart") or os.environ.get("TYPEDB_SEED_ON_START") or "1"),
        "seedReplaceRuleBox": str((settings or {}).get("typedbSeedReplaceRuleBox") or os.environ.get("TYPEDB_SEED_REPLACE_RULEBOX") or "1"),
        "seedKeepInference": str((settings or {}).get("typedbSeedKeepInference") or os.environ.get("TYPEDB_SEED_KEEP_INFERENCE") or "1"),
        "seedTimeoutSeconds": str((settings or {}).get("typedbSeedTimeoutSeconds") or os.environ.get("TYPEDB_SEED_TIMEOUT_SECONDS") or "360"),
        "seedRetryCount": str((settings or {}).get("typedbSeedRetryCount") or os.environ.get("TYPEDB_SEED_RETRY_COUNT") or "2"),
        "sharedWorldProjectionRebuildTimeoutSeconds": str(
            (settings or {}).get("typedbSharedWorldProjectionRebuildTimeoutSeconds")
            or os.environ.get("TYPEDB_SHARED_WORLD_PROJECTION_REBUILD_TIMEOUT_SECONDS")
            or "900"
        ),
        "sharedWorldProjectionRebuildLimit": str(
            (settings or {}).get("typedbSharedWorldProjectionRebuildLimit")
            or os.environ.get("TYPEDB_SHARED_WORLD_PROJECTION_REBUILD_LIMIT")
            or "100"
        ),
        "missingReason": (
            "TypeDB executable was not found. Install TypeDB or set TYPEDB_COMMAND."
            if not executable
            else (
                "TypeDB requires a non-default TYPEDB_PASSWORD in .env.local."
                if weak_password and not allow_weak_password
                else ""
            )
        ),
    }


def mysql_executable() -> str:
    explicit = str(os.environ.get("MYSQLD_COMMAND") or "").strip()
    if explicit:
        return explicit
    return shutil.which("mysqld") or "/usr/local/opt/mysql/bin/mysqld"


def mysql_worker_spec(settings: Dict[str, object]) -> Dict[str, object]:
    executable = mysql_executable()
    connection_settings = mysql_settings(settings)
    data_path = Path(str(os.environ.get("MYSQL_DATA_DIR") or data_dir() / "mysql-runtime"))
    port = int_value(os.environ.get("MYSQL_PORT") or (settings or {}).get("mysqlPort"), 3306, 1)
    redo_log_capacity_mb = int_value(
        os.environ.get("MYSQL_INNODB_REDO_LOG_CAPACITY_MB")
        or (settings or {}).get("mysqlInnoDbRedoLogCapacityMb"),
        256,
        64,
    )
    redo_log_capacity_mb = min(4096, redo_log_capacity_mb)
    socket_path = str(os.environ.get("MYSQL_UNIX_SOCKET") or data_path / "mysql.sock")
    command = [
        executable,
        "--no-defaults",
        "--basedir=/usr/local/opt/mysql",
        "--datadir=" + str(data_path),
        "--port=" + str(port),
        "--bind-address=127.0.0.1",
        "--socket=" + socket_path,
        "--pid-file=" + str(data_path / "mysqld.pid"),
        "--log-error=" + str(data_path / "mysql.err"),
        "--mysqlx=0",
        "--skip-log-bin",
        "--innodb-buffer-pool-size=536870912",
        "--innodb-redo-log-capacity=" + str(redo_log_capacity_mb * 1024 * 1024),
        "--max-connections=100",
    ] if executable and Path(executable).exists() else []
    return {
        "label": "MySQL operational store",
        "pid": data_dir() / "mysql-service.pid",
        "log": data_dir() / "mysql-service.log",
        "command": command,
        "needle": "mysqld --no-defaults",
        "role": "mysql",
        "dataPath": data_path,
        "healthAddress": "127.0.0.1:" + str(port),
        "startupWaitSeconds": str((settings or {}).get("mysqlStartupWaitSeconds") or "60"),
        # Used only after this manager initializes a brand-new local data
        # directory. Never log these values or apply them to an existing DB.
        "mysqlUser": str(connection_settings.get("user") or ""),
        "mysqlPassword": str(connection_settings.get("password") or ""),
        "mysqlDatabase": str(connection_settings.get("database") or "orbit_alpha"),
        "operationalSettings": dict(settings or {}),
        "missingReason": "" if command else "MySQL executable was not found. Set MYSQLD_COMMAND.",
    }


def ensure_mysql_operational_schema(spec: Dict[str, object]) -> bool:
    """Bootstrap MySQL once before workers use fast store construction.

    Isolated TypeDB cycles intentionally skip schema DDL so a fresh child does
    not spend its inference deadline on metadata work. The service manager is
    the durable startup boundary that guarantees those tables exist first.
    """
    settings = dict(spec.get("operationalSettings") or {})
    settings["_skipOperationalHistoryRetention"] = "1"
    settings.pop("_skipOperationalSchemaBootstrap", None)
    try:
        MySQLOperationalConnection(settings)
        MySQLMonitorAccountJobStore(settings)
    except Exception as error:  # noqa: BLE001 - dependent workers cannot safely run without their tables.
        message = "operational schema bootstrap failed: " + str(error)[:300]
        append_log(spec["log"], message)
        print(str(spec["label"]) + " " + message)
        return False
    append_log(spec["log"], "operational schema bootstrap ready")
    print(str(spec["label"]) + " operational schema ready.")
    return True


def web_worker_spec(settings: Dict[str, object]) -> Dict[str, object]:
    port = int_value(os.environ.get("PORT") or (settings or {}).get("webPort"), 3000, 1)
    return {
        "label": "Orbit Alpha web server",
        "pid": data_dir() / "python-web.pid",
        "log": data_dir() / "python-web.log",
        "command": [sys.executable, "-u", "python_service/service.py", "web"],
        "needle": "python_service/service.py web",
        "role": "web",
        "healthAddress": "127.0.0.1:" + str(port),
        "startupWaitSeconds": str((settings or {}).get("webStartupWaitSeconds") or "30"),
        "env": {
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "ALLOW_PORT_FALLBACK": "0",
        },
    }


def worker_specs() -> Dict[str, Dict[str, object]]:
    try:
        settings = runtime_settings()
    except Exception:  # noqa: BLE001 - service manager should still manage Python workers.
        settings = {}
    workers = {}
    if truthy((settings or {}).get("mysqlRuntimeManaged", os.environ.get("MYSQL_RUNTIME_MANAGED", "1"))):
        workers["mysql"] = mysql_worker_spec(settings)
    if typedb_requested(settings):
        workers["typedb"] = typedb_worker_spec(settings)
    workers.update(BASE_WORKERS)
    workers["web"] = web_worker_spec(settings)
    return workers


def read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def command_for_pid(pid: int) -> str:
    if not pid:
        return ""
    try:
        output = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True, stderr=subprocess.DEVNULL)
        return output.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def is_worker_command(command: str, spec: Dict[str, object]) -> bool:
    return str(spec["needle"]) in command


def pid_exists(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def is_running(pid: int, spec: Dict[str, object]) -> bool:
    if not pid_exists(pid):
        return False
    if os.name != "nt":
        return is_worker_command(command_for_pid(pid), spec)
    return True


def remove_pid(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def append_log(path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n[" + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "] manager " + label + "\n")


def int_value(value: object, fallback: int, lower: int = 0) -> int:
    try:
        parsed = int(float(str(value or "").strip()))
    except ValueError:
        parsed = fallback
    return max(lower, parsed)


def directory_size_bytes(path: Path) -> int:
    """Return actual allocated bytes for TypeDB reset and rotation decisions."""
    return storage_directory_physical_size_bytes(path)


def typedb_retention_marker_path() -> Path:
    return data_dir() / "typedb-retention.json"


def read_typedb_retention_marker() -> Dict[str, object]:
    try:
        return json.loads(typedb_retention_marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def write_typedb_retention_marker(payload: Dict[str, object]) -> None:
    path = typedb_retention_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(path, 0o600)


def typedb_rotation_lock_path() -> Path:
    return data_dir() / "typedb-rotation.lock"


def acquire_typedb_rotation_lock() -> Dict[str, object]:
    """Acquire a small cross-command lock before stopping graph workers."""

    path = typedb_rotation_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "startedAt": iso_now()}
    for _attempt in range(2):
        try:
            descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                existing = {}
            owner = int_value(existing.get("pid"), 0, 0)
            if owner and pid_exists(owner):
                return {"acquired": False, "reason": "another TypeDB rotation is active", "ownerPid": owner}
            try:
                path.unlink()
            except OSError:
                return {"acquired": False, "reason": "TypeDB rotation lock is unavailable"}
            continue
        try:
            os.write(descriptor, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(descriptor)
        return {"acquired": True, "path": str(path), **payload}
    return {"acquired": False, "reason": "TypeDB rotation lock could not be acquired"}


def release_typedb_rotation_lock(lock: Dict[str, object]) -> None:
    if not bool((lock or {}).get("acquired")):
        return
    try:
        typedb_rotation_lock_path().unlink()
    except OSError:
        return


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def typedb_data_age_hours(path: Path, marker: Dict[str, object]) -> float:
    raw = str((marker or {}).get("lastResetAt") or "").strip()
    if raw:
        try:
            parsed = time.strptime(raw.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
            return max(0.0, (time.time() - calendar.timegm(parsed)) / 3600.0)
        except ValueError:
            pass
    try:
        return max(0.0, (time.time() - path.stat().st_mtime) / 3600.0)
    except OSError:
        return 0.0


def typedb_reset_needed(
    spec: Dict[str, object],
    ignore_auto_reset: bool = False,
) -> Dict[str, object]:
    """Describe whether a TypeDB rebuild is required without mutating data.

    Routine supervisors honor ``typedbAutoResetEnabled`` and never delete a
    graph store.  Controlled operator rotation still needs to evaluate the
    physical size even while that automatic switch is deliberately disabled.
    """

    data_path = Path(spec.get("dataPath") or "")
    enabled = truthy(spec.get("autoResetEnabled"))
    age_reset_enabled = truthy(spec.get("ageResetEnabled")) if spec.get("ageResetEnabled") not in (None, "") else False
    retention_hours = int_value(spec.get("retentionHours"), 24, 1)
    max_size_mb = int_value(spec.get("maxSizeMb"), 2048, 1)
    size_bytes = directory_size_bytes(data_path)
    marker = read_typedb_retention_marker()
    age_hours = typedb_data_age_hours(data_path, marker)
    reasons = []
    if not enabled and not ignore_auto_reset:
        return {
            "needed": False,
            "reason": "disabled",
            "sizeBytes": size_bytes,
            "ageHours": age_hours,
            "retentionHours": retention_hours,
            "ageResetEnabled": age_reset_enabled,
            "maxSizeMb": max_size_mb,
            "autoResetEnabled": False,
        }
    if not data_path.exists() or size_bytes <= 0:
        return {"needed": False, "reason": "empty", "sizeBytes": size_bytes, "ageHours": age_hours}
    if age_reset_enabled and age_hours >= retention_hours:
        reasons.append("age " + str(round(age_hours, 2)) + "h >= " + str(retention_hours) + "h")
    if size_bytes >= max_size_mb * 1024 * 1024:
        reasons.append("size " + str(round(size_bytes / 1024 / 1024, 1)) + "MB >= " + str(max_size_mb) + "MB")
    return {
        "needed": bool(reasons),
        "reason": "; ".join(reasons),
        "sizeBytes": size_bytes,
        "ageHours": age_hours,
        "retentionHours": retention_hours,
        "ageResetEnabled": age_reset_enabled,
        "maxSizeMb": max_size_mb,
        "autoResetEnabled": enabled,
    }


def typedb_auto_rotation_needed(
    spec: Dict[str, object],
    now_epoch: float = None,
    size_provider=None,
) -> Dict[str, object]:
    """Return whether a source-verified automatic TypeDB rotation is due.

    This is intentionally independent from the historical ``autoReset``
    setting. The latter remains an explicit legacy retention switch; capacity
    rotation only happens when the supervisor can rebuild the graph from the
    durable operational store and the configured pressure threshold is met.
    """

    configured = dict(spec or {})
    enabled = truthy(configured.get("autoRotationEnabled"))
    data_path = Path(configured.get("dataPath") or "")
    maximum_mb = int_value(configured.get("maxSizeMb"), 2048, 1)
    threshold_percent = int_value(configured.get("autoRotationPercent"), 90, 50)
    threshold_percent = min(100, threshold_percent)
    cooldown_minutes = min(24 * 60, int_value(configured.get("autoRotationCooldownMinutes"), 60, 1))
    size_bytes = int((size_provider or directory_size_bytes)(data_path))
    size_mb = round(size_bytes / 1024 / 1024, 1)
    usage_percent = round(size_bytes / (maximum_mb * 1024 * 1024) * 100.0, 1)
    now = float(now_epoch if now_epoch is not None else time.time())
    marker = read_typedb_retention_marker()
    try:
        last_attempt_epoch = float(marker.get("lastAutoRotationAttemptEpoch") or 0)
    except (TypeError, ValueError):
        last_attempt_epoch = 0.0
    cooldown_remaining_seconds = max(
        0,
        int(cooldown_minutes * 60 - max(0.0, now - last_attempt_epoch)),
    ) if last_attempt_epoch > 0 else 0
    threshold_reached = bool(
        data_path.exists()
        and size_bytes > 0
        and usage_percent >= threshold_percent
    )
    hard_limit_reached = usage_percent >= 100.0
    if not enabled:
        return {
            "needed": False,
            "reason": "disabled",
            "enabled": False,
            "typedbSizeMb": size_mb,
            "typedbUsagePercent": usage_percent,
            "thresholdPercent": threshold_percent,
            "maxSizeMb": maximum_mb,
        }
    if not threshold_reached:
        return {
            "needed": False,
            "reason": "below-threshold",
            "enabled": True,
            "typedbSizeMb": size_mb,
            "typedbUsagePercent": usage_percent,
            "thresholdPercent": threshold_percent,
            "maxSizeMb": maximum_mb,
            "cooldownRemainingSeconds": cooldown_remaining_seconds,
        }
    if cooldown_remaining_seconds > 0 and not hard_limit_reached:
        return {
            "needed": False,
            "reason": "cooldown",
            "enabled": True,
            "typedbSizeMb": size_mb,
            "typedbUsagePercent": usage_percent,
            "thresholdPercent": threshold_percent,
            "maxSizeMb": maximum_mb,
            "cooldownRemainingSeconds": cooldown_remaining_seconds,
        }
    return {
        "needed": True,
        "reason": (
            "size " + str(size_mb) + "MB (" + str(usage_percent) + "%) >= automatic rotation "
            + str(threshold_percent) + "%"
        ),
        "enabled": True,
        "typedbSizeMb": size_mb,
        "typedbUsagePercent": usage_percent,
        "thresholdPercent": threshold_percent,
        "maxSizeMb": maximum_mb,
        "cooldownRemainingSeconds": cooldown_remaining_seconds,
        "hardLimitReached": hard_limit_reached,
    }


def typedb_auto_rotation_recovery_preflight(
    specs: Dict[str, Dict[str, object]],
) -> Dict[str, object]:
    """Require the managed MySQL recovery source before an automatic reset."""

    mysql_spec = dict((specs or {}).get("mysql") or {})
    if not mysql_spec:
        return {
            "ready": False,
            "reason": "managed MySQL recovery source is not configured",
        }
    pid = read_pid(mysql_spec.get("pid"))
    if not is_running(pid, mysql_spec) or not tcp_ready(mysql_spec.get("healthAddress")):
        return {
            "ready": False,
            "reason": "managed MySQL recovery source is not healthy",
        }
    return {
        "ready": True,
        "reason": "managed MySQL recovery source is healthy",
        "mysqlPid": pid,
    }


def record_typedb_auto_rotation_incident(
    spec: Dict[str, object],
    decision: Dict[str, object],
) -> Dict[str, object]:
    """Page the operator once before a supervisor-owned graph rebuild.

    The normal capacity sampler runs in a low-priority worker. A rapid
    automatic rotation can finish between those samples, so record a distinct
    operational incident before stopping the worker set.
    """

    try:
        from .infrastructure.operational_storage_guard import operational_storage_inventory
        from .infrastructure.service_factory import observe_operational_storage_capacity

        settings = runtime_settings(fast_operational_read=True)
        snapshot = operational_storage_inventory(settings)
        health = observe_operational_storage_capacity(
            settings,
            snapshot=snapshot,
            force_alert=True,
            force_alert_kind="typedb-auto-rotation",
        )
        return {
            "recorded": True,
            "alertRequired": bool(health.get("alertRequired")),
            "typedbSizeMb": health.get("typedbSizeMb"),
            "typedbUsagePercent": decision.get("typedbUsagePercent"),
        }
    except Exception as error:  # noqa: BLE001 - notification failure must not leave TypeDB unsafe.
        return {"recorded": False, "reason": str(error)[:180]}


def record_typedb_auto_rotation_state(**updates: object) -> Dict[str, object]:
    marker = read_typedb_retention_marker()
    marker.update({key: value for key, value in updates.items() if value is not None})
    write_typedb_retention_marker(marker)
    return marker


def typedb_storage_preflight(spec: Dict[str, object]) -> Dict[str, object]:
    return typedb_storage_health(
        {
            "ontologyTypeDbEnabled": "1",
            "typedbMinimumFreeSpaceMb": spec.get("minimumFreeSpaceMb") or "4096",
        },
        data_path=Path(spec.get("dataPath") or data_dir() / "typedb-data"),
    )


def run_typedb_data_retention(spec: Dict[str, object], force: bool = False) -> Dict[str, object]:
    if str(spec.get("role") or "") != "typedb":
        return {"status": "skipped", "reason": "not typedb"}
    data_path = Path(spec.get("dataPath") or "")
    decision = typedb_reset_needed(spec, ignore_auto_reset=force)
    if not force:
        if not decision.get("needed"):
            return {"status": "skipped", **decision}
        # The graph can always be rebuilt from MySQL, but an automatic reset
        # still destroys the active ABox, RuleBox history, and inference audit
        # at the worst possible time.  Only an explicit maintenance command
        # may remove the TypeDB data directory.
        return {
            "status": "maintenance-required",
            "destructiveResetBlocked": True,
            **decision,
            "dataPath": str(data_path),
        }
    previous_marker = read_typedb_retention_marker()
    if data_path.exists():
        shutil.rmtree(data_path)
    write_typedb_retention_marker({
        **previous_marker,
        "lastResetAt": iso_now(),
        "reason": "forced" if force else decision.get("reason", ""),
        "previousSizeBytes": int(decision.get("sizeBytes") or 0),
        "retentionHours": int(decision.get("retentionHours") or int_value(spec.get("retentionHours"), 24, 1)),
        "maxSizeMb": int(decision.get("maxSizeMb") or int_value(spec.get("maxSizeMb"), 2048, 1)),
        # A local CE data reset recreates the built-in admin user with the
        # documented initial password. Reapply the configured local password
        # before dependent workers are allowed to connect.
        "credentialsBootstrapPending": True,
        # TypeDB holds materialized shared worlds while the verified inputs
        # remain durable in MySQL. Rebuild them before live reasoning starts.
        "sharedWorldProjectionRebuildPending": True,
        "sharedWorldProjectionRebuildReason": "forced-data-reset",
    })
    return {"status": "reset", **decision, "dataPath": str(data_path)}


def tail(path: Path, count: int = 8) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return lines[-count:]
    except OSError:
        return []


def status_worker(spec: Dict[str, object]) -> int:
    pid_path = spec["pid"]
    log_path = spec["log"]
    pid = read_pid(pid_path)
    running = is_running(pid, spec)
    print(str(spec["label"]) + ": " + ("running" if running else "stopped"))
    if spec.get("missingReason"):
        print("Unavailable: " + str(spec.get("missingReason")))
    if pid:
        print("PID: " + str(pid))
    if running:
        print("Command: " + command_for_pid(pid))
        if spec.get("healthAddress"):
            print("Health: " + ("ready" if tcp_ready(spec.get("healthAddress")) else "not-ready") + " · " + str(spec.get("healthAddress")))
    if str(spec.get("role") or "") == "typedb":
        storage = typedb_storage_preflight(spec)
        print(
            "Storage: " + str(storage.get("status") or "unknown")
            + " · free=" + str(storage.get("freeMb") or "-") + "MB"
            + " · minimum=" + str(storage.get("minimumFreeMb") or "-") + "MB"
        )
        inventory = typedb_storage_inventory(
            {
                "ontologyTypeDbEnabled": "1",
                "typedbMinimumFreeSpaceMb": spec.get("minimumFreeSpaceMb") or "4096",
                "typedbDataMaxSizeMb": spec.get("maxSizeMb") or "4096",
            },
            data_path=Path(spec.get("dataPath") or data_dir() / "typedb-data"),
        )
        capacity = typedb_auto_rotation_needed(spec)
        print(
            "Capacity (physical): " + str(inventory.get("typedbSizeMb") or 0) + "MB / "
            + str(inventory.get("typedbLimitMb") or 0) + "MB ("
            + str(capacity.get("typedbUsagePercent") or 0) + "%)"
            + " · WAL=" + str(inventory.get("typedbWalMb") or 0) + "MB"
            + " · checkpoint reference=" + str(inventory.get("typedbCheckpointReferencedMb") or inventory.get("typedbCheckpointMb") or 0) + "MB"
            + " · hard-link deduplicated=" + str(inventory.get("typedbSharedLinkedMb") or 0) + "MB"
        )
        rotation_status = "due" if capacity.get("needed") else str(capacity.get("reason") or "unknown")
        print(
            "Automatic rotation: " + ("enabled" if capacity.get("enabled") else "disabled")
            + " · threshold=" + str(capacity.get("thresholdPercent") or "-") + "%"
            + " · status=" + rotation_status
            + " · cooldown=" + str(capacity.get("cooldownRemainingSeconds") or 0) + "s"
        )
    if log_path.exists():
        print("Log: " + str(log_path))
        print("Log updated: " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(log_path.stat().st_mtime)))
        recent = tail(log_path)
        if recent:
            print("Recent log:")
            for line in recent:
                print(line)
    else:
        print("Log: " + str(log_path) + " (not created)")
    if pid and not running:
        remove_pid(pid_path)
    return 0


def typedb_host_port(address: object) -> tuple:
    raw = str(address or "").strip() or "127.0.0.1:1729"
    raw = raw.split(",", 1)[0].strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0].strip()
    if raw.startswith("[") and "]" in raw:
        host = raw[1 : raw.find("]")]
        port_text = raw[raw.find("]") + 1 :].lstrip(":") or "1729"
    elif ":" in raw:
        host, port_text = raw.rsplit(":", 1)
    else:
        host, port_text = raw, "1729"
    try:
        port = int(float(port_text))
    except ValueError:
        port = 1729
    return (host or "127.0.0.1", port)


def tcp_ready(address: object, timeout_seconds: float = 1.0) -> bool:
    host, port = typedb_host_port(address)
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout_seconds)
        return True
    except OSError:
        return False
    finally:
        try:
            if sock:
                sock.close()
        except OSError:
            pass


def prepare_mysql_data_dir(spec: Dict[str, object]) -> bool:
    data_path = Path(spec.get("dataPath") or "")
    if not data_path:
        return False
    data_path.mkdir(parents=True, exist_ok=True)
    os.chmod(data_path, 0o700)
    if (data_path / "mysql").exists():
        return True
    executable = str((spec.get("command") or [""])[0] or "")
    result = subprocess.run(
        [
            executable,
            "--no-defaults",
            "--initialize-insecure",
            "--basedir=/usr/local/opt/mysql",
            "--datadir=" + str(data_path),
        ],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    append_log_text(spec["log"], "initialize exit=" + str(result.returncode), (result.stdout or "") + (result.stderr or ""))
    return result.returncode == 0


def mysql_sql_literal(value: object) -> str:
    return "'" + str(value or "").replace("\\", "\\\\").replace("'", "\\'") + "'"


def ensure_mysql_runtime_application_user(spec: Dict[str, object]) -> bool:
    """Provision only a freshly initialized local MySQL runtime.

    The managed server is initialized with an empty local root password. A
    reset must restore the configured application account before workers and
    isolated test schemas can connect. Existing data directories are never
    changed by this helper.
    """
    application_user = str(spec.get("mysqlUser") or "").strip()
    application_password = str(spec.get("mysqlPassword") or "")
    database = str(spec.get("mysqlDatabase") or "orbit_alpha").strip() or "orbit_alpha"
    if not application_user or application_user == "root":
        return True
    try:
        import pymysql
    except ImportError:
        append_log(spec["log"], "MySQL application user provisioning unavailable: pymysql is missing")
        return False
    socket_path = Path(spec.get("dataPath") or "") / "mysql.sock"
    database_identifier = "`" + database.replace("`", "``") + "`"
    test_database_identifier = "`" + (database + "_test%").replace("`", "``") + "`"
    account_identifier = mysql_sql_literal(application_user) + "@'%'"
    connection = None
    try:
        connection = pymysql.connect(
            unix_socket=str(socket_path),
            user="root",
            password="",
            charset="utf8mb4",
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE DATABASE IF NOT EXISTS " + database_identifier
                + " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(
                "CREATE USER IF NOT EXISTS " + account_identifier
                + " IDENTIFIED BY " + mysql_sql_literal(application_password)
            )
            cursor.execute(
                "ALTER USER " + account_identifier
                + " IDENTIFIED BY " + mysql_sql_literal(application_password)
            )
            cursor.execute("GRANT ALL PRIVILEGES ON " + database_identifier + ".* TO " + account_identifier)
            # Test workers use isolated names such as orbit_alpha_test_*.
            # They are dropped by the fixture cleanup and the retention CLI.
            cursor.execute("GRANT ALL PRIVILEGES ON " + test_database_identifier + ".* TO " + account_identifier)
            cursor.execute("FLUSH PRIVILEGES")
        append_log(spec["log"], "provisioned local MySQL application and test-schema grants")
        return True
    except Exception:
        append_log(spec["log"], "MySQL application user provisioning failed")
        return False
    finally:
        try:
            if connection:
                connection.close()
        except Exception:
            pass


def wait_for_tcp_service(spec: Dict[str, object]) -> bool:
    wait_seconds = int_value(spec.get("startupWaitSeconds"), 30, 0)
    address = str(spec.get("healthAddress") or "")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() <= deadline:
        pid = read_pid(spec["pid"])
        if pid and not pid_exists(pid):
            return False
        if tcp_ready(address):
            append_log(spec["log"], "ready " + address)
            return True
        time.sleep(0.5)
    append_log(spec["log"], "not-ready timeout " + address)
    return False


def typedb_driver_components():
    try:
        from typedb.driver import Credentials, DriverOptions, DriverTlsConfig, TypeDB
    except Exception:
        return None
    return TypeDB, Credentials, DriverOptions, DriverTlsConfig


def typedb_driver_ready(spec: Dict[str, object]) -> bool:
    """Verify TypeDB accepts authenticated driver requests, not only TCP."""
    components = typedb_driver_components()
    if components is None:
        # The seed process performs the definitive driver check. Retain the
        # socket check when the optional driver is not importable here.
        return True
    TypeDB, Credentials, DriverOptions, DriverTlsConfig = components
    address = str(spec.get("healthAddress") or spec.get("typedbAddress") or "127.0.0.1:1729")
    tls_enabled = truthy(spec.get("typedbTlsEnabled"))
    tls_config = DriverTlsConfig.enabled() if tls_enabled else DriverTlsConfig.disabled()
    driver = None
    try:
        driver = TypeDB.driver(
            address,
            Credentials(
                str(spec.get("typedbUser") or "admin"),
                str(spec.get("typedbPassword") or "password"),
            ),
            DriverOptions(tls_config, request_timeout_millis=1000),
        )
        # ``contains`` is valid before the application database is seeded. A
        # successful response proves the server has completed gRPC startup.
        driver.databases.contains(str(spec.get("typedbDatabase") or "orbit_alpha_ontology"))
        return True
    except Exception:
        return False
    finally:
        try:
            if driver:
                driver.close()
        except Exception:
            pass


def typedb_credentials_bootstrap_pending() -> bool:
    return bool(read_typedb_retention_marker().get("credentialsBootstrapPending"))


def clear_typedb_credentials_bootstrap_pending() -> None:
    marker = read_typedb_retention_marker()
    if not marker.get("credentialsBootstrapPending"):
        return
    marker["credentialsBootstrapPending"] = False
    marker["credentialsBootstrapCompletedAt"] = iso_now()
    write_typedb_retention_marker(marker)


def typedb_shared_world_projection_rebuild_pending() -> bool:
    return bool(read_typedb_retention_marker().get("sharedWorldProjectionRebuildPending"))


def clear_typedb_shared_world_projection_rebuild_pending() -> None:
    marker = read_typedb_retention_marker()
    if not marker.get("sharedWorldProjectionRebuildPending"):
        return
    marker["sharedWorldProjectionRebuildPending"] = False
    marker["sharedWorldProjectionRebuildCompletedAt"] = iso_now()
    write_typedb_retention_marker(marker)


def bootstrap_typedb_credentials_after_reset(spec: Dict[str, object]) -> bool:
    """Restore local TypeDB credentials after an intentional empty-store boot.

    TypeDB CE recreates only ``admin/password`` after its data directory is
    removed. The configured password remains in the local settings store, so
    no secret needs to be regenerated or logged. This path is gated by the
    reset marker and is disabled as soon as the configured credentials work.
    """
    if not typedb_credentials_bootstrap_pending():
        return False
    components = typedb_driver_components()
    if components is None:
        return False
    TypeDB, Credentials, DriverOptions, DriverTlsConfig = components
    configured_user = str(spec.get("typedbUser") or "admin").strip() or "admin"
    configured_password = str(spec.get("typedbPassword") or "").strip()
    if not configured_password:
        return False
    address = str(spec.get("healthAddress") or spec.get("typedbAddress") or "127.0.0.1:1729")
    tls_enabled = truthy(spec.get("typedbTlsEnabled"))
    tls_config = DriverTlsConfig.enabled() if tls_enabled else DriverTlsConfig.disabled()
    options = DriverOptions(tls_config, request_timeout_millis=1000)
    default_driver = None
    try:
        default_driver = TypeDB.driver(address, Credentials("admin", "password"), options)
        # A simple authenticated request proves this is a fresh CE server,
        # rather than trying to modify a running server with unrelated auth.
        default_driver.databases.contains(str(spec.get("typedbDatabase") or "orbit_alpha_ontology"))
        if configured_user != "admin":
            default_driver.users.create(configured_user, configured_password)
        if configured_password != "password":
            default_driver.users.get_current().update_password(configured_password)
    except Exception:
        append_log(spec["log"], "credential bootstrap unavailable after fresh TypeDB reset")
        return False
    finally:
        try:
            if default_driver:
                default_driver.close()
        except Exception:
            pass
    if not typedb_driver_ready(spec):
        append_log(spec["log"], "credential bootstrap verification failed")
        return False
    clear_typedb_credentials_bootstrap_pending()
    append_log(spec["log"], "configured TypeDB credentials restored after reset")
    print(str(spec["label"]) + " restored configured TypeDB credentials after reset.")
    return True


def wait_for_typedb_ready(spec: Dict[str, object]) -> bool:
    wait_seconds = int_value(spec.get("startupWaitSeconds"), 600, 0)
    address = spec.get("healthAddress") or spec.get("typedbAddress") or "127.0.0.1:1729"
    if wait_seconds <= 0:
        return True
    deadline = time.monotonic() + wait_seconds
    bootstrap_attempted = False
    while time.monotonic() <= deadline:
        pid = read_pid(spec["pid"])
        if pid and not pid_exists(pid):
            append_log(spec["log"], "not-ready process-exited")
            print(str(spec["label"]) + " did not become ready because the process exited.")
            return False
        if tcp_ready(address):
            if typedb_driver_ready(spec):
                clear_typedb_credentials_bootstrap_pending()
                append_log(spec["log"], "ready " + str(address))
                print(str(spec["label"]) + " ready. address=" + str(address))
                return True
            if not bootstrap_attempted and typedb_credentials_bootstrap_pending():
                bootstrap_attempted = True
                if bootstrap_typedb_credentials_after_reset(spec):
                    continue
        time.sleep(0.5)
    append_log(spec["log"], "not-ready timeout " + str(address))
    print(str(spec["label"]) + " not ready after " + str(wait_seconds) + "s. address=" + str(address))
    return False


def typedb_seed_command(spec: Dict[str, object]) -> List[str]:
    command = [sys.executable, "-u", "python_service/service.py", "ontology", "seed"]
    # This command is only reached after this manager started a fresh TypeDB
    # server and before dependent workers start. A retained lease belongs to a
    # writer connected to the previous server process and is therefore safe to
    # reclaim now; normal live reseeds do not pass this flag.
    command.append("--recover-scoped-write-lease")
    if truthy(spec.get("seedReplaceRuleBox")):
        command.append("--replace-rulebox")
    if truthy(spec.get("seedKeepInference")):
        command.append("--keep-inference")
    return command


def typedb_shared_world_projection_rebuild_command(spec: Dict[str, object]) -> List[str]:
    limit = int_value(spec.get("sharedWorldProjectionRebuildLimit"), 100, 1)
    return [
        sys.executable,
        "-u",
        "python_service/service.py",
        "ontology-world-projection",
        "rebuild",
        "--limit",
        str(limit),
    ]


def append_log_text(path: Path, label: str, text: str) -> None:
    append_log(path, label)
    if not text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")


def ensure_typedb_seeded(spec: Dict[str, object]) -> bool:
    if str(spec.get("role") or "") != "typedb":
        return True
    if not truthy(spec.get("seedOnStart")):
        append_log(spec["log"], "seed skipped")
        print(str(spec["label"]) + " RuleBox seed skipped.")
        return True
    command = typedb_seed_command(spec)
    timeout_seconds = int_value(spec.get("seedTimeoutSeconds"), 180, 1)
    attempts = int_value(spec.get("seedRetryCount"), 2, 0) + 1
    for attempt in range(1, attempts + 1):
        append_log(spec["log"], "seed start attempt=" + str(attempt))
        print(str(spec["label"]) + " seeding ontology RuleBox. attempt=" + str(attempt))
        try:
            result = subprocess.run(
                command,
                cwd=str(ROOT_DIR),
                env=dict(os.environ, PYTHONUNBUFFERED="1"),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            output = (error.stdout or "") + ("\n" if error.stdout and error.stderr else "") + (error.stderr or "")
            append_log_text(spec["log"], "seed timeout attempt=" + str(attempt), output)
            print(str(spec["label"]) + " RuleBox seed timed out after " + str(timeout_seconds) + "s.")
        else:
            output = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
            if result.returncode == 0:
                append_log_text(spec["log"], "seed ok attempt=" + str(attempt), output)
                print(str(spec["label"]) + " RuleBox seed ok.")
                return True
            append_log_text(
                spec["log"],
                "seed failed attempt=" + str(attempt) + " exit=" + str(result.returncode),
                output,
            )
            print(str(spec["label"]) + " RuleBox seed failed. exit=" + str(result.returncode))
        if attempt < attempts:
            time.sleep(1.0)
    print(str(spec["label"]) + " RuleBox seed failed after " + str(attempts) + " attempts.")
    return False


def ensure_typedb_shared_world_projection_rebuilt(spec: Dict[str, object]) -> bool:
    """Restore MySQL-backed shared-world inputs before dependent workers run."""
    if str(spec.get("role") or "") != "typedb" or not typedb_shared_world_projection_rebuild_pending():
        return True
    command = typedb_shared_world_projection_rebuild_command(spec)
    timeout_seconds = int_value(spec.get("sharedWorldProjectionRebuildTimeoutSeconds"), 900, 30)
    append_log(spec["log"], "shared-world rebuild start")
    print(str(spec["label"]) + " rebuilding shared MarketWorld/KnowledgeWorld from durable outbox.")
    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT_DIR),
            env=dict(os.environ, PYTHONUNBUFFERED="1"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + ("\n" if error.stdout and error.stderr else "") + (error.stderr or "")
        append_log_text(spec["log"], "shared-world rebuild timeout", output)
        print(str(spec["label"]) + " shared-world rebuild timed out after " + str(timeout_seconds) + "s.")
        return False
    output = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
    if result.returncode != 0:
        append_log_text(spec["log"], "shared-world rebuild failed exit=" + str(result.returncode), output)
        print(str(spec["label"]) + " shared-world rebuild failed. exit=" + str(result.returncode))
        return False
    append_log_text(spec["log"], "shared-world rebuild ok", output)
    clear_typedb_shared_world_projection_rebuild_pending()
    print(str(spec["label"]) + " shared-world rebuild ok.")
    return True


def recover_typedb_scoped_write_lease_after_worker_restart(spec: Dict[str, object]) -> bool:
    """Defer lease recovery until the affected world actually writes.

    A normal service restart must never scan TypeDB's entire durable lease
    inventory before application workers can start.  That scan is an
    operational maintenance action, while write acquisition can safely
    recover an exact, proven-dead local owner for its own world.  Deferring
    the work keeps a temporary TypeDB planner stall from turning into a full
    service outage.
    """
    append_log(spec["log"], "scoped write lease recovery deferred per-world-acquisition")
    print(str(spec["label"]) + " scoped ABox lease recovery deferred to per-world acquisition.")
    return True


def clear_typedb_rulebox_prewarm_activity() -> bool:
    """Discard a compiler hand-off only after a new TypeDB server is ready.

    A bounded cooldown protects a server-side schema commit after its client
    disconnects.  A managed TypeDB restart terminates that commit, so carrying
    the old hand-off into the new server lifetime would delay both prewarm and
    live reasoning for no safety benefit.
    """
    settings = dict(runtime_settings())
    settings["_skipOperationalHistoryRetention"] = "1"
    settings["_skipOperationalSchemaBootstrap"] = "1"
    try:
        MySQLOntologyRuleboxPrewarmStateStore(settings).replace({
            "status": "idle",
            "active": False,
            "updatedAt": iso_now(),
            "expiresAtEpoch": 0,
            "reason": "typedb-server-restarted",
        })
    except Exception:  # noqa: BLE001 - a stale hint must never fail a healthy graph start.
        return False
    return True


def start_worker(spec: Dict[str, object]) -> int:
    if spec.get("missingReason") or not spec.get("command"):
        print(str(spec["label"]) + " not started. " + str(spec.get("missingReason") or "Command is not configured."))
        return 1 if str(spec.get("role") or "") in {"mysql", "typedb", "web"} else 0
    pid_path = spec["pid"]
    log_path = spec["log"]
    existing = read_pid(pid_path)
    if is_running(existing, spec):
        print(str(spec["label"]) + " already running.")
        if str(spec.get("role") or "") == "typedb":
            if not wait_for_typedb_ready(spec):
                return 1
            # A healthy TypeDB server may be serving an ABox staging write.
            # Seeding is only required after this manager starts a new server;
            # repeating it on every generic worker restart can interrupt that
            # write and needlessly rewrites the static ontology boxes.
        return status_worker(spec)
    if existing:
        remove_pid(pid_path)
    role = str(spec.get("role") or "")
    if role in {"mysql", "web"} and tcp_ready(spec.get("healthAddress")):
        print(str(spec["label"]) + " not started. Canonical address is already owned by an unmanaged process: " + str(spec.get("healthAddress") or ""))
        return 1
    fresh_mysql_data_path = False
    if role == "mysql":
        mysql_data_path = Path(spec.get("dataPath") or "")
        fresh_mysql_data_path = not (mysql_data_path / "mysql").exists()
        if not prepare_mysql_data_dir(spec):
            print(str(spec["label"]) + " data directory initialization failed.")
            return 1
    if str(spec.get("role") or "") == "typedb":
        storage = typedb_storage_preflight(spec)
        if not storage.get("ready"):
            message = "storage preflight blocked. " + str(storage.get("reason") or "TypeDB storage is not ready.")
            append_log(log_path, message)
            print(str(spec["label"]) + " not started. " + message)
            return 1
        data_path = Path(spec.get("dataPath") or "")
        fresh_data_path = not data_path.exists()
        retention = run_typedb_data_retention(spec)
        if retention.get("status") == "reset":
            previous_mb = round(float(retention.get("sizeBytes") or 0) / 1024 / 1024, 1)
            print(str(spec["label"]) + " data reset before start. previousSizeMb=" + str(previous_mb) + " reason=" + str(retention.get("reason") or ""))
        elif retention.get("status") == "maintenance-required":
            previous_mb = round(float(retention.get("sizeBytes") or 0) / 1024 / 1024, 1)
            message = "retention maintenance required; destructive reset blocked. sizeMb=" + str(previous_mb) + " reason=" + str(retention.get("reason") or "")
            append_log(log_path, message)
            print(str(spec["label"]) + " " + message)
        elif fresh_data_path:
            # The first local boot has the same CE credential lifecycle as a
            # retention reset. Mark it before the server creates its files.
            marker = read_typedb_retention_marker()
            marker["credentialsBootstrapPending"] = True
            marker["credentialsBootstrapReason"] = "fresh-data-directory"
            marker["sharedWorldProjectionRebuildPending"] = True
            marker["sharedWorldProjectionRebuildReason"] = "fresh-data-directory"
            write_typedb_retention_marker(marker)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    append_log(log_path, "start")
    out = log_path.open("a", encoding="utf-8")
    process_env = dict(os.environ, PYTHONUNBUFFERED="1")
    process_env.update({str(key): str(value) for key, value in dict(spec.get("env") or {}).items()})
    process = subprocess.Popen(
        spec["command"],
        cwd=str(ROOT_DIR),
        env=process_env,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=out,
        start_new_session=True,
    )
    pid_path.write_text(str(process.pid) + "\n", encoding="utf-8")
    os.chmod(pid_path, 0o600)
    print(str(spec["label"]) + " started. pid=" + str(process.pid))
    print("Log: " + str(log_path))
    if str(spec.get("role") or "") == "typedb":
        if not wait_for_typedb_ready(spec):
            return 1
        if not ensure_typedb_seeded(spec):
            return 1
        if not ensure_typedb_shared_world_projection_rebuilt(spec):
            return 1
        if not clear_typedb_rulebox_prewarm_activity():
            append_log(log_path, "RuleBox compiler activity marker could not be cleared after TypeDB restart")
    elif role in {"mysql", "web"}:
        if not wait_for_tcp_service(spec):
            print(str(spec["label"]) + " did not become ready at " + str(spec.get("healthAddress") or ""))
            return 1
        if role == "mysql" and fresh_mysql_data_path and not ensure_mysql_runtime_application_user(spec):
            print(str(spec["label"]) + " application user provisioning failed after fresh initialization.")
            return 1
        if role == "mysql" and not ensure_mysql_operational_schema(spec):
            print(str(spec["label"]) + " operational schema bootstrap failed.")
            return 1
    return 0


def stop_worker(spec: Dict[str, object]) -> int:
    pid_path = spec["pid"]
    log_path = spec["log"]
    pid = read_pid(pid_path)
    if not pid:
        print(str(spec["label"]) + " is not running.")
        return 0
    if not is_running(pid, spec):
        remove_pid(pid_path)
        print(str(spec["label"]) + " was not running. Removed stale pid file.")
        return 0
    os.kill(pid, signal.SIGTERM)
    attempts = 150 if str(spec.get("role") or "") in {"mysql", "typedb"} else 25
    for _index in range(attempts):
        time.sleep(0.2)
        if not is_running(pid, spec):
            remove_pid(pid_path)
            append_log(log_path, "stop")
            print(str(spec["label"]) + " stopped. pid=" + str(pid))
            return 0
    os.kill(pid, signal.SIGKILL)
    remove_pid(pid_path)
    append_log(log_path, "kill")
    print(str(spec["label"]) + " killed. pid=" + str(pid))
    return 0


def status() -> int:
    for spec in worker_specs().values():
        status_worker(spec)
    return 0


def start(excluded_roles=None) -> int:
    excluded = {str(role or "").strip() for role in (excluded_roles or set())}
    for spec in worker_specs().values():
        if str(spec.get("role") or "").strip() in excluded:
            continue
        result = start_worker(spec)
        if result != 0:
            print("Service start aborted before dependent workers. failed=" + str(spec.get("label") or "unknown"))
            return result
    return 0


def stop(excluded_roles=None, include_supervisor: bool = True) -> int:
    if include_supervisor:
        stop_supervisor()
    excluded = {str(role or "").strip() for role in (excluded_roles or set())}
    for spec in reversed(list(worker_specs().values())):
        if str(spec.get("role") or "").strip() in excluded:
            continue
        stop_worker(spec)
    return 0


def restart(restart_typedb: bool = False, restart_mysql: bool = False) -> int:
    """Restart application workers without disrupting an active graph store.

    TypeDB owns durable graph generations and can legitimately be writing an
    ABox for longer than a web or worker restart. Preserve it for the normal
    restart path; explicit infrastructure maintenance can opt in to a full
    TypeDB restart and seed.
    """
    excluded = set()
    if not restart_typedb:
        typedb_spec = worker_specs().get("typedb")
        typedb_pid_path = typedb_spec.get("pid") if isinstance(typedb_spec, dict) else None
        if typedb_spec and typedb_pid_path and is_running(read_pid(typedb_pid_path), typedb_spec):
            excluded.add("typedb")
    if not restart_mysql:
        mysql_spec = worker_specs().get("mysql")
        mysql_pid_path = mysql_spec.get("pid") if isinstance(mysql_spec, dict) else None
        if mysql_spec and mysql_pid_path and is_running(read_pid(mysql_pid_path), mysql_spec):
            excluded.add("mysql")
    maintenance_window_seconds = 300
    if restart_typedb:
        typedb_spec = worker_specs().get("typedb")
        if isinstance(typedb_spec, dict):
            maintenance_window_seconds = typedb_restart_maintenance_window_seconds(typedb_spec)
    pause_supervisor = supervisor_running()
    if pause_supervisor:
        if restart_typedb:
            maintenance_token = begin_supervisor_maintenance(
                "restart",
                max_age_seconds=maintenance_window_seconds,
            )
        else:
            maintenance_token = begin_supervisor_maintenance("restart")
        if not wait_for_supervisor_maintenance_ack(maintenance_token):
            end_supervisor_maintenance()
            print("Service restart aborted because the supervisor did not acknowledge maintenance mode.")
            return 1
    try:
        stop(excluded_roles=excluded, include_supervisor=False)
        if "typedb" in excluded:
            typedb_spec = worker_specs().get("typedb")
            if isinstance(typedb_spec, dict):
                recover_typedb_scoped_write_lease_after_worker_restart(typedb_spec)
        return start(excluded_roles=excluded)
    finally:
        if pause_supervisor:
            end_supervisor_maintenance()


def supervisor_pid_path() -> Path:
    return data_dir() / "python-supervisor.pid"


def supervisor_log_path() -> Path:
    return data_dir() / "python-supervisor.log"


def supervisor_maintenance_path() -> Path:
    return data_dir() / "python-supervisor-maintenance.json"


def write_supervisor_maintenance_payload(payload: Dict[str, object]) -> None:
    path = supervisor_maintenance_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_supervisor_maintenance_payload() -> Dict[str, object]:
    try:
        payload = json.loads(supervisor_maintenance_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def typedb_restart_maintenance_window_seconds(spec: Dict[str, object]) -> int:
    """Bound the supervisor pause to the complete managed TypeDB restart path."""
    startup_seconds = int_value(spec.get("startupWaitSeconds"), 600, 0)
    seed_attempts = int_value(spec.get("seedRetryCount"), 2, 0) + 1
    seed_seconds = int_value(spec.get("seedTimeoutSeconds"), 180, 1) * seed_attempts
    rebuild_seconds = int_value(spec.get("sharedWorldProjectionRebuildTimeoutSeconds"), 900, 0)
    # Keep the fallback recovery bounded even if a local setting is malformed.
    return min(3600, max(300, startup_seconds + seed_seconds + rebuild_seconds + 60))


def begin_supervisor_maintenance(reason: str, max_age_seconds: int = 300) -> str:
    token = str(os.getpid()) + "-" + str(time.time_ns())
    duration_seconds = max(30, int(max_age_seconds or 300))
    write_supervisor_maintenance_payload({
        "pid": os.getpid(),
        "token": token,
        "reason": str(reason or "maintenance"),
        "startedAt": iso_now(),
        "expiresAtEpoch": time.time() + duration_seconds,
    })
    return token


def acknowledge_supervisor_maintenance() -> None:
    payload = read_supervisor_maintenance_payload()
    token = str(payload.get("token") or "")
    if not token or int(payload.get("acknowledgedByPid") or 0) == os.getpid():
        return
    payload["acknowledgedByPid"] = os.getpid()
    payload["acknowledgedAt"] = iso_now()
    write_supervisor_maintenance_payload(payload)


def wait_for_supervisor_maintenance_ack(token: str, timeout_seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds or 10.0))
    supervisor_pid = read_pid(supervisor_pid_path())
    while time.monotonic() <= deadline:
        payload = read_supervisor_maintenance_payload()
        if (
            str(payload.get("token") or "") == str(token or "")
            and int(payload.get("acknowledgedByPid") or 0) == supervisor_pid
        ):
            return True
        if supervisor_pid and not pid_exists(supervisor_pid):
            return True
        time.sleep(0.1)
    return False


def end_supervisor_maintenance() -> None:
    remove_pid(supervisor_maintenance_path())


def supervisor_maintenance_active(max_age_seconds: int = 300) -> bool:
    path = supervisor_maintenance_path()
    payload = read_supervisor_maintenance_payload()
    if not payload:
        return False
    try:
        expires_at = float(payload.get("expiresAtEpoch") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    if expires_at > 0:
        owner_pid = int_value(payload.get("pid"), 0, 0)
        if owner_pid and not pid_exists(owner_pid):
            remove_pid(path)
            append_log(supervisor_log_path(), "removed orphaned maintenance marker")
            return False
        if time.time() <= expires_at:
            return True
        remove_pid(path)
        append_log(supervisor_log_path(), "removed expired maintenance marker")
        return False
    try:
        age_seconds = max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return False
    if age_seconds <= max(30, int(max_age_seconds or 300)):
        return True
    remove_pid(path)
    append_log(supervisor_log_path(), "removed stale maintenance marker")
    return False


def supervisor_running() -> bool:
    pid = read_pid(supervisor_pid_path())
    return bool(pid and pid_exists(pid) and "monitor_service.py supervise" in command_for_pid(pid))


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.orbitalpha.services.plist"


def bootout_supervisor_launch_agent() -> None:
    path = launch_agent_path()
    launchctl = shutil.which("launchctl")
    if not launchctl or not path.exists():
        return
    domain = "gui/" + str(os.getuid())
    subprocess.run([launchctl, "bootout", domain, str(path)], capture_output=True, text=True)


def stop_supervisor() -> None:
    # KeepAlive would immediately relaunch the supervisor unless launchd is
    # detached before honoring an explicit service stop.
    bootout_supervisor_launch_agent()
    pid = read_pid(supervisor_pid_path())
    if not pid or pid == os.getpid():
        return
    if "monitor_service.py supervise" not in command_for_pid(pid):
        remove_pid(supervisor_pid_path())
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        remove_pid(supervisor_pid_path())
        return
    for _index in range(900):
        if not pid_exists(pid):
            remove_pid(supervisor_pid_path())
            return
        time.sleep(0.2)


def supervise() -> int:
    if supervisor_running() and read_pid(supervisor_pid_path()) != os.getpid():
        print("Orbit Alpha supervisor is already running.")
        return 0
    supervisor_pid_path().parent.mkdir(parents=True, exist_ok=True)
    supervisor_pid_path().write_text(str(os.getpid()) + "\n", encoding="utf-8")
    os.chmod(supervisor_pid_path(), 0o600)
    append_log(supervisor_log_path(), "start")
    stopping = {"value": False}

    def request_stop(_signum, _frame):
        stopping["value"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        if start() != 0:
            return 1
        last_maintenance_at = 0.0
        last_typedb_capacity_notice = ""
        last_typedb_auto_rotation_notice = ""
        while not stopping["value"]:
            if supervisor_maintenance_active():
                acknowledge_supervisor_maintenance()
                time.sleep(1)
                continue
            specs = worker_specs()
            for spec in specs.values():
                if stopping["value"]:
                    break
                if supervisor_maintenance_active():
                    acknowledge_supervisor_maintenance()
                    break
                pid = read_pid(spec["pid"])
                if not is_running(pid, spec):
                    append_log(supervisor_log_path(), "restart " + str(spec.get("label") or "unknown"))
                    start_worker(spec)
            if time.monotonic() - last_maintenance_at >= 60:
                typedb_spec = specs.get("typedb")
                decision = typedb_reset_needed(typedb_spec, ignore_auto_reset=True) if typedb_spec else {}
                automatic = typedb_auto_rotation_needed(typedb_spec) if typedb_spec else {}
                if automatic.get("needed"):
                    recovery = typedb_auto_rotation_recovery_preflight(specs)
                    if bool(recovery.get("ready")):
                        notice = str(automatic.get("reason") or "TypeDB automatic capacity rotation")
                        append_log(supervisor_log_path(), "typedb automatic rotation starting. " + notice)
                        incident = record_typedb_auto_rotation_incident(typedb_spec, automatic)
                        if not bool(incident.get("recorded")):
                            append_log(
                                supervisor_log_path(),
                                "typedb automatic rotation incident record failed. "
                                + str(incident.get("reason") or "unknown"),
                            )
                        result = typedb_rotate(
                            force=True,
                            supervisor_owned=True,
                            rotation_reason=notice,
                        )
                        outcome = "completed" if result == 0 else "failed"
                        append_log(supervisor_log_path(), "typedb automatic rotation " + outcome + ". " + notice)
                        last_typedb_auto_rotation_notice = outcome + "|" + notice
                        last_maintenance_at = time.monotonic()
                        continue
                    notice = str(automatic.get("reason") or "TypeDB automatic capacity rotation")
                    recovery_reason = str(recovery.get("reason") or "MySQL recovery source is unavailable")
                    combined = notice + " | " + recovery_reason
                    if combined != last_typedb_auto_rotation_notice:
                        append_log(
                            supervisor_log_path(),
                            "typedb automatic rotation deferred; " + combined,
                        )
                        last_typedb_auto_rotation_notice = combined
                elif str(automatic.get("reason") or "") != "below-threshold":
                    last_typedb_auto_rotation_notice = ""
                if decision.get("needed"):
                    notice = str(decision.get("reason") or "TypeDB storage capacity reached")
                    if notice != last_typedb_capacity_notice:
                        append_log(
                            supervisor_log_path(),
                            "typedb storage maintenance required; controlled rotation not yet run. " + notice,
                        )
                        last_typedb_capacity_notice = notice
                else:
                    last_typedb_capacity_notice = ""
                last_maintenance_at = time.monotonic()
            time.sleep(5)
    finally:
        stop(include_supervisor=False)
        remove_pid(supervisor_pid_path())
        append_log(supervisor_log_path(), "stop")
    return 0


def install_supervisor() -> int:
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": "com.orbitalpha.services",
        "ProgramArguments": [sys.executable, str(ROOT_DIR / "python_service" / "monitor_service.py"), "supervise"],
        "WorkingDirectory": str(ROOT_DIR),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ExitTimeOut": 180,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(supervisor_log_path()),
        "StandardErrorPath": str(supervisor_log_path()),
    }
    with path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    os.chmod(path, 0o600)
    domain = "gui/" + str(os.getuid())
    subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True, text=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        print("LaunchAgent install failed: " + str(result.stderr or result.stdout).strip())
        return result.returncode
    # RunAtLoad normally starts the service during bootstrap. A non-destructive
    # kickstart covers the narrow case where launchd has not scheduled it yet;
    # ``-k`` would kill that first supervisor and interrupt healthy workers.
    subprocess.run(["launchctl", "kickstart", domain + "/com.orbitalpha.services"], capture_output=True, text=True)
    print("Orbit Alpha supervisor installed: " + str(path))
    return 0


def uninstall_supervisor() -> int:
    path = launch_agent_path()
    stop_supervisor()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    print("Orbit Alpha supervisor uninstalled.")
    return 0


def typedb_maintenance(force: bool = False) -> int:
    specs = worker_specs()
    spec = specs.get("typedb")
    if not spec:
        print("TypeDB maintenance skipped. TypeDB worker is not configured.")
        return 0
    pid = read_pid(spec["pid"])
    if is_running(pid, spec):
        print("TypeDB maintenance skipped. Stop TypeDB first or run restart so the data directory is not modified while TypeDB is running.")
        return 0
    result = run_typedb_data_retention(spec, force=force)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def typedb_rotate(
    force: bool = False,
    supervisor_owned: bool = False,
    rotation_reason: str = "",
) -> int:
    """Safely rebuild an oversized TypeDB store and restart all dependents.

    This is intentionally the only automated-manager path that removes the
    TypeDB data directory.  It pauses the supervisor, stops project-managed
    workers that may hold graph connections, records the reset marker, then
    lets the normal startup path seed the RuleBox and rebuild durable shared
    worlds from MySQL before inference workers resume.
    """

    specs = worker_specs()
    spec = specs.get("typedb")
    if not spec:
        print(json.dumps({"status": "skipped", "reason": "TypeDB worker is not configured."}, ensure_ascii=False))
        return 0
    decision = typedb_reset_needed(spec, ignore_auto_reset=True)
    if not force and not decision.get("needed"):
        print(json.dumps({"status": "skipped", **decision}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    rotation_lock = acquire_typedb_rotation_lock()
    if not bool(rotation_lock.get("acquired")):
        print(json.dumps({"status": "skipped", "reason": rotation_lock.get("reason") or "rotation lock unavailable"}, ensure_ascii=False))
        return 0

    pause_supervisor = supervisor_running() and not supervisor_owned
    maintenance_token = ""
    if supervisor_owned:
        maintenance_token = begin_supervisor_maintenance(
            "typedb-auto-rotate",
            max_age_seconds=typedb_restart_maintenance_window_seconds(spec),
        )
    elif pause_supervisor:
        maintenance_token = begin_supervisor_maintenance(
            "typedb-rotate",
            max_age_seconds=typedb_restart_maintenance_window_seconds(spec),
        )
        if not wait_for_supervisor_maintenance_ack(maintenance_token):
            end_supervisor_maintenance()
            release_typedb_rotation_lock(rotation_lock)
            print("TypeDB rotation aborted because the supervisor did not acknowledge maintenance mode.")
            return 1

    result = {}
    try:
        if supervisor_owned:
            record_typedb_auto_rotation_state(
                lastAutoRotationAttemptAt=iso_now(),
                lastAutoRotationAttemptEpoch=time.time(),
                lastAutoRotationReason=str(rotation_reason or decision.get("reason") or "capacity"),
                lastAutoRotationStatus="running",
            )
        stop(include_supervisor=False)
        result = run_typedb_data_retention(spec, force=True)
        if result.get("status") != "reset":
            if supervisor_owned:
                record_typedb_auto_rotation_state(
                    lastAutoRotationFinishedAt=iso_now(),
                    lastAutoRotationStatus="reset-failed",
                    lastAutoRotationResult=dict(result),
                )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 1
        start_status = start()
        result["restartStatus"] = "ok" if start_status == 0 else "failed"
        if supervisor_owned:
            record_typedb_auto_rotation_state(
                lastAutoRotationFinishedAt=iso_now(),
                lastAutoRotationStatus="ok" if start_status == 0 else "restart-failed",
                lastAutoRotationResult={
                    "status": result.get("status"),
                    "restartStatus": result.get("restartStatus"),
                    "previousSizeBytes": result.get("previousSizeBytes"),
                },
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return start_status
    finally:
        if pause_supervisor or supervisor_owned:
            end_supervisor_maintenance()
        release_typedb_rotation_lock(rotation_lock)


def main(argv: List[str] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    command = args[0] if args else "status"
    if command == "start":
        return start()
    if command == "stop":
        return stop()
    if command == "restart":
        return restart(
            restart_typedb="--restart-typedb" in args[1:],
            restart_mysql="--restart-mysql" in args[1:],
        )
    if command == "status":
        return status()
    if command == "typedb-maintenance":
        return typedb_maintenance(force="--force" in args[1:])
    if command == "typedb-rotate":
        return typedb_rotate(force="--force" in args[1:])
    if command == "supervise":
        return supervise()
    if command == "supervisor-install":
        return install_supervisor()
    if command == "supervisor-uninstall":
        return uninstall_supervisor()
    print("Usage: python3 python_service/monitor_service.py start|stop|restart|status|supervise|supervisor-install|supervisor-uninstall|typedb-maintenance|typedb-rotate [--force]")
    return 1
