"""A separate local MySQL process for fixtures; never fall back to the app DB."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "data" / "test-runtime" / "mysql-isolated"
_VERIFIED = set()


class TestMySQLSetupError(RuntimeError):
    pass


def _identity(values):
    return (values["MYSQL_HOST"], values["MYSQL_PORT"], values["MYSQL_UNIX_SOCKET"])


def _probe(values, expected_directory=None):
    try:
        import pymysql
    except ImportError as error:
        raise TestMySQLSetupError("Install the existing PyMySQL test dependency before starting MySQL fixtures.") from error
    kwargs = {
        "host": values["MYSQL_HOST"], "port": int(values["MYSQL_PORT"]),
        "user": values["MYSQL_USER"], "password": values["MYSQL_PASSWORD"],
        "connect_timeout": 2, "read_timeout": 2, "autocommit": True,
    }
    if values["MYSQL_UNIX_SOCKET"]:
        kwargs["unix_socket"] = values["MYSQL_UNIX_SOCKET"]
    with pymysql.connect(**kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@port, @@datadir, @@server_uuid")
            port, directory, server = cursor.fetchone()
    primary = Path(os.environ.get("MYSQL_DATA_DIR") or ROOT / "data" / "mysql-runtime").resolve()
    if Path(directory).resolve() == primary:
        raise TestMySQLSetupError("Tests cannot use the project MySQL data directory.")
    if expected_directory is not None and Path(directory).resolve() != expected_directory.resolve():
        raise TestMySQLSetupError("The isolated socket does not belong to the managed test data directory.")
    if not values["MYSQL_UNIX_SOCKET"] and int(port) != int(values["MYSQL_PORT"]):
        raise TestMySQLSetupError("The test MySQL endpoint does not match its server port.")
    return str(server)


def _managed_endpoint():
    socket = RUNTIME / "mysql.sock"
    if len(str(socket).encode()) > 95:
        digest = hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
        socket = Path(tempfile.gettempdir()) / ("orbit-test-mysql-" + digest + ".sock")
    return {
        "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "33307",
        "MYSQL_USER": "root", "MYSQL_PASSWORD": "", "MYSQL_UNIX_SOCKET": str(socket),
        "MYSQL_URL": "", "DATABASE_URL": "",
    }


def _ensure_managed():
    values = _managed_endpoint()
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUNTIME / "startup.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            _probe(values, RUNTIME / "data")
            return values
        except TestMySQLSetupError:
            raise
        except Exception:
            pass
        executable = shutil.which("mysqld")
        if not executable:
            raise TestMySQLSetupError("Install mysqld or configure an explicit MYSQL_TEST_HOST/PORT endpoint.")
        executable = str(Path(executable).resolve())
        basedir = str(Path(executable).parent.parent)
        data = RUNTIME / "data"
        log = RUNTIME / "mysql.log"
        pid_file = RUNTIME / "mysql.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), 0)
            except (OSError, ValueError):
                pass
            else:
                raise TestMySQLSetupError("The isolated MySQL process is alive but not ready; inspect its local log.")
        with log.open("ab") as output:
            process_env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
            if not (data / "mysql").exists():
                data.mkdir(parents=True, exist_ok=True)
                result = subprocess.run(
                    [executable, "--no-defaults", "--initialize-insecure", "--basedir=" + basedir, "--datadir=" + str(data)],
                    stdout=output, stderr=subprocess.STDOUT, timeout=30, env=process_env,
                )
                if result.returncode:
                    raise TestMySQLSetupError("Isolated MySQL initialization failed; inspect data/test-runtime/mysql-isolated/mysql.log.")
            process = subprocess.Popen([
                executable, "--no-defaults", "--basedir=" + basedir, "--datadir=" + str(data),
                "--bind-address=127.0.0.1", "--port=" + values["MYSQL_PORT"],
                "--socket=" + values["MYSQL_UNIX_SOCKET"], "--pid-file=" + str(pid_file),
                "--mysqlx=0", "--skip-log-bin", "--innodb-buffer-pool-size=134217728",
                "--innodb-redo-log-capacity=134217728", "--innodb-log-writer-threads=OFF",
                "--max-connections=64",
            ], stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True, env=process_env)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise TestMySQLSetupError("Isolated MySQL exited during startup; inspect its local log.")
            try:
                _probe(values, data)
                return values
            except TestMySQLSetupError:
                raise
            except Exception:
                time.sleep(0.1)
        raise TestMySQLSetupError("Isolated MySQL startup exceeded 25 seconds; no production fallback was attempted.")


def mysql_test_environment(environment=None):
    env = os.environ if environment is None else environment
    explicit = bool(env.get("MYSQL_TEST_HOST") or env.get("MYSQL_TEST_PORT") or env.get("MYSQL_TEST_UNIX_SOCKET"))
    # Only test-specific endpoint variables authorize an external test server.
    # Ordinary MYSQL_* and URL credentials always belong to the application.
    if explicit:
        values = {"MYSQL_URL": "", "DATABASE_URL": ""}
        for key, default in (("HOST", "127.0.0.1"), ("PORT", "3306"), ("USER", "root"),
                             ("PASSWORD", ""), ("UNIX_SOCKET", "")):
            values["MYSQL_" + key] = str(env.get("MYSQL_TEST_" + key, env.get("MYSQL_" + key, default)))
        values["MYSQL_UNIX_SOCKET"] = str(env.get("MYSQL_TEST_UNIX_SOCKET") or "")
        if _identity(values) not in _VERIFIED:
            try:
                _probe(values)
            except TestMySQLSetupError:
                raise
            except Exception as error:
                raise TestMySQLSetupError("The explicit isolated test MySQL endpoint is unavailable; no app DB fallback.") from error
            _VERIFIED.add(_identity(values))
        return values
    values = _managed_endpoint()
    if _identity(values) not in _VERIFIED:
        values = _ensure_managed()
    _VERIFIED.add(_identity(values))
    return values


def ensure_mysql_test_environment():
    values = mysql_test_environment()
    os.environ.update(values)
    return values


if __name__ == "__main__":
    try:
        print(json.dumps(mysql_test_environment()))
    except Exception as error:
        message = str(error) if isinstance(error, TestMySQLSetupError) else type(error).__name__
        print("Isolated MySQL test setup failed: " + message, file=sys.stderr)
        sys.exit(1)
