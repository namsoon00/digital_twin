"""Capture bounded, versioned ABox facts without requiring an inference candidate."""
import json
from digital_twin.modules.ai_orchestration.domain.planning import stamp


class GraphObservationReader:
    def __init__(self, repository):
        self.repository = repository

    def __call__(self, job):
        repository = self.repository
        portfolio = repository.active_abox_metadata(job["worldId"])
        if portfolio.get("status") != "ok" or not portfolio.get("aboxSnapshotId"):
            raise ValueError("portfolio graph not ready")
        if portfolio.get("accountId") and portfolio["accountId"] != job["accountId"]:
            raise ValueError("graph account mismatch")
        worlds = [job["worldId"]]
        shared = str(portfolio.get("sharedPremiseWorldId") or "")
        if shared:
            if not shared.startswith("premise:"):
                raise ValueError("invalid shared evidence world")
            worlds.append(shared)
        versions = {world: repository.active_abox_snapshot_id(world) for world in worlds}
        rows, total = [], 0
        for world in worlds:
            if not versions[world]:
                raise ValueError("source graph snapshot missing")
            context = repository.active_abox_rule_context([job["symbol"]], world_id=world)
            if context.get("status") != "ok":
                raise ValueError("graph evidence index unavailable")
            ids = list((context.get("sourceIdsBySymbol") or {}).get(job["symbol"], []))
            total += len(ids)
            for row in repository.read_entity_rows_by_ids(ids[:40], boxes=["ABox"], world_id=world) if ids else []:
                if row.get("symbol") and row["symbol"] != job["symbol"]:
                    raise ValueError("graph symbol mismatch")
                if row.get("accountId") and row["accountId"] != job["accountId"]:
                    raise ValueError("graph fact account mismatch")
                if row.get("worldId") and row["worldId"] != world:
                    raise ValueError("graph fact world mismatch")
                compact = {key: value for key, value in row.items()
                           if value not in (None, "", [], {}) and key not in {"propertiesJson", "proposedRuleJson"}
                           and not key.startswith(("condition", "derivation", "activeTbox"))}
                rows.append({**compact, "id": world + ":" + str(row["id"]), "sourceEntityId": row["id"],
                             "sourceWorldId": world, "sourceSnapshotId": versions[world]})
        if any(repository.active_abox_snapshot_id(world) != version for world, version in versions.items()):
            raise ValueError("graph changed during capture")
        packet = {"accountId": job["accountId"], "symbol": job["symbol"], "name": job["name"],
                  "worldId": job["worldId"], "sourceSnapshotId": versions[job["worldId"]],
                  "sourceSnapshots": versions, "capturedAt": stamp(), "facts": rows,
                  "availableFactCount": total, "includedFactCount": len(rows), "requiresMatchedRule": False}
        if len(json.dumps(packet, ensure_ascii=False).encode()) > 96000:
            raise ValueError("graph input exceeds context budget")
        return packet
