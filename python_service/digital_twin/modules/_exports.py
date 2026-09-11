"""Load explicit module interfaces without constructing unrelated services."""

from importlib import import_module
import sys


def resolve_export(surface, exports, name):
    target = exports.get(name)
    if target is None:
        raise AttributeError("module {!r} has no attribute {!r}".format(surface, name))
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    setattr(sys.modules[surface], name, value)
    return value
