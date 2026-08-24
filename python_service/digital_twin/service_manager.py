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
import uuid
from pathlib import Path
from typing import Dict, List

from .infrastructure.mysql_monitoring import mysql_settings
from .infrastructure.mysql_monitoring import MySQLMonitorAccountJobStore
from .infrastructure.mysql_operational import MySQLOntologyRuleboxPrewarmStateStore
from .infrastructure.mysql_operational_connection import MySQLOperationalConnection
from .infrastructure.settings import ROOT_DIR, data_dir, runtime_settings
from .infrastructure.share_runtime import fixed_entry_url, share_credentials_environment
from .infrastructure.typedb_storage_guard import typedb_storage_health, typedb_storage_inventory
from .infrastructure.operational_storage_guard import storage_directory_physical_size_bytes


BASE_WORKERS = {
    "external-data": {
        "label": "Python external data collector",
        "pid": data_dir() / "python-external-data.pid",
        "log": data_dir() / "python-external-data.log",
        "command": [sys.executable, "-u", "python_service/service.py", "external-data", "watch"],
        "needle": "python_service/service.py external-data watch",
    },
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
    "reasoning-engine-delivery": {
        "label": "Python V2 delivery reasoning worker",
        "pid": data_dir() / "python-reasoning-delivery.pid",
        "log": data_dir() / "python-reasoning-delivery.log",
        "command": [
            sys.executable, "-u", "python_service/service.py", "reasoning-engine",
            "v2-watch", "--role", "delivery", "--worker-id", "delivery",
        ],
        "needle": "python_service/service.py reasoning-engine v2-watch --role delivery",
    },
    "reasoning-engine-shadow": {
        "label": "Python V2 candidate reasoning worker",
        "pid": data_dir() / "python-reasoning-shadow.pid",
        "log": data_dir() / "python-reasoning-shadow.log",
        "command": [
            sys.executable, "-u", "python_service/service.py", "reasoning-engine",
            "v2-watch", "--role", "candidate", "--worker-id", "candidate",
        ],
        "needle": "python_service/service.py reasoning-engine v2-watch --role candidate",
        "needles": [
            "python_service/service.py reasoning-engine v2-watch --role candidate",
            "python_service/service.py reasoning-engine v2-watch",
            "python_service/service.py reasoning-engine shadow-watch",
        ],
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
        "label": "Python notification delivery worker",
        "pid": data_dir() / "python-notifications.pid",
        "log": data_dir() / "python-notifications.log",
        "command": [sys.executable, "-u", "python_service/service.py", "notifications", "watch"],
        # The generic needle also recognizes the pre-lane worker during the
        # first upgrade restart so it is terminated instead of orphaned.
        "needle": "python_service/service.py notifications watch",
    },
    "operational-maintenance": {
        "label": "Python operational history maintenance worker",
        "pid": data_dir() / "python-operational-maintenance.pid",
        "log": data_dir() / "python-operational-maintenance.log",
        "command": [sys.executable, "-u", "python_service/service.py", "maintenance", "watch"],
        "needle": "python_service/service.py maintenance watch",
    },
    "time-series-projection": {
        "label": "Python time-series backend projection worker",
        "pid": data_dir() / "python-time-series-projection.pid",
        "log": data_dir() / "python-time-series-projection.log",
        "command": [sys.executable, "-u", "python_service/service.py", "time-series-platform", "watch"],
        "needle": "python_service/service.py time-series-platform watch",
    },
}

MAX_NOTIFICATION_AI_WORKERS = 8


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


def managed_executable(name: str, explicit: object = "") -> str:
    """Resolve a managed binary even under launchd's minimal PATH."""

    configured = str(explicit or "").strip()
    candidates = [
        configured,
        shutil.which(str(name or "")) or "",
        "/usr/local/bin/" + str(name or ""),
        "/opt/homebrew/bin/" + str(name or ""),
        "/usr/bin/" + str(name or ""),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return ""


def source_revision() -> str:
    """Return the code revision that a managed process must keep for its lifetime."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(ROOT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return str(result.stdout or "").strip() or "unknown"


def managed_process_environment(spec: Dict[str, object] = None) -> Dict[str, str]:
    environment = dict(os.environ, PYTHONUNBUFFERED="1")
    environment.update({str(key): str(value) for key, value in dict((spec or {}).get("env") or {}).items()})
    environment["ORBIT_RUNTIME_REVISION"] = source_revision()
    environment.setdefault("ORBIT_RUNTIME_VERSION", "local-managed")
    environment.setdefault("ORBIT_RUNTIME_ENV", "production")
    return environment


def typedb_worker_spec(settings: Dict[str, object]) -> Dict[str, object]:
    executable = typedb_executable()
    address = str((settings or {}).get("typedbAddress") or "127.0.0.1:1729").strip() or "127.0.0.1:1729"
    configured_data_path = str((settings or {}).get("typedbDataPath") or "").strip()
    data_path = Path(configured_data_path).expanduser() if configured_data_path else data_dir() / "typedb-data"
    if not data_path.is_absolute():
        data_path = ROOT_DIR / data_path
    log_dir = data_dir() / "typedb-logs"
    http_address = str((settings or {}).get("typedbHttpAddress") or "127.0.0.1:8000").strip() or "127.0.0.1:8000"
    password = str((settings or {}).get("typedbPassword") or os.environ.get("TYPEDB_PASSWORD") or "").strip()
    allow_weak_password = truthy(
        os.environ.get("TYPEDB_ALLOW_DEFAULT_PASSWORD")
        or (settings or {}).get("typedbAllowDefaultPassword")
    )
    weak_password = password.lower() in {"", "admin", "password", "typedb"}
    primary_database = str(
        (settings or {}).get("typedbDatabase")
        or os.environ.get("TYPEDB_DATABASE")
        or "orbit_alpha_ontology"
    ).strip() or "orbit_alpha_ontology"
    compatibility_databases_enabled = truthy(
        (settings or {}).get("typedbBlueGreenSeedCompatibilityDatabasesEnabled")
    )
    database_candidates = [primary_database]
    if compatibility_databases_enabled:
        database_candidates.extend([
            (settings or {}).get("reasoningEngineV1TypeDbDatabase"),
            (settings or {}).get("reasoningEngineV2TypeDbDatabase"),
            (settings or {}).get("reasoningEngineShadowTypeDbDatabase"),
        ])
    managed_databases = []
    for database in database_candidates:
        clean = str(database or "").strip()
        if clean and clean not in managed_databases:
            managed_databases.append(clean)
    command = [
        executable,
        "server",
        "--server.listen-address",
        address,
        "--server.advertise-address",
        address,
        "--server.http.listen-address",
        http_address,
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
        "retentionHours": str((settings or {}).get("typedbDataRetentionHours") or "72"),
        "maxSizeMb": str((settings or {}).get("typedbDataMaxSizeMb") or "16384"),
        "minimumFreeSpaceMb": str(max(
            int_value((settings or {}).get("typedbMinimumFreeSpaceMb"), 4096, 1),
            int_value((settings or {}).get("operationalMinimumFreeSpaceMb"), 12288, 1),
        )),
        # TypeDB is the durable ontology store. Capacity pressure must surface
        # as an explicit, source-verified rotation rather than an arbitrary
        # worker restart. The active graph is rebuildable from MySQL.
        "autoResetEnabled": str((settings or {}).get("typedbAutoResetEnabled") or "0"),
        "autoRotationEnabled": str((settings or {}).get("typedbCapacityAutoRotateEnabled") or "1"),
        "autoRotationPercent": str((settings or {}).get("typedbCapacityAutoRotatePercent") or "80"),
        "autoRotationWalMb": str((settings or {}).get("typedbCapacityAutoRotateWalMb") or "4096"),
        "autoRotationFreeSpaceMb": str(
            (settings or {}).get("typedbCapacityAutoRotateFreeSpaceMb") or "24576"
        ),
        "autoRotationCooldownMinutes": str(
            (settings or {}).get("typedbCapacityAutoRotateCooldownMinutes") or "60"
        ),
        "autoRotationFailureRetrySeconds": str(
            (settings or {}).get("typedbCapacityAutoRotateFailureRetrySeconds") or "300"
        ),
        "blueGreenRotationEnabled": str(
            (settings or {}).get("typedbBlueGreenRotationEnabled") or "1"
        ),
        "blueGreenStagePortOffset": str(
            (settings or {}).get("typedbBlueGreenStagePortOffset") or "1"
        ),
        "blueGreenRetiredRetentionMinutes": str(
            (settings or {}).get("typedbBlueGreenRetiredRetentionMinutes") or "120"
        ),
        "blueGreenMinimumHeadroomMb": str(
            (settings or {}).get("typedbBlueGreenMinimumHeadroomMb") or "12288"
        ),
        "blueGreenEstimatedCandidateMaxMb": str(
            (settings or {}).get("typedbBlueGreenEstimatedCandidateMaxMb") or "4096"
        ),
        "blueGreenResourceGuardEnabled": str(
            (settings or {}).get("typedbBlueGreenResourceGuardEnabled") or "1"
        ),
        "blueGreenMaxLoadPerCpu": str(
            (settings or {}).get("typedbBlueGreenMaxLoadPerCpu") or "1.25"
        ),
        "blueGreenMinimumAvailableMemoryPercent": str(
            (settings or {}).get("typedbBlueGreenMinimumAvailableMemoryPercent") or "15"
        ),
        "blueGreenProcessNice": str(
            (settings or {}).get("typedbBlueGreenProcessNice") or "10"
        ),
        "processNice": str((settings or {}).get("typedbProcessNice") or "5"),
        "blueGreenSeedCompatibilityDatabasesEnabled": (
            "1" if compatibility_databases_enabled else "0"
        ),
        "ageResetEnabled": str((settings or {}).get("typedbAgeResetEnabled") or "0"),
        "healthAddress": address,
        "httpAddress": http_address,
        "typedbUser": str((settings or {}).get("typedbUser") or os.environ.get("TYPEDB_USER") or "admin"),
        "typedbPassword": password,
        "typedbDatabase": primary_database,
        "managedTypeDbDatabases": managed_databases,
        "typedbTlsEnabled": str((settings or {}).get("typedbTlsEnabled") or os.environ.get("TYPEDB_TLS_ENABLED") or "0"),
        # A durable TypeDB may need several minutes to replay its WAL and
        # rebuild the type cache after a clean server restart.  Treating that
        # normal recovery as a 60-second failure leaves every dependent worker
        # down and turns a recoverable restart into a reasoning backlog.
        "startupWaitSeconds": str((settings or {}).get("typedbStartupWaitSeconds") or "1800"),
        "seedOnStart": str((settings or {}).get("typedbSeedOnStart") or os.environ.get("TYPEDB_SEED_ON_START") or "1"),
        "seedReplaceRuleBox": str((settings or {}).get("typedbSeedReplaceRuleBox") or os.environ.get("TYPEDB_SEED_REPLACE_RULEBOX") or "1"),
        "seedKeepInference": str((settings or {}).get("typedbSeedKeepInference") or os.environ.get("TYPEDB_SEED_KEEP_INFERENCE") or "1"),
        "schemaFunctionDirectQueryFallbackEnabled": str(
            (settings or {}).get("typedbNativeRuleDirectQueryFallbackEnabled")
            or os.environ.get("TYPEDB_NATIVE_RULE_DIRECT_QUERY_FALLBACK_ENABLED")
            or "0"
        ),
        # A fresh TypeDB needs to persist the complete static TBox, RuleBox,
        # and language contract before any ABox worker is allowed to run.
        # The safe one-query static writes intentionally trade bootstrap speed
        # for planner stability and can exceed six minutes on a cold store.
        "seedTimeoutSeconds": str((settings or {}).get("typedbSeedTimeoutSeconds") or os.environ.get("TYPEDB_SEED_TIMEOUT_SECONDS") or "900"),
        "seedRetryCount": str((settings or {}).get("typedbSeedRetryCount") or os.environ.get("TYPEDB_SEED_RETRY_COUNT") or "2"),
        "freshSchemaBootstrapBatchSize": str(
            (settings or {}).get("typedbFreshSchemaBootstrapBatchSize") or "512"
        ),
        "freshSchemaBootstrapTimeoutSeconds": str(
            (settings or {}).get("typedbFreshSchemaBootstrapTimeoutSeconds") or "900"
        ),
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
        "portfolioWorldProjectionRebuildTimeoutSeconds": str(
            (settings or {}).get("typedbPortfolioWorldProjectionRebuildTimeoutSeconds")
            or os.environ.get("TYPEDB_PORTFOLIO_WORLD_PROJECTION_REBUILD_TIMEOUT_SECONDS")
            or "1800"
        ),
        "portfolioWorldProjectionRebuildLimit": str(
            (settings or {}).get("typedbPortfolioWorldProjectionRebuildLimit")
            or os.environ.get("TYPEDB_PORTFOLIO_WORLD_PROJECTION_REBUILD_LIMIT")
            or "20"
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


def questdb_executable() -> str:
    explicit = str(os.environ.get("QUESTDB_COMMAND") or "").strip()
    if explicit:
        return explicit
    return shutil.which("questdb") or "/usr/local/opt/questdb/bin/questdb"


def questdb_worker_spec(settings: Dict[str, object]) -> Dict[str, object]:
    executable = questdb_executable()
    configured_path = str((settings or {}).get("questDbDataPath") or "").strip()
    data_path = Path(configured_path).expanduser() if configured_path else data_dir() / "questdb-data"
    if not data_path.is_absolute():
        data_path = ROOT_DIR / data_path
    http_url = str((settings or {}).get("questDbHttpUrl") or "http://127.0.0.1:9000").strip()
    address = http_url.split("://", 1)[-1].split("/", 1)[0]
    command = [executable, "start", "-d", str(data_path), "-n", "-f"] if executable and Path(executable).exists() else []
    return {
        "label": "QuestDB time-series store",
        "pid": data_dir() / "questdb.pid",
        "log": data_dir() / "questdb.log",
        "command": command,
        "needle": "questdb.sh start -d " + str(data_path),
        "needles": ["questdb.sh start -d " + str(data_path), "io.questdb.ServerMain"],
        "role": "questdb",
        "env": {
            "JAVA_HOME": "/usr/local/opt/openjdk",
            "QDB_HTTP_NET_BIND_TO": "127.0.0.1:9000",
            "QDB_HTTP_MIN_NET_BIND_TO": "127.0.0.1:9003",
            "QDB_LINE_TCP_NET_BIND_TO": "127.0.0.1:9009",
            "QDB_LINE_UDP_BIND_TO": "127.0.0.1:9009",
            "QDB_QWP_UDP_BIND_TO": "127.0.0.1:9007",
            "QDB_PG_NET_BIND_TO": "127.0.0.1:8812",
        },
        "dataPath": data_path,
        "healthAddress": address,
        "startupWaitSeconds": str((settings or {}).get("questDbStartupWaitSeconds") or "90"),
        "missingReason": "QuestDB executable was not found. Install QuestDB or set QUESTDB_COMMAND." if not command else "",
    }


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
        "schemaBootstrapAttempts": str(
            (settings or {}).get("mysqlOperationalSchemaBootstrapAttempts") or "3"
        ),
        "schemaBootstrapRetrySeconds": str(
            (settings or {}).get("mysqlOperationalSchemaBootstrapRetrySeconds") or "5"
        ),
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
    # A cold 5GB+ InnoDB store can need longer than the ordinary interactive
    # query deadline to inspect and migrate its schema. Do not turn that normal
    # recovery into a stop/start loop that repeatedly discards the warm cache.
    settings["mysqlOperationTimeoutSeconds"] = str(max(
        60,
        int_value(settings.get("mysqlOperationTimeoutSeconds"), 10, 1),
    ))
    attempts = int_value(spec.get("schemaBootstrapAttempts"), 3, 1)
    retry_seconds = int_value(spec.get("schemaBootstrapRetrySeconds"), 5, 0)
    for attempt in range(1, attempts + 1):
        try:
            MySQLOperationalConnection(settings)
            MySQLMonitorAccountJobStore(settings)
        except Exception as error:  # noqa: BLE001 - startup can race MySQL recovery after reboot.
            message = (
                "operational schema bootstrap failed attempt="
                + str(attempt)
                + "/"
                + str(attempts)
                + ": "
                + str(error)[:300]
            )
            append_log(spec["log"], message)
            print(str(spec["label"]) + " " + message)
            if attempt < attempts:
                time.sleep(retry_seconds)
                continue
            return False
        append_log(spec["log"], "operational schema bootstrap ready attempt=" + str(attempt))
        print(str(spec["label"]) + " operational schema ready.")
        return True
    return False


def web_worker_spec(settings: Dict[str, object]) -> Dict[str, object]:
    port = int_value(os.environ.get("PORT") or (settings or {}).get("webPort"), 3000, 1)
    environment = {
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "ALLOW_PORT_FALLBACK": "0",
    }
    if truthy((settings or {}).get("cloudflareShareManagedEnabled")):
        environment.update(share_credentials_environment())
        environment["SHARE_FIXED_ENTRY_URL"] = fixed_entry_url(settings)
    return {
        "label": "Orbit Alpha web server",
        "pid": data_dir() / "python-web.pid",
        "log": data_dir() / "python-web.log",
        "command": [sys.executable, "-u", "python_service/service.py", "web"],
        "needle": "python_service/service.py web",
        "role": "web",
        "healthAddress": "127.0.0.1:" + str(port),
        "startupWaitSeconds": str((settings or {}).get("webStartupWaitSeconds") or "30"),
        "env": environment,
    }


def cloudflare_share_worker_spec(settings: Dict[str, object]) -> Dict[str, object]:
    node = managed_executable("node", (settings or {}).get("cloudflareNodeExecutable"))
    cloudflared = managed_executable(
        "cloudflared",
        (settings or {}).get("cloudflaredExecutable"),
    )
    script = ROOT_DIR / "scripts" / "share-local.js"
    port = int_value(os.environ.get("PORT") or (settings or {}).get("webPort"), 3000, 1)
    missing = ""
    if not node:
        missing = "Node.js executable was not found."
    elif not cloudflared:
        missing = "cloudflared executable was not found."
    elif not script.exists():
        missing = "Cloudflare share script was not found."
    return {
        "label": "Cloudflare notification evidence share",
        "pid": data_dir() / "cloudflare-share.pid",
        "log": data_dir() / "cloudflare-share.log",
        "command": [node, str(script)] if not missing else [],
        "needle": "scripts/share-local.js",
        "role": "cloudflare-share",
        "env": {
            "PORT": str(port),
            "TUNNEL_PROVIDER": "cloudflared",
            "CLOUDFLARED_COMMAND": cloudflared,
            "PATH": os.pathsep.join(dict.fromkeys([
                str(Path(node).parent) if node else "",
                str(Path(cloudflared).parent) if cloudflared else "",
                "/usr/local/bin",
                "/opt/homebrew/bin",
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            ]).keys()).strip(os.pathsep),
            "SHARE_FIXED_ENTRY_URL": fixed_entry_url(settings),
            "SHARE_PUBLISH_TARGET": "1" if truthy((settings or {}).get("cloudflareSharePublishTargetEnabled", "1")) else "0",
        },
        "missingReason": missing,
    }


def notification_ai_worker_specs(worker_count: int) -> Dict[str, Dict[str, object]]:
    workers = {}
    for index in range(1, min(MAX_NOTIFICATION_AI_WORKERS, max(0, int(worker_count or 0))) + 1):
        name = "notification-ai" if index == 1 else "notification-ai-" + str(index)
        pid_name = "python-notification-ai.pid" if index == 1 else "python-notification-ai-" + str(index) + ".pid"
        log_name = "python-notification-ai.log" if index == 1 else "python-notification-ai-" + str(index) + ".log"
        command_needle = "python_service/service.py ai-inference watch --worker-id ai-" + str(index) + " --limit 1"
        needles = [command_needle]
        if index == 1:
            # Recognize and terminate the pre-queue synchronous AI lane during
            # the first restart after this migration.
            needles.append("python_service/service.py notifications watch --lane ai --limit 1")
        workers[name] = {
            "label": "Python notification AI inference worker " + str(index),
            "pid": data_dir() / pid_name,
            "log": data_dir() / log_name,
            "command": [
                sys.executable,
                "-u",
                "python_service/service.py",
                "ai-inference",
                "watch",
                "--worker-id",
                "ai-" + str(index),
                "--limit",
                "1",
            ],
            "needle": command_needle,
            "needles": needles,
        }
    return workers


def disabled_notification_ai_worker_specs(
    active_specs: Dict[str, Dict[str, object]],
) -> Dict[str, Dict[str, object]]:
    """Return configured-out AI workers that still own a managed PID file."""
    return {
        name: spec
        for name, spec in notification_ai_worker_specs(MAX_NOTIFICATION_AI_WORKERS).items()
        if name not in active_specs and read_pid(spec["pid"])
    }


def active_reasoning_engine_version(settings: Dict[str, object]) -> str:
    explicit = str((settings or {}).get("reasoningEngineActiveVersion") or "").strip().lower()
    if explicit:
        return explicit
    active_id = str((settings or {}).get("reasoningEngineActiveDeploymentId") or "").strip()
    v2_id = str((settings or {}).get("reasoningEngineV2DeploymentId") or "ontology-v2-shadow").strip()
    return "v2" if active_id and active_id == v2_id else "v1"


def disabled_reasoning_worker_specs(
    active_specs: Dict[str, Dict[str, object]],
) -> Dict[str, Dict[str, object]]:
    """Return switched-out reasoning workers that still own a managed PID."""
    return {
        name: BASE_WORKERS[name]
        for name in (
            "ontology-reasoning", "reasoning-engine-delivery", "reasoning-engine-shadow"
        )
        if name not in active_specs and read_pid(BASE_WORKERS[name]["pid"])
    }


def worker_specs() -> Dict[str, Dict[str, object]]:
    try:
        settings = runtime_settings()
    except Exception:  # noqa: BLE001 - service manager should still manage Python workers.
        settings = {}
    workers = {}
    if truthy((settings or {}).get("mysqlRuntimeManaged", os.environ.get("MYSQL_RUNTIME_MANAGED", "1"))):
        workers["mysql"] = mysql_worker_spec(settings)
    # Keep the read-only console available while graph and time-series stores
    # recover. API handlers expose dependency freshness explicitly, so the web
    # process does not need to wait behind a long TypeDB replay or seed.
    workers["web"] = web_worker_spec(settings)
    if truthy((settings or {}).get("cloudflareShareManagedEnabled")):
        workers["cloudflare-share"] = cloudflare_share_worker_spec(settings)
    if typedb_requested(settings):
        workers["typedb"] = typedb_worker_spec(settings)
    if truthy((settings or {}).get("timeSeriesQuestDbEnabled")):
        workers["questdb"] = questdb_worker_spec(settings)
    # Schema-function readiness is a prerequisite for investment inference.
    # Start its dedicated worker before collectors can create fresh reasoning
    # pressure; the reasoning worker itself still fails closed until the
    # verified receipt for the active RuleBox/TBox is ready.
    if "ontology-rulebox-prewarm" in BASE_WORKERS:
        workers["ontology-rulebox-prewarm"] = BASE_WORKERS["ontology-rulebox-prewarm"]
    active_engine_version = active_reasoning_engine_version(settings)
    independent_v2_enabled = truthy(
        (settings or {}).get("reasoningEngineV2IndependentEnabled", "1")
    )
    configured_v2_id = str(
        (settings or {}).get("reasoningEngineV2DeploymentId") or ""
    ).strip()
    candidate_v2_id = str(
        (settings or {}).get("reasoningEngineCandidateDeploymentId") or ""
    ).strip()
    candidate_worker_enabled = bool(
        independent_v2_enabled
        and configured_v2_id
        and candidate_v2_id == configured_v2_id
    )
    workers.update({
        name: spec
        for name, spec in BASE_WORKERS.items()
        if name != "ontology-rulebox-prewarm"
        and not (name == "ontology-reasoning" and active_engine_version != "v1")
        and not (
            name in {"reasoning-engine-delivery", "reasoning-engine-shadow"}
            and not independent_v2_enabled
        )
        and not (name == "reasoning-engine-shadow" and not candidate_worker_enabled)
    })
    # Zero is an explicit operational pause: keep collection and deterministic
    # notifications running without launching external AI inference workers.
    # The operational settings store can be unavailable while MySQL itself is
    # starting.  Failing closed prevents an old/default configuration from
    # issuing AI requests before the persisted pause setting is readable.
    ai_worker_count = int_value(
        (settings or {}).get("notificationAiQueueWorkerCount"),
        0,
        0,
    )
    workers.update(notification_ai_worker_specs(ai_worker_count))
    background_nice = str((settings or {}).get("managedBackgroundProcessNice") or "5")
    local_ai_environment = {
        "ORBIT_LOCAL_AI_MAX_CONCURRENT": str(
            (settings or {}).get("localAiMaxConcurrentProcesses") or "2"
        ),
        "ORBIT_LOCAL_AI_CAPACITY_WAIT_SECONDS": str(
            (settings or {}).get("localAiCapacityWaitSeconds") or "300"
        ),
    }
    for name, spec in list(workers.items()):
        environment = dict(spec.get("env") or {})
        environment.update(local_ai_environment)
        spec = {**dict(spec), "env": environment}
        if name in {"mysql", "web", "cloudflare-share"}:
            workers[name] = spec
            continue
        workers[name] = {
            **spec,
            "processNice": str(spec.get("processNice") or background_nice),
        }
    return workers


GRAPH_DEPENDENT_WORKERS = {
    "ontology-rulebox-prewarm",
    "ontology-reasoning",
    "reasoning-engine-delivery",
    "reasoning-engine-shadow",
    "ontology-world-projection",
    "ontology-inference-detail",
    "ontology-maintenance",
    "ontology-lab",
}


def worker_startup_phase(name: str, spec: Dict[str, object]) -> int:
    """Keep source collection available while a cold graph store recovers."""

    role = str((spec or {}).get("role") or "").strip()
    if name == "mysql" or role == "mysql":
        return 0
    if name == "typedb" or role == "typedb":
        return 2
    if name in GRAPH_DEPENDENT_WORKERS:
        return 3
    return 1


def ordered_worker_specs(specs: Dict[str, Dict[str, object]] = None):
    """Return stable startup order without changing stop-order ownership."""

    selected = dict(specs or worker_specs())
    indexed = list(enumerate(selected.items()))
    indexed.sort(key=lambda item: (worker_startup_phase(item[1][0], item[1][1]), item[0]))
    return [item for _index, item in indexed]


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
    needles = spec.get("needles") if isinstance(spec.get("needles"), (list, tuple, set)) else [spec.get("needle")]
    return any(str(needle or "") in command for needle in needles if str(needle or ""))


def pid_exists(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def recover_project_questdb_pid(spec: Dict[str, object]) -> int:
    """Adopt an orphaned QuestDB launcher only when it owns this workspace data path."""

    if str(spec.get("role") or "") != "questdb" or os.name == "nt":
        return 0
    data_path = str(Path(spec.get("dataPath") or "").resolve())
    if not data_path:
        return 0
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,pgid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return 0
    candidates = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid, process_group = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        command = parts[2]
        if data_path not in command or not is_worker_command(command, spec):
            continue
        candidates.append((0 if pid == process_group else 1, pid))
    return sorted(candidates)[0][1] if candidates else 0


def process_group_exists(process_group: int) -> bool:
    if not process_group or os.name == "nt":
        return False
    try:
        os.killpg(process_group, 0)
    except OSError:
        return False
    return True


def is_running(pid: int, spec: Dict[str, object]) -> bool:
    # A developer may run the web UI directly on the canonical local port.
    # It is not a managed child, but it must not trigger repeated start
    # attempts or interrupt inference and notification workers.
    if str(spec.get("role") or "") == "web" and not pid_exists(pid):
        return tcp_ready(spec.get("healthAddress"))
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


def float_value(value: object, fallback: float, lower: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(fallback)
    return max(float(lower), parsed)


def system_available_memory_percent() -> float:
    """Return an OS-level memory-pressure estimate without adding a dependency."""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["memory_pressure"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return -1.0
        prefix = "System-wide memory free percentage:"
        for line in str(result.stdout or "").splitlines():
            if line.strip().startswith(prefix):
                return float_value(line.split(":", 1)[-1].strip().rstrip("%"), -1.0, -1.0)
        return -1.0
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        values = {}
        try:
            for line in meminfo.read_text(encoding="utf-8").splitlines():
                key, raw = line.split(":", 1)
                values[key] = float(raw.strip().split()[0])
        except (OSError, ValueError, IndexError):
            return -1.0
        total = float(values.get("MemTotal") or 0.0)
        available = float(values.get("MemAvailable") or 0.0)
        return round(available * 100.0 / total, 2) if total > 0 else -1.0
    return -1.0


def typedb_rotation_resource_preflight(
    spec: Dict[str, object],
    loadavg_provider=None,
    cpu_count_provider=None,
    memory_percent_provider=None,
) -> Dict[str, object]:
    """Keep a cold TypeDB build from competing with an already saturated host."""
    if not truthy((spec or {}).get("blueGreenResourceGuardEnabled")):
        return {"ready": True, "status": "disabled", "blockers": []}
    loadavg_provider = loadavg_provider or os.getloadavg
    cpu_count_provider = cpu_count_provider or os.cpu_count
    memory_percent_provider = memory_percent_provider or system_available_memory_percent
    try:
        one_minute_load = float((loadavg_provider() or (0.0,))[0])
    except (AttributeError, OSError, TypeError, ValueError):
        one_minute_load = 0.0
    cpu_count = max(1, int(cpu_count_provider() or 1))
    load_per_cpu = round(one_minute_load / cpu_count, 3)
    memory_available_percent = float(memory_percent_provider())
    maximum_load_per_cpu = float_value(spec.get("blueGreenMaxLoadPerCpu"), 1.25, 0.1)
    minimum_memory_percent = min(
        100.0,
        float_value(spec.get("blueGreenMinimumAvailableMemoryPercent"), 15.0, 0.0),
    )
    blockers = []
    if load_per_cpu > maximum_load_per_cpu:
        blockers.append("system-load")
    if 0.0 <= memory_available_percent < minimum_memory_percent:
        blockers.append("available-memory")
    reason_parts = []
    if "system-load" in blockers:
        reason_parts.append(
            "load per CPU " + str(load_per_cpu) + " exceeds " + str(maximum_load_per_cpu)
        )
    if "available-memory" in blockers:
        reason_parts.append(
            "available memory "
            + str(round(memory_available_percent, 1))
            + "% is below "
            + str(minimum_memory_percent)
            + "%"
        )
    return {
        "ready": not blockers,
        "status": "ready" if not blockers else "resource-pressure",
        "blockers": blockers,
        "reason": "; ".join(reason_parts),
        "oneMinuteLoad": round(one_minute_load, 2),
        "cpuCount": cpu_count,
        "loadPerCpu": load_per_cpu,
        "maximumLoadPerCpu": maximum_load_per_cpu,
        "availableMemoryPercent": round(memory_available_percent, 1),
        "minimumAvailableMemoryPercent": minimum_memory_percent,
    }


def low_priority_command(spec: Dict[str, object], command: List[str]) -> List[str]:
    """Run expensive candidate work below interactive desktop processes."""
    nice_value = min(19, int_value((spec or {}).get("processNice"), 0, 0))
    nice_command = shutil.which("nice") if os.name != "nt" else ""
    if nice_value <= 0 or not nice_command:
        return list(command)
    return [nice_command, "-n", str(nice_value), *list(command)]


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


def acquire_typedb_maintenance_lock(
    operation: str = "maintenance",
    max_age_seconds: int = 3600,
) -> Dict[str, object]:
    """Fence every full-store TypeDB mutation with one process-wide lease.

    Scoped ABox write leases coordinate graph transactions, but they do not
    stop a service startup seed from racing a blue-green candidate seed. This
    filesystem lease covers that wider process boundary and remains available
    even while MySQL or TypeDB itself is recovering.
    """

    path = typedb_rotation_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    started_epoch = time.time()
    payload = {
        "pid": os.getpid(),
        "operation": str(operation or "maintenance")[:80],
        "token": token,
        "startedAt": iso_now(),
        "startedAtEpoch": started_epoch,
        "expiresAtEpoch": started_epoch + max(60, int(max_age_seconds or 3600)),
    }
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
                return {
                    "acquired": False,
                    "reason": "another TypeDB maintenance operation is active",
                    "ownerPid": owner,
                    "ownerOperation": str(existing.get("operation") or "maintenance"),
                    "ownerStartedAt": str(existing.get("startedAt") or ""),
                }
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
    return {"acquired": False, "reason": "TypeDB maintenance lock could not be acquired"}


def acquire_typedb_rotation_lock() -> Dict[str, object]:
    return acquire_typedb_maintenance_lock("blue-green-rotation", max_age_seconds=7200)


def typedb_maintenance_lock_owned(lock: Dict[str, object]) -> bool:
    """Return true only for the current fencing token.

    PID checks alone are not sufficient because a stale process can outlive a
    lease replacement and later attempt a cutover. The token is verified again
    immediately before swapping the active data directory.
    """

    expected = str((lock or {}).get("token") or "")
    if not bool((lock or {}).get("acquired")) or not expected:
        return False
    try:
        current = json.loads(typedb_rotation_lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        int_value(current.get("pid"), 0, 0) == int_value((lock or {}).get("pid"), 0, 0)
        and str(current.get("token") or "") == expected
    )


def release_typedb_rotation_lock(lock: Dict[str, object]) -> None:
    if not bool((lock or {}).get("acquired")):
        return
    if not typedb_maintenance_lock_owned(lock):
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
    disk_usage_provider=None,
    inventory_provider=None,
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
    maximum_mb = int_value(configured.get("maxSizeMb"), 8192, 1)
    threshold_percent = int_value(configured.get("autoRotationPercent"), 80, 50)
    threshold_percent = min(100, threshold_percent)
    wal_trigger_mb = int_value(configured.get("autoRotationWalMb"), 4096, 0)
    free_space_trigger_mb = int_value(
        configured.get("autoRotationFreeSpaceMb"),
        0,
        0,
    )
    minimum_headroom_mb = int_value(
        configured.get("blueGreenMinimumHeadroomMb"),
        12288,
        1024,
    )
    candidate_max_mb = int_value(
        configured.get("blueGreenEstimatedCandidateMaxMb"),
        4096,
        512,
    )
    cooldown_minutes = min(24 * 60, int_value(configured.get("autoRotationCooldownMinutes"), 60, 1))
    failure_retry_seconds = min(
        3600,
        int_value(configured.get("autoRotationFailureRetrySeconds"), 300, 30),
    )
    size_bytes = int((size_provider or directory_size_bytes)(data_path))
    size_mb = round(size_bytes / 1024 / 1024, 1)
    usage_percent = round(size_bytes / (maximum_mb * 1024 * 1024) * 100.0, 1)
    try:
        if inventory_provider is not None:
            inventory = dict(inventory_provider(
                {
                    "ontologyTypeDbEnabled": "1",
                    "typedbDataMaxSizeMb": str(maximum_mb),
                    "typedbMinimumFreeSpaceMb": str(configured.get("minimumFreeSpaceMb") or "1"),
                },
                data_path=data_path,
                disk_usage_provider=disk_usage_provider,
            ) or {})
        else:
            # Capacity status already scans the complete TypeDB tree. The
            # supervisor only needs WAL pressure here, so avoid repeating two
            # full apparent/physical-size walks every minute.
            wal_bytes = sum(
                storage_directory_physical_size_bytes(path)
                for path in data_path.glob("*/wal")
                if path.is_dir()
            )
            inventory = {"typedbWalMb": round(wal_bytes / 1024 / 1024, 1)}
    except (OSError, ValueError):
        inventory = {}
    try:
        wal_mb = max(0.0, float(inventory.get("typedbWalMb") or 0.0))
    except (TypeError, ValueError):
        wal_mb = 0.0
    probe_path = data_path if data_path.exists() else data_path.parent
    try:
        disk_usage = (disk_usage_provider or shutil.disk_usage)(probe_path)
        free_space_mb = round(int(getattr(disk_usage, "free", 0) or 0) / 1024 / 1024, 1)
    except OSError:
        free_space_mb = None
    estimated_candidate_mb = min(size_mb, float(candidate_max_mb)) if size_mb > 0 else 0.0
    required_staging_mb = round(minimum_headroom_mb + estimated_candidate_mb, 1)
    staging_ready = free_space_mb is not None and free_space_mb >= required_staging_mb
    now = float(now_epoch if now_epoch is not None else time.time())
    marker = read_typedb_retention_marker()
    try:
        last_attempt_epoch = float(marker.get("lastAutoRotationAttemptEpoch") or 0)
    except (TypeError, ValueError):
        last_attempt_epoch = 0.0
    last_attempt_status = str(marker.get("lastAutoRotationStatus") or "").strip().lower()
    failure_statuses = {
        "failed",
        "reset-failed",
        "restart-failed",
        "exception",
        "candidate-failed-active-preserved",
        "cutover-fenced-active-preserved",
    }
    try:
        consecutive_failures = max(
            1 if last_attempt_status in failure_statuses else 0,
            int(marker.get("autoRotationConsecutiveFailureCount") or 0),
        )
    except (TypeError, ValueError):
        consecutive_failures = 1 if last_attempt_status in failure_statuses else 0
    failure_backoff_multiplier = (1, 3, 6, 12)[
        min(max(0, consecutive_failures - 1), 3)
    ]
    failure_backoff_seconds = min(3600, failure_retry_seconds * failure_backoff_multiplier)
    retry_window_seconds = (
        failure_backoff_seconds
        if last_attempt_status in failure_statuses
        else cooldown_minutes * 60
    )
    cooldown_remaining_seconds = max(
        0,
        int(retry_window_seconds - max(0.0, now - last_attempt_epoch)),
    ) if last_attempt_epoch > 0 else 0
    threshold_reached = bool(
        data_path.exists()
        and size_bytes > 0
        and usage_percent >= threshold_percent
    )
    disk_pressure_reached = bool(
        data_path.exists()
        and size_bytes > 0
        and free_space_trigger_mb > 0
        and free_space_mb is not None
        and free_space_mb <= free_space_trigger_mb
    )
    wal_pressure_reached = bool(
        data_path.exists()
        and size_bytes > 0
        and wal_trigger_mb > 0
        and wal_mb >= wal_trigger_mb
    )
    rotation_triggered = threshold_reached or disk_pressure_reached or wal_pressure_reached
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
            "freeSpaceMb": free_space_mb,
            "typedbWalMb": round(wal_mb, 1),
            "walTriggerMb": wal_trigger_mb,
            "freeSpaceTriggerMb": free_space_trigger_mb,
            "stagingReady": staging_ready,
            "requiredStagingMb": required_staging_mb,
        }
    if not rotation_triggered:
        return {
            "needed": False,
            "reason": "below-threshold",
            "enabled": True,
            "typedbSizeMb": size_mb,
            "typedbUsagePercent": usage_percent,
            "thresholdPercent": threshold_percent,
            "maxSizeMb": maximum_mb,
            "cooldownRemainingSeconds": cooldown_remaining_seconds,
            "lastAttemptStatus": last_attempt_status,
            "consecutiveFailureCount": consecutive_failures,
            "retryWindowSeconds": retry_window_seconds,
            "freeSpaceMb": free_space_mb,
            "typedbWalMb": round(wal_mb, 1),
            "walTriggerMb": wal_trigger_mb,
            "walPressureReached": wal_pressure_reached,
            "freeSpaceTriggerMb": free_space_trigger_mb,
            "diskPressureReached": False,
            "stagingReady": staging_ready,
            "requiredStagingMb": required_staging_mb,
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
            "lastAttemptStatus": last_attempt_status,
            "consecutiveFailureCount": consecutive_failures,
            "retryWindowSeconds": retry_window_seconds,
            "freeSpaceMb": free_space_mb,
            "typedbWalMb": round(wal_mb, 1),
            "walTriggerMb": wal_trigger_mb,
            "walPressureReached": wal_pressure_reached,
            "freeSpaceTriggerMb": free_space_trigger_mb,
            "diskPressureReached": disk_pressure_reached,
            "stagingReady": staging_ready,
            "requiredStagingMb": required_staging_mb,
        }
    return {
        "needed": True,
        "reason": (
            "insufficient blue-green staging headroom: free "
            + str(free_space_mb) + "MB < required " + str(required_staging_mb) + "MB"
            if not staging_ready
            else "shared disk free " + str(free_space_mb) + "MB <= automatic rotation "
            + str(free_space_trigger_mb) + "MB"
            if disk_pressure_reached and not threshold_reached
            else "WAL " + str(round(wal_mb, 1)) + "MB >= automatic rotation "
            + str(wal_trigger_mb) + "MB"
            if wal_pressure_reached and not threshold_reached
            else "size " + str(size_mb) + "MB (" + str(usage_percent) + "%) >= automatic rotation "
            + str(threshold_percent) + "%"
        ),
        "enabled": True,
        "typedbSizeMb": size_mb,
        "typedbUsagePercent": usage_percent,
        "thresholdPercent": threshold_percent,
        "maxSizeMb": maximum_mb,
        "cooldownRemainingSeconds": cooldown_remaining_seconds,
        "lastAttemptStatus": last_attempt_status,
        "consecutiveFailureCount": consecutive_failures,
        "retryWindowSeconds": retry_window_seconds,
        "hardLimitReached": hard_limit_reached,
        "trigger": (
            "multiple"
            if sum([threshold_reached, disk_pressure_reached, wal_pressure_reached]) > 1
            else "shared-disk"
            if disk_pressure_reached
            else "typedb-wal"
            if wal_pressure_reached
            else "typedb-size"
        ),
        "freeSpaceMb": free_space_mb,
        "freeSpaceTriggerMb": free_space_trigger_mb,
        "diskPressureReached": disk_pressure_reached,
        "typedbWalMb": round(wal_mb, 1),
        "walTriggerMb": wal_trigger_mb,
        "walPressureReached": wal_pressure_reached,
        "stagingReady": staging_ready,
        "requiredStagingMb": required_staging_mb,
        "estimatedCandidateMb": estimated_candidate_mb,
        "minimumHeadroomMb": minimum_headroom_mb,
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
    alert_kind: str = "typedb-auto-rotation",
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
            force_alert_kind=str(alert_kind or "typedb-auto-rotation"),
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
    status = str(updates.get("lastAutoRotationStatus") or "").strip().lower()
    failure_statuses = {
        "failed",
        "reset-failed",
        "restart-failed",
        "exception",
        "candidate-failed-active-preserved",
        "cutover-fenced-active-preserved",
    }
    if status in failure_statuses:
        try:
            previous_failures = int(marker.get("autoRotationConsecutiveFailureCount") or 0)
        except (TypeError, ValueError):
            previous_failures = 0
        marker["autoRotationConsecutiveFailureCount"] = previous_failures + 1
    elif status == "ok":
        marker["autoRotationConsecutiveFailureCount"] = 0
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
                "typedbDataMaxSizeMb": spec.get("maxSizeMb") or "16384",
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
            + " · free=" + str(capacity.get("freeSpaceMb") or "-") + "MB"
            + " / trigger=" + str(capacity.get("freeSpaceTriggerMb") or "-") + "MB"
            + " · staging=" + ("ready" if capacity.get("stagingReady") else "blocked")
            + " · status=" + rotation_status
            + " · cooldown=" + str(capacity.get("cooldownRemainingSeconds") or 0) + "s"
            + " · last-attempt=" + str(capacity.get("lastAttemptStatus") or "none")
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


def configure_fresh_typedb_credentials(spec: Dict[str, object]) -> bool:
    """Apply configured credentials to a known fresh TypeDB CE data path."""
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
        append_log(spec["log"], "fresh credential configuration verification failed")
        return False
    append_log(spec["log"], "configured TypeDB credentials applied to fresh store")
    return True


def bootstrap_typedb_credentials_after_reset(spec: Dict[str, object]) -> bool:
    """Restore local TypeDB credentials after an intentional empty-store boot."""
    if not typedb_credentials_bootstrap_pending():
        return False
    if not configure_fresh_typedb_credentials(spec):
        return False
    clear_typedb_credentials_bootstrap_pending()
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
    if str(spec.get("role") or "") != "typedb-stage":
        command.append("--recover-scoped-write-lease")
    if truthy(spec.get("seedReplaceRuleBox")):
        command.append("--replace-rulebox")
    if truthy(spec.get("seedKeepInference")):
        command.append("--keep-inference")
    return command


def typedb_shared_world_projection_rebuild_command(spec: Dict[str, object]) -> List[str]:
    limit = int_value(spec.get("sharedWorldProjectionRebuildLimit"), 100, 1)
    command = [
        sys.executable,
        "-u",
        "python_service/service.py",
        "ontology-world-projection",
        "rebuild",
        "--limit",
        str(limit),
    ]
    if str(spec.get("role") or "") == "typedb-stage":
        command.append("--read-only-source")
    return command


def typedb_portfolio_world_projection_rebuild_command(spec: Dict[str, object]) -> List[str]:
    limit = int_value(spec.get("portfolioWorldProjectionRebuildLimit"), 20, 1)
    return [
        sys.executable,
        "-u",
        "python_service/service.py",
        "ontology-world-projection",
        "rebuild-portfolios",
        "--limit",
        str(limit),
    ]


def typedb_rulebox_prewarm_status_command(_spec: Dict[str, object]) -> List[str]:
    return [
        sys.executable,
        "-u",
        "python_service/service.py",
        "ontology-rulebox-prewarm",
        "status",
    ]


def typedb_subprocess_environment(spec: Dict[str, object]) -> Dict[str, str]:
    """Pin maintenance commands to the exact TypeDB instance being managed."""
    environment = managed_process_environment(spec)
    environment.update({
        "ORBIT_INFRASTRUCTURE_OVERRIDE_ENABLED": "1",
        "TYPEDB_ADDRESS": str(spec.get("healthAddress") or "127.0.0.1:1729"),
        "TYPEDB_HTTP_ADDRESS": str(spec.get("httpAddress") or "127.0.0.1:8000"),
        "TYPEDB_DATABASE": str(spec.get("typedbDatabase") or "orbit_alpha_ontology"),
        "TYPEDB_USER": str(spec.get("typedbUser") or "admin"),
        "TYPEDB_PASSWORD": str(spec.get("typedbPassword") or ""),
        "TYPEDB_TLS_ENABLED": str(spec.get("typedbTlsEnabled") or "0"),
        "ONTOLOGY_TYPEDB_ENABLED": "1",
    })
    if str(spec.get("role") or "") == "typedb-stage":
        environment.update({
            "TYPEDB_FRESH_CANDIDATE_REBUILD": "1",
            "TYPEDB_FRESH_SCHEMA_BOOTSTRAP_BATCH_SIZE": str(
                spec.get("freshSchemaBootstrapBatchSize") or "512"
            ),
            "TYPEDB_FRESH_SCHEMA_BOOTSTRAP_TIMEOUT_SECONDS": str(
                spec.get("freshSchemaBootstrapTimeoutSeconds") or "900"
            ),
            "TYPEDB_SCHEMA_OPERATION_TIMEOUT_SECONDS": str(max(
                int_value(spec.get("freshSchemaBootstrapTimeoutSeconds"), 900, 1),
                int_value(spec.get("schemaOperationTimeoutSeconds"), 120, 1),
            )),
        })
    return environment


def append_log_text(path: Path, label: str, text: str) -> None:
    append_log(path, label)
    if not text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")


def launch_typedb_stage_process(spec: Dict[str, object], log_label: str) -> bool:
    """Launch an isolated candidate without touching its committed data."""
    append_log(spec["log"], log_label)
    output = Path(spec["log"]).open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            low_priority_command(spec, spec["command"]),
            cwd=str(ROOT_DIR),
            env=managed_process_environment(spec),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
    finally:
        output.close()
    spec["pid"].write_text(str(process.pid) + "\n", encoding="utf-8")
    os.chmod(spec["pid"], 0o600)
    return wait_for_fresh_typedb_candidate(spec)


def typedb_process_ids_for_data_path(data_path: object) -> List[int]:
    """Find only TypeDB processes that explicitly own one managed data path."""
    if os.name == "nt":
        return []
    path = str(Path(data_path or "").resolve()) if data_path else ""
    if not path:
        return []
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    result = []
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or path not in parts[1] or "typedb_server_bin" not in parts[1]:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid > 1 and pid != os.getpid():
            result.append(pid)
    return sorted(set(result))


def stop_typedb_stage_data_path_processes(spec: Dict[str, object]) -> bool:
    """Stop orphaned candidate owners before reopening RocksDB files."""
    pids = typedb_process_ids_for_data_path(spec.get("dataPath"))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + 30.0
    while pids and time.monotonic() < deadline:
        pids = [pid for pid in pids if pid_exists(pid)]
        if pids:
            time.sleep(0.2)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    return not any(pid_exists(pid) for pid in pids)


def clear_typedb_stage_incomplete_checkpoints(spec: Dict[str, object]) -> List[str]:
    """Remove crash-only checkpoint workdirs after every owner is stopped."""
    raw_data_path = str(spec.get("dataPath") or "").strip()
    removed = []
    if not raw_data_path:
        return removed
    data_path = Path(raw_data_path)
    if not data_path.exists():
        return removed
    for path in data_path.glob("*/checkpoint/*.tmp"):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    return removed


def restart_typedb_stage_for_seed_retry(spec: Dict[str, object]) -> bool:
    """Cancel a detached schema commit, then resume from durable batches."""
    if str(spec.get("role") or "") != "typedb-stage":
        return False
    append_log(spec["log"], "seed retry requires candidate server restart")
    stop_worker(spec)
    if not stop_typedb_stage_data_path_processes(spec):
        append_log(spec["log"], "seed retry candidate data path still owned")
        return False
    removed = clear_typedb_stage_incomplete_checkpoints(spec)
    if removed:
        append_log(
            spec["log"],
            "seed retry removed incomplete checkpoints count=" + str(len(removed)),
        )
    remove_pid(spec["pid"])
    return launch_typedb_stage_process(spec, "seed retry candidate restart")


def ensure_typedb_seeded(spec: Dict[str, object]) -> bool:
    if str(spec.get("role") or "") not in {"typedb", "typedb-stage"}:
        return True
    if not truthy(spec.get("seedOnStart")):
        if str(spec.get("role") or "") == "typedb-stage":
            append_log(spec["log"], "candidate seed rejected; fresh candidate requires schema seed")
            print(str(spec["label"]) + " fresh candidate seed is required.")
            return False
        append_log(spec["log"], "seed skipped")
        print(str(spec["label"]) + " RuleBox seed skipped.")
        return True
    if str(spec.get("role") or "") == "typedb":
        marker = read_typedb_retention_marker()
        prepared_path = str(marker.get("blueGreenPreparedDataPath") or "").strip()
        if bool(marker.get("blueGreenCutoverPending")) and prepared_path == str(spec.get("dataPath") or ""):
            marker["blueGreenCutoverPending"] = False
            marker["blueGreenCutoverActivatedAt"] = iso_now()
            write_typedb_retention_marker(marker)
            append_log(spec["log"], "seed skipped for prevalidated blue-green cutover")
            print(str(spec["label"]) + " reused prevalidated blue-green seed.")
            return True
    maintenance_lock = dict(spec.get("_typedbMaintenanceLock") or {})
    acquired_here = False
    if not typedb_maintenance_lock_owned(maintenance_lock):
        maintenance_lock = acquire_typedb_maintenance_lock(
            "candidate-seed" if str(spec.get("role") or "") == "typedb-stage" else "active-seed",
            max_age_seconds=(
                int_value(spec.get("seedTimeoutSeconds"), 180, 1)
                * (int_value(spec.get("seedRetryCount"), 2, 0) + 1)
                + 300
            ),
        )
        if not bool(maintenance_lock.get("acquired")):
            owner = str(maintenance_lock.get("ownerOperation") or "maintenance")
            message = "seed deferred; TypeDB maintenance lock owned by " + owner
            append_log(spec["log"], message)
            print(str(spec["label"]) + " " + message + ".")
            # The active store remains the serving generation while a validated
            # candidate is prepared. Keep the web/read side available instead
            # of starting a second full seed. An isolated candidate without the
            # parent fencing token must fail closed.
            return str(spec.get("role") or "") == "typedb"
        acquired_here = True
    try:
        command = typedb_seed_command(spec)
        timeout_seconds = int_value(spec.get("seedTimeoutSeconds"), 180, 1)
        attempts = int_value(spec.get("seedRetryCount"), 2, 0) + 1
        for attempt in range(1, attempts + 1):
            append_log(spec["log"], "seed start attempt=" + str(attempt))
            print(str(spec["label"]) + " seeding ontology RuleBox. attempt=" + str(attempt))
            try:
                result = subprocess.run(
                    low_priority_command(spec, command),
                    cwd=str(ROOT_DIR),
                    env=typedb_subprocess_environment(spec),
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
                # A TypeDB schema commit may continue compiling after the Python
                # driver times out or is terminated. Starting another seed client
                # against that server only queues behind the detached transaction.
                # A blue-green candidate is isolated, so restart only that server;
                # committed schema batches remain durable and the next attempt
                # resumes from schema inspection. The active graph is untouched.
                if str(spec.get("role") or "") == "typedb-stage":
                    if not restart_typedb_stage_for_seed_retry(spec):
                        append_log(spec["log"], "seed retry candidate restart failed")
                        return False
                else:
                    time.sleep(1.0)
        print(str(spec["label"]) + " RuleBox seed failed after " + str(attempts) + " attempts.")
        return False
    finally:
        if acquired_here:
            release_typedb_rotation_lock(maintenance_lock)


def validate_typedb_candidate_inference_runtime(spec: Dict[str, object]) -> Dict[str, object]:
    """Require a complete native-rule read path before blue-green cutover.

    Generated TypeDB functions are an optimization. A fresh candidate can be
    activated while they are still staging only when bounded direct TypeQL is
    explicitly enabled; the following portfolio rebuild then exercises that
    fallback against real durable snapshots.
    """
    try:
        result = subprocess.run(
            low_priority_command(spec, typedb_rulebox_prewarm_status_command(spec)),
            cwd=str(ROOT_DIR),
            env=typedb_subprocess_environment(spec),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "status": "error",
            "ready": False,
            "reason": str(error)[:220],
        }
    if result.returncode != 0:
        return {
            "status": "error",
            "ready": False,
            "reason": str(result.stderr or result.stdout or "RuleBox readiness command failed")[:220],
        }
    try:
        payload = json.loads(str(result.stdout or "{}"))
    except json.JSONDecodeError as error:
        return {
            "status": "error",
            "ready": False,
            "reason": "Invalid RuleBox readiness response: " + str(error)[:180],
        }
    prewarm = dict(payload.get("prewarm") or {})
    functions_ready = bool(prewarm.get("functionsReady"))
    direct_fallback_ready = truthy(spec.get("schemaFunctionDirectQueryFallbackEnabled"))
    return {
        "status": "ready" if functions_ready or direct_fallback_ready else "blocked",
        "ready": bool(functions_ready or direct_fallback_ready),
        "mode": "schema-functions" if functions_ready else "direct-typeql-fallback",
        "functionsReady": functions_ready,
        "directTypeqlFallbackReady": direct_fallback_ready,
        "ruleCount": int_value(prewarm.get("ruleCount"), 0, 0),
        "reason": (
            ""
            if functions_ready or direct_fallback_ready
            else "TypeDB schema functions are incomplete and direct TypeQL fallback is disabled."
        ),
    }


def validate_typedb_candidate_release_contract(
    spec: Dict[str, object],
    settings_provider=None,
    repository_factory=None,
    registry_factory=None,
) -> Dict[str, object]:
    """Fence blue-green cutover against the frozen delivery RuleBox.

    A storage rotation must preserve the executable reasoning release. The
    normal seed command reflects the current source catalog, which may be
    newer than the release serving production. Reading the candidate before
    cutover prevents a storage maintenance operation from silently changing
    investment behavior or stopping the versioned reasoning worker.
    """
    if settings_provider is None:
        settings_provider = runtime_settings
    if repository_factory is None:
        from .infrastructure.ontology_graph_store import ontology_repository_from_settings
        repository_factory = ontology_repository_from_settings
    if registry_factory is None:
        from .infrastructure.operational_store import reasoning_engine_registry_store
        registry_factory = reasoning_engine_registry_store

    try:
        configured = dict(settings_provider(fast_operational_read=True) or {})
    except TypeError:
        configured = dict(settings_provider() or {})
    database_name = str(spec.get("typedbDatabase") or "").strip()
    candidate_settings = {
        **configured,
        "ontologyTypeDbEnabled": "1",
        "typedbAddress": str(spec.get("healthAddress") or ""),
        "typedbHttpAddress": str(spec.get("httpAddress") or ""),
        "typedbDatabase": database_name,
        "typedbUser": str(spec.get("typedbUser") or configured.get("typedbUser") or "admin"),
        "typedbPassword": str(spec.get("typedbPassword") or configured.get("typedbPassword") or "password"),
        "typedbTlsEnabled": str(spec.get("typedbTlsEnabled") or configured.get("typedbTlsEnabled") or "0"),
    }
    try:
        registry = registry_factory(configured)
        control = registry.control()
        deployment_ids = []
        for field in ("delivery_deployment_id", "active_deployment_id"):
            value = (
                control.get(field)
                if isinstance(control, dict)
                else getattr(control, field, "")
            )
            clean = str(value or "").strip()
            if clean and clean not in deployment_ids:
                deployment_ids.append(clean)
        candidate_deployment_id = str(
            (
                control.get("candidate_deployment_id")
                if isinstance(control, dict)
                else getattr(control, "candidate_deployment_id", "")
            )
            or ""
        ).strip()
        registered_candidate = (
            dict(registry.get(candidate_deployment_id) or {})
            if candidate_deployment_id and candidate_deployment_id not in deployment_ids
            else {}
        )
        candidate_governs_database = bool(
            registered_candidate
            and str(registered_candidate.get("graphStoreBinding") or "").strip() == database_name
            and str(registered_candidate.get("status") or "").strip().lower()
            in {"provisioning", "replaying", "shadow", "candidate"}
        )
        governed = []
        for deployment_id in deployment_ids:
            deployment = dict(registry.get(deployment_id) or {})
            if str(deployment.get("graphStoreBinding") or "").strip() != database_name:
                continue
            health = dict(deployment.get("health") or {})
            frozen_fingerprint = str(health.get("ruleboxFingerprint") or "").strip()
            if not frozen_fingerprint and not str(health.get("candidateReleaseId") or "").strip():
                # Compatibility with the pre-v2 health contract.
                frozen_fingerprint = str(health.get("releaseFingerprint") or "").strip()
            governed.append({
                "deploymentId": deployment_id,
                "status": str(deployment.get("status") or ""),
                "frozenRuleboxFingerprint": frozen_fingerprint,
            })
        if not governed:
            return {
                "status": "not-governed",
                "ready": True,
                "database": database_name,
                "governedDeployments": [],
            }
        missing = [item for item in governed if not item["frozenRuleboxFingerprint"]]
        if missing:
            return {
                "status": "release-fingerprint-missing",
                "ready": False,
                "database": database_name,
                "governedDeployments": governed,
                "reason": "The active reasoning release has no frozen RuleBox fingerprint.",
            }

        snapshot = dict(repository_factory(candidate_settings).rulebox_snapshot() or {})
        if str(snapshot.get("status") or "") != "ok" or not snapshot.get("rules"):
            return {
                "status": "candidate-rulebox-unavailable",
                "ready": False,
                "database": database_name,
                "governedDeployments": governed,
                "reason": "The seeded candidate RuleBox could not be read before cutover.",
            }
        from .domain.reasoning_shadow import payload_hash
        candidate_fingerprint = str(
            snapshot.get("sourceRulesHash")
            or snapshot.get("rulesHash")
            or snapshot.get("ruleboxRulesHash")
            or payload_hash(snapshot.get("rules") or [])
        ).strip()

        # A registered release change intentionally replaces the RuleBox in a
        # fresh blue-green store.  It must match the complete source release
        # bundle frozen at registration; otherwise storage maintenance keeps
        # enforcing the active/delivery fingerprint below.  The candidate
        # worker freezes the concrete RuleBox fingerprint after cutover, so
        # this branch breaks the startup cycle without weakening unregistered
        # mutation protection.
        if candidate_governs_database:
            from .application.reasoning_engine_platform import ReasoningEnginePlatformService
            from .infrastructure.runtime_identity import runtime_identity

            release_settings = dict(configured)
            if not isinstance(release_settings.get("_runtimeIdentity"), dict):
                release_settings["_runtimeIdentity"] = runtime_identity()

            expected_descriptor = next(
                (
                    descriptor
                    for descriptor in ReasoningEnginePlatformService(
                        registry,
                        release_settings,
                    ).descriptors()
                    if descriptor.deployment_id == candidate_deployment_id
                ),
                None,
            )
            registered_bundle = dict(registered_candidate.get("releaseBundle") or {})
            expected_bundle = (
                expected_descriptor.release_bundle.to_dict()
                if expected_descriptor is not None
                else {}
            )
            if not expected_bundle or registered_bundle != expected_bundle:
                return {
                    "status": "registered-candidate-source-contract-mismatch",
                    "ready": False,
                    "database": database_name,
                    "candidateDeploymentId": candidate_deployment_id,
                    "candidateRuleboxFingerprint": candidate_fingerprint,
                    "reason": (
                        "The registered candidate release bundle no longer matches "
                        "the current source release contract."
                    ),
                }
            candidate_health = dict(registered_candidate.get("health") or {})
            frozen_candidate_fingerprint = str(
                candidate_health.get("ruleboxFingerprint") or ""
            ).strip()
            if (
                frozen_candidate_fingerprint
                and frozen_candidate_fingerprint != candidate_fingerprint
            ):
                return {
                    "status": "registered-candidate-fingerprint-mismatch",
                    "ready": False,
                    "database": database_name,
                    "candidateDeploymentId": candidate_deployment_id,
                    "candidateRuleboxFingerprint": candidate_fingerprint,
                    "frozenRuleboxFingerprint": frozen_candidate_fingerprint,
                    "reason": (
                        "The seeded RuleBox differs from the fingerprint already "
                        "frozen for the registered candidate release."
                    ),
                }
            return {
                "status": "registered-candidate-ready",
                "ready": True,
                "database": database_name,
                "candidateDeploymentId": candidate_deployment_id,
                "candidateRuleboxFingerprint": candidate_fingerprint,
                "governedDeployments": [{
                    "deploymentId": candidate_deployment_id,
                    "status": str(registered_candidate.get("status") or ""),
                    "frozenRuleboxFingerprint": frozen_candidate_fingerprint,
                }],
            }
        mismatches = [
            item for item in governed
            if item["frozenRuleboxFingerprint"] != candidate_fingerprint
        ]
        if mismatches:
            return {
                "status": "release-fingerprint-mismatch",
                "ready": False,
                "database": database_name,
                "candidateRuleboxFingerprint": candidate_fingerprint,
                "governedDeployments": governed,
                "reason": (
                    "The candidate RuleBox differs from the frozen delivery release; "
                    "register and validate a new reasoning deployment before rotating storage."
                ),
            }
        return {
            "status": "ready",
            "ready": True,
            "database": database_name,
            "candidateRuleboxFingerprint": candidate_fingerprint,
            "governedDeployments": governed,
        }
    except Exception as error:  # noqa: BLE001 - maintenance must fail closed.
        return {
            "status": "release-contract-error",
            "ready": False,
            "database": database_name,
            "reason": str(error)[:300],
        }


def ensure_typedb_shared_world_projection_rebuilt(
    spec: Dict[str, object],
    force: bool = False,
) -> bool:
    """Restore MySQL-backed shared-world inputs before dependent workers run."""
    if str(spec.get("role") or "") not in {"typedb", "typedb-stage"}:
        return True
    if not force and not typedb_shared_world_projection_rebuild_pending():
        return True
    command = typedb_shared_world_projection_rebuild_command(spec)
    timeout_seconds = int_value(spec.get("sharedWorldProjectionRebuildTimeoutSeconds"), 900, 30)
    append_log(spec["log"], "shared-world rebuild start")
    print(str(spec["label"]) + " rebuilding shared MarketWorld/KnowledgeWorld from durable outbox.")
    try:
        result = subprocess.run(
            low_priority_command(spec, command),
            cwd=str(ROOT_DIR),
            env=typedb_subprocess_environment(spec),
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
    if not force:
        clear_typedb_shared_world_projection_rebuild_pending()
    print(str(spec["label"]) + " shared-world rebuild ok.")
    return True


def ensure_typedb_portfolio_world_projection_rebuilt(spec: Dict[str, object]) -> bool:
    """Rebuild every current account world before a candidate cutover."""
    if str(spec.get("role") or "") != "typedb-stage":
        return True
    command = typedb_portfolio_world_projection_rebuild_command(spec)
    timeout_seconds = int_value(spec.get("portfolioWorldProjectionRebuildTimeoutSeconds"), 1800, 60)
    append_log(spec["log"], "portfolio-world rebuild start")
    print(str(spec["label"]) + " rebuilding current PortfolioWorlds from durable MySQL snapshots.")
    environment = typedb_subprocess_environment(spec)
    # This subprocess targets a database created moments earlier by this
    # manager. It cannot contain a prior PortfolioWorld ABox, so the recorder
    # may skip historical storage-identity reads while retaining every normal
    # live-path reuse and conflict check.
    environment["TYPEDB_FRESH_CANDIDATE_REBUILD"] = "1"
    try:
        result = subprocess.run(
            low_priority_command(spec, command),
            cwd=str(ROOT_DIR),
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + ("\n" if error.stdout and error.stderr else "") + (error.stderr or "")
        append_log_text(spec["log"], "portfolio-world rebuild timeout", output)
        print(str(spec["label"]) + " portfolio-world rebuild timed out after " + str(timeout_seconds) + "s.")
        return False
    output = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
    if result.returncode != 0:
        append_log_text(spec["log"], "portfolio-world rebuild failed exit=" + str(result.returncode), output)
        print(str(spec["label"]) + " portfolio-world rebuild failed. exit=" + str(result.returncode))
        return False
    append_log_text(spec["log"], "portfolio-world rebuild ok", output)
    print(str(spec["label"]) + " portfolio-world rebuild ok.")
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
    """Mark RuleBox compilation as required after a new TypeDB server is ready.

    A bounded cooldown protects a server-side schema commit after its client
    disconnects.  A managed TypeDB restart terminates that commit, so carrying
    the old hand-off into the new server lifetime would delay prewarm for no
    safety benefit. Do not mark the RuleBox as idle, however: the next native
    inference must wait for receipts from this TypeDB server lifetime. The
    prewarm scheduler uses this durable marker to break the otherwise circular
    wait between a cold receipt and a non-empty mailbox.
    """
    settings = dict(runtime_settings())
    settings["_skipOperationalHistoryRetention"] = "1"
    settings["_skipOperationalSchemaBootstrap"] = "1"
    try:
        MySQLOntologyRuleboxPrewarmStateStore(settings).replace({
            "status": "bootstrap-required",
            "active": False,
            "updatedAt": iso_now(),
            "expiresAtEpoch": 0,
            "reason": "typedb-server-restarted-require-rulebox-receipt",
            "lastResult": {
                "status": "bootstrap-required",
                "functionsReady": False,
                "reason": "TypeDB server restarted; RuleBox receipts must be verified before native investment inference.",
            },
        })
    except Exception:  # noqa: BLE001 - a stale hint must never fail a healthy graph start.
        return False
    return True


def start_worker(spec: Dict[str, object]) -> int:
    if spec.get("missingReason") or not spec.get("command"):
        print(str(spec["label"]) + " not started. " + str(spec.get("missingReason") or "Command is not configured."))
        return 1 if str(spec.get("role") or "") in {"mysql", "typedb", "questdb", "web"} else 0
    pid_path = spec["pid"]
    log_path = spec["log"]
    role = str(spec.get("role") or "")
    existing = read_pid(pid_path)
    if is_running(existing, spec):
        print(str(spec["label"]) + " already running.")
        if role == "typedb":
            if not wait_for_typedb_ready(spec):
                return 1
            # A healthy TypeDB server may be serving an ABox staging write.
            # Seeding is only required after this manager starts a new server;
            # repeating it on every generic worker restart can interrupt that
            # write and needlessly rewrites the static ontology boxes.
        elif role == "mysql" and not ensure_mysql_operational_schema(spec):
            # The previous supervisor attempt may have started MySQL but lost
            # its first schema query while InnoDB was still recovering. Keep
            # the healthy server and retry the idempotent bootstrap boundary.
            return 1
        return status_worker(spec)
    if existing:
        remove_pid(pid_path)
    if role == "questdb":
        recovered = recover_project_questdb_pid(spec)
        if recovered and is_running(recovered, spec):
            pid_path.write_text(str(recovered) + "\n", encoding="utf-8")
            os.chmod(pid_path, 0o600)
            append_log(log_path, "adopt orphaned project QuestDB pid=" + str(recovered))
            print(str(spec["label"]) + " adopted existing project process. pid=" + str(recovered))
            return status_worker(spec)
    if role in {"mysql", "questdb", "web"} and tcp_ready(spec.get("healthAddress")):
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
    process_env = managed_process_environment(spec)
    process = subprocess.Popen(
        low_priority_command(spec, spec["command"]),
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
    elif role in {"mysql", "questdb", "web"}:
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
    # `is_running` deliberately treats an unmanaged web listener on the
    # canonical port as healthy.  A stale managed PID must not inherit that
    # listener's health and receive a signal after its process has exited.
    if not pid_exists(pid):
        remove_pid(pid_path)
        print(str(spec["label"]) + " was not running. Removed stale pid file.")
        return 0
    if not is_running(pid, spec):
        remove_pid(pid_path)
        print(str(spec["label"]) + " was not running. Removed stale pid file.")
        return 0
    role = str(spec.get("role") or "")
    process_group = 0
    if role == "questdb" and os.name != "nt":
        try:
            process_group = os.getpgid(pid)
        except OSError:
            process_group = 0
    try:
        if process_group:
            os.killpg(process_group, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        remove_pid(pid_path)
        append_log(log_path, "stop")
        print(str(spec["label"]) + " stopped before signal delivery. pid=" + str(pid))
        return 0
    attempts = 150 if str(spec.get("role") or "") in {"mysql", "typedb", "typedb-stage"} else 25
    for _index in range(attempts):
        time.sleep(0.2)
        still_running = process_group_exists(process_group) if process_group else is_running(pid, spec)
        if not still_running:
            remove_pid(pid_path)
            append_log(log_path, "stop")
            print(str(spec["label"]) + " stopped. pid=" + str(pid))
            return 0
    try:
        if process_group:
            os.killpg(process_group, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        remove_pid(pid_path)
        append_log(log_path, "stop")
        print(str(spec["label"]) + " stopped before forced termination. pid=" + str(pid))
        return 0
    remove_pid(pid_path)
    append_log(log_path, "kill")
    print(str(spec["label"]) + " killed. pid=" + str(pid))
    return 0


def status() -> int:
    for spec in worker_specs().values():
        status_worker(spec)
    configured = launch_agent_path().exists() and bool(shutil.which("launchctl"))
    print(
        "Orbit Alpha runtime supervisor: "
        + ("running" if supervisor_running() else "stopped")
        + " · launch-agent="
        + ("configured" if configured else "not-configured")
    )
    return 0


def start(excluded_roles=None) -> int:
    excluded = {str(role or "").strip() for role in (excluded_roles or set())}
    for _name, spec in ordered_worker_specs(worker_specs()):
        if str(spec.get("role") or "").strip() in excluded:
            continue
        result = start_worker(spec)
        if result != 0:
            if str(spec.get("role") or "").strip() == "web":
                # A user-managed web process may own the canonical port while
                # data collection and notification workers remain healthy.
                continue
            print("Service start aborted before dependent workers. failed=" + str(spec.get("label") or "unknown"))
            return result
    return 0


def stop(excluded_roles=None, include_supervisor: bool = True) -> int:
    if include_supervisor:
        stop_supervisor()
    excluded = {str(role or "").strip() for role in (excluded_roles or set())}
    specs = worker_specs()
    specs.update(disabled_notification_ai_worker_specs(specs))
    specs.update(disabled_reasoning_worker_specs(specs))
    for spec in reversed(list(specs.values())):
        if str(spec.get("role") or "").strip() in excluded:
            continue
        stop_worker(spec)
    return 0


def restart(
    restart_typedb: bool = False,
    restart_mysql: bool = False,
    restart_share: bool = False,
) -> int:
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
    if not restart_share:
        share_spec = worker_specs().get("cloudflare-share")
        share_pid_path = share_spec.get("pid") if isinstance(share_spec, dict) else None
        if share_spec and share_pid_path and is_running(read_pid(share_pid_path), share_spec):
            excluded.add("cloudflare-share")
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


def configured_supervisor_available() -> bool:
    return bool(launch_agent_path().exists() and shutil.which("launchctl"))


def restore_configured_supervisor() -> int:
    """Reattach launchd after an explicit stop/start maintenance cycle."""

    if supervisor_running():
        return 0
    if not configured_supervisor_available():
        return 0
    result = install_supervisor()
    if result == 0:
        print("Orbit Alpha runtime supervisor restored after service maintenance.")
    return result


def reload_configured_supervisor() -> int:
    """Reload only the launchd supervisor without stopping managed workers."""

    if not configured_supervisor_available():
        return 0
    if supervisor_running():
        previous_pid = read_pid(supervisor_pid_path())
        try:
            # SIGHUP is intentionally not handled by ``supervise``. launchd
            # replaces only the supervisor process, so its worker-shutdown
            # ``finally`` block does not run during a code handoff.
            os.kill(previous_pid, signal.SIGHUP)
        except OSError:
            remove_pid(supervisor_pid_path())
        else:
            if wait_for_supervisor_replacement(previous_pid):
                print("Orbit Alpha runtime supervisor reloaded after service restart.")
                return 0
    result = install_supervisor()
    if result == 0:
        print("Orbit Alpha runtime supervisor reloaded after service restart.")
    return result


def wait_for_supervisor_replacement(previous_pid: int, timeout_seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds or 15.0))
    while time.monotonic() <= deadline:
        current_pid = read_pid(supervisor_pid_path())
        if (
            current_pid
            and current_pid != int(previous_pid or 0)
            and pid_exists(current_pid)
            and "monitor_service.py supervise" in command_for_pid(current_pid)
        ):
            return True
        time.sleep(0.1)
    return False


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
        unavailable_optional_workers = {}
        while not stopping["value"]:
            if supervisor_maintenance_active():
                acknowledge_supervisor_maintenance()
                time.sleep(1)
                continue
            specs = worker_specs()
            for spec in disabled_notification_ai_worker_specs(specs).values():
                stop_worker(spec)
            for spec in disabled_reasoning_worker_specs(specs).values():
                stop_worker(spec)
            for name, spec in ordered_worker_specs(specs):
                if stopping["value"]:
                    break
                if supervisor_maintenance_active():
                    acknowledge_supervisor_maintenance()
                    break
                missing_reason = str(spec.get("missingReason") or "").strip()
                if missing_reason:
                    if unavailable_optional_workers.get(name) != missing_reason:
                        append_log(
                            supervisor_log_path(),
                            "optional worker unavailable " + name + ": " + missing_reason,
                        )
                        unavailable_optional_workers[name] = missing_reason
                    continue
                unavailable_optional_workers.pop(name, None)
                pid = read_pid(spec["pid"])
                if not is_running(pid, spec):
                    append_log(supervisor_log_path(), "restart " + str(spec.get("label") or "unknown"))
                    start_worker(spec)
            if time.monotonic() - last_maintenance_at >= 60:
                typedb_spec = specs.get("typedb")
                decision = typedb_reset_needed(typedb_spec, ignore_auto_reset=True) if typedb_spec else {}
                automatic = typedb_auto_rotation_needed(typedb_spec) if typedb_spec else {}
                if automatic.get("needed"):
                    if not bool(automatic.get("stagingReady", True)):
                        notice = str(
                            automatic.get("reason")
                            or "TypeDB blue-green staging headroom is insufficient"
                        )
                        if notice != last_typedb_auto_rotation_notice:
                            append_log(
                                supervisor_log_path(),
                                "typedb automatic rotation deferred; " + notice,
                            )
                            last_typedb_auto_rotation_notice = notice
                        last_maintenance_at = time.monotonic()
                        continue
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
                if typedb_spec:
                    removed_retired = prune_retired_typedb_data_paths(typedb_spec)
                    if removed_retired:
                        append_log(
                            supervisor_log_path(),
                            "typedb retired blue-green stores removed count=" + str(len(removed_retired)),
                        )
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
        "SoftResourceLimits": {"NumberOfFiles": 65536},
        "HardResourceLimits": {"NumberOfFiles": 65536},
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
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


def typedb_blue_green_stage_spec(spec: Dict[str, object]) -> Dict[str, object]:
    """Build a fully isolated candidate server next to the active TypeDB."""
    host, port = typedb_host_port(spec.get("healthAddress"))
    http_host, http_port = typedb_host_port(spec.get("httpAddress") or "127.0.0.1:8000")
    offset = int_value(spec.get("blueGreenStagePortOffset"), 1, 1)
    active_path = Path(spec.get("dataPath") or data_dir() / "typedb-data")
    candidate_path = active_path.with_name(active_path.name + "-candidate")
    candidate_log_dir = data_dir() / "typedb-logs-candidate"
    candidate_pid = data_dir() / "typedb-candidate.pid"
    candidate_log = data_dir() / "typedb-candidate.log"
    address = str(host) + ":" + str(port + offset)
    http_address = str(http_host) + ":" + str(http_port + offset)
    executable = str((spec.get("command") or [""])[0] or typedb_executable())
    return {
        **dict(spec or {}),
        "label": "TypeDB ontology graph store candidate",
        "role": "typedb-stage",
        "processNice": str(spec.get("blueGreenProcessNice") or "10"),
        "pid": candidate_pid,
        "log": candidate_log,
        "needle": "typedb_server_bin",
        "dataPath": candidate_path,
        # A blue-green candidate starts from an empty data directory. Active
        # instances may deliberately skip startup seeding, but inheriting that
        # switch here produces a database without the TBox and makes the world
        # replay fail on its first ontology-node write.
        "seedOnStart": "1",
        "seedReplaceRuleBox": "1",
        # TypeDB 3.12 can spend more than 15 minutes compiling the first cold
        # schema commit. Interrupting it during checkpoint replacement may
        # leave a transient checkpoint directory that crashes the next server.
        "seedTimeoutSeconds": str(max(
            1200,
            int_value(spec.get("seedTimeoutSeconds"), 900, 1),
        )),
        "healthAddress": address,
        "httpAddress": http_address,
        "command": [
            executable,
            "server",
            "--server.listen-address", address,
            "--server.advertise-address", address,
            "--server.http.listen-address", http_address,
            "--diagnostics.monitoring.enabled", "false",
            "--diagnostics.reporting.metrics", "false",
            "--diagnostics.reporting.errors", "false",
            "--storage.data-directory", str(candidate_path),
            "--logging.directory", str(candidate_log_dir),
        ],
    }


def typedb_blue_green_database_specs(candidate: Dict[str, object]) -> List[Dict[str, object]]:
    """Return one candidate contract for every deployed reasoning database."""
    primary = str(candidate.get("typedbDatabase") or "orbit_alpha_ontology").strip()
    names = []
    for value in [primary, *(candidate.get("managedTypeDbDatabases") or [])]:
        clean = str(value or "").strip()
        if clean and clean not in names:
            names.append(clean)
    return [
        {
            **dict(candidate),
            "label": str(candidate.get("label") or "TypeDB candidate") + " [" + name + "]",
            "typedbDatabase": name,
        }
        for name in names
    ]


def wait_for_fresh_typedb_candidate(spec: Dict[str, object]) -> bool:
    wait_seconds = int_value(spec.get("startupWaitSeconds"), 600, 30)
    deadline = time.monotonic() + wait_seconds
    configured = False
    while time.monotonic() <= deadline:
        pid = read_pid(spec["pid"])
        if pid and not pid_exists(pid):
            return False
        if tcp_ready(spec.get("healthAddress")):
            if typedb_driver_ready(spec):
                return True
            if not configured:
                configured = True
                if configure_fresh_typedb_credentials(spec):
                    continue
        time.sleep(0.5)
    return False


def prepare_typedb_blue_green_candidate(spec: Dict[str, object]) -> Dict[str, object]:
    candidate = typedb_blue_green_stage_spec(spec)
    candidate_path = Path(candidate["dataPath"])
    stop_worker(candidate)
    if not stop_typedb_stage_data_path_processes(candidate):
        return {
            "status": "candidate-owner-stop-failed",
            "candidate": candidate,
        }
    if candidate_path.exists():
        shutil.rmtree(candidate_path)
    remove_pid(candidate["pid"])
    try:
        if not launch_typedb_stage_process(candidate, "blue-green candidate start"):
            return {"status": "candidate-start-failed", "candidate": candidate}
        validated_databases = []
        validated_inference_modes = {}
        validated_release_contracts = {}
        for database_spec in typedb_blue_green_database_specs(candidate):
            database_name = str(database_spec.get("typedbDatabase") or "")
            if not ensure_typedb_seeded(database_spec):
                return {
                    "status": "candidate-seed-failed",
                    "database": database_name,
                    "candidate": candidate,
                }
            inference_readiness = validate_typedb_candidate_inference_runtime(database_spec)
            if not bool(inference_readiness.get("ready")):
                return {
                    "status": "candidate-inference-readiness-failed",
                    "database": database_name,
                    "inferenceReadiness": inference_readiness,
                    "candidate": candidate,
                }
            release_contract = validate_typedb_candidate_release_contract(database_spec)
            if not bool(release_contract.get("ready")):
                return {
                    "status": "candidate-release-contract-failed",
                    "database": database_name,
                    "releaseContract": release_contract,
                    "candidate": candidate,
                }
            if not ensure_typedb_shared_world_projection_rebuilt(database_spec, force=True):
                return {
                    "status": "candidate-world-rebuild-failed",
                    "database": database_name,
                    "candidate": candidate,
                }
            if not ensure_typedb_portfolio_world_projection_rebuilt(database_spec):
                return {
                    "status": "candidate-portfolio-rebuild-failed",
                    "database": database_name,
                    "candidate": candidate,
                }
            if not typedb_driver_ready(database_spec):
                return {
                    "status": "candidate-validation-failed",
                    "database": database_name,
                    "candidate": candidate,
                }
            validated_databases.append(database_name)
            validated_inference_modes[database_name] = str(
                inference_readiness.get("mode") or "unknown"
            )
            validated_release_contracts[database_name] = str(
                release_contract.get("status") or "unknown"
            )
        return {
            "status": "prepared",
            "candidate": candidate,
            "candidateSizeBytes": directory_size_bytes(candidate_path),
            "validatedDatabases": validated_databases,
            "validatedInferenceModes": validated_inference_modes,
            "validatedReleaseContracts": validated_release_contracts,
        }
    except Exception as error:  # noqa: BLE001 - active store stays untouched.
        return {
            "status": "candidate-error",
            "reason": str(error)[:300],
            "candidate": candidate,
        }


def cleanup_typedb_candidate(candidate: Dict[str, object], remove_data: bool = True) -> None:
    if candidate:
        stop_worker(candidate)
        owners_stopped = stop_typedb_stage_data_path_processes(candidate)
        if remove_data:
            path = Path(candidate.get("dataPath") or "")
            if owners_stopped and path.exists():
                shutil.rmtree(path)


def swap_typedb_blue_green_data_paths(spec: Dict[str, object], candidate: Dict[str, object]) -> Dict[str, object]:
    active_path = Path(spec.get("dataPath") or data_dir() / "typedb-data")
    candidate_path = Path(candidate.get("dataPath") or "")
    retired_path = active_path.with_name(active_path.name + "-retired-" + str(int(time.time())))
    if not candidate_path.exists():
        return {"status": "candidate-missing"}
    if active_path.exists():
        os.replace(active_path, retired_path)
        # ``os.replace`` preserves the old directory mtime. Using that stale
        # timestamp for retention can delete the rollback store immediately
        # after a successful cutover. Stamp the retirement boundary now; the
        # epoch in the path remains the durable fallback when mtime changes.
        os.utime(retired_path, None)
    try:
        os.replace(candidate_path, active_path)
    except Exception:
        if retired_path.exists() and not active_path.exists():
            os.replace(retired_path, active_path)
        raise
    marker = read_typedb_retention_marker()
    marker.update({
        "blueGreenCutoverPending": True,
        "blueGreenPreparedDataPath": str(active_path),
        "blueGreenRetiredDataPath": str(retired_path),
        "blueGreenCutoverAt": iso_now(),
        "credentialsBootstrapPending": False,
        "sharedWorldProjectionRebuildPending": False,
    })
    write_typedb_retention_marker(marker)
    return {"status": "swapped", "activePath": str(active_path), "retiredPath": str(retired_path)}


def rollback_typedb_blue_green_data_paths(spec: Dict[str, object], retired_path: object) -> Dict[str, object]:
    active_path = Path(spec.get("dataPath") or data_dir() / "typedb-data")
    rollback_path = Path(str(retired_path or ""))
    if not rollback_path.exists():
        return {"status": "rollback-source-missing", "retiredPath": str(rollback_path)}
    failed_path = active_path.with_name(active_path.name + "-failed-" + str(int(time.time())))
    if active_path.exists():
        os.replace(active_path, failed_path)
    try:
        os.replace(rollback_path, active_path)
    except Exception:
        if failed_path.exists() and not active_path.exists():
            os.replace(failed_path, active_path)
        raise
    marker = read_typedb_retention_marker()
    marker.update({
        "blueGreenCutoverPending": False,
        "blueGreenRollbackAt": iso_now(),
        "blueGreenRollbackSourcePath": str(rollback_path),
        "blueGreenFailedDataPath": str(failed_path),
        "blueGreenPreparedDataPath": "",
        "credentialsBootstrapPending": False,
        "sharedWorldProjectionRebuildPending": False,
    })
    write_typedb_retention_marker(marker)
    return {"status": "rolled-back", "activePath": str(active_path), "failedPath": str(failed_path)}


def prune_retired_typedb_data_paths(spec: Dict[str, object]) -> List[str]:
    active_path = Path(spec.get("dataPath") or data_dir() / "typedb-data")
    retention_minutes = int_value(spec.get("blueGreenRetiredRetentionMinutes"), 30, 0)
    cutoff = time.time() - retention_minutes * 60
    removed = []
    for path in active_path.parent.glob(active_path.name + "-retired-*"):
        try:
            suffix = path.name.rsplit("-retired-", 1)[-1]
            try:
                retired_at = float(int(suffix))
            except (TypeError, ValueError):
                retired_at = 0.0
            # Prefer the later trustworthy boundary. Existing directories may
            # have a legacy suffix but a corrected mtime, while newly swapped
            # paths carry both the cutover epoch and the explicit stamp.
            retention_boundary = max(float(path.stat().st_mtime), retired_at)
            if retention_boundary <= cutoff:
                shutil.rmtree(path)
                removed.append(str(path))
        except OSError:
            continue
    return removed


def typedb_rotate(
    force: bool = False,
    supervisor_owned: bool = False,
    rotation_reason: str = "",
) -> int:
    """Rebuild TypeDB beside the active store, then perform a bounded cutover.

    The default path seeds and validates an isolated candidate while the live
    server remains available. Only the directory swap restarts dependents. A
    retained rollback directory restores the prior store when cutover startup
    fails. The legacy full-stop reset remains an explicit compatibility path.
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

    resource_preflight = typedb_rotation_resource_preflight(spec)
    if not bool(resource_preflight.get("ready")):
        result = {
            "status": "deferred-resource-pressure",
            "reason": str(resource_preflight.get("reason") or "host resource pressure"),
            "resourcePreflight": resource_preflight,
            "activeStorePreserved": True,
        }
        record_typedb_auto_rotation_state(
            lastAutoRotationAttemptAt=iso_now(),
            lastAutoRotationAttemptEpoch=time.time(),
            lastAutoRotationReason=str(rotation_reason or decision.get("reason") or "manual"),
            lastAutoRotationStatus="deferred-resource-pressure",
            lastAutoRotationResult=result,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
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
    candidate = {}
    services_stopped = False
    restart_attempted = False
    try:
        spec["_typedbMaintenanceLock"] = dict(rotation_lock)
        # Manual and supervisor-owned rotations share one cooldown/status
        # contract. Omitting a successful manual cutover here lets the
        # supervisor immediately launch another expensive candidate because
        # it still sees the preceding failed attempt.
        record_typedb_auto_rotation_state(
            lastAutoRotationAttemptAt=iso_now(),
            lastAutoRotationAttemptEpoch=time.time(),
            lastAutoRotationReason=str(
                rotation_reason
                or decision.get("reason")
                or ("capacity" if supervisor_owned else "manual")
            ),
            lastAutoRotationStatus="running",
        )
        if truthy(spec.get("blueGreenRotationEnabled")):
            prepared = prepare_typedb_blue_green_candidate(spec)
            candidate = dict(prepared.get("candidate") or {})
            if prepared.get("status") != "prepared":
                cleanup_typedb_candidate(candidate, remove_data=True)
                result = {
                    "status": "candidate-failed-active-preserved",
                    "reason": str(prepared.get("reason") or prepared.get("status") or "candidate validation failed"),
                    "database": str(prepared.get("database") or ""),
                    "activeStorePreserved": True,
                }
                record_typedb_auto_rotation_state(
                    lastAutoRotationFinishedAt=iso_now(),
                    lastAutoRotationStatus="candidate-failed-active-preserved",
                    lastAutoRotationResult=result,
                )
                result["failureIncident"] = record_typedb_auto_rotation_incident(
                    spec,
                    decision,
                    alert_kind="typedb-auto-rotation-failed",
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
                return 1
            if not typedb_maintenance_lock_owned(rotation_lock):
                cleanup_typedb_candidate(candidate, remove_data=False)
                result = {
                    "status": "cutover-fenced-active-preserved",
                    "reason": "TypeDB maintenance ownership changed before cutover.",
                    "activeStorePreserved": True,
                }
                record_typedb_auto_rotation_state(
                    lastAutoRotationFinishedAt=iso_now(),
                    lastAutoRotationStatus="cutover-fenced-active-preserved",
                    lastAutoRotationResult=result,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
                return 1
            cleanup_typedb_candidate(candidate, remove_data=False)
            services_stopped = True
            stop(include_supervisor=False)
            result = swap_typedb_blue_green_data_paths(spec, candidate)
        else:
            services_stopped = True
            stop(include_supervisor=False)
            try:
                result = run_typedb_data_retention(spec, force=True)
            except Exception as error:  # noqa: BLE001 - the stopped runtime must still be recovered.
                result = {
                    "status": "reset-failed",
                    "reason": str(error)[:300],
                    "errorType": error.__class__.__name__,
                }
        if result.get("status") not in {"reset", "swapped"}:
            recovery_status = start()
            restart_attempted = True
            result["restartStatus"] = "ok" if recovery_status == 0 else "failed"
            record_typedb_auto_rotation_state(
                lastAutoRotationFinishedAt=iso_now(),
                lastAutoRotationStatus="reset-failed",
                lastAutoRotationResult=dict(result),
            )
            result["failureIncident"] = record_typedb_auto_rotation_incident(
                spec,
                decision,
                alert_kind="typedb-auto-rotation-failed",
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 1
        start_status = start()
        restart_attempted = True
        result["restartStatus"] = "ok" if start_status == 0 else "failed"
        if start_status != 0 and result.get("status") == "swapped":
            stop(include_supervisor=False)
            rollback = rollback_typedb_blue_green_data_paths(spec, result.get("retiredPath"))
            rollback_start_status = start()
            result["rollback"] = {
                **rollback,
                "restartStatus": "ok" if rollback_start_status == 0 else "failed",
            }
            start_status = 1
        if start_status == 0 and result.get("status") == "swapped":
            result["retiredPathsRemoved"] = prune_retired_typedb_data_paths(spec)
        record_typedb_auto_rotation_state(
            lastAutoRotationFinishedAt=iso_now(),
            lastAutoRotationStatus="ok" if start_status == 0 else "restart-failed",
            lastAutoRotationResult={
                "status": result.get("status"),
                "restartStatus": result.get("restartStatus"),
                "previousSizeBytes": result.get("previousSizeBytes"),
            },
        )
        if start_status != 0:
            result["failureIncident"] = record_typedb_auto_rotation_incident(
                spec,
                decision,
                alert_kind="typedb-auto-rotation-failed",
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return start_status
    finally:
        if services_stopped and not restart_attempted:
            try:
                start()
            except Exception as error:  # noqa: BLE001 - launchd remains the final recovery boundary.
                append_log(
                    supervisor_log_path(),
                    "typedb rotation emergency restart failed. " + str(error)[:180],
                )
        if pause_supervisor or supervisor_owned:
            end_supervisor_maintenance()
        if candidate and read_pid(candidate.get("pid")):
            cleanup_typedb_candidate(candidate, remove_data=False)
        release_typedb_rotation_lock(rotation_lock)


def main(argv: List[str] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    command = args[0] if args else "status"
    if command == "start":
        if configured_supervisor_available():
            return restore_configured_supervisor()
        return start()
    if command == "stop":
        return stop()
    if command == "restart":
        result = restart(
            restart_typedb="--restart-typedb" in args[1:],
            restart_mysql="--restart-mysql" in args[1:],
            restart_share="--restart-share" in args[1:],
        )
        # A long-lived supervisor imports worker definitions only once. Reload
        # it after a code deployment so it cannot recreate workers using an
        # older routing policy after the managed workers have restarted.
        supervisor_result = reload_configured_supervisor()
        return result if result != 0 else supervisor_result
    if command == "status":
        return status()
    if command == "typedb-maintenance":
        return typedb_maintenance(force="--force" in args[1:])
    if command == "typedb-rotate":
        result = typedb_rotate(force="--force" in args[1:])
        supervisor_result = restore_configured_supervisor()
        return result if result != 0 else supervisor_result
    if command == "supervise":
        return supervise()
    if command == "supervisor-install":
        return install_supervisor()
    if command == "supervisor-uninstall":
        return uninstall_supervisor()
    print("Usage: python3 python_service/monitor_service.py start|stop|restart [--restart-share]|status|supervise|supervisor-install|supervisor-uninstall|typedb-maintenance|typedb-rotate [--force]")
    return 1
