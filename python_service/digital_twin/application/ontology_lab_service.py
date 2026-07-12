import hashlib
import json
import signal
import time
from dataclasses import fields
from typing import Dict, Iterable, List

from ..domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology, entity_id
from ..domain.ontology_experiments import (
    OntologyExperiment,
    clean_symbols,
    compact_rulebox_snapshot,
    experiment_id_for,
    normalize_candidate_rules,
    rule_payloads_from_snapshot,
    run_experiment_on_graph,
    summarize_experiment_result,
)
from ..domain.portfolio import AccountSnapshot, DecisionItem, PortfolioSummary, Position, utc_now_iso
from ..domain.portfolio_ontology_builder import build_portfolio_ontology
from ..domain.ontology_schema import default_tbox_metadata, tbox_entities, tbox_relations
from ..domain.ontology_tbox import TBOX_CLASSES, TBOX_RELATION_TYPES


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
ACTIVE_STATUS = "active"
PAUSED_STATUS = "paused"


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 1, upper: int = 1000) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(lower, min(upper, parsed))


class OntologyLabService:
    def __init__(
        self,
        ontology_repository,
        experiment_store,
        monitor_store=None,
        settings: Dict[str, object] = None,
    ):
        self.ontology_repository = ontology_repository
        self.experiment_store = experiment_store
        self.monitor_store = monitor_store
        self.settings = dict(settings or {})

    def list(self) -> Dict[str, object]:
        experiments = self.experiment_store.list()
        return {
            "experiments": [item.to_dict() for item in experiments],
            "count": len(experiments),
        }

    def enabled(self) -> bool:
        return truthy(self.settings.get("ontologyLabEnabled"), True)

    def batch_size(self) -> int:
        return int_setting(self.settings, "ontologyLabBatchSize", 5, 1, 100)

    def history_limit(self) -> int:
        return int_setting(self.settings, "ontologyLabRunHistoryLimit", 50, 1, 500)

    def status(self) -> Dict[str, object]:
        experiments = self.experiment_store.list()
        statuses: Dict[str, int] = {}
        latest_run = {}
        for experiment in experiments:
            statuses[experiment.status] = statuses.get(experiment.status, 0) + 1
            for item in experiment.run_history or []:
                if not latest_run or str(item.get("completedAt") or "") > str(latest_run.get("completedAt") or ""):
                    latest_run = dict(item)
        return {
            "enabled": self.enabled(),
            "count": len(experiments),
            "activeCount": statuses.get(ACTIVE_STATUS, 0),
            "pausedCount": statuses.get(PAUSED_STATUS, 0),
            "draftCount": statuses.get("draft", 0),
            "completedCount": statuses.get("completed", 0),
            "batchSize": self.batch_size(),
            "historyLimit": self.history_limit(),
            "latestRun": latest_run,
            "experiments": [item.to_dict() for item in experiments],
        }

    def create(self, payload: Dict[str, object]) -> Dict[str, object]:
        body = dict(payload or {})
        rulebox = self.rulebox_snapshot()
        candidate_rules, warnings = normalize_candidate_rules(body, rulebox)
        stamp = utc_now_iso()
        experiment = OntologyExperiment(
            experiment_id=str(body.get("id") or body.get("experimentId") or "") or experiment_id_for(body, stamp),
            title=str(body.get("title") or "Ontology Lab Experiment"),
            hypothesis=str(body.get("hypothesis") or ""),
            symbols=clean_symbols(body.get("symbols") or []),
            candidate_rules=candidate_rules,
            baseline_rulebox=compact_rulebox_snapshot(rulebox),
            status="draft",
            created_at=stamp,
            updated_at=stamp,
            validation_warnings=warnings,
        )
        self.experiment_store.save(experiment)
        return {"experiment": experiment.to_dict()}

    def suggest_from_rule_candidates(
        self,
        candidate_result: Dict[str, object],
        payload: Dict[str, object] = None,
    ) -> Dict[str, object]:
        body = dict(payload or {})
        candidate_result = dict(candidate_result or {})
        candidates = [dict(item) for item in (candidate_result.get("candidates") or []) if isinstance(item, dict)]
        clean_limit = int_setting(body, "limit", len(candidates) or 3, 0, 20)
        if clean_limit:
            candidates = candidates[:clean_limit]
        symbols = clean_symbols(body.get("symbols") or candidate_result.get("symbols") or [])
        activate = truthy(body.get("activate"), False)
        run_after_create = truthy(body.get("run"), False)
        rulebox = self.rulebox_snapshot()
        existing_rule_ids = experiment_candidate_rule_ids(self.experiment_store.list())
        stamp = utc_now_iso()
        created = []
        skipped = []
        for candidate in candidates:
            proposed_rule = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else None
            if not proposed_rule:
                skipped.append(compact_candidate_skip(candidate, "no-proposed-rule"))
                continue
            if str(candidate.get("status") or "").lower() == "covered":
                skipped.append(compact_candidate_skip(candidate, "covered"))
                continue
            rule_id = rule_id_from_payload(proposed_rule)
            if not rule_id:
                skipped.append(compact_candidate_skip(candidate, "missing-rule-id"))
                continue
            if rule_id in existing_rule_ids:
                skipped.append(compact_candidate_skip(candidate, "duplicate-rule-id"))
                continue
            experiment_id = experiment_id_for_candidate(candidate, proposed_rule)
            if self.experiment_store.get(experiment_id):
                skipped.append(compact_candidate_skip(candidate, "duplicate-experiment"))
                existing_rule_ids.add(rule_id)
                continue
            candidate_rules, warnings = normalize_candidate_rules({"rules": [proposed_rule]}, rulebox)
            if not candidate_rules:
                skipped.append(compact_candidate_skip(candidate, "invalid-proposed-rule"))
                continue
            experiment = OntologyExperiment(
                experiment_id=experiment_id,
                title="AI 제안: " + str(candidate.get("title") or candidate_rules[0].get("label") or rule_id),
                hypothesis=hypothesis_from_candidate(candidate),
                symbols=symbols,
                candidate_rules=candidate_rules,
                baseline_rulebox=compact_rulebox_snapshot(rulebox),
                status=ACTIVE_STATUS if activate else "draft",
                created_at=stamp,
                updated_at=stamp,
                last_result={
                    "status": "suggested",
                    "suggestedAt": stamp,
                    "sourceCandidate": compact_source_candidate(candidate, rule_id),
                },
                active_since=stamp if activate else "",
                validation_warnings=warnings + [
                    "sourceCandidateId=" + str(candidate.get("id") or ""),
                    "sourceCandidateStatus=" + str(candidate.get("status") or ""),
                ],
            )
            self.experiment_store.save(experiment)
            existing_rule_ids.add(rule_id)
            if run_after_create:
                run_result = self._run_experiment(
                    experiment,
                    {},
                    keep_active_status=activate,
                    force=True,
                    run_kind="ai-suggested",
                )
                created.append(run_result.get("experiment") or experiment.to_dict())
            else:
                created.append(experiment.to_dict())
        status = "created" if created else ("no-candidates" if not candidates else "skipped")
        return {
            "status": status,
            "candidateStatus": str(candidate_result.get("status") or ""),
            "candidateCount": len(candidates),
            "createdCount": len(created),
            "skippedCount": len(skipped),
            "symbols": symbols,
            "experiments": created,
            "skipped": skipped,
        }

    def activate(self, experiment_id: str) -> Dict[str, object]:
        experiment = self.experiment_store.get(experiment_id)
        if not experiment:
            return {"status": "not-found", "id": experiment_id}
        stamp = utc_now_iso()
        experiment.status = ACTIVE_STATUS
        experiment.active_since = experiment.active_since or stamp
        experiment.paused_at = ""
        experiment.updated_at = stamp
        self.experiment_store.save(experiment)
        return {"status": ACTIVE_STATUS, "experiment": experiment.to_dict()}

    def pause(self, experiment_id: str) -> Dict[str, object]:
        experiment = self.experiment_store.get(experiment_id)
        if not experiment:
            return {"status": "not-found", "id": experiment_id}
        stamp = utc_now_iso()
        experiment.status = PAUSED_STATUS
        experiment.paused_at = stamp
        experiment.updated_at = stamp
        self.experiment_store.save(experiment)
        return {"status": PAUSED_STATUS, "experiment": experiment.to_dict()}

    def report(self, experiment_id: str) -> Dict[str, object]:
        experiment = self.experiment_store.get(experiment_id)
        if not experiment:
            return {"status": "not-found", "id": experiment_id}
        return {"experiment": experiment.to_dict()}

    def run(self, experiment_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        experiment = self.experiment_store.get(experiment_id)
        if not experiment:
            return {"status": "not-found", "id": experiment_id}
        return self._run_experiment(experiment, payload or {}, keep_active_status=False, force=True, run_kind="manual")

    def apply_recommendations(self, experiment_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        experiment = self.experiment_store.get(experiment_id)
        if not experiment:
            return {"status": "not-found", "id": experiment_id}
        payload = dict(payload or {})
        last_result = dict(experiment.last_result or {})
        if not last_result:
            return {"status": "no-result", "id": experiment_id}
        proposed = last_result.get("proposedOntologyChanges")
        proposed = dict(proposed) if isinstance(proposed, dict) else {}
        recommendations = [
            dict(item)
            for item in (last_result.get("recommendations") or [])
            if isinstance(item, dict)
        ]
        run_rulebox = truthy(payload.get("runRulebox", payload.get("run_rulebox")), True)
        stamp = utc_now_iso()
        rulebox = self.rulebox_snapshot()
        baseline_rules = rule_payloads_from_snapshot(rulebox)
        merged_rules, added_rules, skipped_rule_ids = merge_candidate_rules(baseline_rules, experiment.candidate_rules)
        tbox_graph = ontology_lab_tbox_graph(experiment, proposed, stamp)
        tbox_result = self.save_tbox_graph(tbox_graph) if tbox_graph.entities else {"status": "skipped", "reason": "No TBox proposal."}
        rulebox_result = {"status": "skipped", "reason": "No new RuleBox rule."}
        if added_rules:
            rulebox_result = self.save_rulebox(
                {
                    "rules": merged_rules,
                    "clearInference": True,
                    "author": "ontology-lab",
                    "changeReason": "Ontology lab proposal applied from " + experiment.experiment_id,
                }
            )
        inference_result = {"status": "skipped", "reason": "RuleBox run disabled or no new rule."}
        if run_rulebox and added_rules:
            inference_result = self.run_rulebox({"clearInference": True, "trigger": "ontology-lab-apply", "experimentId": experiment.experiment_id})
        application = {
            "status": ontology_application_status(rulebox_result, tbox_result, added_rules, tbox_graph.entities),
            "appliedAt": stamp,
            "experimentId": experiment.experiment_id,
            "ruleIds": [rule_id_from_payload(item) for item in added_rules if rule_id_from_payload(item)],
            "skippedRuleIds": skipped_rule_ids,
            "relationTypes": clean_text_list(proposed.get("newRelationTypes") or proposed.get("relationTypes") or []),
            "decisionStages": clean_text_list(proposed.get("newDecisionStages") or proposed.get("decisionStages") or []),
            "tboxClasses": clean_text_list(proposed.get("tboxClasses") or []),
            "recommendationIds": [str(item.get("id") or "") for item in recommendations if str(item.get("id") or "").strip()],
            "ruleboxSave": compact_apply_result(rulebox_result),
            "tboxSave": compact_apply_result(tbox_result),
            "inferenceRun": compact_apply_result(inference_result),
        }
        updated_recommendations = mark_recommendations_applied(recommendations, application)
        last_result["recommendations"] = updated_recommendations
        last_result["appliedOntologyChanges"] = application
        last_result["sandbox"] = {
            **dict(last_result.get("sandbox") or {}),
            "mutatedOperationalRuleBox": bool(added_rules),
            "mutatedNeo4j": bool(added_rules or tbox_graph.entities),
        }
        experiment.last_result = last_result
        experiment.updated_at = stamp
        self.mark_latest_history_application(experiment, application, updated_recommendations)
        self.experiment_store.save(experiment)
        return {"status": application["status"], "experiment": experiment.to_dict(), "application": application}

    def run_once(self, limit: int = 0, force: bool = False) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "processedCount": 0, "runCount": 0, "skippedCount": 0, "experiments": []}
        active = [item for item in self.experiment_store.list() if item.status == ACTIVE_STATUS]
        if not active:
            return {"status": "idle", "processedCount": 0, "runCount": 0, "skippedCount": 0, "experiments": []}
        selected = active[: max(1, int(limit or self.batch_size()))]
        results = [
            self._run_experiment(experiment, {}, keep_active_status=True, force=force, run_kind="scheduled")
            for experiment in selected
        ]
        run_count = len([item for item in results if item.get("status") == "completed"])
        skipped_count = len([item for item in results if item.get("status") == "skipped"])
        return {
            "status": "ok" if run_count else "idle",
            "processedCount": len(results),
            "runCount": run_count,
            "skippedCount": skipped_count,
            "experiments": results,
        }

    def _run_experiment(
        self,
        experiment: OntologyExperiment,
        payload: Dict[str, object] = None,
        keep_active_status: bool = False,
        force: bool = False,
        run_kind: str = "manual",
    ) -> Dict[str, object]:
        payload = dict(payload or {})
        symbols = clean_symbols(payload.get("symbols") or experiment.symbols or [])
        if symbols and symbols != experiment.symbols:
            experiment.symbols = symbols
        snapshots = self.monitor_snapshots(symbols=symbols)
        snapshot_key = monitor_snapshot_key(snapshots, symbols)
        if keep_active_status and not force and experiment.last_snapshot_key == snapshot_key:
            return {
                "status": "skipped",
                "reason": "snapshot-unchanged",
                "snapshotKey": snapshot_key,
                "experiment": experiment.to_dict(),
                "result": experiment.last_result,
            }
        rulebox = self.rulebox_snapshot()
        baseline_rules = rule_payloads_from_snapshot(rulebox)
        graph_runs = []
        for graph in self.facts_graphs(snapshots=snapshots):
            graph_runs.append(run_experiment_on_graph(graph, baseline_rules, experiment.candidate_rules))
        result = summarize_experiment_result(experiment, baseline_rules, graph_runs)
        result["baselineRulebox"] = compact_rulebox_snapshot(rulebox)
        result["snapshotKey"] = snapshot_key
        result["runKind"] = run_kind
        experiment.status = ACTIVE_STATUS if keep_active_status else "completed"
        experiment.updated_at = utc_now_iso()
        experiment.last_result = result
        experiment.last_snapshot_key = snapshot_key
        self.append_run_history(experiment, result, snapshot_key, run_kind)
        self.experiment_store.save(experiment)
        return {"status": "completed", "experiment": experiment.to_dict(), "result": result}

    def append_run_history(
        self,
        experiment: OntologyExperiment,
        result: Dict[str, object],
        snapshot_key: str,
        run_kind: str,
    ) -> None:
        readiness = result.get("promotionReadiness") if isinstance(result.get("promotionReadiness"), dict) else {}
        inference = result.get("inference") if isinstance(result.get("inference"), dict) else {}
        aggregate_delta = inference.get("aggregateDelta") if isinstance(inference.get("aggregateDelta"), dict) else {}
        sandbox = result.get("sandbox") if isinstance(result.get("sandbox"), dict) else {}
        recommendations = [dict(item) for item in (result.get("recommendations") or []) if isinstance(item, dict)]
        completed_at = str(result.get("completedAt") or utc_now_iso())
        seed = experiment.experiment_id + "|" + snapshot_key + "|" + completed_at + "|" + run_kind
        entry = {
            "runId": "ontology-lab-run-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12],
            "runKind": run_kind,
            "status": str(result.get("status") or "completed"),
            "completedAt": completed_at,
            "snapshotKey": snapshot_key,
            "graphRunCount": int(sandbox.get("graphRunCount") or 0),
            "promotionStatus": str(readiness.get("status") or ""),
            "promotionScore": readiness.get("score"),
            "derivedRelationDelta": int(aggregate_delta.get("derivedRelationCount") or 0),
            "newRelationTypes": list(aggregate_delta.get("newRelationTypes") or [])[:12],
            "findings": list(result.get("findings") or [])[:6],
            "recommendationCount": len(recommendations),
            "recommendations": recommendations[:4],
        }
        history = [entry] + [dict(item) for item in (experiment.run_history or []) if isinstance(item, dict)]
        experiment.run_history = history[: self.history_limit()]

    def mark_latest_history_application(
        self,
        experiment: OntologyExperiment,
        application: Dict[str, object],
        recommendations: List[Dict[str, object]],
    ) -> None:
        history = [dict(item) for item in (experiment.run_history or []) if isinstance(item, dict)]
        if not history:
            return
        history[0].update({
            "applyStatus": str(application.get("status") or ""),
            "appliedAt": str(application.get("appliedAt") or ""),
            "appliedOntologyChanges": dict(application),
            "recommendations": recommendations[:4],
        })
        experiment.run_history = history[: self.history_limit()]

    def rulebox_snapshot(self) -> Dict[str, object]:
        if not self.ontology_repository or not hasattr(self.ontology_repository, "rulebox_snapshot"):
            return {}
        snapshot = self.ontology_repository.rulebox_snapshot()
        return snapshot if isinstance(snapshot, dict) else {}

    def save_rulebox(self, payload: Dict[str, object]) -> Dict[str, object]:
        if not self.ontology_repository or not hasattr(self.ontology_repository, "save_rulebox"):
            return {"status": "disabled", "saved": False, "reason": "Ontology repository cannot save RuleBox."}
        result = self.ontology_repository.save_rulebox(payload)
        return result if isinstance(result, dict) else {"status": "unknown", "saved": False}

    def run_rulebox(self, payload: Dict[str, object]) -> Dict[str, object]:
        if not self.ontology_repository or not hasattr(self.ontology_repository, "run_rulebox"):
            return {"status": "disabled", "reason": "Ontology repository cannot run RuleBox."}
        result = self.ontology_repository.run_rulebox(payload)
        return result if isinstance(result, dict) else {"status": "unknown"}

    def save_tbox_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        if not self.ontology_repository or not hasattr(self.ontology_repository, "save_graph"):
            return {"status": "disabled", "saved": False, "reason": "Ontology repository cannot save TBox graph."}
        result = self.ontology_repository.save_graph(graph)
        return result if isinstance(result, dict) else {"status": "unknown", "saved": False}

    def facts_graphs(self, symbols: Iterable[str] = None, snapshots: List[AccountSnapshot] = None) -> List[object]:
        return [
            self.facts_graph_from_snapshot(snapshot)
            for snapshot in (snapshots if snapshots is not None else self.monitor_snapshots(symbols))
        ]

    def facts_graph_from_snapshot(self, snapshot: AccountSnapshot) -> PortfolioOntology:
        graph = build_portfolio_ontology(
            list(snapshot.positions or []) + list(snapshot.watchlist or []),
            snapshot.portfolio,
            legacy_by_symbol={item.symbol.upper(): item.to_dict() for item in snapshot.decisions},
            external_signals=snapshot.external_signals,
            portfolio_id=snapshot.account_id,
            runtime_context=self.runtime_context(snapshot),
            include_reasoning_outputs=False,
        )
        return facts_only_graph(graph)

    def runtime_context(self, snapshot: AccountSnapshot) -> Dict[str, object]:
        active_tbox = {}
        if self.ontology_repository and hasattr(self.ontology_repository, "active_tbox_metadata"):
            try:
                active_tbox = self.ontology_repository.active_tbox_metadata()
            except Exception as error:  # noqa: BLE001 - lab runs should report through results, not crash on metadata.
                active_tbox = {"status": "error", "reason": str(error)[:180], "source": "code-fallback"}
        as_of = str(snapshot.generated_at or "").strip()
        snapshot_seed = "|".join([str(snapshot.account_id or ""), as_of or "unknown"])
        return {
            "settings": dict(self.settings),
            "snapshotId": "lab-abox-snapshot:" + hashlib.sha256(snapshot_seed.encode("utf-8")).hexdigest()[:16],
            "asOf": as_of,
            "activeTBox": active_tbox,
            "account": {
                "accountId": snapshot.account_id,
                "accountLabel": snapshot.account_label,
                "provider": snapshot.provider,
                "mode": snapshot.mode,
                "status": snapshot.status,
            },
            "metadata": dict(snapshot.metadata or {}),
            "decisionItems": [item.to_dict() for item in snapshot.decisions],
        }

    def monitor_snapshots(self, symbols: Iterable[str] = None) -> List[AccountSnapshot]:
        if not self.monitor_store or not hasattr(self.monitor_store, "previous"):
            return []
        allowed = set(clean_symbols(symbols or []))
        snapshots = []
        for state in (self.monitor_store.previous or {}).values():
            snapshot = account_snapshot_from_monitor_state(state)
            if not snapshot:
                continue
            if allowed and not snapshot_has_symbol(snapshot, allowed):
                continue
            snapshots.append(snapshot)
        return snapshots


class OntologyLabScheduler:
    def __init__(self, service: OntologyLabService, interval_seconds: int):
        self.service = service
        self.interval_seconds = max(5, int(interval_seconds or 300))
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self, limit: int = 0, force: bool = False) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print("Python ontology lab worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.service.run_once(limit=limit, force=force)
                if result.get("processedCount"):
                    print(
                        "Ontology lab "
                        + str(result.get("status"))
                        + " processed="
                        + str(result.get("processedCount", 0))
                        + " runs="
                        + str(result.get("runCount", 0))
                        + " skipped="
                        + str(result.get("skippedCount", 0))
                    )
            except Exception as error:  # noqa: BLE001 - long-running lab worker must continue after a cycle failure.
                print("Python ontology lab worker error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))


def monitor_snapshot_key(snapshots: Iterable[AccountSnapshot], symbols: Iterable[str] = None) -> str:
    rows = []
    allowed = set(clean_symbols(symbols or []))
    for snapshot in snapshots or []:
        snapshot_symbols = [
            str(item.symbol or "").upper().strip()
            for item in list(snapshot.positions or []) + list(snapshot.watchlist or [])
            if str(item.symbol or "").strip()
        ]
        if allowed:
            snapshot_symbols = [item for item in snapshot_symbols if item in allowed]
        rows.append(
            {
                "accountId": str(snapshot.account_id or ""),
                "generatedAt": str(snapshot.generated_at or ""),
                "status": str(snapshot.status or ""),
                "symbols": sorted(set(snapshot_symbols)),
            }
        )
    encoded = json.dumps(
        {
            "symbols": clean_symbols(symbols or []),
            "snapshots": sorted(rows, key=lambda item: (item.get("accountId") or "", item.get("generatedAt") or "")),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "monitor:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def snapshot_has_symbol(snapshot: AccountSnapshot, symbols: set) -> bool:
    for item in list(snapshot.positions or []) + list(snapshot.watchlist or []):
        if str(item.symbol or "").upper().strip() in symbols:
            return True
    return False


def account_snapshot_from_monitor_state(state: Dict[str, object]) -> AccountSnapshot:
    if not isinstance(state, dict) or not isinstance(state.get("portfolio"), dict):
        return None
    portfolio = dataclass_from_dict(PortfolioSummary, state.get("portfolio") or {})
    positions = positions_from_map(state.get("positions"))
    watchlist = positions_from_map(state.get("watchlist"))
    decisions = decisions_from_map(state.get("decisions"))
    return AccountSnapshot(
        account_id=str(state.get("accountId") or state.get("account_id") or "portfolio"),
        account_label=str(state.get("accountLabel") or state.get("account_label") or "투자 계좌"),
        provider=str(state.get("provider") or ""),
        mode=str(state.get("mode") or ""),
        status=str(state.get("status") or ""),
        generated_at=str(state.get("generatedAt") or state.get("generated_at") or ""),
        portfolio=portfolio,
        positions=positions,
        decisions=decisions,
        external_signals=dict(state.get("externalSignals") or {}),
        watchlist=watchlist,
        metadata=dict(state.get("metadata") or {}),
    )


def positions_from_map(value: object) -> List[Position]:
    if isinstance(value, dict):
        rows = value.values()
    elif isinstance(value, list):
        rows = value
    else:
        rows = []
    return [dataclass_from_dict(Position, item) for item in rows if isinstance(item, dict)]


def decisions_from_map(value: object) -> List[DecisionItem]:
    if isinstance(value, dict):
        rows = value.values()
    elif isinstance(value, list):
        rows = value
    else:
        rows = []
    return [dataclass_from_dict(DecisionItem, item) for item in rows if isinstance(item, dict)]


def dataclass_from_dict(cls, payload: Dict[str, object]):
    payload = dict(payload or {})
    allowed = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in payload.items() if key in allowed})


def facts_only_graph(graph: PortfolioOntology) -> PortfolioOntology:
    stripped_boxes = {"RuleBox", "InferenceBox"}
    stripped_ids = set()
    entities = []
    for item in graph.entities:
        box = str((item.properties or {}).get("ontologyBox") or "ABox")
        if box in stripped_boxes:
            stripped_ids.add(item.entity_id)
            continue
        entities.append(item)
    relations = [
        item
        for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "ABox") not in stripped_boxes
        and item.source not in stripped_ids
        and item.target not in stripped_ids
    ]
    evidence = [
        item
        for item in graph.evidence
        if str((item.value or {}).get("ontologyBox") or "ABox") not in stripped_boxes
    ]
    beliefs = [
        item
        for item in graph.beliefs
        if not str(item.belief_id or "").startswith("belief:inference:")
    ]
    return PortfolioOntology(
        graph.portfolio_id,
        entities=entities,
        relations=relations,
        evidence=evidence,
        beliefs=beliefs,
        opinions=[],
        reasoning_cards=[],
        worldview={**dict(graph.worldview or {}), "runtimeProjectionMode": "ontology-lab-facts-only"},
        prompt=graph.prompt,
    )


def rule_id_from_payload(rule: Dict[str, object]) -> str:
    return str((rule or {}).get("rule_id") or (rule or {}).get("ruleId") or "").strip()


def experiment_candidate_rule_ids(experiments: Iterable[OntologyExperiment]) -> set:
    rule_ids = set()
    for experiment in experiments or []:
        for rule in getattr(experiment, "candidate_rules", []) or []:
            rule_id = rule_id_from_payload(rule)
            if rule_id:
                rule_ids.add(rule_id)
    return rule_ids


def experiment_id_for_candidate(candidate: Dict[str, object], proposed_rule: Dict[str, object]) -> str:
    basis = {
        "candidateId": str((candidate or {}).get("id") or ""),
        "ruleId": rule_id_from_payload(proposed_rule),
        "title": str((candidate or {}).get("title") or ""),
    }
    encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "ontology-exp-ai-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def compact_candidate_skip(candidate: Dict[str, object], reason: str) -> Dict[str, object]:
    proposed_rule = (candidate or {}).get("proposedRule") if isinstance((candidate or {}).get("proposedRule"), dict) else {}
    return {
        "id": str((candidate or {}).get("id") or ""),
        "title": str((candidate or {}).get("title") or ""),
        "ruleId": rule_id_from_payload(proposed_rule),
        "reason": reason,
    }


def compact_source_candidate(candidate: Dict[str, object], rule_id: str) -> Dict[str, object]:
    return {
        "id": str((candidate or {}).get("id") or ""),
        "title": str((candidate or {}).get("title") or ""),
        "status": str((candidate or {}).get("status") or ""),
        "priority": (candidate or {}).get("priority"),
        "ruleId": rule_id,
        "source": str((candidate or {}).get("source") or ""),
        "rationale": str((candidate or {}).get("rationale") or ""),
        "expectedEffect": str((candidate or {}).get("expectedEffect") or ""),
        "risk": str((candidate or {}).get("risk") or ""),
        "requiresData": clean_text_list((candidate or {}).get("requiresData") or []),
    }


def hypothesis_from_candidate(candidate: Dict[str, object]) -> str:
    parts = []
    rationale = str((candidate or {}).get("rationale") or "").strip()
    expected = str((candidate or {}).get("expectedEffect") or "").strip()
    risk = str((candidate or {}).get("risk") or "").strip()
    if rationale:
        parts.append("근거: " + rationale)
    if expected:
        parts.append("기대 효과: " + expected)
    if risk:
        parts.append("검증 리스크: " + risk)
    return " / ".join(parts) or "AI가 제안한 RuleBox 후보를 샌드박스에서 검증합니다."


def clean_text_list(values: Iterable[object]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def merge_candidate_rules(
    baseline_rules: Iterable[Dict[str, object]],
    candidate_rules: Iterable[Dict[str, object]],
) -> tuple:
    merged = [dict(item) for item in (baseline_rules or []) if isinstance(item, dict)]
    existing_ids = {rule_id_from_payload(item) for item in merged if rule_id_from_payload(item)}
    added: List[Dict[str, object]] = []
    skipped: List[str] = []
    for candidate in candidate_rules or []:
        if not isinstance(candidate, dict):
            continue
        rule_id = rule_id_from_payload(candidate)
        if not rule_id:
            continue
        if rule_id in existing_ids:
            skipped.append(rule_id)
            continue
        promoted = dict(candidate)
        promoted["enabled"] = True
        merged.append(promoted)
        added.append(promoted)
        existing_ids.add(rule_id)
    return merged, added, skipped


def ontology_lab_tbox_graph(
    experiment: OntologyExperiment,
    proposal: Dict[str, object],
    stamp: str,
) -> PortfolioOntology:
    proposal = dict(proposal or {})
    relation_types = [
        item
        for item in clean_text_list(proposal.get("newRelationTypes") or [])
        if item not in set(TBOX_RELATION_TYPES)
    ]
    decision_stages = clean_text_list(proposal.get("newDecisionStages") or proposal.get("decisionStages") or [])
    tbox_classes = [
        item
        for item in clean_text_list(proposal.get("tboxClasses") or [])
        if item not in set(TBOX_CLASSES)
    ]
    graph = PortfolioOntology("ontology-lab-tbox:" + experiment.experiment_id)
    if not (relation_types or decision_stages or tbox_classes):
        return graph
    graph.entities.extend(tbox_entities())
    graph.relations.extend(tbox_relations())
    metadata = default_tbox_metadata()
    owner_id = entity_id("bounded-context", "reasoning-insight")
    base_properties = {
        "ontologyBox": "TBox",
        "box": "TBox",
        "version": metadata["version"],
        "tboxVersion": metadata["version"],
        "tboxFingerprint": metadata["fingerprint"],
        "boundedContext": "reasoning-insight",
        "source": "ontology-lab",
        "experimentId": experiment.experiment_id,
        "proposedAt": stamp,
    }
    for class_name in tbox_classes:
        class_id = entity_id("tbox-class", class_name)
        graph.entities.append(OntologyEntity(class_id, class_name, "tbox-class", {
            **base_properties,
            "className": class_name,
            "label": class_name,
            "description": "Ontology lab proposed TBox class from " + experiment.experiment_id,
            "materializationPolicy": "runtime-proposed",
            "materializationBox": "InferenceBox",
        }))
        graph.relations.append(OntologyRelation(owner_id, class_id, "DEFINES_CLASS", properties={
            **base_properties,
            "proposalSource": "ontology-lab",
        }))
    for relation_type in relation_types:
        relation_id = entity_id("tbox-relation", relation_type)
        graph.entities.append(OntologyEntity(relation_id, relation_type, "tbox-relation", {
            **base_properties,
            "relationType": relation_type,
            "label": relation_type,
            "sourceContext": "investment-core",
            "targetContext": "reasoning-insight",
            "description": "Ontology lab proposed relation type from " + experiment.experiment_id,
            "materializationPolicy": "runtime-proposed",
            "materializationBox": "InferenceBox",
        }))
        graph.relations.append(OntologyRelation(owner_id, relation_id, "DEFINES_RELATION", properties={
            **base_properties,
            "sourceContext": "investment-core",
            "targetContext": "reasoning-insight",
            "proposalSource": "ontology-lab",
        }))
    for stage in decision_stages:
        stage_id = entity_id("decision-stage", stage)
        graph.entities.append(OntologyEntity(stage_id, stage, "decision-stage", {
            **base_properties,
            "tboxClass": "DecisionStage",
            "decisionStage": stage,
            "label": stage,
            "description": "Ontology lab proposed decision stage from " + experiment.experiment_id,
            "materializationPolicy": "runtime-proposed",
            "materializationBox": "InferenceBox",
        }))
        graph.relations.append(OntologyRelation(owner_id, stage_id, "DEFINES_DECISION_STAGE", properties={
            **base_properties,
            "decisionStage": stage,
            "proposalSource": "ontology-lab",
        }))
    graph.worldview = {
        "model": "ontology-lab-tbox-application",
        "experimentId": experiment.experiment_id,
        "appliedAt": stamp,
        "skipNativeReasoning": True,
    }
    return graph


def compact_apply_result(result: Dict[str, object]) -> Dict[str, object]:
    result = dict(result or {})
    compact = {
        "status": str(result.get("status") or ""),
        "saved": bool(result.get("saved")) if "saved" in result else None,
        "reason": str(result.get("reason") or ""),
    }
    for key in ["ruleCount", "entityCount", "relationCount", "statementCount", "versionCount"]:
        if key in result:
            compact[key] = result.get(key)
    return {key: value for key, value in compact.items() if value is not None and value != ""}


def ontology_apply_succeeded(result: Dict[str, object]) -> bool:
    result = dict(result or {})
    return bool(result.get("saved")) or str(result.get("status") or "").lower() == "ok"


def ontology_apply_disabled(result: Dict[str, object]) -> bool:
    return str((result or {}).get("status") or "").lower() == "disabled"


def ontology_apply_failed(result: Dict[str, object]) -> bool:
    status = str((result or {}).get("status") or "").lower()
    return status in {"error", "neo4j-error", "invalid-rulebox", "unsupported-uri", "driver-missing"}


def ontology_application_status(
    rulebox_result: Dict[str, object],
    tbox_result: Dict[str, object],
    added_rules: List[Dict[str, object]],
    tbox_entities_to_apply: List[OntologyEntity],
) -> str:
    if ontology_apply_failed(rulebox_result) or ontology_apply_failed(tbox_result):
        return "error"
    if ontology_apply_succeeded(rulebox_result) or ontology_apply_succeeded(tbox_result):
        return "applied"
    if ontology_apply_disabled(rulebox_result) or ontology_apply_disabled(tbox_result):
        return "disabled"
    if not added_rules and not tbox_entities_to_apply:
        return "already-applied"
    return "pending"


def mark_recommendations_applied(
    recommendations: List[Dict[str, object]],
    application: Dict[str, object],
) -> List[Dict[str, object]]:
    applied_types = {
        "promote-rule",
        "review-rule-promotion",
        "register-relation-types",
        "register-decision-stages",
        "reuse-existing-relation-types",
    }
    applied = []
    for item in recommendations or []:
        recommendation = dict(item)
        if str(recommendation.get("type") or "") in applied_types:
            recommendation["applyStatus"] = str(application.get("status") or "")
            recommendation["appliedAt"] = str(application.get("appliedAt") or "")
            recommendation["appliedRuleIds"] = list(application.get("ruleIds") or [])
        applied.append(recommendation)
    return applied
