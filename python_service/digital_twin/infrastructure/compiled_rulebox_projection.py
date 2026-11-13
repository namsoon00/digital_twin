"""Portable RuleBox projection with durable evidence and no TypeDB runtime I/O."""

from copy import deepcopy
from dataclasses import replace
import time

from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from digital_twin.infrastructure.graph_store_inferencebox import inferencebox_snapshot_from_rows
from digital_twin.modules.model_registry.contracts import rulebox_rules_hash, rulebox_semantic_violations, GraphInferenceRule
from digital_twin.modules.reasoning.domain.ontology_schema import normalize_tbox_metadata
from digital_twin.modules.outcomes.contracts import hypothesis_calibration_snapshot_from_abox_rows
from digital_twin.modules.reasoning.domain.ontology_worlds import world_from_snapshot, world_metadata
from digital_twin.modules.reasoning.domain.compiled_rulebox import (
    COMPILED_RULEBOX_VERSION, CompiledRuleboxEvaluator, compile_rulebox, fingerprint,
)
from digital_twin.modules.reasoning.domain.ontology_projection_audit import (
    build_ontology_projection_run, complete_ontology_projection_run,
)
from digital_twin.modules.reasoning.domain.rule_execution_proof import compiled_rule_execution_completed


class FrozenRuleboxRepository:
    """Read an immutable approved release artifact; never silently read today's catalog."""
    backend = "compiled-rulebox"

    def __init__(self, artifact):
        self.artifact = deepcopy(artifact)
        self.rules = self.artifact.get("rules") or []
        if rulebox_rules_hash(self.rules) != self.artifact.get("ruleboxFingerprint"):
            raise ValueError("Compiled RuleBox artifact fingerprint mismatch")
        violations = rulebox_semantic_violations([GraphInferenceRule.from_dict(r) for r in self.rules])
        if violations or normalize_tbox_metadata(self.artifact.get("tboxMetadata") or {}).get("fingerprint") != self.artifact.get("tboxFingerprint"):
            raise ValueError("Invalid frozen RuleBox/TBox semantics: " + str(violations)[:300])
        self.program = compile_rulebox(self.rules, tbox_fingerprint=self.artifact.get("tboxFingerprint", ""))

    def rulebox_snapshot(self):
        return {"status": "ok", "configured": True, "graphStore": self.backend, "frozenReleaseVerified": True,
                "rules": deepcopy(self.rules), "ruleCount": len(self.rules),
                "sourceRulesHash": self.artifact["ruleboxFingerprint"],
                "rulesHash": self.artifact["ruleboxFingerprint"],
                "ruleboxRulesHash": self.artifact["ruleboxFingerprint"],
                "source": "immutable-rulebox-release-artifact"}

    def active_tbox_metadata(self):
        return {**deepcopy(self.artifact["tboxMetadata"]), "status": "ok"}


def inference_snapshot(evaluation, *, snapshot_id, source_fingerprint, account_id, world_id, symbols, as_of, rules_hash):
    graph = evaluation["graph"]
    generation = "compiled-generation:" + fingerprint({"input": source_fingerprint,
        "rules": evaluation["compiledRuleboxFingerprint"], "account": account_id,
        "source": snapshot_id, "symbols": symbols})[:32]
    metadata = {"reasoningMode": COMPILED_RULEBOX_VERSION, "materializationSource": COMPILED_RULEBOX_VERSION,
                "ruleExecutionBackend": COMPILED_RULEBOX_VERSION,
                "inferenceGenerationId": generation, "inferenceGenerationAt": as_of,
                "sourceAboxSnapshotId": snapshot_id, "ruleboxRulesHash": rules_hash,
                "nativeTypeDbReasoned": False, "typedbNativeRuleReasoned": False,
                "compiledRuleboxReasoned": True, "worldId": world_id, "accountId": account_id}
    entities, relations = [], []
    by_id = {e.entity_id: e for e in graph.entities}
    for e in graph.entities:
        if e.properties.get("ontologyBox") != "InferenceBox":
            continue
        p = {**e.properties, **metadata}
        entities.append({**p, "id": e.entity_id, "label": e.label, "kind": e.kind,
                         "semanticRuleId": p.get("ruleId", ""), "properties": p})
    for r in graph.relations:
        if r.properties.get("ontologyBox") != "InferenceBox":
            continue
        p = {**r.properties, **metadata}
        relations.append({**p, "type": r.relation_type, "source": r.source, "target": r.target,
            "sourceLabel": by_id[r.source].label if r.source in by_id else r.source,
            "targetLabel": by_id[r.target].label if r.target in by_id else r.target,
            "semanticRuleId": p.get("ruleId", ""), "properties": p})
    traces = [e for e in entities if e["kind"] == "inference-trace"]
    inference = inferencebox_snapshot_from_rows({"entities": entities, "relations": relations,
                                               "traces": traces}, "graphStoreInferenceBox", symbols)
    for rows in (inference.get("entities", []), inference.get("relations", []), inference.get("traces", [])):
        for row in rows:
            row.update(metadata)
    for trace in inference.get("traces", []):
        trace.setdefault("matched", True)
    proof = {"version": COMPILED_RULEBOX_VERSION, "complete": True, "durable": False,
             "programFingerprint": evaluation["compiledRuleboxFingerprint"], "sourceFingerprint": source_fingerprint,
             "accountId": account_id, "worldId": world_id, "targetSymbols": symbols,
             "generationId": generation, "sourceAboxSnapshotId": snapshot_id,
             "enabledRuleIds": list(evaluation["enabledRuleIds"]),
             "evaluatedRuleIds": [e["ruleId"] for e in evaluation["evaluations"]]}
    inference.update({**metadata, "graphStore": "compiled-rulebox", "source": "graphStoreInferenceBox",
        "status": "ok" if relations or traces else "empty", "targetSymbols": symbols,
        "generationAligned": True, "nativeTypeDbReasoningUsed": False,
        "nativeTypeDbReasoningCompleted": False, "typedbNativeRuleEvaluationCompleted": False,
        "compiledRuleboxFingerprint": evaluation["compiledRuleboxFingerprint"],
        "compiledExecutionProof": proof, "ruleExecutionBackend": COMPILED_RULEBOX_VERSION,
        "ruleExecutionCompleted": True, "compiledInferenceOutcome": "matched" if traces else "no-match",
        "entityCount": len(entities), "relationCount": len(relations), "traceCount": len(traces),
        "querySource": "immutable-source-rulebox-evaluation"})
    inference["hypothesisCalibration"] = hypothesis_calibration_snapshot_from_abox_rows(
        [{**e.properties, "entityId": e.entity_id} for e in graph.entities if e.kind == "hypothesis-calibration"],
        symbols=symbols, source_abox_snapshot_id=snapshot_id, generation_aligned=True,
        active_membership_verified=True, source="compiled-abox-hypothesis-calibration")
    return inference


class CompiledPortfolioProjectionRecorder(PortfolioOntologyProjectionRecorder):
    """Reuse source/model assembly; replace physical graph writes and rule reads."""

    def prepare_runtime_context(self, snapshot, target_symbols=None, progress_callback=None):
        # Native preparation released a scarce writer before the source reads.
        # This backend has no writer. Preparing the unscoped source and then
        # the compact graph input produced different cache keys and two reads.
        return {"status": "inline-once", "sourceAsOf": str(snapshot.generated_at or "")}

    def runtime_context_cache_enabled(self):
        # Each live request owns one context. Full-payload hash/copy caches cost
        # more than their reads for distinct observation clocks; dependency
        # owners still retain their small revision-checked prepared inputs.
        return False

    def graph_assembly_cache_enabled(self):
        return False

    def active_graph_store_key(self, result=None):
        return "compiled-rulebox"

    def record_snapshot(self, snapshot, target_symbols=None, reasoning_context=None, progress_callback=None):
        started = time.perf_counter()
        context = dict(reasoning_context or {})
        symbols = sorted(set(target_symbols or self.snapshot_symbols(snapshot)))
        context["projectionComputationTargetSymbols"] = symbols
        program = self.repository.program

        def progress(stage, **details):
            if progress_callback:
                progress_callback("compiled_rulebox." + stage, details)

        catalog = self.ensure_rulebox_ready()
        if catalog.get("status") != "ready":
            raise RuntimeError("Frozen RuleBox is not ready: " + str(catalog.get("reason")))
        _, graph, assembly = self.build_graph_assembly(snapshot, catalog,
            target_symbols=symbols, target_scoped_input=True,
            progress_callback=progress, reasoning_context=context)
        world = world_from_snapshot(snapshot, self.settings)
        world_id = world.world_id
        graph.worldview.update(**world_metadata(world), evidenceEvaluationAsOf=snapshot.generated_at)
        source_packet = {"portfolioId": graph.portfolio_id, "entities": [e.to_dict() for e in graph.entities],
            "relations": [r.to_dict() for r in graph.relations], "worldview": dict(graph.worldview)}
        source_fingerprint = fingerprint(source_packet)
        snapshot_id = "compiled-abox:" + source_fingerprint[:32]
        graph.worldview["aboxSnapshotId"] = snapshot_id
        progress("evaluation.start", ruleCount=len(program.rule_order))
        evaluation = CompiledRuleboxEvaluator(program).evaluate(graph, symbols=symbols)
        progress("evaluation.done", runtimeMs=evaluation["durationMs"], matchedCount=len(evaluation["matches"]))
        inference = inference_snapshot(evaluation, snapshot_id=snapshot_id,
            source_fingerprint=source_fingerprint, account_id=snapshot.account_id,
            world_id=world_id, symbols=symbols, as_of=snapshot.generated_at,
            rules_hash=self.repository.artifact["ruleboxFingerprint"])
        result = {"status": "ok", "saved": True, "configured": True, "graphStore": "compiled-rulebox",
            "activeGraphStore": "compiled-rulebox", "worldId": world_id,
            "accountId": snapshot.account_id, "sourceAboxSnapshotId": snapshot_id,
            "inferenceBox": inference, "materialFingerprint": source_fingerprint,
            "reasoningContext": context, "entityCount": len(graph.entities),
            "relationCount": len(graph.relations), "graphInput": assembly,
            "ruleboxExecution": {"status": "ok", "graphStore": "compiled-rulebox",
                "backend": COMPILED_RULEBOX_VERSION, "matches": evaluation["matches"],
                "ruleEvaluations": evaluation["evaluations"], "ruleCount": len(program.rule_order),
                "executedRules": [{**row, "candidateSymbols": symbols, "status": "completed",
                    "executionStage": "compiled", "queryMode": COMPILED_RULEBOX_VERSION}
                    for row in evaluation["evaluations"]],
                "evidenceEvaluationAsOf": snapshot.generated_at,
                "ruleExecutionCompleted": True, "nativeTypeDbReasoningUsed": False},
            "runtimeStages": {**assembly.get("runtimeStages", {}),
                "compiledRuleEvaluationMs": evaluation["durationMs"], "aboxPersistenceMs": 0,
                "typeDbNativeInferenceMs": 0},
            "sharedInferenceExecution": {"evaluatedSymbols": symbols},
            "ruleboxRulesHash": self.repository.artifact["ruleboxFingerprint"]}
        # A result is never delivery-eligible until its exact facts and proof
        # have been durably recorded. No TypeDB completion flag is fabricated.
        run = build_ontology_projection_run(snapshot, graph, source_fingerprint, snapshot_id,
            "compiled-rulebox", target_symbols=symbols, rulebox_metadata=catalog,
            reasoning_context=context, execution_namespace=self.execution_namespace())
        run = replace(run, context_payload={**run.context_payload,
            "compiledSourceGraph": source_packet, "compiledRuleboxFingerprint": program.fingerprint})
        if self.projection_run_store is None:
            raise RuntimeError("Compiled reasoning requires durable source and execution audit")
        audit_started = time.perf_counter()
        atomic_commit = getattr(self.projection_run_store, "commit_compiled_projection", None)
        if not callable(atomic_commit):
            self.projection_run_store.begin(run)
        inference["compiledExecutionProof"]["durable"] = True
        result["projectionAudit"] = {"status": "recorded", "runId": run.run_id, "sourceSnapshotRecorded": True}
        result["runtimeStages"]["totalProjectionMs"] = round((time.perf_counter() - started) * 1000)
        complete = complete_ontology_projection_run(run, result)
        complete = replace(complete, result_payload={**complete.result_payload,
            "compiledInferenceBox": inference,
            "compiledRuleEvaluations": evaluation["evaluations"]})
        if callable(atomic_commit):
            atomic_commit(complete, result)
            result["projectionAudit"]["traceMaterialization"] = "deferred"
        else:
            self.projection_run_store.complete_with_execution_trace(complete, result)
        result["runtimeStages"]["auditPersistenceMs"] = round((time.perf_counter() - audit_started) * 1000)
        result["runtimeStages"]["totalProjectionMs"] = round((time.perf_counter() - started) * 1000)
        if not compiled_rule_execution_completed(inference):
            raise RuntimeError("Compiled RuleBox result proof is incomplete")
        snapshot.metadata.setdefault("ontology", {}).update(
            projection=result, activeGraphStore="compiled-rulebox")
        return result
