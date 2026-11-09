"""Opt-in shared-world regression against a temporary native TypeDB database.

Run with PYTHONPATH=python_service:python_service/tests python3
python_service/tests/native_world_manifest_smoke.py. Only its own DB is deleted.
"""

from copy import deepcopy
import json
import uuid

from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from digital_twin.infrastructure.settings import load_local_env, runtime_settings
from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository
from digital_twin.modules.reasoning.domain.abox_lifecycle import validate_scope_manifest
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_PERSISTENCE_MODE
from digital_twin.modules.reasoning.domain.ontology_worlds import knowledge_world
from test_ontology_worlds import MultiAccountProjectionTests


def run():
    load_local_env()
    settings = runtime_settings(fast_operational_read=True, require_store_read=True)
    database = "orbit_world_manifest_smoke_" + uuid.uuid4().hex[:12]
    repo = TypeDBOntologyGraphRepository(
        settings["typedbAddress"], user=settings["typedbUser"], password=settings["typedbPassword"],
        database=database, tls_enabled=str(settings.get("typedbTlsEnabled", "")).lower() in {"1", "true"},
        timeout_seconds=30, query_timeout_seconds=10, schema_operation_timeout_seconds=180,
        write_operation_timeout_seconds=60, retry_count=0, persistent_driver_enabled=True,
    )
    imported = repo.driver_imports()
    if imported[0] is None:
        raise RuntimeError("TypeDB driver unavailable")
    driver = repo.create_driver(imported)
    created = False
    try:
        driver.databases.create(database)
        created = True
        repo.ensure_schema(driver, imported)
        recorder = PortfolioOntologyProjectionRecorder(repo, settings=settings)
        world = knowledge_world("kr")
        first, old_article = MultiAccountProjectionTests.news_graph(0)
        result = recorder.project_knowledge_world(first, world)
        assert result["status"] == "ok", (result.get("status"), result.get("reason"))
        before = repo.active_abox_metadata(world.world_id)
        unrelated = next(row for row in before["scopePlan"] if "stock:005930" in row.get("nodeIds", []))

        incoming, new_article = MultiAccountProjectionTests.news_graph(11)
        incoming.worldview["targetScopedManifestPatch"] = {"status": "applied", "targetSymbols": ["066570"]}
        result = recorder.project_knowledge_world(incoming, world)
        assert result["status"] == "ok", (result.get("status"), result.get("reason"))
        after = repo.active_abox_metadata(world.world_id)
        final = {row["scopeId"]: row for row in after["scopePlan"]}
        assert validate_scope_manifest(after["scopePlan"]).valid
        retired = result["targetScopedManifestPatch"]["retiredScopeIds"]
        assert retired and not set(retired).intersection(final)
        assert final[unrelated["scopeId"]]["generationId"] == unrelated["generationId"]
        assert repo.read_entity_rows_by_ids([new_article], ["ABox"], world_id=world.world_id)
        assert not repo.read_entity_rows_by_ids([old_article], ["ABox"], world_id=world.world_id)
        previous = repo.scoped_manifest_metadata(before["worldviewManifestId"], world.world_id)
        assert previous["status"] == "ok", previous
        assert previous["scopePlan"] == before["scopePlan"], "historical source inventory changed"
        integrity = repo.scoped_abox_integrity_audit(world.world_id, limit=200)
        assert integrity["status"] == "ok" and integrity["checkedScopeCount"] == len(final), integrity

        broken = deepcopy(after)
        for row in broken["scopePlan"]:
            row["nodeIds"] = [node for node in row.get("nodeIds", []) if node != new_article]
        broken.update(persistenceMode=SCOPED_ABOX_PERSISTENCE_MODE, worldviewManifestId="abox-manifest:invalid-smoke")
        rejected = repo.save_scoped_abox_graph(PortfolioOntology(world.world_id, worldview=broken))
        assert rejected["status"] == "invalid-scoped-abox-manifest", rejected
        assert repo.active_abox_metadata(world.world_id)["worldviewManifestId"] == after["worldviewManifestId"]
        return {"status": "passed", "plannedRetirements": len(retired),
                "checkedActiveScopes": integrity["checkedScopeCount"], "unrelatedGenerationPreserved": True,
                "historicalManifestPreserved": True, "invalidCandidateRejected": True,
                "operationalGraphMutated": False, "notificationPathUsed": False}
    finally:
        try:
            if created:
                driver.databases.get(database).delete()
        finally:
            driver.close()
            repo.invalidate_persistent_driver()


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
