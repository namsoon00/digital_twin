"""Background preparation of TypeDB-native RuleBox schema functions.

The active RuleBox is an investment-policy contract. Its generated TypeDB
functions are a compiled implementation detail, so compilation belongs to a
separate bounded worker and must never be started by a live alert inference.
"""

from __future__ import annotations

import time
from typing import Dict


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def _integer_setting(
    settings: Dict[str, object],
    key: str,
    fallback: int,
    lower: int,
    upper: int,
) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


class OntologyRuleboxPrewarmRunner:
    """Run one bounded full-RuleBox schema preparation pass at a time."""

    def __init__(self, ontology_repository, settings: Dict[str, object] = None):
        self.ontology_repository = ontology_repository
        self.settings = dict(settings or {})

    def enabled(self) -> bool:
        value = str(self.settings.get("ontologyRuleboxPrewarmEnabled") or "1").strip().lower()
        return value not in DISABLED_VALUES

    def interval_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmIntervalSeconds",
            60,
            5,
            3600,
        )

    def execution_timeout_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmExecutionTimeoutSeconds",
            180,
            30,
            1800,
        )

    def execution_timeout_grace_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyRuleboxPrewarmExecutionTimeoutGraceSeconds",
            10,
            1,
            120,
        )

    def process_isolation_enabled(self) -> bool:
        value = str(
            self.settings.get("ontologyRuleboxPrewarmProcessIsolationEnabled") or "1"
        ).strip().lower()
        return value not in DISABLED_VALUES

    def status(self) -> Dict[str, object]:
        result = {
            "enabled": self.enabled(),
            "intervalSeconds": self.interval_seconds(),
            "executionTimeoutSeconds": self.execution_timeout_seconds(),
            "executionTimeoutGraceSeconds": self.execution_timeout_grace_seconds(),
            "processIsolationEnabled": self.process_isolation_enabled(),
            # A missing prewarm is itself a live-inference blocker, so this
            # worker intentionally does not wait for the reasoning queue.
            "deferWhenReasoningPending": False,
        }
        reader = getattr(self.ontology_repository, "schema_function_prewarm_status", None)
        if not callable(reader):
            result["prewarm"] = {
                "status": "unsupported",
                "functionsReady": False,
                "reason": "Ontology repository has no TypeDB schema-function prewarm capability.",
            }
            return result
        try:
            result["prewarm"] = dict(reader() or {})
        except Exception as error:  # noqa: BLE001 - status must not stop the worker process.
            result["prewarm"] = {
                "status": "error",
                "functionsReady": False,
                "reason": str(error)[:220],
            }
        return result

    def run_once(self, force: bool = False) -> Dict[str, object]:
        if not self.enabled():
            return {
                "status": "disabled",
                "configured": True,
                "functionsReady": False,
                "reason": "RuleBox schema-function prewarm worker is disabled.",
                "durationMs": 0,
            }
        prewarm = getattr(self.ontology_repository, "prewarm_typedb_native_rule_functions", None)
        if not callable(prewarm):
            return {
                "status": "unsupported",
                "configured": False,
                "functionsReady": False,
                "reason": "Ontology repository has no TypeDB schema-function prewarm capability.",
                "durationMs": 0,
            }
        started_at = time.perf_counter()
        try:
            result = dict(prewarm(force=bool(force)) or {})
        except Exception as error:  # noqa: BLE001 - the next bounded pass can retry safely.
            result = {
                "status": "error",
                "configured": True,
                "functionsReady": False,
                "reason": str(error)[:220],
            }
        result.setdefault("durationMs", int((time.perf_counter() - started_at) * 1000))
        result.setdefault("background", True)
        result.setdefault("liveInferenceDeploymentAvoided", True)
        return result
