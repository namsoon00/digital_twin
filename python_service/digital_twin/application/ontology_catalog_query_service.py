"""Read-only catalog joining the ontology schema and its runtime lineage.

The catalog does not reason about investments.  It exposes the canonical TBox,
the actually deployed TypeDB RuleBox/InferenceBox, and durable hypothesis and
decision audit records through stable identifiers.
"""

from collections import Counter
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from ..domain.ontology_rule_manifest import rule_domain_manifest
from ..domain.ontology_schema import ontology_tbox
from ..domain.ontology_rule_knowledge import knowledge_basis_summary, resolved_rule_knowledge_basis, rule_knowledge_basis_from_rows
from ..domain.statistical_signals.registry import model_registry_payload


ONTOLOGY_CATALOG_VERSION = "ontology-catalog-v1"
CATALOG_SECTIONS = {"classes", "relations", "rules", "hypotheses", "inferences"}
AVAILABLE_STATUSES = {"ok", "ready", "active", "empty"}


def text(value: object) -> str:
    return str(value or "").strip()


def lower(value: object) -> str:
    return text(value).lower()


def item_dict(value: object) -> Dict[str, object]:
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        return dict(payload or {}) if isinstance(payload, Mapping) else {}
    return dict(value or {}) if isinstance(value, Mapping) else {}


def list_values(value: object) -> List[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    result: List[str] = []
    for raw in values:
        resolved = text(raw)
        if resolved and resolved not in result:
            result.append(resolved)
    return result


def safe_limit(value: object, default: int = 40, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def cursor_offset(value: object) -> int:
    raw = text(value)
    if raw.startswith("offset:"):
        raw = raw.split(":", 1)[1]
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def page_payload(items: List[Dict[str, object]], offset: int, limit: int, total: Optional[int] = None) -> Dict[str, object]:
    resolved_total = len(items) if total is None else max(0, int(total))
    next_offset = offset + len(items)
    has_more = next_offset < resolved_total
    return {
        "limit": limit,
        "offset": offset,
        "cursor": "offset:" + str(offset),
        "previousCursor": "offset:" + str(max(0, offset - limit)) if offset > 0 else "",
        "nextCursor": "offset:" + str(next_offset) if has_more else "",
        "hasMore": has_more,
        "total": resolved_total,
    }


def searchable_text(row: Mapping[str, object]) -> str:
    values: List[str] = []
    for key, value in row.items():
        if isinstance(value, (str, int, float, bool)):
            values.append(str(value))
        elif isinstance(value, (list, tuple, set)):
            values.extend(str(item) for item in value if isinstance(item, (str, int, float, bool)))
    return " ".join(values).lower()


class OntologyCatalogQueryService:
    """Compose ontology management read models without changing graph state."""

    def __init__(
        self,
        ontology_repository=None,
        hypothesis_lifecycle_store=None,
        decision_episode_store=None,
        notification_job_store=None,
        statistical_signal_store=None,
        tbox_provider: Callable[[], Dict[str, object]] = ontology_tbox,
        rulebox_provider: Callable[[], Dict[str, object]] = None,
    ):
        self.ontology_repository = ontology_repository
        self.hypothesis_lifecycle_store = hypothesis_lifecycle_store
        self.decision_episode_store = decision_episode_store
        self.notification_job_store = notification_job_store
        self.statistical_signal_store = statistical_signal_store
        self.tbox_provider = tbox_provider
        self.rulebox_provider = rulebox_provider

    def source_tbox(self) -> Dict[str, object]:
        payload = self.tbox_provider()
        if not isinstance(payload, Mapping):
            raise ValueError("canonical TBox provider returned an invalid payload")
        return dict(payload)

    def class_rows(self) -> List[Dict[str, object]]:
        rows = []
        for raw in self.source_tbox().get("classDefinitions") or []:
            row = item_dict(raw)
            class_id = text(row.get("name"))
            if not class_id:
                continue
            rows.append({
                "id": class_id,
                "type": "class",
                "name": class_id,
                "label": text(row.get("label")) or class_id,
                "boundedContext": text(row.get("bounded_context") or row.get("boundedContext")),
                "parent": text(row.get("parent")),
                "description": text(row.get("description")),
                "materializationPolicy": text(row.get("materializationPolicy")),
                "materializationBox": text(row.get("materializationBox")) or "TBox",
            })
        return sorted(rows, key=lambda item: (lower(item.get("boundedContext")), lower(item.get("name"))))

    def relation_rows(self) -> List[Dict[str, object]]:
        rows = []
        for raw in self.source_tbox().get("relationDefinitions") or []:
            row = item_dict(raw)
            relation_id = text(row.get("name"))
            if not relation_id:
                continue
            rows.append({
                "id": relation_id,
                "type": "relation",
                "name": relation_id,
                "label": relation_id,
                "boundedContext": text(row.get("bounded_context") or row.get("boundedContext")),
                "sourceContext": text(row.get("source_context") or row.get("sourceContext")),
                "targetContext": text(row.get("target_context") or row.get("targetContext")),
                "description": text(row.get("description")),
                "materializationPolicy": text(row.get("materializationPolicy")),
                "materializationBox": text(row.get("materializationBox")) or "TBox",
            })
        return sorted(rows, key=lambda item: (lower(item.get("boundedContext")), lower(item.get("name"))))

    def tbox_deployment(self) -> Dict[str, object]:
        source = self.source_tbox()
        repository = self.ontology_repository
        if not repository or not callable(getattr(repository, "active_tbox_metadata", None)):
            return {
                "status": "unavailable",
                "alignment": "unavailable",
                "reason": "TypeDB TBox metadata reader is unavailable.",
                "sourceVersion": text(source.get("version")),
                "sourceFingerprint": text(source.get("fingerprint")),
            }
        try:
            deployed = item_dict(repository.active_tbox_metadata())
        except Exception as error:  # noqa: BLE001 - a read failure is catalog data.
            return {
                "status": "error",
                "alignment": "unavailable",
                "reason": str(error)[:220],
                "sourceVersion": text(source.get("version")),
                "sourceFingerprint": text(source.get("fingerprint")),
            }
        status = lower(deployed.get("status")) or "unknown"
        configured = bool(deployed.get("configured", status not in {"disabled", "code-fallback", "not-configured"}))
        deployed_version = text(deployed.get("version") or deployed.get("tboxVersion"))
        deployed_fingerprint = text(deployed.get("fingerprint") or deployed.get("tboxFingerprint"))
        available = configured and status not in {"disabled", "code-fallback", "not-configured", "error", "unavailable"}
        aligned = bool(
            available
            and deployed_version == text(source.get("version"))
            and deployed_fingerprint == text(source.get("fingerprint"))
        )
        return {
            "status": status if available else "unavailable",
            "alignment": "aligned" if aligned else ("drift" if available else "unavailable"),
            "configured": configured,
            "source": text(deployed.get("source")),
            "sourceVersion": text(source.get("version")),
            "sourceFingerprint": text(source.get("fingerprint")),
            "deployedVersion": deployed_version,
            "deployedFingerprint": deployed_fingerprint,
            "reason": text(deployed.get("reason")),
        }

    def rulebox(self) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
        repository = self.ontology_repository
        reader = self.rulebox_provider or (getattr(repository, "rulebox_snapshot", None) if repository else None)
        if not callable(reader):
            return ({"status": "unavailable", "reason": "TypeDB RuleBox reader is unavailable."}, [])
        try:
            snapshot = item_dict(reader())
        except Exception as error:  # noqa: BLE001 - expose the unavailable runtime source.
            return ({"status": "error", "reason": str(error)[:220]}, [])
        status = lower(snapshot.get("status")) or "unknown"
        configured = bool(snapshot.get("configured", True))
        fallback_used = bool(snapshot.get("defaultsFallbackUsed"))
        available = configured and status in AVAILABLE_STATUSES and not fallback_used
        metadata = {
            "status": status if available else "unavailable",
            "configured": configured,
            "source": text(snapshot.get("source")),
            "snapshotId": text(snapshot.get("ruleboxSnapshotId")),
            "fingerprint": text(snapshot.get("ruleboxRulesHash") or snapshot.get("ruleboxShortHash")),
            "fallbackBlocked": fallback_used,
            "reason": text(snapshot.get("reason")) or (
                "TypeDB가 비활성이라 제공된 기본 규칙은 실행 규칙으로 표시하지 않습니다."
                if fallback_used else ""
            ),
        }
        if not available:
            return metadata, []
        rows = [self.normalize_rule(raw) for raw in snapshot.get("rules") or [] if isinstance(raw, Mapping)]
        rows = [row for row in rows if row.get("id")]
        rows.sort(key=lambda item: (not bool(item.get("enabled")), lower(item.get("label")), lower(item.get("id"))))
        metadata["count"] = len(rows)
        return metadata, rows

    def normalize_rule(self, raw: Mapping[str, object]) -> Dict[str, object]:
        conditions = [item_dict(item) for item in raw.get("conditions") or [] if isinstance(item, Mapping)]
        derivations = [item_dict(item) for item in raw.get("derivations") or [] if isinstance(item, Mapping)]
        input_relations = list_values([
            item.get("relation_type") or item.get("relationType")
            for item in conditions
            if text(item.get("relation_type") or item.get("relationType"))
        ])
        output_relations = list_values([
            item.get("relation_type") or item.get("relationType")
            for item in derivations
            if text(item.get("relation_type") or item.get("relationType"))
        ])
        tbox_classes = list_values([
            value
            for item in derivations
            for value in ([item.get("tbox_class") or item.get("tboxClass")] + list(item.get("tbox_classes") or item.get("tboxClasses") or []))
            if text(value)
        ])
        decision_stages = list_values([
            item.get("decision_stage") or item.get("decisionStage")
            for item in derivations
            if text(item.get("decision_stage") or item.get("decisionStage"))
        ])
        rule_id = text(raw.get("rule_id") or raw.get("ruleId") or raw.get("id"))
        manifest = item_dict(raw.get("domain_manifest") or raw.get("domainManifest"))
        if not manifest:
            manifest = rule_domain_manifest(raw)
        knowledge_basis = resolved_rule_knowledge_basis(raw).to_dict()
        return {
            "id": rule_id,
            "type": "rule",
            "ruleId": rule_id,
            "label": text(raw.get("label")) or rule_id,
            "version": text(raw.get("version")),
            "enabled": bool(raw.get("enabled", True)),
            "sourceKind": text(raw.get("source_kind") or raw.get("sourceKind")),
            "hypothesisFamilyKey": text(raw.get("hypothesis_family_key") or raw.get("hypothesisFamilyKey")),
            "conditionCount": len(conditions),
            "derivationCount": len(derivations),
            "inputRelationTypes": input_relations,
            "outputRelationTypes": output_relations,
            "relationTypes": list_values(input_relations + output_relations),
            "tboxClasses": tbox_classes,
            "decisionStages": decision_stages,
            "actionGroup": text(raw.get("action_group") or raw.get("actionGroup")),
            "actionLevel": text(raw.get("action_level") or raw.get("actionLevel")),
            "promptHint": text(raw.get("prompt_hint") or raw.get("promptHint")),
            "domainManifest": manifest,
            "assessmentScope": text(manifest.get("assessmentScope")),
            "triggerFamilies": list_values(manifest.get("triggerFamilies")),
            "lifecycleClass": text(manifest.get("lifecycleClass")),
            "knowledgeBasis": knowledge_basis,
            "ruleKind": text(knowledge_basis.get("ruleKind")),
            "owner": text(knowledge_basis.get("owner")),
            "inputContract": text(knowledge_basis.get("inputContract")),
            "outputContractOwner": text(knowledge_basis.get("outputContract")),
            "decisionAuthority": text(knowledge_basis.get("decisionAuthority")),
            "migrationDisposition": text(knowledge_basis.get("migrationDisposition")),
            "ownershipContractVersion": text(knowledge_basis.get("ownershipContractVersion")),
            "theoryFamily": text(knowledge_basis.get("theoryFamily")),
            "thesisFamily": text(knowledge_basis.get("thesisFamily")),
            "knowledgeValidationStatus": text(knowledge_basis.get("validationStatus")),
            "decisionEligibility": text(knowledge_basis.get("decisionEligibility")),
            "requiresHypothesis": bool(knowledge_basis.get("requiresHypothesis")),
            "statisticalSignalContract": item_dict(manifest.get("statisticalSignalContract")),
            "conditions": conditions,
            "derivations": derivations,
        }

    def inference_recovery(self, world_id: str) -> Dict[str, object]:
        if not world_id:
            return {"status": "world-required", "reason": "계정 또는 PortfolioWorld를 선택해야 추론 세대를 조회할 수 있습니다."}
        repository = self.ontology_repository
        reader = getattr(repository, "inferencebox_recovery_metadata", None) if repository else None
        if not callable(reader):
            return {"status": "unavailable", "reason": "TypeDB InferenceBox 상태 reader가 없습니다."}
        try:
            payload = item_dict(reader(world_id))
        except TypeError:
            payload = item_dict(reader(world_id=world_id))
        except Exception as error:  # noqa: BLE001
            return {"status": "error", "reason": str(error)[:220], "worldId": world_id}
        return {
            "status": text(payload.get("status")) or "unknown",
            "configured": bool(payload.get("configured", True)),
            "graphStore": text(payload.get("graphStore")) or "typedb",
            "worldId": text(payload.get("worldId")) or world_id,
            "inferenceGenerationId": text(payload.get("inferenceGenerationId")),
            "sourceAboxSnapshotId": text(payload.get("sourceAboxSnapshotId")),
            "reasoningMode": text(payload.get("reasoningMode")),
            "nativeTypeDbReasoningCompleted": bool(payload.get("nativeTypeDbReasoningCompleted")),
            "nativeInferenceOutcome": text(payload.get("nativeInferenceOutcome")),
            "executedRuleCount": int(payload.get("typedbNativeRuleExecutedCount") or 0),
            "matchedRuleCount": int(payload.get("typedbNativeRuleMatchedCount") or 0),
            "targetSymbols": list_values(payload.get("targetSymbols")),
            "reason": text(payload.get("reason")),
        }

    def inferencebox(self, world_id: str, symbols: Iterable[str] = None, limit: int = 500) -> Dict[str, object]:
        if not world_id:
            return {
                "status": "world-required",
                "reason": "계정 또는 PortfolioWorld를 선택해야 추론 결과를 조회할 수 있습니다.",
                "entities": [],
                "relations": [],
                "traces": [],
            }
        repository = self.ontology_repository
        reader = getattr(repository, "inferencebox_snapshot", None) if repository else None
        if not callable(reader):
            return {"status": "unavailable", "reason": "TypeDB InferenceBox reader가 없습니다.", "entities": [], "relations": [], "traces": []}
        clean_symbols = list_values([text(item).upper() for item in symbols or []])
        try:
            return item_dict(reader(symbols=clean_symbols, limit=min(500, max(1, limit)), world_id=world_id))
        except TypeError as error:
            if "world_id" not in str(error) and "unexpected keyword" not in str(error):
                raise
            return item_dict(reader(symbols=clean_symbols, limit=min(500, max(1, limit))))
        except Exception as error:  # noqa: BLE001
            return {
                "status": "error",
                "reason": str(error)[:220],
                "worldId": world_id,
                "entities": [],
                "relations": [],
                "traces": [],
            }

    def hypothesis_count(self, **filters) -> Dict[str, object]:
        store = self.hypothesis_lifecycle_store
        if not store:
            return {"status": "unavailable", "count": 0, "complete": False, "reason": "가설 수명주기 저장소가 없습니다."}
        counter = getattr(store, "count_current", None)
        try:
            if callable(counter):
                return {"status": "ok", "count": int(counter(**filters) or 0), "complete": True}
            reader = getattr(store, "list_current_summary", None) or getattr(store, "list_current", None)
            rows = reader(limit=1000, **filters) if callable(reader) else []
            return {"status": "ok", "count": len(rows), "complete": len(rows) < 1000}
        except Exception as error:  # noqa: BLE001
            return {"status": "error", "count": 0, "complete": False, "reason": str(error)[:220]}

    def decision_performance_summary(self) -> Dict[str, object]:
        store = self.decision_episode_store
        reader = getattr(store, "performance", None) if store else None
        if not callable(reader):
            return {
                "status": "unavailable",
                "reason": "판단 사후 성과 저장소가 이 조회에 연결되지 않았습니다.",
            }
        try:
            payload = item_dict(reader(limit=500))
        except Exception as error:  # noqa: BLE001 - catalog remains readable without outcomes.
            return {"status": "error", "reason": str(error)[:220]}
        summary = item_dict(payload.get("summary"))
        governance = item_dict(payload.get("governance"))
        return {
            "status": text(payload.get("status")) or "insufficient-data",
            "episodeCount": int(payload.get("episodeCount") or 0),
            "episodeWithOutcomeCount": int(payload.get("episodeWithOutcomeCount") or 0),
            "calibrationEligibleEpisodeCount": int(payload.get("calibrationEligibleEpisodeCount") or 0),
            "outcomeCoveragePct": float(payload.get("outcomeCoveragePct") or 0),
            "calibrationCoveragePct": float(payload.get("calibrationCoveragePct") or 0),
            "summary": summary,
            "byAction": [item_dict(item) for item in payload.get("byAction") or [] if isinstance(item, Mapping)],
            "governance": governance,
        }

    def summary(self, world_id: str = "", account_id: str = "") -> Dict[str, object]:
        tbox = self.source_tbox()
        classes = self.class_rows()
        relations = self.relation_rows()
        contexts = [item_dict(item) for item in tbox.get("boundedContexts") or []]
        context_ids = {text(item.get("key")) for item in contexts if text(item.get("key"))}
        class_ids = {text(item.get("id")) for item in classes}
        invalid_classes = [item for item in classes if item.get("boundedContext") not in context_ids or (item.get("parent") and item.get("parent") not in class_ids)]
        invalid_relations = [
            item for item in relations
            if item.get("boundedContext") not in context_ids
            or item.get("sourceContext") not in context_ids
            or item.get("targetContext") not in context_ids
        ]
        deployment = self.tbox_deployment()
        rulebox_meta, rules = self.rulebox()
        rule_knowledge_summary = knowledge_basis_summary([
            resolved_rule_knowledge_basis(item)
            for item in rules
        ])
        signal_migration_counts = Counter(
            text((rule.get("statisticalSignalContract") or {}).get("migrationState")) or "missing"
            for rule in rules
        )
        try:
            statistical_signal_status = (
                item_dict(self.statistical_signal_store.status())
                if self.statistical_signal_store
                else {
                    "status": "not-configured",
                    "reason": "이 조회 경로에는 통계 신호 저장소 진단이 연결되지 않았습니다.",
                }
            )
        except Exception as error:  # noqa: BLE001 - catalog health must remain readable.
            statistical_signal_status = {"status": "error", "reason": str(error)[:220]}
        relation_ids = {text(item.get("id")) for item in relations}
        undefined_rule_relations = sorted({
            relation_type
            for rule in rules
            for relation_type in rule.get("relationTypes") or []
            if relation_type not in relation_ids
        })
        undefined_rule_classes = sorted({
            class_id
            for rule in rules
            for class_id in rule.get("tboxClasses") or []
            if class_id not in class_ids
        })
        hypotheses = self.hypothesis_count(account_id=account_id)
        inference = self.inference_recovery(world_id)
        decision_performance = self.decision_performance_summary()
        diagnostics = [
            {
                "id": "tbox.deployment",
                "status": "ok" if deployment.get("alignment") == "aligned" else ("warning" if deployment.get("alignment") == "drift" else "unavailable"),
                "label": "소스 TBox와 TypeDB 배포본",
                "count": 0 if deployment.get("alignment") == "aligned" else 1,
                "detail": "버전과 지문이 일치합니다." if deployment.get("alignment") == "aligned" else (deployment.get("reason") or "배포본 일치 여부를 확인할 수 없습니다."),
            },
            {
                "id": "tbox.contracts",
                "status": "ok" if not invalid_classes and not invalid_relations else "warning",
                "label": "TBox 문맥·상속 계약",
                "count": len(invalid_classes) + len(invalid_relations),
                "detail": "누락된 경계 문맥과 부모 개념이 없습니다." if not invalid_classes and not invalid_relations else "문맥 또는 부모 개념을 찾지 못한 정의가 있습니다.",
            },
            {
                "id": "rulebox.references",
                "status": "unavailable" if rulebox_meta.get("status") == "unavailable" else ("ok" if not undefined_rule_relations and not undefined_rule_classes else "warning"),
                "label": "실행 규칙의 TBox 참조",
                "count": len(undefined_rule_relations) + len(undefined_rule_classes),
                "detail": rulebox_meta.get("reason") if rulebox_meta.get("status") == "unavailable" else ("모든 관계·개념 참조가 TBox에 있습니다." if not undefined_rule_relations and not undefined_rule_classes else "TBox에 없는 관계 또는 개념을 참조하는 실행 규칙이 있습니다."),
                "undefinedRelationTypes": undefined_rule_relations,
                "undefinedClasses": undefined_rule_classes,
            },
            {
                "id": "inferencebox.availability",
                "status": "ok" if lower(inference.get("status")) in AVAILABLE_STATUSES else "unavailable",
                "label": "현재 PortfolioWorld 추론 세대",
                "count": 0,
                "detail": text(inference.get("reason")) or ("현재 추론 세대 표식을 확인했습니다." if lower(inference.get("status")) in AVAILABLE_STATUSES else "현재 추론 세대를 확인할 수 없습니다."),
            },
            {
                "id": "statistical-signals.availability",
                "status": (
                    "ok"
                    if lower(statistical_signal_status.get("status")) in {"ready", "not-configured"}
                    else "unavailable"
                ),
                "label": "통계 신호 최신 상태 저장소",
                "count": int(statistical_signal_status.get("headCount") or 0),
                "detail": text(statistical_signal_status.get("reason")) or (
                    "종목·신호별 최신 헤드가 저장되고 있습니다."
                    if lower(statistical_signal_status.get("status")) == "ready"
                    else "통계 신호 저장 상태를 확인할 수 없습니다."
                ),
            },
        ]
        return {
            "version": ONTOLOGY_CATALOG_VERSION,
            "status": "ok",
            "readOnly": True,
            "worldId": world_id,
            "accountId": account_id,
            "sourceTBox": {
                "status": "ok",
                "version": text(tbox.get("version")),
                "domainModelVersion": text(tbox.get("domainModelVersion")),
                "fingerprint": text(tbox.get("fingerprint")),
            },
            "deployedTBox": deployment,
            "rulebox": rulebox_meta,
            "ruleKnowledge": rule_knowledge_summary,
            "statisticalSignals": {
                "registry": model_registry_payload(),
                "store": statistical_signal_status,
                "migrationCounts": dict(sorted(signal_migration_counts.items())),
            },
            "decisionPerformance": decision_performance,
            "hypotheses": hypotheses,
            "inferencebox": inference,
            "counts": {
                "boundedContexts": len(contexts),
                "classes": len(classes),
                "relations": len(relations),
                "tboxReasoningRules": len(tbox.get("reasoningRuleDefinitions") or []),
                "executableRules": len(rules),
                "hypotheses": int(hypotheses.get("count") or 0),
                "inferences": int(inference.get("traceCount") or inference.get("nativeTraceCount") or 0),
            },
            "boundedContexts": contexts,
            "diagnostics": diagnostics,
            "lineage": [
                {"type": "class", "label": "TBox 개념"},
                {"type": "relation", "label": "TBox 관계"},
                {"type": "rule", "label": "TypeDB 실행 규칙"},
                {"type": "hypothesis", "label": "가설"},
                {"type": "inference", "label": "InferenceBox 추론"},
                {"type": "decision", "label": "투자 판단"},
                {"type": "notification", "label": "알림"},
            ],
        }

    def list_section(
        self,
        section: str,
        query: str = "",
        cursor: str = "",
        limit: int = 40,
        bounded_context: str = "",
        enabled: str = "",
        scope: str = "",
        state: str = "",
        symbol: str = "",
        account_id: str = "",
        market_id: str = "",
        world_id: str = "",
        rule_kind: str = "",
        theory_family: str = "",
        validation_status: str = "",
    ) -> Dict[str, object]:
        section_id = lower(section)
        if section_id not in CATALOG_SECTIONS:
            return {"version": ONTOLOGY_CATALOG_VERSION, "status": "invalid-section", "section": section_id, "items": []}
        page_limit = safe_limit(limit)
        offset = cursor_offset(cursor)
        if section_id == "classes":
            return self._paged_static(section_id, self.class_rows(), query, offset, page_limit, bounded_context=bounded_context)
        if section_id == "relations":
            return self._paged_static(section_id, self.relation_rows(), query, offset, page_limit, bounded_context=bounded_context)
        if section_id == "rules":
            metadata, rows = self.rulebox()
            if enabled in {"true", "false"}:
                expected = enabled == "true"
                rows = [row for row in rows if bool(row.get("enabled")) is expected]
            if rule_kind:
                rows = [row for row in rows if text(row.get("ruleKind")) == rule_kind]
            if theory_family:
                rows = [row for row in rows if text(row.get("theoryFamily")) == theory_family]
            if validation_status:
                rows = [row for row in rows if text(row.get("knowledgeValidationStatus")) == validation_status]
            payload = self._paged_static(section_id, rows, query, offset, page_limit)
            payload["items"] = [self.rule_list_item(row) for row in payload.get("items") or []]
            payload.update({"status": metadata.get("status"), "source": metadata})
            return payload
        if section_id == "hypotheses":
            return self._hypothesis_page(query, offset, page_limit, account_id, symbol, market_id, scope, state)
        return self._inference_page(query, offset, page_limit, world_id, symbol)

    @staticmethod
    def rule_list_item(row: Mapping[str, object]) -> Dict[str, object]:
        """Keep only scan fields in the rule list; full governance stays in lineage detail."""

        knowledge = item_dict(row.get("knowledgeBasis"))
        statistical = item_dict(row.get("statisticalSignalContract"))
        return {
            key: row.get(key)
            for key in [
                "id", "type", "ruleId", "label", "version", "enabled", "sourceKind",
                "hypothesisFamilyKey", "conditionCount", "derivationCount", "inputRelationTypes",
                "outputRelationTypes", "relationTypes", "tboxClasses", "decisionStages", "actionGroup",
                "actionLevel", "assessmentScope", "triggerFamilies", "lifecycleClass", "ruleKind",
                "owner", "theoryFamily", "thesisFamily", "knowledgeValidationStatus",
                "decisionEligibility", "requiresHypothesis",
            ]
            if key in row
        } | {
            "knowledgeBasis": {
                key: knowledge.get(key)
                for key in [
                    "ruleKind", "owner", "theoryFamily", "thesisFamily", "validationStatus",
                    "decisionEligibility", "requiresHypothesis",
                ]
                if key in knowledge
            },
            "statisticalSignalContract": {
                key: statistical.get(key)
                for key in ["version", "migrationState", "productionEligible"]
                if key in statistical
            },
            "detailRequired": True,
        }

    def _paged_static(self, section: str, rows: List[Dict[str, object]], query: str, offset: int, limit: int, bounded_context: str = "") -> Dict[str, object]:
        filtered = rows
        if bounded_context:
            filtered = [row for row in filtered if text(row.get("boundedContext")) == bounded_context]
        if query:
            needle = lower(query)
            filtered = [row for row in filtered if needle in searchable_text(row)]
        page = filtered[offset:offset + limit]
        return {
            "version": ONTOLOGY_CATALOG_VERSION,
            "status": "ok",
            "section": section,
            "readOnly": True,
            "items": page,
            "page": page_payload(page, offset, limit, len(filtered)),
        }

    def _hypothesis_page(self, query: str, offset: int, limit: int, account_id: str, symbol: str, market_id: str, scope: str, state: str) -> Dict[str, object]:
        store = self.hypothesis_lifecycle_store
        if not store:
            return self._unavailable_section("hypotheses", "가설 수명주기 저장소가 없습니다.", offset, limit)
        reader = getattr(store, "list_current_summary", None) or getattr(store, "list_current", None)
        if not callable(reader):
            return self._unavailable_section("hypotheses", "가설 목록 reader가 없습니다.", offset, limit)
        filters = {"account_id": account_id, "symbol": symbol, "market_id": market_id, "scope": scope}
        try:
            rows = reader(limit=limit, offset=offset, search=query, state=state, **filters)
            counter = getattr(store, "count_current", None)
            total = int(counter(search=query, state=state, **filters) or 0) if callable(counter) else offset + len(rows)
        except TypeError:
            try:
                all_rows = reader(limit=1000, **filters)
            except Exception as error:  # noqa: BLE001
                return self._unavailable_section("hypotheses", str(error)[:220], offset, limit, status="error")
            normalized = [self.normalize_hypothesis(row) for row in all_rows]
            if state:
                normalized = [row for row in normalized if text(row.get("state")) == state]
            if query:
                normalized = [row for row in normalized if lower(query) in searchable_text(row)]
            total = len(normalized)
            rows = normalized[offset:offset + limit]
        except Exception as error:  # noqa: BLE001
            return self._unavailable_section("hypotheses", str(error)[:220], offset, limit, status="error")
        items = [self.normalize_hypothesis(row) for row in rows]
        return {
            "version": ONTOLOGY_CATALOG_VERSION,
            "status": "ok",
            "section": "hypotheses",
            "readOnly": True,
            "items": items,
            "page": page_payload(items, offset, limit, total),
        }

    def normalize_hypothesis(self, raw: object) -> Dict[str, object]:
        row = item_dict(raw)
        lifecycle_key = text(row.get("lifecycleKey") or row.get("lifecycle_key"))
        source_rule_ids = list_values(row.get("sourceRuleIds") or row.get("source_rule_ids"))
        knowledge_basis = rule_knowledge_basis_from_rows(source_rule_ids[0], [row]).to_dict() if source_rule_ids else {}
        return {
            "id": lifecycle_key,
            "type": "hypothesis",
            "lifecycleKey": lifecycle_key,
            "lifecycleId": text(row.get("lifecycleId") or row.get("lifecycle_id")),
            "scope": text(row.get("scope")),
            "accountId": text(row.get("accountId") or row.get("account_id")),
            "marketId": text(row.get("marketId") or row.get("market_id")),
            "symbol": text(row.get("symbol")).upper(),
            "familyId": text(row.get("familyId") or row.get("family_id")),
            "state": text(row.get("state")),
            "transitionReason": text(row.get("transitionReason") or row.get("transition_reason")),
            "materialChange": bool(row.get("materialChange") or row.get("material_change")),
            "firstObservedAt": text(row.get("firstObservedAt") or row.get("first_observed_at")),
            "lastObservedAt": text(row.get("lastObservedAt") or row.get("last_observed_at")),
            "inferenceGenerationId": text(row.get("inferenceGenerationId") or row.get("inference_generation_id")),
            "sourceRuleIds": source_rule_ids,
            "linkStatus": "linked" if source_rule_ids else "detail-required",
            "knowledgeBasis": knowledge_basis,
            "theoryFamily": text(knowledge_basis.get("theoryFamily")),
            "thesisFamily": text(knowledge_basis.get("thesisFamily")),
            "knowledgeValidationStatus": text(knowledge_basis.get("validationStatus")),
        }

    def _inference_page(self, query: str, offset: int, limit: int, world_id: str, symbol: str) -> Dict[str, object]:
        snapshot = self.inferencebox(world_id, [symbol] if symbol else [], 500)
        status = lower(snapshot.get("status"))
        if status not in AVAILABLE_STATUSES:
            return self._unavailable_section("inferences", text(snapshot.get("reason")), offset, limit, status=status or "unavailable", extra={"worldId": world_id})
        relations = [item_dict(item) for item in snapshot.get("relations") or []]
        rows = []
        missing_ids = 0
        for raw in snapshot.get("traces") or []:
            trace = item_dict(raw)
            trace_id = text(trace.get("id"))
            if not trace_id:
                missing_ids += 1
                continue
            linked_relations = [item for item in relations if text(item.get("inferenceTraceId")) == trace_id]
            rows.append({
                "id": trace_id,
                "type": "inference",
                "traceId": trace_id,
                "label": text(trace.get("label")) or trace_id,
                "symbol": text(trace.get("symbol")).upper(),
                "ruleId": text(trace.get("sourceRuleId") or trace.get("ruleId") or trace.get("semanticRuleId") or trace.get("nativeRuleId")),
                "inferenceGenerationId": text(snapshot.get("inferenceGenerationId")),
                "matchedConditionIds": list_values(trace.get("matchedConditionIds")),
                "matchedConditionCount": len(trace.get("matchedConditionIds") or []),
                "requiredConditionCount": int(trace.get("requiredConditionCount") or 0),
                "groundedConditionCount": int(trace.get("groundedConditionCount") or 0),
                "relationCount": len(linked_relations),
                "decisionStages": list_values([item.get("decisionStage") for item in linked_relations]),
                "validationState": text(trace.get("validationState")),
                "freshnessStatus": text(trace.get("freshnessStatus")),
                "knowledgeBasis": item_dict(trace.get("knowledgeBasis")),
                "ruleKind": text(trace.get("ruleKind")),
                "theoryFamily": text(trace.get("theoryFamily")),
                "thesisFamily": text(trace.get("thesisFamily")),
                "updatedAt": text(trace.get("updatedAt")),
            })
        if query:
            rows = [row for row in rows if lower(query) in searchable_text(row)]
        if symbol:
            rows = [row for row in rows if text(row.get("symbol")) == text(symbol).upper()]
        rows.sort(key=lambda item: (lower(item.get("symbol")), lower(item.get("ruleId")), lower(item.get("traceId"))))
        page = rows[offset:offset + limit]
        return {
            "version": ONTOLOGY_CATALOG_VERSION,
            "status": status,
            "section": "inferences",
            "readOnly": True,
            "worldId": world_id,
            "generationId": text(snapshot.get("inferenceGenerationId")),
            "items": page,
            "page": page_payload(page, offset, limit, len(rows)),
            "diagnostics": {
                "missingStableTraceIdCount": missing_ids,
                "nativeTypeDbReasoningUsed": bool(snapshot.get("nativeTypeDbReasoningUsed")),
                "generationAligned": bool(snapshot.get("generationAligned")),
            },
        }

    def _unavailable_section(self, section: str, reason: str, offset: int, limit: int, status: str = "unavailable", extra: Dict[str, object] = None) -> Dict[str, object]:
        return {
            "version": ONTOLOGY_CATALOG_VERSION,
            "status": status,
            "section": section,
            "readOnly": True,
            "reason": reason,
            "items": [],
            "page": page_payload([], offset, limit, 0),
            **dict(extra or {}),
        }

    def hypothesis_detail(self, lifecycle_key: str) -> Dict[str, object]:
        store = self.hypothesis_lifecycle_store
        if not store:
            return {}
        reader = getattr(store, "current_for_keys", None)
        try:
            if callable(reader):
                rows = reader([lifecycle_key]) or {}
                return item_dict(rows.get(lifecycle_key))
            fallback = getattr(store, "list_current", None)
            rows = fallback(limit=1000) if callable(fallback) else []
            for row in rows:
                payload = item_dict(row)
                if text(payload.get("lifecycleKey")) == lifecycle_key:
                    return payload
        except Exception:  # noqa: BLE001 - the lineage response reports the gap.
            return {}
        return {}

    def lineage(self, item_type: str, item_id: str, world_id: str = "", account_id: str = "", symbol: str = "") -> Dict[str, object]:
        selected_type = lower(item_type)
        selected_id = text(item_id)
        if selected_type not in {"class", "relation", "rule", "hypothesis", "inference"} or not selected_id:
            return {"version": ONTOLOGY_CATALOG_VERSION, "status": "invalid-selection", "selection": {"type": selected_type, "id": selected_id}}
        classes = self.class_rows()
        relations = self.relation_rows()
        rulebox_meta, rules = self.rulebox()
        result = {"classes": [], "relations": [], "rules": [], "hypotheses": [], "inferences": [], "decisions": [], "notifications": []}
        gaps: List[Dict[str, str]] = []
        selected: Dict[str, object] = {}
        rule_ids: List[str] = []
        generation_ids: List[str] = []
        lifecycle_ids: List[str] = []

        if selected_type == "class":
            selected = next((row for row in classes if row.get("id") == selected_id), {})
            result["classes"] = [row for row in classes if row.get("id") == selected_id or row.get("parent") == selected_id or selected.get("parent") == row.get("id")]
            contexts = {text(selected.get("boundedContext"))}
            result["relations"] = [row for row in relations if contexts & {text(row.get("boundedContext")), text(row.get("sourceContext")), text(row.get("targetContext"))}]
        elif selected_type == "relation":
            selected = next((row for row in relations if row.get("id") == selected_id), {})
            result["relations"] = [selected] if selected else []
            result["rules"] = [row for row in rules if selected_id in (row.get("relationTypes") or [])]
            rule_ids = [text(row.get("id")) for row in result["rules"]]
        elif selected_type == "rule":
            selected = next((row for row in rules if row.get("id") == selected_id), {})
            result["rules"] = [selected] if selected else []
            result["relations"] = [row for row in relations if row.get("id") in (selected.get("relationTypes") or [])]
            result["classes"] = [row for row in classes if row.get("id") in (selected.get("tboxClasses") or [])]
            rule_ids = [selected_id]
        elif selected_type == "hypothesis":
            detail = self.hypothesis_detail(selected_id)
            selected = self.normalize_hypothesis(detail) if detail else {}
            result["hypotheses"] = [selected] if selected else []
            rule_ids = list_values(detail.get("sourceRuleIds") if detail else [])
            generation_ids = list_values([detail.get("inferenceGenerationId") if detail else ""])
            lifecycle_ids = list_values([detail.get("lifecycleId") if detail else ""])
            result["rules"] = [row for row in rules if row.get("id") in rule_ids]
        else:
            snapshot = self.inferencebox(world_id, [symbol] if symbol else [], 500)
            trace = next((item_dict(row) for row in snapshot.get("traces") or [] if text(item_dict(row).get("id")) == selected_id), {})
            selected = trace
            selected_rule_id = text(trace.get("sourceRuleId") or trace.get("ruleId") or trace.get("semanticRuleId") or trace.get("nativeRuleId"))
            rule_ids = list_values([selected_rule_id])
            generation_ids = list_values([snapshot.get("inferenceGenerationId")])
            result["inferences"] = [self._lineage_inference(trace, snapshot)] if trace else []
            result["rules"] = [row for row in rules if row.get("id") in rule_ids]

        if rule_ids or generation_ids:
            snapshot = self.inferencebox(world_id, [symbol] if symbol else [], 500)
            for trace in snapshot.get("traces") or []:
                row = item_dict(trace)
                row_rule_id = text(row.get("sourceRuleId") or row.get("ruleId") or row.get("semanticRuleId") or row.get("nativeRuleId"))
                generation_match = not generation_ids or text(snapshot.get("inferenceGenerationId")) in generation_ids
                rule_match = not rule_ids or row_rule_id in rule_ids
                if generation_match and rule_match and text(row.get("id")):
                    normalized = self._lineage_inference(row, snapshot)
                    if normalized.get("id") not in {item.get("id") for item in result["inferences"]}:
                        result["inferences"].append(normalized)
        if selected_type in {"rule", "relation"} and rule_ids:
            result["hypotheses"] = self._hypotheses_for_rules(rule_ids, account_id, symbol)
            lifecycle_ids.extend(text(row.get("lifecycleId")) for row in result["hypotheses"] if text(row.get("lifecycleId")))
            generation_ids.extend(text(row.get("inferenceGenerationId")) for row in result["hypotheses"] if text(row.get("inferenceGenerationId")))

        decisions = self._decisions_for_lineage(account_id, symbol, rule_ids, generation_ids, lifecycle_ids)
        result["decisions"] = decisions
        result["notifications"] = self._notifications_for_decisions([text(row.get("episodeId")) for row in decisions])
        if rulebox_meta.get("status") == "unavailable":
            gaps.append({"code": "rulebox-unavailable", "detail": text(rulebox_meta.get("reason"))})
        if selected_type == "hypothesis" and not selected:
            gaps.append({"code": "hypothesis-not-found", "detail": "선택한 lifecycleKey의 현재 가설 기록을 찾지 못했습니다."})
        if selected_type == "inference" and not selected:
            gaps.append({"code": "inference-not-found", "detail": "선택한 traceId를 현재 InferenceBox 세대에서 찾지 못했습니다."})
        if result["inferences"] and not decisions:
            gaps.append({"code": "decision-link-missing", "detail": "이 추론 세대와 정확히 연결된 DecisionEpisode를 찾지 못했습니다."})
        if decisions and not result["notifications"]:
            gaps.append({"code": "notification-link-missing", "detail": "연결된 판단은 있지만 해당 decisionEpisodeId의 알림을 찾지 못했습니다."})
        return {
            "version": ONTOLOGY_CATALOG_VERSION,
            "status": "ok" if selected else "not-found",
            "readOnly": True,
            "worldId": world_id,
            "selection": {"type": selected_type, "id": selected_id, "item": selected},
            "lineage": result,
            "gaps": gaps,
        }

    def _lineage_inference(self, trace: Mapping[str, object], snapshot: Mapping[str, object]) -> Dict[str, object]:
        trace_id = text(trace.get("id"))
        return {
            "id": trace_id,
            "type": "inference",
            "traceId": trace_id,
            "label": text(trace.get("label")) or trace_id,
            "symbol": text(trace.get("symbol")).upper(),
            "ruleId": text(trace.get("sourceRuleId") or trace.get("ruleId") or trace.get("semanticRuleId") or trace.get("nativeRuleId")),
            "inferenceGenerationId": text(snapshot.get("inferenceGenerationId")),
            "matchedConditionIds": list_values(trace.get("matchedConditionIds")),
            "updatedAt": text(trace.get("updatedAt")),
        }

    def _hypotheses_for_rules(self, rule_ids: List[str], account_id: str, symbol: str) -> List[Dict[str, object]]:
        store = self.hypothesis_lifecycle_store
        reader = getattr(store, "list_current", None) if store else None
        if not callable(reader):
            return []
        try:
            rows = reader(account_id=account_id, symbol=symbol, limit=1000)
        except Exception:  # noqa: BLE001
            return []
        result = []
        for raw in rows:
            payload = item_dict(raw)
            if set(list_values(payload.get("sourceRuleIds"))) & set(rule_ids):
                result.append(self.normalize_hypothesis(payload))
        return result

    def _decisions_for_lineage(self, account_id: str, symbol: str, rule_ids: List[str], generation_ids: List[str], lifecycle_ids: List[str]) -> List[Dict[str, object]]:
        store = self.decision_episode_store
        reader = getattr(store, "list", None) if store else None
        if not callable(reader):
            return []
        try:
            rows = reader(account_id=account_id, symbol=symbol, limit=500)
        except Exception:  # noqa: BLE001
            return []
        result = []
        for raw in rows:
            payload = item_dict(raw)
            hypothesis_set = item_dict(payload.get("hypothesisSet"))
            episode_rule_ids = set()
            episode_hypothesis_ids = set()
            for hypothesis in hypothesis_set.get("hypotheses") or []:
                hypothesis_payload = item_dict(hypothesis)
                episode_rule_ids.update(list_values(hypothesis_payload.get("supportingRuleIds")))
                episode_rule_ids.update(list_values(hypothesis_payload.get("counterRuleIds")))
                episode_hypothesis_ids.update(list_values([
                    hypothesis_payload.get("hypothesisId"),
                    hypothesis_payload.get("marketHypothesisId"),
                    hypothesis_payload.get("accountHypothesisOverlayId"),
                ]))
            exact_match = bool(
                (generation_ids and text(payload.get("inferenceGenerationId")) in set(generation_ids))
                or (rule_ids and episode_rule_ids & set(rule_ids))
                or (lifecycle_ids and (text(payload.get("selectedHypothesisId")) in set(lifecycle_ids) or episode_hypothesis_ids & set(lifecycle_ids)))
            )
            if exact_match:
                result.append({
                    "id": text(payload.get("episodeId")),
                    "episodeId": text(payload.get("episodeId")),
                    "symbol": text(payload.get("symbol")).upper(),
                    "action": text(payload.get("action")),
                    "reviewLevel": text(payload.get("reviewLevel")),
                    "validationState": text(payload.get("validationState")),
                    "selectedHypothesisId": text(payload.get("selectedHypothesisId")),
                    "inferenceGenerationId": text(payload.get("inferenceGenerationId")),
                    "decisionSummary": text(payload.get("decisionSummary")),
                    "decidedAt": text(payload.get("decidedAt")),
                })
        return result[:50]

    def _notifications_for_decisions(self, episode_ids: List[str]) -> List[Dict[str, object]]:
        store = self.notification_job_store
        reader = getattr(store, "recent_page", None) if store else None
        if not callable(reader):
            return []
        result: List[Dict[str, object]] = []
        for episode_id in list_values(episode_ids)[:20]:
            try:
                jobs, _total = reader(limit=20, query=episode_id)
            except Exception:  # noqa: BLE001
                continue
            for raw in jobs or []:
                payload = item_dict(raw)
                if text(payload.get("decisionEpisodeId")) != episode_id:
                    continue
                job_id = text(payload.get("jobId"))
                if not job_id or job_id in {item.get("id") for item in result}:
                    continue
                result.append({
                    "id": job_id,
                    "jobId": job_id,
                    "decisionEpisodeId": episode_id,
                    "messageType": text(payload.get("messageType")),
                    "symbol": text(payload.get("symbol")).upper(),
                    "status": text(payload.get("status")),
                    "createdAt": text(payload.get("createdAt")),
                })
        return result[:50]
