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

from .infrastructure.settings import ROOT_DIR, data_dir, runtime_settings


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
    allow_weak_password = truthy(os.environ.get("TYPEDB_ALLOW_DEFAULT_PASSWORD"))
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
        "autoResetEnabled": str((settings or {}).get("typedbAutoResetEnabled") or "1"),
        "ageResetEnabled": str((settings or {}).get("typedbAgeResetEnabled") or "0"),
        "healthAddress": address,
        "typedbUser": str((settings or {}).get("typedbUser") or os.environ.get("TYPEDB_USER") or "admin"),
        "typedbPassword": password,
        "typedbDatabase": str((settings or {}).get("typedbDatabase") or os.environ.get("TYPEDB_DATABASE") or "orbit_alpha_ontology"),
        "typedbTlsEnabled": str((settings or {}).get("typedbTlsEnabled") or os.environ.get("TYPEDB_TLS_ENABLED") or "0"),
        "startupWaitSeconds": str((settings or {}).get("typedbStartupWaitSeconds") or "60"),
        "seedOnStart": str((settings or {}).get("typedbSeedOnStart") or os.environ.get("TYPEDB_SEED_ON_START") or "1"),
        "seedReplaceRuleBox": str((settings or {}).get("typedbSeedReplaceRuleBox") or os.environ.get("TYPEDB_SEED_REPLACE_RULEBOX") or "1"),
        "seedKeepInference": str((settings or {}).get("typedbSeedKeepInference") or os.environ.get("TYPEDB_SEED_KEEP_INFERENCE") or "1"),
        "seedTimeoutSeconds": str((settings or {}).get("typedbSeedTimeoutSeconds") or os.environ.get("TYPEDB_SEED_TIMEOUT_SECONDS") or "360"),
        "seedRetryCount": str((settings or {}).get("typedbSeedRetryCount") or os.environ.get("TYPEDB_SEED_RETRY_COUNT") or "2"),
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
    data_path = Path(str(os.environ.get("MYSQL_DATA_DIR") or data_dir() / "mysql-runtime"))
    port = int_value(os.environ.get("MYSQL_PORT") or (settings or {}).get("mysqlPort"), 3306, 1)
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
        "--innodb-redo-log-capacity=1073741824",
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
        "missingReason": "" if command else "MySQL executable was not found. Set MYSQLD_COMMAND.",
    }


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
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() or item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


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


def typedb_reset_needed(spec: Dict[str, object]) -> Dict[str, object]:
    data_path = Path(spec.get("dataPath") or "")
    enabled = truthy(spec.get("autoResetEnabled"))
    age_reset_enabled = truthy(spec.get("ageResetEnabled")) if spec.get("ageResetEnabled") not in (None, "") else False
    retention_hours = int_value(spec.get("retentionHours"), 24, 1)
    max_size_mb = int_value(spec.get("maxSizeMb"), 2048, 1)
    size_bytes = directory_size_bytes(data_path)
    marker = read_typedb_retention_marker()
    age_hours = typedb_data_age_hours(data_path, marker)
    reasons = []
    if not enabled:
        return {"needed": False, "reason": "disabled", "sizeBytes": size_bytes, "ageHours": age_hours}
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
    }


def run_typedb_data_retention(spec: Dict[str, object], force: bool = False) -> Dict[str, object]:
    if str(spec.get("role") or "") != "typedb":
        return {"status": "skipped", "reason": "not typedb"}
    data_path = Path(spec.get("dataPath") or "")
    decision = typedb_reset_needed(spec)
    if not force and not decision.get("needed"):
        return {"status": "skipped", **decision}
    if data_path.exists():
        shutil.rmtree(data_path)
    write_typedb_retention_marker({
        "lastResetAt": iso_now(),
        "reason": "forced" if force else decision.get("reason", ""),
        "previousSizeBytes": int(decision.get("sizeBytes") or 0),
        "retentionHours": int(decision.get("retentionHours") or int_value(spec.get("retentionHours"), 24, 1)),
        "maxSizeMb": int(decision.get("maxSizeMb") or int_value(spec.get("maxSizeMb"), 2048, 1)),
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


def typedb_driver_ready(spec: Dict[str, object]) -> bool:
    """Verify TypeDB accepts authenticated driver requests, not only TCP."""
    try:
        from typedb.driver import Credentials, DriverOptions, DriverTlsConfig, TypeDB
    except Exception:
        # The seed process performs the definitive driver check. Retain the
        # socket check when the optional driver is not importable here.
        return True
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


def wait_for_typedb_ready(spec: Dict[str, object]) -> bool:
    wait_seconds = int_value(spec.get("startupWaitSeconds"), 60, 0)
    address = spec.get("healthAddress") or spec.get("typedbAddress") or "127.0.0.1:1729"
    if wait_seconds <= 0:
        return True
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() <= deadline:
        pid = read_pid(spec["pid"])
        if pid and not pid_exists(pid):
            append_log(spec["log"], "not-ready process-exited")
            print(str(spec["label"]) + " did not become ready because the process exited.")
            return False
        if tcp_ready(address) and typedb_driver_ready(spec):
            append_log(spec["log"], "ready " + str(address))
            print(str(spec["label"]) + " ready. address=" + str(address))
            return True
        time.sleep(0.5)
    append_log(spec["log"], "not-ready timeout " + str(address))
    print(str(spec["label"]) + " not ready after " + str(wait_seconds) + "s. address=" + str(address))
    return False


def typedb_seed_command(spec: Dict[str, object]) -> List[str]:
    command = [sys.executable, "-u", "python_service/service.py", "ontology", "seed"]
    if truthy(spec.get("seedReplaceRuleBox")):
        command.append("--replace-rulebox")
    if truthy(spec.get("seedKeepInference")):
        command.append("--keep-inference")
    return command


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
    if role == "mysql" and not prepare_mysql_data_dir(spec):
        print(str(spec["label"]) + " data directory initialization failed.")
        return 1
    if str(spec.get("role") or "") == "typedb":
        retention = run_typedb_data_retention(spec)
        if retention.get("status") == "reset":
            previous_mb = round(float(retention.get("sizeBytes") or 0) / 1024 / 1024, 1)
            print(str(spec["label"]) + " data reset before start. previousSizeMb=" + str(previous_mb) + " reason=" + str(retention.get("reason") or ""))
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
    elif role in {"mysql", "web"}:
        if not wait_for_tcp_service(spec):
            print(str(spec["label"]) + " did not become ready at " + str(spec.get("healthAddress") or ""))
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
    pause_supervisor = supervisor_running()
    if pause_supervisor:
        begin_supervisor_maintenance("restart")
    try:
        stop(excluded_roles=excluded, include_supervisor=False)
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


def begin_supervisor_maintenance(reason: str) -> None:
    path = supervisor_maintenance_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pid": os.getpid(),
        "reason": str(reason or "maintenance"),
        "startedAt": iso_now(),
    }, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.chmod(path, 0o600)


def end_supervisor_maintenance() -> None:
    remove_pid(supervisor_maintenance_path())


def supervisor_maintenance_active(max_age_seconds: int = 300) -> bool:
    path = supervisor_maintenance_path()
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
        while not stopping["value"]:
            if supervisor_maintenance_active():
                time.sleep(1)
                continue
            specs = worker_specs()
            for spec in specs.values():
                if stopping["value"]:
                    break
                pid = read_pid(spec["pid"])
                if not is_running(pid, spec):
                    append_log(supervisor_log_path(), "restart " + str(spec.get("label") or "unknown"))
                    start_worker(spec)
            if time.monotonic() - last_maintenance_at >= 60:
                typedb_spec = specs.get("typedb")
                if typedb_spec and typedb_reset_needed(typedb_spec).get("needed"):
                    append_log(supervisor_log_path(), "typedb storage limit maintenance")
                    stop_worker(typedb_spec)
                    run_typedb_data_retention(typedb_spec)
                    start_worker(typedb_spec)
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
    if command == "supervise":
        return supervise()
    if command == "supervisor-install":
        return install_supervisor()
    if command == "supervisor-uninstall":
        return uninstall_supervisor()
    print("Usage: python3 python_service/monitor_service.py start|stop|restart|status|supervise|supervisor-install|supervisor-uninstall|typedb-maintenance [--force]")
    return 1
