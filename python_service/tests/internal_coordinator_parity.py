"""AST reconstruction for pre-extraction coordinator golden tests.

This is test-only source composition, not runtime delegation. No file imports
or database constructors are needed. It preserves SQL string constants exactly.
"""

import ast
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"
HISTORY = ROOT / "infrastructure/transactions"
MANIFEST = ROOT / "modules/reasoning/infrastructure/projection_write"


def declarations(path):
    return {
        node.name: node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }


def replace_names(nodes, replacements):
    class Restore(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id in replacements:
                return ast.copy_location(ast.parse(replacements[node.id], mode="eval").body, node)
            return node

    return [Restore().visit(deepcopy(node)) for node in nodes]


def decision_history_members():
    """Return the original class and free helpers as reconstructed AST nodes.

    Wrapper signatures are unchanged. Each direct delegate maps one-to-one to
    its original method; only explicit keyword capability names are restored.
    save and pending_outcome_targets are split at their transaction/read stage.
    """
    parts = HISTORY / "decision_history_parts"
    facade = declarations(HISTORY / "decision_history.py")
    cls = deepcopy(facade["MySQLInvestmentDecisionEpisodeStore"])
    for method in cls.body:
        if not isinstance(method, ast.FunctionDef):
            continue
        if method.name in {"outcome_collection_targets", "record_outcome_baselines", "ontology_evolution_comparison", "experiment_observation_plans"}:
            # New closed-loop entry points have no pre-extraction equivalent.
            continue
        if method.name == "save":
            helper = declarations(parts / "decision_write.py")
            prepare = helper["prepare_decision"].body[:-1]
            write = replace_names(
                helper["write_decision"].body[5:],
                {
                    "_supersede_prior_follow_ups_for_current": "self.supersede_prior_follow_ups_for_current",
                    "_sync_outcome_targets": "self.sync_outcome_targets",
                },
            )
            transaction = deepcopy(method.body[2])
            transaction.body = write
            method.body = deepcopy(prepare) + [method.body[1], transaction, method.body[-1]]
            continue
        if method.name == "pending_outcome_targets":
            helper = declarations(parts / "target_queries.py")[method.name]
            method.body = method.body[:-1] + replace_names(
                helper.body[3:], {"_connect": "self.connect"}
            )
            continue
        call = method.body[0].value
        helper = declarations(parts / (call.func.value.id + ".py"))[call.func.attr]
        replacements = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in call.keywords
            if isinstance(keyword.value, ast.Attribute)
            and isinstance(keyword.value.value, ast.Name)
            and keyword.value.value.id == "self"
        }
        method.body = replace_names(helper.body, replacements)
        # Black dedents method docstrings when they become module functions.
        # Restore only that documentation indentation, never SQL literals.
        if (
            method.body
            and isinstance(method.body[0], ast.Expr)
            and isinstance(method.body[0].value, ast.Constant)
            and isinstance(method.body[0].value.value, str)
        ):
            doc = method.body[0].value.value.split("\n")
            method.body[0].value.value = "\n".join(
                [doc[0]] + [("    " + line if line else line) for line in doc[1:]]
            )
    policy = declarations(parts / "outcome_policy.py")
    exports = next(
        node
        for node in ast.parse((HISTORY / "decision_history.py").read_text()).body
        if isinstance(node, ast.ImportFrom)
        and node.module == "decision_history_parts.outcome_policy"
    )
    return {cls.name: cls, **{name.name: deepcopy(policy[name.name]) for name in exports.names}}


def patch_manifest_member():
    """Inline the three algorithm blocks without packet plumbing.

    source[10:-1] owns the fallback, identity[7:-1] owns topology/identity,
    diagnostics[7:] owns applied metadata, and blocked[4:] owns rejection.
    Their leading assignments unpack frozen inputs; final returns pack results.
    """
    method = deepcopy(declarations(MANIFEST / "patch_manifest.py")["patch_manifest"])
    source = declarations(MANIFEST / "patch_manifest_source.py")["repair_manifest_source"]
    identity = declarations(MANIFEST / "patch_manifest_identity.py")["merge_manifest_identity"]
    diagnostics = declarations(MANIFEST / "patch_manifest_diagnostics.py")
    outer = method.body[0]
    repair = outer.body[5]
    repair.body = replace_names(
        source.body[10:-1],
        {
            "build_projection_graph": "_store.build_projection_graph",
            "target_scoped_patch_targets": "_store.target_scoped_patch_targets",
            "clock": "time.perf_counter",
        },
    )
    applied = outer.body[8]
    metadata = replace_names(
        diagnostics["applied_patch_diagnostics"].body[7:],
        {
            "compact_target_scope_selection_trace": "_bindings.compact_target_scope_selection_trace",
            "scope_integrity_audit_interval_minutes": "_store.scope_integrity_audit_interval_minutes",
        },
    )
    metadata[-1] = ast.Assign(
        targets=[ast.Name(id="target_scoped_patch", ctx=ast.Store())],
        value=metadata[-1].value,
    )
    applied.body = deepcopy(identity.body[7:-1]) + metadata + [applied.body[-1]]
    rejected = applied.orelse[0]
    blocked = replace_names(
        diagnostics["blocked_patch_result"].body[4:],
        {
            "active_graph_store_key": "_store.active_graph_store_key",
        },
    )
    blocked[-1] = ast.Assign(
        targets=[ast.Name(id="result", ctx=ast.Store())], value=blocked[-1].value
    )
    rejected.body = blocked + rejected.body[1:]
    fallback = diagnostics["full_manifest_fallback"].body[0]
    rejected.orelse = [
        ast.Assign(
            targets=[ast.Name(id="target_scoped_patch", ctx=ast.Store())],
            value=deepcopy(fallback.value),
        )
    ]
    return ast.fix_missing_locations(method)
