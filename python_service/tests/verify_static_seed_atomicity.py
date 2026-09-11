"""Opt-in test of production manifest writes on a private temporary TypeDB.

Only the manifest's physical schema is installed. This is not a production
release rebuild or full-engine recovery test. No managed server is touched.
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
from unittest.mock import patch


def run(executable):
    from typedb.driver import (
        Credentials,
        DriverOptions,
        DriverTlsConfig,
        TransactionOptions,
        TransactionType,
        TypeDB,
    )
    from digital_twin.infrastructure import typedb_ontology as api
    from digital_twin.modules.reasoning.infrastructure.static_seed.artifact import (
        ontology_seed_graph_from_artifact,
    )
    from static_seed_fixture import artifact

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        address = "127.0.0.1:" + str(probe.getsockname()[1])
    process = None
    driver = None
    with tempfile.TemporaryDirectory(prefix="orbit-static-seed-") as directory:
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
        with (root / "server.log").open("w") as log:
            try:
                environment = {
                    k: v
                    for k, v in os.environ.items()
                    if not k.startswith(("TYPEDB_", "ORBIT_", "MYSQL_"))
                }
                process = subprocess.Popen(
                    command,
                    cwd=directory,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Temporary TypeDB exited before readiness.")
                    try:
                        # Native bootstrap credentials for this new empty
                        # server only, never user or managed credentials.
                        driver = TypeDB.driver(
                            address,
                            Credentials("admin", "password"),
                            DriverOptions(
                                DriverTlsConfig.disabled(), request_timeout_millis=2000
                            ),
                        )
                        driver.databases.all()
                        break
                    except Exception:
                        if driver is not None:
                            with suppress(Exception):
                                driver.close()
                        driver = None
                        time.sleep(0.3)
                if driver is None:
                    raise TimeoutError(
                        "Temporary server readiness exceeded 90 seconds."
                    )
                database = "static_seed_atomicity_fixture"
                driver.databases.create(database)
                options = TransactionOptions(transaction_timeout_millis=10000)
                attrs = [
                    "ontology-id",
                    "ontology-storage-id",
                    "ontology-content-fingerprint",
                    "ontology-label",
                    "ontology-kind",
                    "ontology-box",
                    "ontology-tbox-class",
                    "ontology-semantic-type",
                    "ontology-updated-at",
                    "ontology-json",
                ]
                schema = "define\n" + "\n".join(
                    "attribute " + a + ", value string;" for a in attrs
                )
                schema += (
                    "\nentity ontology-node @abstract, "
                    + ", ".join(
                        "owns " + a + (" @unique" if a == "ontology-storage-id" else "")
                        for a in attrs
                    )
                    + ";\nentity ontology-entity, sub ontology-node;"
                )
                with driver.transaction(
                    database, TransactionType.SCHEMA, options
                ) as tx:
                    tx.query(schema).resolve()
                    tx.commit()
                with patch.object(api, "runtime_settings", return_value={}):
                    repo = api.TypeDBOntologyGraphRepository(address, database=database)
                imported = (
                    (
                        TypeDB,
                        Credentials,
                        DriverOptions,
                        DriverTlsConfig,
                        TransactionType,
                    ),
                    None,
                )
                repo.driver_imports = lambda: imported
                repo.open_driver = lambda _: driver
                repo.close_driver = lambda _: None
                repo.ensure_database = lambda _: None
                repo.ensure_schema = lambda *_: None
                repo.with_typedb_retries = lambda op: op()
                repo.write_transaction_options = lambda: options

                def visible():
                    with driver.transaction(
                        database, TransactionType.READ, options
                    ) as tx:
                        return list(
                            tx.query(
                                'match $n isa ontology-node, has ontology-json $json; fetch { "value": $json };'
                            )
                            .resolve()
                            .as_concept_documents()
                        )

                class FaultDriver:
                    def __init__(self, phase):
                        self.phase = phase

                    def transaction(self, *args, **kwargs):
                        transaction = driver.transaction(*args, **kwargs)
                        phase = self.phase

                        class FaultTransaction:
                            def __enter__(self):
                                transaction.__enter__()
                                return self

                            def __exit__(self, *args):
                                return transaction.__exit__(*args)

                            def query(self, query):
                                if phase == "insert" and query.startswith("insert "):
                                    raise RuntimeError(
                                        "Injected interruption after delete"
                                    )
                                return transaction.query(query)

                            def commit(self):
                                if phase == "commit":
                                    raise RuntimeError(
                                        "Injected interruption before commit"
                                    )
                                transaction.commit()
                                if phase == "ack":
                                    raise RuntimeError("Injected acknowledgement loss")

                        return FaultTransaction()

                source = artifact()
                graph = ontology_seed_graph_from_artifact(source)
                with patch.object(api, "runtime_settings", return_value={}):
                    initial = repo.save_seed_static_manifest(
                        graph, source["rules"], schema_prepared=True
                    )
                    if not initial.get("saved"):
                        raise AssertionError(
                            "Initial fixture publication failed: "
                            + str(initial.get("reason"))
                        )
                    old = visible()
                    assert len(old) == 1
                    changed_rules = [
                        dict(
                            source["rules"][0],
                            legacy_extension={"version": "fixture-next"},
                        )
                    ]
                    for phase in ("insert", "commit"):
                        repo.open_driver = lambda _, p=phase: FaultDriver(p)
                        result = repo.save_seed_static_manifest(
                            graph, changed_rules, schema_prepared=True
                        )
                        assert not result.get("saved"), phase
                        assert visible() == old, phase
                    repo.open_driver = lambda _: FaultDriver("ack")
                    ambiguous = repo.save_seed_static_manifest(
                        graph, changed_rules, schema_prepared=True
                    )
                    assert not ambiguous.get("saved")
                    assert visible() != old
                    repo.open_driver = lambda _: driver
                    retried = repo.save_seed_static_manifest(
                        graph, changed_rules, schema_prepared=True
                    )
                    assert retried.get("saved"), retried
                    rows = visible()
                    assert len(rows) == 1
                    stored = json.loads(rows[0]["value"])
                    assert (
                        stored["staticSeedFingerprint"]
                        == retried["staticSeedFingerprint"]
                    )
                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "nativeRollbackAfterDelete": True,
                            "nativeRollbackBeforeCommit": True,
                            "ambiguousRetryRowCount": len(rows),
                            "productionManifestWriter": True,
                            "managedRuntimeTouched": False,
                        }
                    )
                )
            finally:
                if driver is not None:
                    with suppress(Exception):
                        driver.close()
                if process is not None and process.poll() is None:
                    # This group was spawned here, never selected by port/name.
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--typedb-command", required=True)
    run(parser.parse_args().typedb_command)
