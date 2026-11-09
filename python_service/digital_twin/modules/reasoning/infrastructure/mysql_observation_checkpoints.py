"""One leased refresh and one bounded verified inventory per exact subject route."""
import gzip
import io
import json
import uuid
from datetime import datetime, timedelta, timezone

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from ..domain.observation_checkpoint import MAX_BYTES, REFRESH_SECONDS, instant, scope, validate
from ..domain.observation_evidence import EvidenceContractError, canonical_json, content_hash, evidence_change_identity

SCHEMA = """CREATE TABLE IF NOT EXISTS observation_evidence_checkpoints (
 scope_id VARCHAR(64) PRIMARY KEY, request_json TEXT NOT NULL, binding_json TEXT NOT NULL,
 status VARCHAR(24) NOT NULL, available_at VARCHAR(40) NOT NULL,
 lease_token VARCHAR(64) NOT NULL DEFAULT '', lease_until VARCHAR(40) NOT NULL DEFAULT '',
 failures INT NOT NULL DEFAULT 0, last_error VARCHAR(160) NOT NULL DEFAULT '',
 captured_at VARCHAR(40) NOT NULL DEFAULT '', artifact_hash VARCHAR(64) NOT NULL DEFAULT '',
 material_hash VARCHAR(64) NOT NULL DEFAULT '', artifact_gzip LONGBLOB,
 updated_at VARCHAR(40) NOT NULL, INDEX observation_checkpoint_due(status,available_at),
 INDEX observation_checkpoint_updated(updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def stamp(value=None):
    return (value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


class MySQLObservationCheckpointStore(MySQLOperationalConnection):
    def __init__(self, settings=None, publisher=None):
        super().__init__(settings)
        self.publisher = publisher
        with self.connect() as connection:
            connection.execute(SCHEMA)

    @staticmethod
    def request_with_connection(connection, key, request, binding, now, refresh_seconds=REFRESH_SECONDS, force=False):
        payload = {k: request.get(k, "") for k in ("accountId", "symbol", "name", "worldId", "marketWorldId")}
        if key != scope(payload, binding):
            raise EvidenceContractError("checkpoint request scope mismatch")
        connection.execute("INSERT IGNORE INTO observation_evidence_checkpoints "
            "(scope_id,request_json,binding_json,status,available_at,updated_at) VALUES (%s,%s,%s,'pending',%s,%s)",
            (key, json.dumps(payload, ensure_ascii=False), json.dumps(binding), stamp(now), stamp(now)))
        # Repeated requests cannot shorten a failure backoff or steal a lease.
        connection.execute("UPDATE observation_evidence_checkpoints SET status='pending',updated_at=%s,available_at=LEAST(available_at,%s) "
            "WHERE scope_id=%s AND status='ready' AND (%s=1 OR (available_at<=%s AND captured_at<=%s))",
            (stamp(now), stamp(now), key, int(force), stamp(now), stamp(now - timedelta(seconds=refresh_seconds))))

    def request(self, key, request, binding, now, force=False):
        with self.transaction() as connection:
            self.request_with_connection(connection, key, request, binding, now, force=force)

    def seed(self, subjects, binding, now):
        # Model quota waits must not stop evidence preparation. Idle subjects
        # refresh at most every 15 minutes; active readers can request sooner.
        for offset in range(0, len(subjects), 64):
            with self.transaction() as connection:
                for subject in subjects[offset:offset + 64]:
                    self.request_with_connection(connection, scope(subject, binding), subject, binding, now, 900)

    def read(self, key):
        with self.connect() as connection:
            row = connection.execute("SELECT status,captured_at,available_at,last_error,artifact_hash,artifact_gzip "
                "FROM observation_evidence_checkpoints WHERE scope_id=%s", (key,)).fetchone()
        if not row:
            return None, {"status": "missing"}
        state = {"status": row["status"], "retryAt": row["available_at"], "lastError": row["last_error"]}
        if not row["artifact_gzip"]:
            return None, state
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(row["artifact_gzip"])) as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("oversized")
            artifact = json.loads(raw)
            if content_hash(artifact) != row["artifact_hash"]:
                raise ValueError("hash mismatch")
        except (OSError, ValueError, EOFError, TypeError) as error:
            raise EvidenceContractError("checkpoint integrity failure") from error
        return artifact, state

    def claim(self, eligible_scopes=None):
        now, token = datetime.now(timezone.utc), uuid.uuid4().hex
        keys = tuple(sorted(set(eligible_scopes))) if eligible_scopes is not None else None
        scope_sql = " AND scope_id IN (" + ",".join(["%s"] * len(keys)) + ")" if keys else ""
        hint = " FORCE INDEX(PRIMARY)" if keys else ""
        params = (stamp(now), *(keys or ()))
        with self.transaction() as connection:
            # Idle routes/deployments cannot retain account evidence forever.
            connection.execute("DELETE FROM observation_evidence_checkpoints WHERE status IN ('ready','pending') "
                "AND updated_at<%s ORDER BY updated_at LIMIT 10", (stamp(now - timedelta(days=7)),))
            # Once dispatch ignores retired routes, expired leases on those
            # routes also need bounded cleanup. Live producer leases survive.
            connection.execute("DELETE FROM observation_evidence_checkpoints WHERE status='processing' "
                "AND available_at<%s AND lease_until<%s AND updated_at<%s ORDER BY available_at LIMIT 10",
                (stamp(now), stamp(now), stamp(now - timedelta(days=7))))
            if keys == ():
                return None
            # Separate indexed scans avoid an OR scan across completed artifacts.
            row = connection.execute("SELECT scope_id,request_json,binding_json,available_at FROM observation_evidence_checkpoints"
                + hint + " WHERE status='processing' AND available_at<=%s" + scope_sql
                + " ORDER BY available_at,scope_id LIMIT 1 FOR UPDATE SKIP LOCKED", params).fetchone()
            if not row:
                row = connection.execute("SELECT scope_id,request_json,binding_json,available_at FROM observation_evidence_checkpoints"
                    + hint + " WHERE status='pending' AND available_at<=%s" + scope_sql
                    + " ORDER BY available_at,scope_id LIMIT 1 FOR UPDATE SKIP LOCKED", params).fetchone()
            if not row:
                return None
            until = stamp(now + timedelta(minutes=5))
            connection.execute("UPDATE observation_evidence_checkpoints SET status='processing',lease_token=%s,"
                "lease_until=%s,available_at=%s,updated_at=%s WHERE scope_id=%s",
                (token, until, until, stamp(now), row["scope_id"]))
        try:
            waited = max(0, round((now - instant(row["available_at"])).total_seconds() * 1000, 3))
        except EvidenceContractError:
            waited = None
        return {"scopeId": row["scope_id"], "leaseToken": token, "queueWaitMs": waited,
            "request": json.loads(row["request_json"]), "binding": json.loads(row["binding_json"])}

    def complete(self, job, artifact):
        validate(artifact)
        packet = artifact["packet"]
        if scope(packet | {"marketWorldId": artifact.get("marketWorldId", "")}, packet["reasoningBinding"]) != job["scopeId"]:
            raise EvidenceContractError("checkpoint completion scope mismatch")
        raw = canonical_json(artifact).encode()
        if len(raw) > MAX_BYTES:
            raise EvidenceContractError("checkpoint exceeds bounded inventory")
        digest = content_hash(artifact)
        material = evidence_change_identity(packet, [])
        now = datetime.now(timezone.utc)
        with self.transaction() as connection:
            row = connection.execute("SELECT material_hash FROM observation_evidence_checkpoints WHERE scope_id=%s "
                "AND status='processing' AND lease_token=%s AND lease_until>=%s FOR UPDATE",
                (job["scopeId"], job["leaseToken"], stamp(now))).fetchone()
            if not row:
                return False
            connection.execute("UPDATE observation_evidence_checkpoints SET status='ready',artifact_hash=%s,material_hash=%s,"
                "artifact_gzip=%s,captured_at=%s,available_at=%s,updated_at=%s,failures=0,last_error='',lease_token='',lease_until='' "
                "WHERE scope_id=%s", (digest, material, gzip.compress(raw), packet["capturedAt"],
                stamp(now + timedelta(seconds=REFRESH_SECONDS)), stamp(now), job["scopeId"]))
            if self.publisher:
                self.publisher(connection, job, artifact, material, row["material_hash"] != material)
        return True

    def fail(self, job, reason):
        now = datetime.now(timezone.utc)
        with self.transaction() as connection:
            row = connection.execute("SELECT failures FROM observation_evidence_checkpoints WHERE scope_id=%s "
                "AND status='processing' AND lease_token=%s AND lease_until>=%s FOR UPDATE",
                (job["scopeId"], job["leaseToken"], stamp(now))).fetchone()
            if not row:
                return False
            delay = min(1800, 60 * 2 ** min(int(row["failures"]), 5))
            connection.execute("UPDATE observation_evidence_checkpoints SET status='pending',failures=failures+1,"
                "last_error=%s,available_at=%s,updated_at=%s,lease_token='',lease_until='' WHERE scope_id=%s",
                (reason[:160], stamp(now + timedelta(seconds=delay)), stamp(now), job["scopeId"]))
        return True

    def retire(self, job):
        with self.transaction() as connection:
            connection.execute("DELETE FROM observation_evidence_checkpoints WHERE scope_id=%s AND lease_token=%s",
                (job["scopeId"], job["leaseToken"]))


def checkpoint_status(connection, account_id=""):
    """Metadata-only diagnostic; no initialization, private facts or raw errors."""
    rows = connection.execute("SELECT /*+ MAX_EXECUTION_TIME(3000) */ status,COUNT(*) AS count,"
        "SUM(artifact_hash<>'') AS stored_copies,MIN(available_at) AS next_at,MAX(captured_at) AS captured_at "
        "FROM observation_evidence_checkpoints WHERE (%s='' OR "
        "JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.accountId'))=%s) GROUP BY status", (account_id, account_id)).fetchall()
    return {"version": "verified-observation-checkpoint-v1", "status": "available", "queues": [
        {"status": r["status"], "count": int(r["count"]), "storedCopies": int(r["stored_copies"] or 0),
         "nextAt": r["next_at"], "lastCaptureAt": r["captured_at"]} for r in rows],
        "authority": "evidence-preparation-only", "storedCopyIsCurrentEvidence": False}
