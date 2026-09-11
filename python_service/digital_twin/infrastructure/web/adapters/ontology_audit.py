"""Web ontology audit boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.runtime_identity import runtime_identity
from digital_twin.infrastructure.service_factory import build_investment_strategy_proposal_service
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.web.adapters.ontology_access import ontology_audit_symbols
from digital_twin.infrastructure.web.adapters.ontology_access import ontology_repository_world_call
from digital_twin.infrastructure.web.adapters.ontology_access import ontology_world_id_from_query
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.common import safe_int
from digital_twin.modules.read_models.public import OntologyDiagnosticsService
from typing import Dict
from typing import List
import hashlib
import json


ONTOLOGY_AUDIT_BOXES = ["TBox", "ABox", "RuleBox", "RuleBoxGovernance", "LanguageGovernance", "InferenceBox"]


ONTOLOGY_AUDIT_SECTION_LABELS = {
    "tbox": ("TBox", "스키마와 관계 타입"),
    "abox": ("ABox", "현재 실체 데이터"),
    "rulebox": ("RuleBox", "운영 규칙과 후보"),
    "inferencebox": ("InferenceBox", "세대별 추론 결과"),
    "language": ("LanguageGovernance", "보편언어 사전과 승인 상태"),
    "evidence": ("Evidence Trace", "근거, 믿음, 의견, 실행 계획"),
    "sync": ("TypeDB Sync", "동기화와 저장소 상태"),
}


ONTOLOGY_AUDIT_EVIDENCE_KINDS = {
    "evidence",
    "research-evidence",
    "belief",
    "investment-opinion",
    "opinion",
    "active-investment-opinion",
    "execution-plan",
    "reasoning-card",
    "inference-trace",
    "insight",
    "data-quality",
    "data-freshness",
    "provenance",
    "source-reliability",
    "missing-data",
}


def ontology_audit_row_text(row: Dict[str, object]) -> str:
    try:
        return json.dumps(row, ensure_ascii=False, sort_keys=True).lower()
    except (TypeError, ValueError):
        return str(row or "").lower()


def ontology_audit_row_payload(row: Dict[str, object], row_type: str) -> Dict[str, object]:
    raw = dict(row or {})
    label = str(raw.get("label") or raw.get("title") or raw.get("id") or raw.get("relationType") or raw.get("type") or row_type)
    kind = str(raw.get("kind") or raw.get("nodeKind") or raw.get("relationType") or raw.get("type") or row_type)
    box = str(raw.get("ontologyBox") or raw.get("box") or "")
    source = str(raw.get("sourceLabel") or raw.get("source") or "")
    target = str(raw.get("targetLabel") or raw.get("target") or "")
    relation_type = str(raw.get("relationType") or raw.get("type") or "")
    stable_source = json.dumps({
        "type": row_type,
        "box": box,
        "id": raw.get("id"),
        "source": raw.get("source"),
        "target": raw.get("target"),
        "relationType": relation_type,
        "label": label,
    }, ensure_ascii=False, sort_keys=True)
    return {
        "key": hashlib.sha1(stable_source.encode("utf-8")).hexdigest()[:14],
        "rowType": row_type,
        "id": str(raw.get("id") or ""),
        "label": label,
        "kind": kind,
        "box": box,
        "relationType": relation_type,
        "source": source,
        "target": target,
        "symbol": str(raw.get("symbol") or ""),
        "ruleId": str(raw.get("ruleId") or raw.get("sourceRuleId") or raw.get("semanticRuleId") or ""),
        "status": str(raw.get("status") or ""),
        "updatedAt": str(raw.get("updatedAt") or raw.get("createdAt") or ""),
        "weight": raw.get("weight"),
        "raw": raw,
    }


def ontology_audit_filtered_rows(rows: List[Dict[str, object]], search: str, symbols: List[str]) -> List[Dict[str, object]]:
    needle = str(search or "").strip().lower()
    clean_symbols = [item.upper() for item in symbols or [] if item]
    result = []
    for row in rows or []:
        haystack = ontology_audit_row_text(row)
        if needle and needle not in haystack:
            continue
        if clean_symbols and not any(symbol in haystack.upper() for symbol in clean_symbols):
            continue
        result.append(row)
    return result


def ontology_audit_section_payload(
    section_id: str,
    rows: List[Dict[str, object]],
    limit: int,
    offset: int,
    search: str,
    symbols: List[str],
) -> Dict[str, object]:
    label, description = ONTOLOGY_AUDIT_SECTION_LABELS.get(section_id, (section_id, ""))
    filtered = ontology_audit_filtered_rows(rows, search, symbols)
    paged = filtered[offset: offset + limit]
    return {
        "id": section_id,
        "label": label,
        "description": description,
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "hasMore": offset + limit < len(filtered),
        "entityCount": len([row for row in filtered if row.get("rowType") == "entity"]),
        "relationCount": len([row for row in filtered if row.get("rowType") == "relation"]),
        "rows": paged,
    }


def ontology_audit_rulebox_rows(rulebox: Dict[str, object], graph_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    rows = list(graph_rows or [])
    if rows:
        return rows
    for rule in rulebox.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        rows.append(ontology_audit_row_payload({
            **rule,
            "id": rule.get("id") or rule.get("rule_id") or rule.get("name"),
            "label": rule.get("label") or rule.get("title") or rule.get("id") or "RuleBox rule",
            "kind": "rule",
            "ontologyBox": "RuleBox",
            "status": "fallback" if rulebox.get("defaultsFallbackUsed") else str(rule.get("status") or "active"),
        }, "rule"))
    return rows


def ontology_audit_sync_rows(
    repo,
    tbox: Dict[str, object],
    rulebox: Dict[str, object],
    inferencebox: Dict[str, object],
    diagnostics: Dict[str, object],
    world_id: str = "",
    include_generation_records: bool = True,
) -> List[Dict[str, object]]:
    rows = [
        ontology_audit_row_payload({
            "id": "typedb.tbox",
            "label": "TBox metadata",
            "kind": "sync-status",
            "ontologyBox": "TBox",
            "status": tbox.get("status") or ("ok" if tbox.get("configured") else "disabled"),
            "updatedAt": tbox.get("updatedAt") or "",
            "source": tbox.get("source") or tbox.get("storeSource") or "",
            "raw": tbox,
        }, "status"),
        ontology_audit_row_payload({
            "id": "typedb.rulebox",
            "label": "RuleBox snapshot",
            "kind": "sync-status",
            "ontologyBox": "RuleBox",
            "status": rulebox.get("status") or ("ok" if rulebox.get("configured") else "disabled"),
            "updatedAt": rulebox.get("updatedAt") or "",
            "ruleCount": rulebox.get("ruleCount"),
            "raw": rulebox,
        }, "status"),
        ontology_audit_row_payload({
            "id": "typedb.inferencebox",
            "label": "InferenceBox snapshot",
            "kind": "sync-status",
            "ontologyBox": "InferenceBox",
            "status": inferencebox.get("status") or ("ok" if inferencebox.get("configured") else "disabled"),
            "updatedAt": inferencebox.get("updatedAt") or "",
            "relationCount": inferencebox.get("relationCount"),
            "traceCount": inferencebox.get("traceCount"),
            "raw": inferencebox,
        }, "status"),
        ontology_audit_row_payload({
            "id": "ontology.diagnostics",
            "label": "Ontology diagnostics",
            "kind": "diagnostics",
            "ontologyBox": "Runtime",
            "status": diagnostics.get("status") or diagnostics.get("readiness") or "",
            "updatedAt": diagnostics.get("generatedAt") or "",
            "raw": diagnostics,
        }, "status"),
    ]
    # The default audit page is deliberately a bounded summary.  Reading every
    # historical InferenceBox node and relation just to show the latest 20
    # generation labels can dominate page load as the graph grows.  The
    # detailed sync section still opts in to this durable history read.
    if include_generation_records and hasattr(repo, "read_inference_generation_records"):
        try:
            for index, generation in enumerate(ontology_repository_world_call(
                repo,
                "read_inference_generation_records",
                world_id=world_id,
            )[:20]):
                rows.append(ontology_audit_row_payload({
                    **generation,
                    "id": generation.get("generationId") or generation.get("snapshotId") or ("generation-" + str(index + 1)),
                    "label": "Inference generation " + str(index + 1),
                    "kind": "inference-generation",
                    "ontologyBox": "InferenceBox",
                    "status": "materialized",
                    "updatedAt": generation.get("updatedAt") or "",
                }, "status"))
        except Exception as error:  # noqa: BLE001 - audit UI should expose read errors instead of failing.
            rows.append(ontology_audit_row_payload({
                "id": "typedb.inference-generation.error",
                "label": "Inference generation read error",
                "kind": "sync-error",
                "ontologyBox": "InferenceBox",
                "status": "error",
                "reason": str(error)[:220],
            }, "status"))
    return rows


def ontology_audit_payload(query: Dict[str, List[str]], requested_section: str = "") -> Dict[str, object]:
    settings = runtime_settings()
    repo = ontology_repository_from_settings(settings)
    limit = safe_int(first_query(query, "limit"), 80, 1, 300)
    offset = safe_int(first_query(query, "offset"), 0, 0, 100000)
    search = first_query(query, "q") or first_query(query, "query")
    symbols = ontology_audit_symbols(query)
    world_id = ontology_world_id_from_query(query)
    section_filter = str(requested_section or first_query(query, "section") or "").strip().lower()
    if section_filter == "all":
        section_filter = ""
    compact_all = not section_filter
    fast_compact_summary = compact_all and not search and not symbols
    section_ids = [section_filter] if section_filter in ONTOLOGY_AUDIT_SECTION_LABELS else list(ONTOLOGY_AUDIT_SECTION_LABELS.keys())
    section_box_map = {
        "tbox": ["TBox"],
        "abox": ["ABox"],
        "rulebox": ["RuleBox", "RuleBoxGovernance"],
        "inferencebox": ["InferenceBox"],
        "evidence": ["ABox", "InferenceBox"],
        "sync": [],
    }
    read_boxes = sorted({
        box
        for section_id in section_ids
        for box in section_box_map.get(section_id, ONTOLOGY_AUDIT_BOXES)
    })
    graph_entities: List[Dict[str, object]] = []
    graph_relations: List[Dict[str, object]] = []
    graph_error = ""
    if fast_compact_summary:
        graph_error = ""
    elif read_boxes and hasattr(repo, "read_entity_rows") and hasattr(repo, "read_relation_rows"):
        try:
            if compact_all:
                sample_limit = max(5, min(80, limit))
                for box in read_boxes:
                    graph_entities.extend([
                        ontology_audit_row_payload(row, "entity")
                        for row in ontology_repository_world_call(
                            repo,
                            "read_entity_rows",
                            [box],
                            sample_limit,
                            world_id=world_id,
                        )
                    ])
                    graph_relations.extend([
                        ontology_audit_row_payload(row, "relation")
                        for row in ontology_repository_world_call(
                            repo,
                            "read_relation_rows",
                            [box],
                            sample_limit,
                            world_id=world_id,
                        )
                    ])
            else:
                graph_entities = [
                    ontology_audit_row_payload(row, "entity")
                    for row in ontology_repository_world_call(
                        repo,
                        "read_entity_rows",
                        read_boxes,
                        world_id=world_id,
                    )
                ]
                graph_relations = [
                    ontology_audit_row_payload(row, "relation")
                    for row in ontology_repository_world_call(
                        repo,
                        "read_relation_rows",
                        read_boxes,
                        world_id=world_id,
                    )
                ]
        except Exception as error:  # noqa: BLE001 - admin audit must degrade gracefully.
            graph_error = str(error)[:240]
    elif read_boxes:
        graph_error = "TypeDB row reader is not available for this graph store."

    graph_rows = graph_entities + graph_relations
    by_box = {}
    for row in graph_rows:
        by_box.setdefault(str(row.get("box") or ""), []).append(row)

    tbox_metadata: Dict[str, object] = {}
    rulebox: Dict[str, object] = {}
    inferencebox: Dict[str, object] = {}
    diagnostics: Dict[str, object] = {}
    try:
        tbox_metadata = (
            {"status": "sampled", "source": "audit-sample", "configured": bool(getattr(repo, "address", ""))}
            if compact_all
            else repo.active_tbox_metadata()
            if ("tbox" in section_ids or "sync" in section_ids) and hasattr(repo, "active_tbox_metadata")
            else {}
        )
    except Exception as error:  # noqa: BLE001
        tbox_metadata = {"status": "error", "reason": str(error)[:220]}
    try:
        rulebox = (
            {"status": "sampled", "source": "audit-sample", "rules": []}
            if compact_all
            else repo.rulebox_snapshot()
            if ("rulebox" in section_ids or "sync" in section_ids) and hasattr(repo, "rulebox_snapshot")
            else {}
        )
    except Exception as error:  # noqa: BLE001
        rulebox = {"status": "error", "reason": str(error)[:220], "rules": []}
    try:
        inferencebox = (
            {"status": "sampled", "source": "audit-sample", "entities": [], "relations": [], "traces": []}
            if compact_all
            else ontology_repository_world_call(
                repo,
                "inferencebox_snapshot",
                symbols=symbols,
                limit=min(300, max(80, limit)),
                world_id=world_id,
            )
            if ("inferencebox" in section_ids or "sync" in section_ids) and hasattr(repo, "inferencebox_snapshot")
            else {}
        )
    except Exception as error:  # noqa: BLE001
        inferencebox = {"status": "error", "reason": str(error)[:220], "entities": [], "relations": [], "traces": []}
    try:
        diagnostics = (
            {"status": "sampled", "reason": "기본 감사 화면은 빠른 샘플만 읽고, 상세 진단은 /api/ontology/audit/sync에서 실행합니다."}
            if compact_all and "sync" in section_ids
            else OntologyDiagnosticsService(
                ontology_repository=repo,
                settings=settings,
                event_log=stores.event_log(settings),
                notification_queue=stores.notification_job_store(settings),
                strategy_proposal_service=build_investment_strategy_proposal_service(settings),
                decision_episode_store=stores.investment_decision_episode_store(settings),
                projection_run_store=stores.ontology_projection_run_store(settings),
                world_projection_outbox=stores.ontology_world_projection_outbox_store(settings),
                inference_detail_outbox=stores.ontology_inference_detail_outbox_store(settings),
                maintenance_state_store=stores.ontology_maintenance_state_store(settings),
                runtime_identity_provider=runtime_identity,
                alert_coverage_provider=lambda: stores.investment_alert_coverage_store(settings).summary(),
            ).status(
                symbols=symbols,
                limit=min(300, max(80, limit)),
                world_id=world_id,
            ) if "sync" in section_ids else {}
        )
    except Exception as error:  # noqa: BLE001
        diagnostics = {"status": "error", "reason": str(error)[:220]}

    inference_rows = by_box.get("InferenceBox", [])
    if not inference_rows:
        inference_rows = [
            ontology_audit_row_payload({**row, "ontologyBox": "InferenceBox"}, "entity")
            for row in (inferencebox.get("entities") or [])
            if isinstance(row, dict)
        ] + [
            ontology_audit_row_payload({**row, "ontologyBox": "InferenceBox"}, "relation")
            for row in (inferencebox.get("relations") or [])
            if isinstance(row, dict)
        ] + [
            ontology_audit_row_payload({**row, "ontologyBox": "InferenceBox", "kind": row.get("kind") or "inference-trace"}, "trace")
            for row in (inferencebox.get("traces") or [])
            if isinstance(row, dict)
        ]

    evidence_rows = [
        row for row in graph_rows
        if str(row.get("kind") or "").lower() in ONTOLOGY_AUDIT_EVIDENCE_KINDS
        or any(token in ontology_audit_row_text(row) for token in ["evidence", "belief", "opinion", "executionplan", "reasoningcard"])
    ]

    sections = {}
    if "tbox" in section_ids:
        sections["tbox"] = ontology_audit_section_payload("tbox", by_box.get("TBox", []), limit, offset, search, symbols)
    if "abox" in section_ids:
        sections["abox"] = ontology_audit_section_payload("abox", by_box.get("ABox", []), limit, offset, search, symbols)
    if "rulebox" in section_ids:
        sections["rulebox"] = ontology_audit_section_payload(
            "rulebox",
            ontology_audit_rulebox_rows(rulebox, by_box.get("RuleBox", []) + by_box.get("RuleBoxGovernance", [])),
            limit,
            offset,
            search,
            symbols,
        )
    if "inferencebox" in section_ids:
        sections["inferencebox"] = ontology_audit_section_payload("inferencebox", inference_rows, limit, offset, search, symbols)
    if "evidence" in section_ids:
        sections["evidence"] = ontology_audit_section_payload("evidence", evidence_rows, limit, offset, search, symbols)
    if "sync" in section_ids:
        sections["sync"] = ontology_audit_section_payload(
            "sync",
            ontology_audit_sync_rows(
                repo,
                tbox_metadata,
                rulebox,
                inferencebox,
                diagnostics,
                world_id=world_id,
                include_generation_records=not fast_compact_summary,
            ),
            limit,
            offset,
            search,
            symbols,
        )

    totals = {key: value.get("total", 0) for key, value in sections.items()}
    status = "error" if graph_error else ("sampled" if fast_compact_summary else "ok")
    if not getattr(repo, "address", "") and all((sections.get(key) or {}).get("total", 0) == 0 for key in ["tbox", "abox", "inferencebox"] if key in sections):
        status = "disabled"
    return {
        "generatedAt": now(),
        "status": status,
        "graphStore": getattr(repo, "store_key", "typedb"),
        "storeLabel": getattr(repo, "store_label", "TypeDB"),
        "worldId": world_id,
        "configured": bool(getattr(repo, "address", "") or rulebox.get("configured") or tbox_metadata.get("configured")),
        "error": graph_error,
        "query": {
            "limit": limit,
            "offset": offset,
            "q": search,
            "symbols": symbols,
            "worldId": world_id,
            "section": section_filter or "all",
        },
        "summaryMode": "sampled" if fast_compact_summary else "full",
        "summary": {
            "sectionTotals": totals if not fast_compact_summary else {},
            "graphRowCount": len(graph_rows) if not fast_compact_summary else None,
            "entityCount": len(graph_entities) if not fast_compact_summary else None,
            "relationCount": len(graph_relations) if not fast_compact_summary else None,
            "ruleCount": rulebox.get("ruleCount") or len(rulebox.get("rules") or []) or (sections.get("rulebox") or {}).get("total", 0),
            "inferenceRelationCount": inferencebox.get("relationCount") or (sections.get("inferencebox") or {}).get("relationCount", 0),
            "inferenceTraceCount": inferencebox.get("traceCount") or 0,
            "diagnosticsStatus": diagnostics.get("status") or diagnostics.get("readiness") or "",
            "tboxStatus": tbox_metadata.get("status") or "",
            "ruleboxStatus": rulebox.get("status") or "",
            "inferenceboxStatus": inferencebox.get("status") or "",
        },
        "sections": sections,
        "tbox": tbox_metadata,
        "rulebox": {
            "status": rulebox.get("status"),
            "ruleCount": rulebox.get("ruleCount"),
            "conditionCount": rulebox.get("conditionCount"),
            "derivationCount": rulebox.get("derivationCount"),
            "defaultsFallbackUsed": rulebox.get("defaultsFallbackUsed"),
            "versionCount": rulebox.get("versionCount"),
            "source": rulebox.get("source"),
        },
        "inferencebox": {
            "status": inferencebox.get("status"),
            "relationCount": inferencebox.get("relationCount"),
            "traceCount": inferencebox.get("traceCount"),
            "reasoningMode": inferencebox.get("reasoningMode"),
            "source": inferencebox.get("source"),
        },
        "diagnostics": diagnostics,
    }
