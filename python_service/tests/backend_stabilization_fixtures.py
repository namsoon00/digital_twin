"""Recompose extracted code for the pre-refactor semantic parity checks."""

import ast
import copy
import inspect
import json
from pathlib import Path
from internal_coordinator_parity import patch_manifest_member

ROOT = Path(__file__).resolve().parents[1] / "digital_twin"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
STAGES = json.loads((FIXTURES / "projection_stages_v1.json").read_text())["stages"]
PARTICIPANTS = json.loads((FIXTURES / "transaction_participants_v1.json").read_text())[
    "participants"
]
CHANGED_METHODS = {
    "MySQLReasoningEngineJobStore": {
        "claim",
        "bind_release",
        "heartbeat",
        "complete",
        "defer",
        "retry",
        "exclude",
        "fail",
        "supersede",
        "await_world_projection",
        "await_target_scope_repair",
        "reshard_claimed_job",
    },
    # Claim now retries its whole transaction; test_ai_claim_retry preserves its SQL/guards.
    "MySQLAIInferenceQueueStore": {"complete", "claim"},
    "MySQLMarketObservationReasoningAnchorStore": {
        "repair_completed_reasoning_receipts"
    },
}
ADDED_METHODS = {"MySQLReasoningEngineJobStore": {"repair_completed_receipts"}}


def function(path, name):
    return next(
        n
        for n in ast.parse((ROOT / path).read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == name
    )


class RestoreStageReturns(ast.NodeTransformer):
    def visit_Return(self, node):
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "CompletedProjection"
        ):
            return ast.Return(value=node.value.args[0])
        return node


class ExpandStages(ast.NodeTransformer):
    def visit_Assign(self, node):
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in STAGES
        ):
            name = node.value.func.id
            spec = STAGES[name]
            assert {k.arg: ast.unparse(k.value) for k in node.value.keywords} == {
                name: name for name in spec["inputs"]
            }
            method = patch_manifest_member() if name == "patch_manifest" else function(
                "modules/reasoning/infrastructure/projection_write/" + name + ".py",
                name,
            )
            assert isinstance(method.body[-1], ast.Return)
            assert {
                k.arg: ast.unparse(k.value) for k in method.body[-1].value.keywords
            } == {name: name for name in spec["outputs"]}
            return [
                RestoreStageReturns().visit(copy.deepcopy(n)) for n in method.body[:-1]
            ]
        if (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "stage_result"
        ):
            assert ast.unparse(node.targets[0]) == node.value.attr
            return None
        return self.generic_visit(node)

    def visit_If(self, node):
        if ast.unparse(node.test) == "isinstance(stage_result, CompletedProjection)":
            assert ast.unparse(node.body[0]) == "return stage_result.result"
            return None
        return self.generic_visit(node)


def expand_projection(node):
    return ExpandStages().visit(copy.deepcopy(node))


class ExpandParticipants(ast.NodeTransformer):
    def participant(self, call):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            return None
        spec = PARTICIPANTS.get(call.func.attr)
        if spec is None:
            return None
        actual = {kw.arg: ast.unparse(kw.value) for kw in call.keywords}
        expected = {**{arg: arg for arg in spec["args"]}, **spec["bindings"]}
        assert actual == expected, (call.func.attr, actual, expected)
        method = function(spec["path"], call.func.attr)
        if "docstring" in spec:
            assert ast.get_docstring(method) == inspect.cleandoc(spec["docstring"])
            method.body[0].value.value = spec["docstring"]

        class Bind(ast.NodeTransformer):
            def visit_Name(self, n):
                if n.id in spec["bindings"]:
                    return ast.copy_location(
                        ast.parse(spec["bindings"][n.id], mode="eval").body, n
                    )
                return n

        return spec, [Bind().visit(copy.deepcopy(n)) for n in method.body]

    def visit_Return(self, node):
        result = self.participant(node.value)
        if result:
            spec, body = result
            assert spec["kind"] == "method"
            return body
        return self.generic_visit(node)

    def visit_Expr(self, node):
        result = self.participant(node.value)
        if result:
            spec, body = result
            assert spec["kind"] == "sql" and len(body) == 1
            return ast.Expr(value=body[0].value)
        return self.generic_visit(node)


def expand_participants(node):
    return ExpandParticipants().visit(copy.deepcopy(node))
