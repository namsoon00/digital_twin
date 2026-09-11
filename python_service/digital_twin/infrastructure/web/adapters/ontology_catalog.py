"""Web ontology catalog boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.adapters.ontology_access import ontology_world_id_from_query
from digital_twin.infrastructure.web.cache import cached_api_payload
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.common import safe_int
from digital_twin.modules.decisions.domain.investment_reasoning.rule_inventory import reasoning_rule_inventory
from digital_twin.modules.read_models.public import OntologyCatalogQueryService
from typing import Dict
from typing import List
import json


ONTOLOGY_RULEBOX_READ_MODEL = StaleReadModelCache(
    "ontology-rulebox",
    ttl_seconds=120,
    retry_cooldown_seconds=30,
)


ONTOLOGY_CATALOG_SUMMARY_READ_MODEL = StaleReadModelCache(
    "ontology-catalog-summary",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)


ONTOLOGY_CATALOG_PAGE_READ_MODEL = StaleReadModelCache(
    "ontology-catalog-page",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)


def _ontology_rulebox_source_payload() -> Dict[str, object]:
    return ontology_repository_from_settings(runtime_settings()).rulebox_snapshot()


def ontology_rulebox_payload(force: bool = False, blocking_first_load: bool = False) -> Dict[str, object]:
    return cached_api_payload(
        ONTOLOGY_RULEBOX_READ_MODEL,
        "active",
        _ontology_rulebox_source_payload,
        force=force,
        blocking_first_load=blocking_first_load,
    )


def ontology_rulebox_summary_payload() -> Dict[str, object]:
    payload = ontology_rulebox_payload(blocking_first_load=True)
    profile = payload.get("nativeReasoningProfile") if isinstance(payload.get("nativeReasoningProfile"), dict) else {}
    inventory = reasoning_rule_inventory(
        item for item in payload.get("rules") or [] if isinstance(item, dict)
    )
    return {
        key: payload.get(key)
        for key in [
            "configured", "saved", "status", "source", "graphStore", "reason", "engineVersion",
            "ruleCount", "conditionCount", "derivationCount", "relationTypes", "versionCount",
            "ruleboxSnapshotId", "ruleboxRulesHash", "ruleboxShortHash", "readCache",
        ]
        if key in payload
    } | {
        "ruleInventory": inventory,
        "nativeReasoningProfile": {
            key: profile.get(key)
            for key in [
                "version", "status", "ruleCount", "readyRuleCount", "partialRuleCount",
                "blockedRuleCount", "supportedConditionCount", "unsupportedConditionCount",
            ]
            if key in profile
        }
    }


def ontology_catalog_api_payload(section: str, query: Dict[str, List[str]]) -> Dict[str, object]:
    """Read one ontology catalog section without mutating graph state."""

    section_id = str(section or "summary").strip().lower()
    account_id = str(first_query(query, "accountId") or first_query(query, "account") or "").strip()
    world_id = ontology_world_id_from_query(query)

    def service() -> OntologyCatalogQueryService:
        settings = operational_read_settings()
        include_lineage = section_id == "lineage"
        return OntologyCatalogQueryService(
            ontology_repository=ontology_repository_from_settings(settings),
            hypothesis_lifecycle_store=stores.hypothesis_lifecycle_store(settings),
            decision_episode_store=(
                stores.investment_decision_episode_store(settings)
                if include_lineage or section_id == "summary"
                else None
            ),
            notification_job_store=stores.notification_job_store(settings) if include_lineage else None,
            statistical_signal_store=stores.statistical_model_signal_store(settings) if section_id == "summary" else None,
            rulebox_provider=lambda: ontology_rulebox_payload(blocking_first_load=True),
        )

    if section_id == "summary":
        cache_key = "|".join([world_id or "none", account_id or "none"])
        return cached_api_payload(
            ONTOLOGY_CATALOG_SUMMARY_READ_MODEL,
            cache_key,
            lambda: service().summary(world_id=world_id, account_id=account_id),
            force=request_bool(first_query(query, "refresh"), False),
            blocking_first_load=False,
        )
    if section_id == "rules" and not ONTOLOGY_RULEBOX_READ_MODEL.snapshot("active").get("hasData"):
        warming = ontology_rulebox_payload(blocking_first_load=False)
        return {
            "status": "warming",
            "section": section_id,
            "items": [],
            "count": 0,
            "total": 0,
            "nextCursor": "",
            "readCache": warming.get("readCache") or {},
        }
    if section_id == "lineage":
        return service().lineage(
            item_type=str(first_query(query, "type") or ""),
            item_id=str(first_query(query, "id") or ""),
            world_id=world_id,
            account_id=account_id,
            symbol=str(first_query(query, "symbol") or "").upper(),
        )
    list_args = {
        "section": section_id,
        "query": str(first_query(query, "query") or first_query(query, "q") or ""),
        "cursor": str(first_query(query, "cursor") or ""),
        "limit": safe_int(first_query(query, "limit"), 40, 1, 100),
        "bounded_context": str(first_query(query, "boundedContext") or first_query(query, "context") or ""),
        "enabled": str(first_query(query, "enabled") or "").lower(),
        "scope": str(first_query(query, "scope") or ""),
        "state": str(first_query(query, "state") or ""),
        "symbol": str(first_query(query, "symbol") or "").upper(),
        "account_id": account_id,
        "market_id": str(first_query(query, "marketId") or first_query(query, "market") or ""),
        "world_id": world_id,
        "rule_kind": str(first_query(query, "ruleKind") or ""),
        "theory_family": str(first_query(query, "theoryFamily") or ""),
        "validation_status": str(first_query(query, "validationStatus") or ""),
    }
    cache_key = json.dumps(list_args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return cached_api_payload(
        ONTOLOGY_CATALOG_PAGE_READ_MODEL,
        cache_key,
        lambda: service().list_section(**list_args),
        force=request_bool(first_query(query, "refresh"), False),
        blocking_first_load=False,
    )
