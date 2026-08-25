"""Read-only use case that proves where ontology reasoning time is spent."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping

from ..domain.ontology_reasoning_proof import (
    ONTOLOGY_REASONING_PROOF_VERSION,
    classify_reasoning_bottleneck,
    summarize_production_stage_evidence,
    summarize_read_only_replay,
)
from ..domain.ontology_worlds import portfolio_world_id


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _symbols(values: Iterable[object]) -> List[str]:
    return sorted({
        str(value or "").upper().strip()
        for value in values or []
        if str(value or "").strip()
    })


class OntologyReasoningProofService:
    """Combine immutable production audit data with a no-write live replay."""

    def __init__(
        self,
        ontology_repository,
        projection_run_store,
        account_repository=None,
        reasoning_cursor_store=None,
        settings: Dict[str, object] = None,
    ):
        self.ontology_repository = ontology_repository
        self.projection_run_store = projection_run_store
        self.account_repository = account_repository
        self.reasoning_cursor_store = reasoning_cursor_store
        self.settings = dict(settings or {})

    def cursor_production_run(self) -> Dict[str, object]:
        loader = getattr(self.reasoning_cursor_store, "load", None)
        if not callable(loader):
            return {}
        try:
            payload = loader() or {}
        except Exception:  # noqa: BLE001 - durable projection audit remains the primary source.
            return {}
        runtime = _mapping(payload.get("lastProjectionRuntime")) if isinstance(payload, Mapping) else {}
        if int(runtime.get("nativeInferenceMs") or 0) <= 0:
            return {}
        return {
            "runId": "reasoning-cursor:last-successful-projection",
            "observedAt": str(runtime.get("observedAt") or payload.get("lastSuccessfulProjectionAt") or ""),
            "status": str(runtime.get("status") or "ok"),
            "durationMs": int(runtime.get("durationMs") or 0),
            "targetSymbols": [],
            "runtimeStages": {
                "totalMs": int(runtime.get("durationMs") or 0),
                "nativeInferenceMs": int(runtime.get("nativeInferenceMs") or 0),
                "aboxPersistenceMs": int(runtime.get("aboxPersistenceMs") or 0),
            },
            "nativeStageTimings": _mapping(runtime.get("nativeStageTimings")),
            "source": "ontology-reasoning-cursor",
        }

    def resolve_account_id(self, account_id: str = "") -> str:
        clean = str(account_id or "").strip()
        if clean:
            return clean
        loader = getattr(self.account_repository, "load", None)
        if callable(loader):
            for account in loader() or []:
                if not bool(getattr(account, "enabled", True)):
                    continue
                candidate = str(getattr(account, "account_id", "") or "").strip()
                if candidate:
                    return candidate
        return str(self.settings.get("defaultAccountId") or "default").strip() or "default"

    def resolve_world_id(self, account_id: str, world_id: str = "") -> str:
        clean = str(world_id or "").strip()
        if clean:
            return clean
        tenant_id = str(
            self.settings.get("ontologyTenantId")
            or self.settings.get("tenantId")
            or ""
        ).strip()
        return portfolio_world_id(account_id, tenant_id)

    def production_runs(self, world_id: str, account_id: str, limit: int) -> List[Dict[str, object]]:
        reader = getattr(self.projection_run_store, "latest", None)
        if not callable(reader):
            return []
        requested_limit = max(1, min(30, int(limit or 10)))
        # Fast no-op/cooldown audit rows can outnumber actual inference runs
        # by an order of magnitude. Scan a bounded wider window, then retain
        # only runs that contain native-stage evidence.
        scan_limit = max(requested_limit, min(500, requested_limit * 20))
        try:
            rows = reader(world_id=world_id, account_id=account_id, limit=scan_limit)
        except TypeError:
            rows = reader(account_id=account_id, limit=scan_limit)
        evidence = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            result = _mapping(row.get("result"))
            observation = _mapping(result.get("runtimeObservation"))
            runtime_stages = _mapping(observation.get("stages"))
            execution = _mapping(result.get("ruleboxExecution"))
            inference = _mapping(observation.get("inference"))
            native_stages = _mapping(
                execution.get("nativeStageTimings")
                or inference.get("nativeStageTimings")
            )
            # Cooldown, unchanged-snapshot, and recovery probes can complete
            # in under a second without running TypeDB inference. They are
            # valid operational audit rows but cannot prove an inference
            # bottleneck, so keep them out of the causal sample.
            if int(runtime_stages.get("nativeInferenceMs") or 0) <= 0 or not native_stages:
                continue
            evidence.append({
                "runId": str(row.get("runId") or observation.get("runId") or ""),
                "observedAt": str(observation.get("observedAt") or row.get("completedAt") or ""),
                "status": str(row.get("status") or observation.get("status") or ""),
                "durationMs": int(observation.get("durationMs") or 0),
                "targetSymbols": _symbols(
                    inference.get("targetSymbols")
                    or row.get("sourceSymbols")
                    or []
                ),
                "runtimeStages": runtime_stages,
                "nativeStageTimings": native_stages,
            })
            if len(evidence) >= requested_limit:
                break
        return evidence

    def latest_rule_trace(self, run_id: str) -> Dict[str, object]:
        reader = getattr(self.projection_run_store, "execution_trace", None)
        if not callable(reader) or not run_id:
            return {"status": "unavailable", "rules": []}
        try:
            payload = reader(run_id=run_id, limit=1)
        except Exception as error:  # noqa: BLE001 - a missing audit trace must not block profiling.
            return {"status": "error", "reason": str(error)[:180], "rules": []}
        run = next(iter(payload.get("runs") or []), {}) if isinstance(payload, Mapping) else {}
        rules = sorted(
            [dict(item) for item in run.get("rules") or [] if isinstance(item, Mapping)],
            key=lambda item: (
                int(item.get("queryDurationMs") or 0),
                int(item.get("durationMs") or 0),
                str(item.get("ruleId") or ""),
            ),
            reverse=True,
        )
        return {
            "status": str(payload.get("status") or "ok"),
            "runId": str(run.get("runId") or run_id),
            "rules": rules,
        }

    @staticmethod
    def replay_slow_rule_ids(samples: Iterable[Mapping[str, object]], limit: int = 8) -> List[str]:
        totals: Dict[str, int] = {}
        for sample in samples or []:
            if not isinstance(sample, Mapping) or not sample.get("validForComparison"):
                continue
            for raw in sample.get("rules") or []:
                if not isinstance(raw, Mapping):
                    continue
                rule_id = str(raw.get("ruleId") or "").strip()
                if rule_id:
                    totals[rule_id] = totals.get(rule_id, 0) + int(raw.get("queryDurationMs") or 0)
        return [
            rule_id
            for rule_id, _duration in sorted(
                totals.items(),
                key=lambda item: (item[1], item[0]),
                reverse=True,
            )[:max(1, int(limit or 8))]
        ]

    def prove(
        self,
        account_id: str = "",
        world_id: str = "",
        symbols: Iterable[str] = None,
        repeats: int = 2,
        production_run_limit: int = 10,
        rule_ids: Iterable[str] = None,
        use_all_active_rules: bool = False,
        compare_subject_fanout: bool = False,
        subject_parallelism: int = 2,
        minimum_fanout_reduction_pct: float = 40.0,
    ) -> Dict[str, object]:
        resolved_account_id = self.resolve_account_id(account_id)
        resolved_world_id = self.resolve_world_id(resolved_account_id, world_id)
        all_runs = self.production_runs(
            resolved_world_id,
            resolved_account_id,
            max(2, min(30, int(production_run_limit or 10))),
        )
        production_source = "projection-audit"
        if not all_runs:
            cursor_run = self.cursor_production_run()
            if cursor_run:
                all_runs = [cursor_run]
                production_source = "reasoning-cursor"
        requested_symbols = _symbols(symbols or [])
        if not requested_symbols and all_runs:
            requested_symbols = _symbols(all_runs[0].get("targetSymbols") or [])
        max_symbols = max(1, min(8, int(self.settings.get("ontologyReasoningProofMaxSymbols") or 4)))
        requested_symbols = requested_symbols[:max_symbols]
        requested_set = set(requested_symbols)
        exact_runs = [
            run for run in all_runs
            if set(_symbols(run.get("targetSymbols") or [])) == requested_set
        ] if requested_set else list(all_runs)
        containing_runs = [
            run for run in all_runs
            if requested_set and requested_set.issubset(set(_symbols(run.get("targetSymbols") or [])))
        ]
        runs = exact_runs or containing_runs or list(all_runs)
        production = summarize_production_stage_evidence(runs)
        production["scopeMatchMode"] = (
            "exact-target-set"
            if exact_runs
            else "target-containing"
            if containing_runs
            else "latest-available"
        )
        production["availableRunCount"] = len(all_runs)
        production["source"] = production_source
        latest_run_id = str((runs[0] if runs else {}).get("runId") or "")
        rule_trace = self.latest_rule_trace(latest_run_id)
        production_query_modes = sorted({
            str(item.get("queryMode") or "").strip()
            for item in rule_trace.get("rules") or []
            if isinstance(item, Mapping)
            and int(item.get("queryCount") or 0) > 0
            and str(item.get("queryMode") or "").strip()
        })
        replay_query_mode = "direct-typeql"
        production_rule_ids = []
        for item in rule_trace.get("rules") or []:
            rule_id = str(item.get("ruleId") or "")
            query_count = int(item.get("queryCount") or 0)
            status = str(item.get("status") or "").strip().lower()
            executed = query_count > 0 or status in {"executed", "matched", "ok", "complete"}
            if executed and rule_id and rule_id not in production_rule_ids:
                production_rule_ids.append(rule_id)
        explicit_rule_ids = [
            str(item)
            for item in rule_ids or []
            if str(item)
        ]
        requested_rule_ids = explicit_rule_ids or production_rule_ids
        profiler = getattr(self.ontology_repository, "profile_native_rule_reads", None)
        if not callable(profiler):
            profile = {
                "status": "unavailable",
                "readOnly": True,
                "mutatedOperationalState": False,
                "writeMethodsInvoked": [],
                "samples": [],
                "reason": "The active ontology repository has no read-only profiler.",
            }
        elif not requested_rule_ids and not use_all_active_rules:
            profile = {
                "status": "unavailable",
                "readOnly": True,
                "mutatedOperationalState": False,
                "writeMethodsInvoked": [],
                "samples": [],
                "reason": (
                    "No executed production rule trace was available. "
                    "Pass explicit rule IDs or opt in to the complete active RuleBox replay."
                ),
            }
        else:
            profile = profiler({
                "worldId": resolved_world_id,
                "symbols": requested_symbols,
                "repeats": max(1, min(3, int(repeats or 2))),
                # Compare the exact production read set by default. Replaying
                # every active rule both distorts the evidence and spends time
                # on predicates that the production generation never used.
                "ruleIds": requested_rule_ids,
                "nativeQueryMode": replay_query_mode,
                "compareSubjectFanout": bool(compare_subject_fanout),
                "subjectParallelism": max(1, min(2, int(subject_parallelism or 2))),
                "minimumFanoutReductionPct": float(minimum_fanout_reduction_pct or 40.0),
            })
        replay = summarize_read_only_replay(profile.get("samples") or [])
        production_slow_rules = production_rule_ids[:8]
        replay_slow_rules = self.replay_slow_rule_ids(profile.get("samples") or [])
        overlap = sorted(set(production_slow_rules) & set(replay_slow_rules))
        verdict = classify_reasoning_bottleneck(production, replay, overlap)
        write_methods = list(profile.get("writeMethodsInvoked") or [])
        mutated_operational_state = bool(profile.get("mutatedOperationalState")) or bool(write_methods)
        read_only_contract_satisfied = bool(profile.get("readOnly")) and not mutated_operational_state
        return {
            "contract": ONTOLOGY_REASONING_PROOF_VERSION,
            "status": (
                "ok"
                if int(production.get("sampleCount") or 0) > 0
                and int(replay.get("validSampleCount") or 0) > 0
                and read_only_contract_satisfied
                else "inconclusive"
            ),
            "readOnly": read_only_contract_satisfied,
            "mutatedOperationalState": mutated_operational_state,
            "writeMethodsInvoked": write_methods,
            "accountId": resolved_account_id,
            "worldId": resolved_world_id,
            "targetSymbols": requested_symbols,
            "verdict": verdict,
            "productionEvidence": production,
            "productionSlowRules": production_slow_rules,
            "productionQueryModes": production_query_modes,
            "replayQueryMode": str(profile.get("nativeQueryMode") or replay_query_mode),
            "subjectFanoutGate": dict(profile.get("subjectFanoutGate") or {}),
            "readOnlyReplay": {
                **replay,
                "slowRuleIds": replay_slow_rules,
                "profileStatus": str(profile.get("status") or ""),
                "profileReason": str(profile.get("reason") or ""),
                "rulebox": dict(profile.get("rulebox") or {}),
                "excludedOperations": list(profile.get("excludedOperations") or []),
                "samples": list(profile.get("samples") or []),
            },
            "productionRuleTrace": rule_trace,
        }
