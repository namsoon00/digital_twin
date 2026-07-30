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

    def __init__(
        self,
        ontology_repository,
        settings: Dict[str, object] = None,
        reasoning_queue_probe=None,
    ):
        self.ontology_repository = ontology_repository
        self.settings = dict(settings or {})
        self.reasoning_queue_probe = reasoning_queue_probe

    def enabled(self) -> bool:
        # A live inference must never be the process that discovers a cold
        # RuleBox function.  The dedicated worker owns that compilation and
        # the reasoning runner waits for its durable receipt instead.
        value = str(self.settings.get("ontologyRuleboxPrewarmEnabled") or "1").strip().lower()
        return value not in DISABLED_VALUES

    def defer_when_reasoning_pending(self) -> bool:
        value = str(
            self.settings.get("ontologyRuleboxPrewarmDeferWhenReasoningPending") or "1"
        ).strip().lower()
        return value not in DISABLED_VALUES

    def reasoning_queue_state(self) -> Dict[str, object]:
        probe = self.reasoning_queue_probe
        if not callable(probe):
            return {"status": "not-supported", "effectivePendingCount": 0}
        try:
            payload = probe()
            return dict(payload or {}) if isinstance(payload, dict) else {
                "status": "invalid",
                "effectivePendingCount": 0,
            }
        except Exception as error:  # noqa: BLE001 - an unsafe compiler pass must not block alerts.
            return {
                "status": "error",
                "effectivePendingCount": 0,
                "reason": str(error)[:180],
            }

    @staticmethod
    def pending_reasoning_count(payload: Dict[str, object]) -> int:
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        candidates = [
            values.get("effectivePendingCount"),
            values.get("mailboxPendingEntryCount"),
            values.get("pendingEntryCount"),
        ]
        parsed = []
        for candidate in candidates:
            try:
                parsed.append(max(0, int(float(candidate or 0))))
            except (TypeError, ValueError):
                continue
        return max(parsed or [0])

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
            "deferWhenReasoningPending": self.defer_when_reasoning_pending(),
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

    def prewarm_readiness(self) -> Dict[str, object]:
        """Read the durable prewarm receipt without beginning compilation."""
        reader = getattr(self.ontology_repository, "schema_function_prewarm_status", None)
        if not callable(reader):
            return {
                "status": "unsupported",
                "functionsReady": False,
                "reason": "Ontology repository has no TypeDB schema-function prewarm capability.",
            }
        try:
            payload = reader()
            return dict(payload or {}) if isinstance(payload, dict) else {
                "status": "error",
                "functionsReady": False,
                "reason": "TypeDB schema-function prewarm returned an invalid readiness payload.",
            }
        except Exception as error:  # noqa: BLE001 - the bounded worker can retry its own read.
            return {
                "status": "error",
                "functionsReady": False,
                "reason": str(error)[:220],
            }

    def run_once(self, force: bool = False) -> Dict[str, object]:
        if not self.enabled():
            return {
                "status": "disabled",
                "configured": True,
                "functionsReady": False,
                "reason": "RuleBox schema-function prewarm worker is disabled.",
                "durationMs": 0,
            }
        queue = self.reasoning_queue_state()
        pending = self.pending_reasoning_count(queue)
        readiness = {}
        # When receipts are already ready, stay out of a live queue exactly as
        # before.  When they are missing, however, deferring this worker makes
        # the first inference fall back to slow direct TypeQL reads.  Give the
        # compiler one exclusive turn while the reasoning runner is gated on
        # the same receipt; no live native inference competes with it.
        if self.defer_when_reasoning_pending() and pending and not force:
            readiness = self.prewarm_readiness()
            if bool(readiness.get("functionsReady")):
                return {
                    "status": "deferred-reasoning-pending",
                    "configured": True,
                    "functionsReady": True,
                    "pendingRuleCount": 0,
                    "reasoningPendingCount": pending,
                    "reasoningQueue": queue,
                    "prewarmReadiness": readiness,
                    "reason": (
                        "Live ontology reasoning is pending and durable RuleBox function receipts are already ready; "
                        "the prewarm worker yields to the alert path."
                    ),
                    "recommendedRetryAfterSeconds": self.interval_seconds(),
                    "durationMs": 0,
                }
        if self.defer_when_reasoning_pending() and str(queue.get("status") or "") == "error" and not force:
            return {
                "status": "deferred-reasoning-queue-probe",
                "configured": True,
                "functionsReady": False,
                "pendingRuleCount": 0,
                "reasoningQueue": queue,
                "reason": (
                    "Live reasoning queue state could not be confirmed; RuleBox schema compilation is deferred "
                    "to avoid competing with an alert."
                ),
                "recommendedRetryAfterSeconds": self.interval_seconds(),
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
        if readiness:
            result["queuePriority"] = True
            result["prewarmReadinessBeforeRun"] = readiness
        return result
