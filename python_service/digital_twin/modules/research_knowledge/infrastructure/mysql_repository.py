"""Canonical local knowledge state and append-only point-in-time revisions."""
import json
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from ..domain.frameworks import framework_catalog
from ..domain.knowledge import digest, stamp, canonical_time

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS research_knowledge_frameworks (
    framework_id VARCHAR(96) NOT NULL, version INT NOT NULL, content_hash CHAR(64) NOT NULL,
    known_at VARCHAR(40) NOT NULL, payload_json LONGTEXT NOT NULL,
    PRIMARY KEY(framework_id,version)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS research_knowledge_claims (
    claim_id CHAR(64) PRIMARY KEY, account_id VARCHAR(191) NOT NULL, symbol VARCHAR(64) NOT NULL,
    world_id VARCHAR(191) NOT NULL, revision INT NOT NULL, status VARCHAR(32) NOT NULL,
    known_at VARCHAR(40) NOT NULL, payload_json LONGTEXT NOT NULL,
    INDEX knowledge_subject(account_id,symbol,world_id,known_at)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS research_knowledge_revisions (
    revision_id CHAR(64) PRIMARY KEY, claim_id CHAR(64) NOT NULL, revision INT NOT NULL,
    account_id VARCHAR(191) NOT NULL, symbol VARCHAR(64) NOT NULL, world_id VARCHAR(191) NOT NULL,
    execution_input_id VARCHAR(191) NOT NULL, kind VARCHAR(32) NOT NULL,
    known_at VARCHAR(40) NOT NULL, payload_json LONGTEXT NOT NULL,
    UNIQUE KEY knowledge_sequence(claim_id,revision),
    UNIQUE KEY knowledge_input(claim_id,execution_input_id,kind),
    INDEX knowledge_history(account_id,symbol,world_id,known_at)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


class MySQLResearchKnowledgeRepository(MySQLOperationalConnection):
    def __init__(self, settings=None, initialize=True):
        super().__init__(settings)
        if not initialize:
            return
        with self.connect() as connection:
            for statement in SCHEMA:
                connection.execute(statement)
            for framework in framework_catalog():
                fingerprint = digest(framework)
                connection.execute("INSERT IGNORE INTO research_knowledge_frameworks "
                    "(framework_id,version,content_hash,known_at,payload_json) VALUES (%s,%s,%s,%s,%s)",
                    (framework["id"], framework["version"], fingerprint, stamp(), json.dumps(framework, ensure_ascii=False)))
                row = connection.execute("SELECT content_hash FROM research_knowledge_frameworks WHERE framework_id=%s AND version=%s",
                                         (framework["id"], framework["version"])).fetchone()
                if row["content_hash"] != fingerprint:
                    raise ValueError("research framework changed without a version increment")

    def frameworks(self, as_of=None):
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json,known_at,content_hash FROM research_knowledge_frameworks "
                "WHERE known_at<=%s ORDER BY framework_id,version", (canonical_time(as_of or stamp()),)).fetchall()
        return [{**json.loads(row["payload_json"]), "knownAt": row["known_at"], "contentHash": row["content_hash"]} for row in rows]

    def records(self, scope, as_of, limit=24):
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM (SELECT payload_json,known_at,"
                "ROW_NUMBER() OVER(PARTITION BY claim_id ORDER BY revision DESC) AS row_number_ "
                "FROM research_knowledge_revisions WHERE account_id=%s AND symbol=%s AND world_id=%s AND known_at<=%s) latest "
                "WHERE row_number_=1 ORDER BY known_at DESC LIMIT %s",
                (scope["accountId"], scope["symbol"], scope["worldId"], canonical_time(as_of), max(1, min(100, limit)))).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def recent(self, account_id="", limit=30):
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM research_knowledge_claims "
                "WHERE (%s='' OR account_id=%s) ORDER BY known_at DESC LIMIT %s",
                (account_id, account_id, max(1, min(100, limit)))).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]
