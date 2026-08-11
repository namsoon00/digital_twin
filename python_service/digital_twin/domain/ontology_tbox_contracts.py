"""Stable contracts shared by the modular TBox catalogs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TBoxBoundedContext:
    key: str
    label: str
    description: str


@dataclass(frozen=True)
class TBoxClassDef:
    name: str
    bounded_context: str
    label: str = ""
    parent: str = ""
    description: str = ""


@dataclass(frozen=True)
class TBoxRelationDef:
    name: str
    bounded_context: str
    source_context: str = ""
    target_context: str = ""
    description: str = ""


@dataclass(frozen=True)
class TBoxRuleDef:
    text: str
    bounded_context: str
    description: str = ""
