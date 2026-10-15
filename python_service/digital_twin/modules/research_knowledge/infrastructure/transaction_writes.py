"""Owner writes on the caller's transaction; never connect or commit here."""
from copy import deepcopy
import json
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from ..domain.knowledge import SCOPE, digest, revise_claim
from ..domain.assessments import assessment_for_claim
from ..domain.events import knowledge_revised_event


def read_claim(connection: BoundWriteConnection, claim_id, scope):
    row = connection.execute("SELECT payload_json FROM research_knowledge_claims "
        "WHERE claim_id=%s AND account_id=%s AND symbol=%s AND world_id=%s FOR UPDATE",
        (claim_id, scope["accountId"], scope["symbol"], scope["worldId"])).fetchone()
    return json.loads(row["payload_json"]) if row else None


def append_revision(connection: BoundWriteConnection, claim, kind):
    payload = json.dumps(claim, ensure_ascii=False, allow_nan=False)
    connection.execute("INSERT INTO research_knowledge_revisions "
        "(revision_id,claim_id,revision,account_id,symbol,world_id,execution_input_id,kind,known_at,payload_json) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (claim["revisionId"], claim["claimId"], claim["revision"], *[claim[key] for key in SCOPE],
         claim["executionInputId"], kind, claim["knownAt"], payload))
    connection.execute("INSERT INTO research_knowledge_claims "
        "(claim_id,account_id,symbol,world_id,revision,status,known_at,payload_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE revision=VALUES(revision),status=VALUES(status),known_at=VALUES(known_at),payload_json=VALUES(payload_json)",
        (claim["claimId"], *[claim[key] for key in SCOPE], claim["revision"], claim["status"], claim["knownAt"], payload))


def write_research(connection: BoundWriteConnection, packet, proposals, input_id, now, business_ids=()):
    receipt = {"status": "recorded", "claimIds": [], "revisionIds": [], "assessmentIds": [], "events": []}
    changed = set()
    memories = packet.get("businessThesisMemory", [])
    for proposal in proposals["claims"]:
        claim_id = proposal["claimId"]
        repeated = connection.execute("SELECT revision_id FROM research_knowledge_revisions "
            "WHERE claim_id=%s AND execution_input_id=%s AND kind='author'", (claim_id, input_id)).fetchone()
        if repeated:
            receipt["revisionIds"].append(repeated["revision_id"])
            changed.add(claim_id)
            continue
        previous = read_claim(connection, claim_id, packet)
        index = proposal["businessThesisIndex"]
        claim = revise_claim(previous, proposal, packet, input_id, now, business_ids[index] if index >= 0 else "")
        claim["assessment"] = assessment_for_claim(claim, memories)
        # A replacement financial contract starts a new observation basis.
        if previous and claim["businessThesisId"] != previous.get("businessThesisId"):
            claim["assessment"] = {"status": "awaiting-observation", "qualification": "not-empirically-qualified"}
        append_revision(connection, claim, "author")
        receipt["events"].append(knowledge_revised_event(claim))
        receipt["claimIds"].append(claim_id)
        receipt["revisionIds"].append(claim["revisionId"])
        changed.add(claim_id)
    for captured in packet.get("researchKnowledge", {}).get("claims", []):
        if captured["claimId"] in changed:
            continue
        assessment = assessment_for_claim(captured, memories)
        if assessment == captured.get("assessment"):
            continue
        repeated = connection.execute("SELECT revision_id FROM research_knowledge_revisions "
            "WHERE claim_id=%s AND execution_input_id=%s AND kind='assessment'", (captured["claimId"], input_id)).fetchone()
        if repeated:
            continue
        previous = read_claim(connection, captured["claimId"], packet)
        if not previous or previous["revision"] != captured["revision"]:
            raise ValueError("knowledge assessment revision conflict")
        claim = deepcopy(previous)
        claim.update(revision=previous["revision"] + 1, previousRevisionId=previous["revisionId"],
            revisionId=digest([previous["claimId"], previous["revision"] + 1, input_id, "assessment"]),
            assessment=assessment, knownAt=now, executionInputId=input_id, asOf=packet["capturedAt"])
        append_revision(connection, claim, "assessment")
        receipt["events"].append(knowledge_revised_event(claim))
        receipt["assessmentIds"].append(assessment["assessmentId"])
        receipt["revisionIds"].append(claim["revisionId"])
    return receipt
