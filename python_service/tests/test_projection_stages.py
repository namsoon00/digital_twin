"""Projection phase contracts and failure ordering, independent of TypeDB I/O."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields
import importlib
import inspect
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import typing
import unittest
from unittest.mock import Mock, patch

from backend_stabilization_fixtures import STAGES, PARTICIPANTS
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from digital_twin.modules.reasoning.infrastructure.projection_write import (
    record,
    stage_results,
)


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"
PACKAGE = "digital_twin.modules.reasoning.infrastructure.projection_write."


class ProjectionStageTests(unittest.TestCase):
    def execute(self, stop="", fail=""):
        calls = []
        snapshot = SimpleNamespace(
            account_id="fixture", generated_at="2026-09-10T01:00:00Z"
        )
        source_graph = object()
        audit = object()
        terminal = {"status": "fixture-terminal", "saved": False}
        store = SimpleNamespace(
            source="fixture", settings={}, store_projection_result=Mock()
        )

        def stage(name):
            def invoke(**kwargs):
                calls.append(name)
                if "snapshot" in kwargs:
                    self.assertIs(snapshot, kwargs["snapshot"])
                if name == fail:
                    raise RuntimeError("fixture-stage-failure")
                if name == stop:
                    return stage_results.CompletedProjection(terminal)
                result_type = getattr(
                    stage_results,
                    "".join(part.title() for part in name.split("_")) + "Result",
                )
                values = {}
                for field in fields(result_type):
                    values[field.name] = kwargs.get(field.name, {})
                    if field.name in {"graph", "persistence_graph"}:
                        values[field.name] = source_graph
                    elif field.name == "projection_run":
                        values[field.name] = audit
                    elif field.name == "result":
                        values[field.name] = {
                            "saved": True,
                            "status": "fixture-published",
                        }
                if "graph" in kwargs:
                    self.assertIs(source_graph, kwargs["graph"])
                return result_type(**values)

            return invoke

        with ExitStack() as stack:
            for name in STAGES:
                stack.enter_context(patch.object(record, name, stage(name)))
            result = record.record_snapshot(
                store,
                snapshot,
                ["AAPL"],
                {"sourceObservedAt": snapshot.generated_at},
                _bindings=object(),
            )
        return result, calls, store, audit, terminal

    def test_projection_stage_order_preserves_source_identity_and_audit_handoff(self):
        result, calls, store, audit, _ = self.execute()
        self.assertEqual(list(STAGES), calls)
        self.assertEqual("fixture-published", result["status"])
        self.assertIn("performanceAssessment", result)
        self.assertIs(audit, store.store_projection_result.call_args.args[2])
        self.assertLess(
            calls.index("validate_manifest"), calls.index("publish_candidate")
        )
        self.assertLess(calls.index("create_audit"), calls.index("begin_publication"))

    def test_projection_stage_early_results_never_run_later_phases(self):
        for index, name in enumerate(STAGES):
            with self.subTest(stage=name):
                result, calls, store, _audit, terminal = self.execute(stop=name)
                self.assertIs(terminal, result)
                self.assertEqual(list(STAGES)[: index + 1], calls)
                store.store_projection_result.assert_not_called()

    def test_projection_stage_failure_keeps_created_audit_and_stops_followups(self):
        for name in ("assemble_source", "begin_publication", "publish_candidate"):
            with self.subTest(stage=name):
                result, calls, store, audit, _ = self.execute(fail=name)
                self.assertEqual("error", result["status"])
                self.assertEqual("RuntimeError", result["errorType"])
                self.assertNotIn("schedule_followups", calls)
                self.assertIs(
                    None if name == "assemble_source" else audit,
                    store.store_projection_result.call_args.args[2],
                )

    def test_projection_stage_signatures_and_result_envelopes_are_explicit(self):
        for name, entry in STAGES.items():
            with self.subTest(stage=name):
                module = importlib.import_module(PACKAGE + name)
                method = getattr(module, name)
                self.assertEqual(
                    set(entry["inputs"]), set(inspect.signature(method).parameters)
                )
                self.assertIn("return", typing.get_type_hints(method))
                result_type = getattr(
                    stage_results,
                    "".join(part.title() for part in name.split("_")) + "Result",
                )
                self.assertEqual(
                    entry["outputs"], [field.name for field in fields(result_type)]
                )
                typing.get_type_hints(result_type)
                if entry["outputs"]:
                    instance = result_type(**{key: None for key in entry["outputs"]})
                    with self.assertRaises(FrozenInstanceError):
                        setattr(instance, entry["outputs"][0], "replacement")

    def test_invalid_manifest_stops_before_graph_write_or_evidence_upgrade(self):
        module = importlib.import_module(PACKAGE + "validate_manifest")
        report = SimpleNamespace(
            status="invalid", error_count=1, to_dict=lambda: {"errorCount": 1}
        )
        store = SimpleNamespace(
            current_state_abox_storage_enabled=lambda: False,
            active_graph_store_key=lambda: "fixture",
            store_projection_result=Mock(),
        )
        kwargs = {name: {} for name in STAGES["validate_manifest"]["inputs"]}
        kwargs.update(
            _store=store,
            active_abox_complete=False,
            active_abox_is_scoped_manifest=False,
            emit_progress=Mock(),
            persistence_graph=SimpleNamespace(worldview={}),
            projection_run=None,
        )
        with patch.object(module, "validate_ontology", return_value=report):
            result = module.validate_manifest(**kwargs)
        self.assertIsInstance(result, stage_results.CompletedProjection)
        self.assertEqual("invalid-abox", result.result["status"])
        store.store_projection_result.assert_called_once()

    def test_source_audit_failure_retains_last_usable_generation(self):
        module = importlib.import_module(PACKAGE + "create_audit")
        store = SimpleNamespace(
            begin_projection_audit_run=Mock(
                return_value=(None, "fixture database unavailable")
            ),
            active_graph_store_key=lambda: "fixture",
            store_projection_result=Mock(),
        )
        kwargs = {name: {} for name in STAGES["create_audit"]["inputs"]}
        kwargs.update(
            _store=store, validation=SimpleNamespace(to_dict=lambda: {"errorCount": 0})
        )
        result = module.create_audit(**kwargs)
        self.assertEqual("source-audit-failed", result.result["status"])
        self.assertTrue(result.result["preservedActiveGeneration"])
        self.assertFalse(result.result["saved"])

    def test_owner_participants_cannot_own_commit_or_open_another_connection(self):
        for path in {entry["path"] for entry in PARTICIPANTS.values()}:
            with self.subTest(path=path):
                module = importlib.import_module(
                    "digital_twin." + ".".join(Path(path).with_suffix("").parts)
                )
                tree = ast.parse((ROOT / path).read_text())
                for function in (
                    node for node in tree.body if isinstance(node, ast.FunctionDef)
                ):
                    self.assertIs(
                        BoundWriteConnection,
                        typing.get_type_hints(getattr(module, function.name))[
                            "connection"
                        ],
                    )
                forbidden = {
                    "connect",
                    "commit",
                    "rollback",
                    "transaction",
                    "transaction_with_deadlock_retry",
                }
                calls = {
                    node.func.attr
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                }
                self.assertFalse(forbidden & calls)

    def test_receipt_publication_extraction_preserves_its_original_sql_and_scope(self):
        path = ROOT / "modules/reasoning/infrastructure/job_receipts.py"
        method = next(
            n
            for n in ast.parse(path.read_text()).body
            if isinstance(n, ast.FunctionDef)
        )
        digest = hashlib.sha256(
            ast.dump(
                ast.Module(body=method.body, type_ignores=[]), include_attributes=False
            ).encode()
        ).hexdigest()
        fixture = json.loads(
            (ROOT.parent / "tests/fixtures/job_receipts_v1.json").read_text()
        )
        self.assertEqual(fixture["bodyHash"], digest)
