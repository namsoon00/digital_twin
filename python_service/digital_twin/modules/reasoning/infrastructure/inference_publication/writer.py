"""Writer for generation-scoped InferenceBox publication."""

from typing import Dict, Iterable, List
import time

from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_projection_fingerprint import material_graph_fingerprint
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from .markers import inference_generation_delete_queries, inference_generation_marker_row
from .ports import PublicationRuntime, PublicationStore
from .values import typedb_bool


def write_inferencebox_graph(
    store: PublicationStore,
    graph: PortfolioOntology,
    *,
    runtime: PublicationRuntime,
) -> Dict[str, object]:
    if not store.address:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        }
    imported = store.driver_imports()
    if imported[0] is None:
        return {
            "configured": True,
            "saved": False,
            "status": "driver-missing",
            "graphStore": "typedb",
            "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160],
        }
    inference_material_fingerprint = material_graph_fingerprint(graph)
    graph.worldview["inferenceMaterialFingerprint"] = inference_material_fingerprint
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    node_rows = [
        row for row in store.node_rows(graph)
        if str(row.get("ontologyBox") or "") == "InferenceBox"
    ]
    relation_rows = [
        row for row in store.rows_for_relations(graph) + store.support_relation_rows(graph)
        if str(row.get("ontologyBox") or "") == "InferenceBox"
    ]
    updated_at = runtime.now()
    settings = runtime.settings()
    node_queries = store.batched_node_insert_queries(
        node_rows,
        updated_at,
        int(number_or_none(settings.get("typedbInferenceBoxNodeBatchSize")) or 25),
        store.write_query_max_bytes(settings),
    )
    relation_write_plans = store.inferencebox_given_relation_insert_plans(
        relation_rows,
        updated_at,
        settings=settings,
    )
    planned_batch_count = len(node_queries) + len(relation_write_plans)
    write_timing: Dict[str, object] = {
        "queryCount": planned_batch_count,
        "nodeQueryCount": len(node_queries),
        "relationQueryCount": len(relation_write_plans),
        "relationBatchSize": store.inferencebox_relation_batch_size(),
        "relationGivenBatchSize": store.inferencebox_given_relation_batch_size(),
    }
    generation_id = str((graph.worldview or {}).get("inferenceGenerationId") or "").strip()
    world_id = str((graph.worldview or {}).get("worldId") or "").strip()
    fresh_generation = typedb_bool(
        (graph.worldview or {}).get("freshInferenceGeneration")
    )
    marker_query = store.node_insert_query(
        inference_generation_marker_row(graph, node_rows, relation_rows, "candidate", now=runtime.now),
        updated_at,
    ) if generation_id else ""
    statement_count = len(node_rows) + len([row for row in relation_rows if row.get("source") and row.get("target")])
    try:
        def operation():
            with runtime.timeout(store.write_operation_timeout_seconds(), "TypeDB InferenceBox graph save"):
                write_started = time.monotonic()
                query_durations_ms: List[float] = []
                driver = store.open_driver(imported)
                try:
                    store.ensure_database(driver)
                    transaction_query_count = store.inferencebox_write_transaction_query_count()
                    write_timing["transactionQueryCount"] = transaction_query_count
                    write_timing["relationGivenBatchCount"] = 0
                    write_timing["relationGivenRowCount"] = 0
                    write_timing["relationGivenFallbackCount"] = 0
                    write_timing["relationLegacyQueryCount"] = 0

                    def write_query_chunks(query_rows: Iterable[str], stage: str) -> None:
                        query_list = [str(query) for query in query_rows or [] if str(query or "").strip()]
                        stage_started = time.monotonic()
                        for offset in range(0, len(query_list), transaction_query_count):
                            query_batch = query_list[offset: offset + transaction_query_count]

                            def write_batch():
                                with driver.transaction(
                                    store.database,
                                    TransactionType.WRITE,
                                    options=store.write_transaction_options(),
                                ) as tx:
                                    for query in query_batch:
                                        query_started = time.monotonic()
                                        tx.query(query).resolve()
                                        query_durations_ms.append(round((time.monotonic() - query_started) * 1000, 1))
                                    tx.commit()

                            store.with_typedb_retries(write_batch)
                        write_timing[stage + "Ms"] = round((time.monotonic() - stage_started) * 1000, 1)

                    candidate_queries = (
                        inference_generation_delete_queries(generation_id, world_id=world_id)
                        if generation_id and not fresh_generation
                        else []
                    )
                    write_timing["candidateDeleteSkipped"] = bool(
                        generation_id and fresh_generation
                    )
                    candidate_started = time.monotonic()
                    write_query_chunks(candidate_queries, "candidateDelete")
                    write_query_chunks(node_queries, "candidateNodeWrite")

                    given_plans = [
                        plan for plan in relation_write_plans
                        if str(plan.get("query") or "")
                        and list(plan.get("givenRows") or [])
                    ]
                    legacy_plans = [
                        plan for plan in relation_write_plans
                        if str(plan.get("query") or "")
                        and not list(plan.get("givenRows") or [])
                    ]

                    def write_given_plan_batch(plans: List[Dict[str, object]]) -> None:
                        if not plans:
                            return

                        def write_given_transaction():
                            with driver.transaction(
                                store.database,
                                TransactionType.WRITE,
                                options=store.write_transaction_options(),
                            ) as tx:
                                for plan in plans:
                                    query_started = time.monotonic()
                                    tx.query(
                                        str(plan.get("query") or ""),
                                        given_rows=list(plan.get("givenRows") or []),
                                    ).resolve()
                                    query_durations_ms.append(round(
                                        (time.monotonic() - query_started) * 1000,
                                        1,
                                    ))
                                tx.commit()

                        store.with_typedb_retries(write_given_transaction)

                    relation_started = time.monotonic()
                    for offset in range(0, len(given_plans), transaction_query_count):
                        plan_batch = given_plans[offset: offset + transaction_query_count]
                        try:
                            write_given_plan_batch(plan_batch)
                            write_timing["relationGivenBatchCount"] += len(plan_batch)
                            write_timing["relationGivenRowCount"] += sum(
                                len(plan.get("givenRows") or [])
                                for plan in plan_batch
                            )
                            continue
                        except Exception:
                            pass

                        for plan in plan_batch:
                            try:
                                write_given_plan_batch([plan])
                                write_timing["relationGivenBatchCount"] += 1
                                write_timing["relationGivenRowCount"] += len(
                                    plan.get("givenRows") or []
                                )
                                continue
                            except Exception:
                                write_timing["relationGivenFallbackCount"] += 1
                            fallback_queries = store.batched_relation_insert_queries(
                                list(plan.get("rows") or []),
                                updated_at,
                                store.inferencebox_relation_batch_size(settings),
                                store.write_query_max_bytes(settings),
                            )
                            write_query_chunks(
                                fallback_queries,
                                "candidateRelationFallback",
                            )
                            write_timing["relationLegacyQueryCount"] += len(
                                fallback_queries
                            )

                    for plan in legacy_plans:
                        write_query_chunks(
                            [str(plan.get("query") or "")],
                            "candidateRelationLegacy",
                        )
                        write_timing["relationLegacyQueryCount"] += 1
                    write_timing["candidateRelationWriteMs"] = round(
                        (time.monotonic() - relation_started) * 1000,
                        1,
                    )
                    if marker_query:
                        write_query_chunks([marker_query], "candidateMarker")
                    write_timing["candidateCommitMs"] = round(
                        (time.monotonic() - candidate_started) * 1000,
                        1,
                    )
                    write_timing["candidateWriteMs"] = write_timing.get(
                        "candidateCommitMs", 0.0
                    )
                    write_timing["relationWriteMode"] = (
                        "given-rows"
                        if write_timing["relationGivenBatchCount"]
                        and not write_timing["relationGivenFallbackCount"]
                        else "given-rows-with-legacy-fallback"
                        if write_timing["relationGivenBatchCount"]
                        else "legacy-single-edge"
                    )
                finally:
                    store.close_driver(driver)
                    write_timing["slowestQueryMs"] = max(query_durations_ms) if query_durations_ms else 0.0
                    write_timing["totalQueryMs"] = round(sum(query_durations_ms), 1)
                    write_timing["totalWriteMs"] = round((time.monotonic() - write_started) * 1000, 1)
        store.with_typedb_retries(operation)
        validation_started = time.monotonic()
        candidate_validation = store.validate_inference_generation_candidate(
            graph,
            generation_id,
            len(node_rows),
            len(relation_rows),
            world_id=world_id,
        ) if generation_id else {"status": "legacy", "valid": True}
        write_timing["candidateValidationMs"] = round((time.monotonic() - validation_started) * 1000, 1)
        if not candidate_validation.get("valid"):
            return {
                "configured": True,
                "saved": False,
                "status": "candidate-validation-failed",
                "graphStore": "typedb",
                "reason": str(candidate_validation.get("reason") or "InferenceBox candidate validation failed."),
                "entityCount": len(node_rows),
                "relationCount": len(relation_rows),
                "statementCount": statement_count,
                "batchCount": planned_batch_count,
                "insertMode": "batched-candidate",
                "publicationStatus": "candidate",
                "preservedPreviousInference": True,
                "inferenceGenerationId": generation_id,
                "candidateValidation": candidate_validation,
                "writeTiming": write_timing,
            }
        activation_started = time.monotonic()
        activation = store.activate_inference_generation(graph, node_rows, relation_rows, world_id=world_id) if generation_id else {"status": "legacy", "activated": True}
        write_timing["activationMs"] = round((time.monotonic() - activation_started) * 1000, 1)
        if not activation.get("activated"):
            return {
                "configured": True,
                "saved": False,
                "status": "activation-failed",
                "graphStore": "typedb",
                "reason": str(activation.get("reason") or "InferenceBox candidate activation failed."),
                "entityCount": len(node_rows),
                "relationCount": len(relation_rows),
                "statementCount": statement_count,
                "batchCount": planned_batch_count,
                "insertMode": "batched-candidate",
                "publicationStatus": "candidate",
                "preservedPreviousInference": True,
                "inferenceGenerationId": generation_id,
                "candidateValidation": candidate_validation,
                "activation": activation,
                "writeTiming": write_timing,
            }
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "entityCount": len(node_rows),
            "relationCount": len(relation_rows),
            "statementCount": statement_count,
            "batchCount": planned_batch_count,
            "insertMode": "batched-candidate-activation",
            "publicationStatus": "active" if marker_query else "legacy-unmarked",
            "inferenceGenerationId": generation_id,
            "inferenceMaterialFingerprint": inference_material_fingerprint,
            "inferenceGenerationAt": str((graph.worldview or {}).get("inferenceGenerationAt") or ""),
            "worldId": world_id,
            "worldType": str((graph.worldview or {}).get("worldType") or ""),
            "tenantId": str((graph.worldview or {}).get("tenantId") or ""),
            "accountId": str((graph.worldview or {}).get("accountId") or ""),
            "candidateValidation": candidate_validation,
            "activation": activation,
            "writeTiming": write_timing,
        }
    except Exception as error:  # noqa: BLE001 - materialization failure must be visible to diagnostics.
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": runtime.error_code(error),
            "reason": str(error)[:220],
            "entityCount": len(node_rows),
            "relationCount": len(relation_rows),
            "statementCount": statement_count,
            "batchCount": planned_batch_count,
            "insertMode": "batched",
            "publicationStatus": "not-published",
            "inferenceGenerationId": generation_id,
            "writeTiming": write_timing,
        }
