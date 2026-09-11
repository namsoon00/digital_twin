"""Opt-in native crash rehearsal using only a new temporary TypeDB process.

This checks source-packet replay and native transaction durability, not the
entire production schema, Manifest recovery, or investment inference engine.
No existing database, credentials, process, or runtime settings are used.
"""

import argparse
from contextlib import suppress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time

from projection_input_fixture import (
    RULES,
    clear_caches,
    fingerprint,
    recorder,
    source_snapshot,
)


def free_loopback_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def run(executable):
    from typedb.driver import (
        Credentials,
        DriverOptions,
        DriverTlsConfig,
        TransactionOptions,
        TransactionType,
        TypeDB,
    )
    from digital_twin.infrastructure import ontology_projection as api
    from dataclasses import asdict

    process = None
    driver = None
    pending = None
    database = "projection_input_recovery_fixture"
    address = "127.0.0.1:" + str(free_loopback_port())
    with tempfile.TemporaryDirectory(prefix="orbit-input-recovery-") as directory:
        root = Path(directory)
        command = [
            str(Path(executable).expanduser().resolve()),
            "server",
            "--server.listen-address",
            address,
            "--server.advertise-address",
            address,
            "--server.http.enabled",
            "false",
            "--server.encryption.enabled",
            "false",
            "--diagnostics.monitoring.enabled",
            "false",
            "--diagnostics.reporting.metrics",
            "false",
            "--diagnostics.reporting.errors",
            "false",
            "--storage.data-directory",
            str(root / "storage"),
            "--logging.directory",
            str(root / "logs"),
            "--storage.rocksdb.cache-size",
            "64mb",
            "--storage.rocksdb.write-buffers-limit",
            "32mb",
        ]

        def stop(sig):
            if process is not None and process.poll() is None:
                # This process group was created here and contains only the
                # temporary server. Never identify/kill a server by port/name.
                os.killpg(process.pid, sig)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)

        def start(log):
            nonlocal process
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("TYPEDB_", "ORBIT_", "MYSQL_"))
            }
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                env=environment,
                cwd=directory,
                start_new_session=True,
            )
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        "Temporary TypeDB exited during startup; no managed process was touched."
                    )
                candidate = None
                try:
                    # Only this empty temporary server uses the native default
                    # bootstrap account. These are not user credentials.
                    candidate = TypeDB.driver(
                        address,
                        Credentials("admin", "password"),
                        DriverOptions(
                            DriverTlsConfig.disabled(), request_timeout_millis=1500
                        ),
                    )
                    candidate.databases.all()
                    return candidate
                except Exception:
                    if candidate is not None:
                        with suppress(Exception):
                            candidate.close()
                    time.sleep(0.3)
            raise TimeoutError("Temporary TypeDB startup exceeded 90 seconds.")

        options = TransactionOptions(transaction_timeout_millis=10000)

        def read_active():
            with driver.transaction(
                database, TransactionType.READ, options
            ) as transaction:
                answer = transaction.query(
                    'match $p isa fixture-packet, has fixture-key "active", has fixture-payload $payload; fetch { "payload": $payload };'
                ).resolve()
                return list(answer.as_concept_documents())

        def write_packet(transaction, payload):
            transaction.query(
                'match $p isa fixture-packet, has fixture-key "active"; delete $p;'
            ).resolve()
            transaction.query(
                'insert $p isa fixture-packet, has fixture-key "active", has fixture-payload '
                + json.dumps(payload)
                + ";"
            ).resolve()

        with (root / "process.log").open("wb") as log:
            try:
                driver = start(log)
                driver.databases.create(database)
                with driver.transaction(
                    database, TransactionType.SCHEMA, options
                ) as transaction:
                    transaction.query(
                        "define attribute fixture-key, value string; attribute fixture-payload, value string; entity fixture-packet, owns fixture-key @key, owns fixture-payload;"
                    ).resolve()
                    transaction.commit()
                snapshot = source_snapshot()
                instance = recorder(api, snapshot, cache=True)
                _, graph, _ = instance.build_graph_assembly(snapshot, RULES)
                source_hash = fingerprint(asdict(graph))
                with driver.transaction(
                    database, TransactionType.WRITE, options
                ) as transaction:
                    write_packet(transaction, source_hash)
                    transaction.commit()
                before = read_active()
                assert len(before) == 1 and before[0]["payload"] == source_hash, before
                print("isolated committed source packet: PASS", flush=True)

                pending = driver.transaction(database, TransactionType.WRITE, options)
                write_packet(pending, "uncommitted-candidate")
                stop(signal.SIGKILL)
                with suppress(Exception):
                    pending.close()
                pending = None
                with suppress(Exception):
                    driver.close()
                driver = start(log)
                assert (
                    read_active() == before
                ), "Uncommitted candidate replaced the active source packet"
                clear_caches(api)
                replay = recorder(api, snapshot, cache=True).build_graph_assembly(
                    snapshot, RULES
                )[1]
                assert (
                    fingerprint(asdict(replay)) == source_hash
                ), "Source assembly changed after a cold restart"
                with driver.transaction(
                    database, TransactionType.WRITE, options
                ) as transaction:
                    write_packet(transaction, source_hash)
                    transaction.commit()
                assert (
                    read_active() == before
                ), "Retry duplicated or changed the source packet"
                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "nativeUncommittedRollback": True,
                            "sourceReplayEquivalent": True,
                            "retryRowCount": 1,
                            "managedRuntimeTouched": False,
                        }
                    ),
                    flush=True,
                )
            finally:
                if pending is not None:
                    with suppress(Exception):
                        pending.close()
                if driver is not None:
                    with suppress(Exception):
                        driver.close()
                stop(signal.SIGTERM)
                clear_caches(api)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--typedb-command",
        required=True,
        help="Local TypeDB launcher; a separate process and temporary directory are always created.",
    )
    run(parser.parse_args().typedb_command)
