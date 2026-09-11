"""Compare historical code contracts while allowing audited module relocation."""

import ast
import copy
import importlib
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OWNERSHIP = json.loads((ROOT / "docs/domain-ownership.json").read_text())
REVERSE = {
    "digital_twin." + target.removesuffix(".py").replace("/", "."):
        "digital_twin." + source.removesuffix(".py").replace("/", ".")
    for source, target in OWNERSHIP.items()
    if isinstance(target, str)
}


def is_domain_dependency(module):
    parts = module.split(".")
    return (
        module.startswith("digital_twin.modules.reasoning.domain.")
        or module.startswith("digital_twin.shared_kernel.")
        or module.startswith("digital_twin.platform.domain.")
        or (len(parts) == 4 and parts[:2] == ["digital_twin", "modules"]
            and parts[-1] == "contracts")
    )


def restore_domain_imports(node):
    class Restore(ast.NodeTransformer):
        def visit_ImportFrom(self, value):
            if value.module in REVERSE:
                value.module = REVERSE[value.module]
            elif value.module and value.module.endswith(".contracts") and value.module.startswith("digital_twin.modules."):
                exports = importlib.import_module(value.module)._EXPORTS
                groups = {}
                for alias in value.names:
                    target, name = exports[alias.name]
                    old_module = REVERSE.get(target, target)
                    local = alias.asname or alias.name
                    groups.setdefault(old_module, []).append(ast.alias(
                        name=name, asname=local if local != name else None,
                    ))
                return [ast.ImportFrom(module=module, names=names, level=0)
                        for module, names in groups.items()]
            return value

    return Restore().visit(copy.deepcopy(node))


def definition_fingerprint(node):
    """Ignore import locations and docstrings, never executable rule/data values."""
    class Normalize(ast.NodeTransformer):
        def visit_Import(self, value):
            return None

        def visit_ImportFrom(self, value):
            return None

        def visit_Name(self, value):
            for alias in ("news_domain", "news_analysis_domain"):
                prefix = "_contract_" + alias + "_"
                if value.id.startswith(prefix):
                    return ast.Attribute(value=ast.Name(id=alias, ctx=ast.Load()),
                                         attr=value.id[len(prefix):], ctx=value.ctx)
            return value

        def strip_docstring(self, value):
            if value.body and isinstance(value.body[0], ast.Expr) and isinstance(value.body[0].value, ast.Constant) and isinstance(value.body[0].value.value, str):
                value.body = value.body[1:]
            return self.generic_visit(value)

        visit_FunctionDef = strip_docstring
        visit_AsyncFunctionDef = strip_docstring
        visit_ClassDef = strip_docstring

    normalized = Normalize().visit(copy.deepcopy(node))
    return hashlib.sha256(ast.dump(normalized, include_attributes=False).encode()).hexdigest()
