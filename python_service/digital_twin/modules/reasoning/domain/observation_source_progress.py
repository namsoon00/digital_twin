"""Operational source progress; immutable evidence and source clocks stay intact."""
import re

from .observation_evidence import content_hash, evidence_change_identity, material_fact

VERSION = "observation-source-progress-v1"


def material(fact):
    value = material_fact(fact)
    # Monthly/daily macro prints carry their actual observation period separately
    # from the collector's asOf. Polling the same print is not a new release.
    # Keep values, previous periods, revisions, release dates and eligibility.
    period = fact.get("observationDate")
    if (fact.get("kind") == "macro-print" and isinstance(period, str)
            and re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", period)
            and fact.get("sourceAsOf") == period and fact.get("sourceTimestampPresent") is True):
        value.pop("asOf", None)
    return value


def facts_identity(packet, facts):
    return content_hash({"version": VERSION,
        **{k: packet.get(k) for k in ("accountId", "symbol", "worldId", "profile")},
        "facts": sorted((material(row) for row in facts), key=content_hash)})


def inventory_identity(packet, inventory):
    # The entire captured inventory participates, including unread and budget
    # excluded sources. Recompute its identity rather than reuse polling hashes.
    coverage = {name: {k: v for k, v in row.items() if k != "inventoryHash"}
                for name, row in packet.get("coverage", {}).items()}
    return content_hash({"version": VERSION, "material": evidence_change_identity(
        {**packet, "facts": [material(row) for row in inventory], "coverage": coverage}, [])})


def evidence_wait_identity(packet, inventory):
    """A read-view lineage hash is not a new source; its actual fields still are.

    Use the entire captured inventory, including unread/budget-excluded facts.
    Keep quote values, eligibility, reporting periods and real revisions so this
    identity cannot hide a risk change or a corrected/new source.
    """
    rows = []
    for fact in inventory:
        value = material(fact)
        if isinstance(value.get("evidenceView"), dict):
            value["evidenceView"] = {k: v for k, v in value["evidenceView"].items() if k != "sourceFactHash"}
        rows.append(value)
    coverage = {name: {k: v for k, v in row.items() if k != "inventoryHash"}
                for name, row in packet.get("coverage", {}).items()}
    return content_hash({"version": "captured-evidence-wait-v1", "material": evidence_change_identity(
        {**packet, "facts": rows, "coverage": coverage}, [])})
