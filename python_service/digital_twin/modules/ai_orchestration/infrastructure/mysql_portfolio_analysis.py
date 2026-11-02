"""Durable report progress, fenced by the central worker's task lease."""
from copy import deepcopy
import gzip
import json

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection, mysql_error_code
from ..domain import portfolio_analysis as contract
from ..domain.portfolio_report import digest, request_identity
from ..domain.planning import stamp
from .mysql_control import MySQLAIControlStore
from .mysql_portfolio_reports import MySQLPortfolioReportStore

SCHEMA = """CREATE TABLE IF NOT EXISTS ai_portfolio_analyses (
analysis_id CHAR(64) PRIMARY KEY, account_id VARCHAR(191) NOT NULL, request_id VARCHAR(64) NOT NULL,
active_account VARCHAR(191) NULL, status VARCHAR(32) NOT NULL, task_id CHAR(64) NOT NULL,
created_at VARCHAR(40) NOT NULL, updated_at VARCHAR(40) NOT NULL, state_gzip LONGBLOB NOT NULL,
UNIQUE KEY analysis_request(account_id,request_id), UNIQUE KEY analysis_active(active_account),
INDEX analysis_account(account_id,created_at,analysis_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""


def pack(state):
    raw = contract.encoded(state).encode()
    if len(raw) > 24_000_000:
        raise ValueError("portfolio analysis storage limit")
    return gzip.compress(raw, mtime=0)


def unpack(row):
    return json.loads(gzip.decompress(row["state_gzip"])) if row else None


class MySQLPortfolioAnalysisStore(MySQLOperationalConnection):
    def __init__(self, settings, *, initialize=False):
        super().__init__({**settings, "_skipOperationalSchemaBootstrap": "1"})
        self.reports = MySQLPortfolioReportStore(settings, initialize=initialize)
        if initialize:
            with self.connect() as connection:
                connection.execute(SCHEMA)

    def get(self, account, analysis_id):
        with self.connect() as connection:
            return unpack(connection.execute("SELECT state_gzip FROM ai_portfolio_analyses WHERE analysis_id=%s AND account_id=%s",
                                             (analysis_id, account)).fetchone())

    def existing(self, account, request_id=""):
        try:
            with self.connect() as connection:
                row = connection.execute("SELECT state_gzip FROM ai_portfolio_analyses WHERE account_id=%s AND request_id=%s",
                                         (account, request_id)).fetchone() if request_id else None
                return unpack(row or connection.execute("SELECT state_gzip FROM ai_portfolio_analyses WHERE active_account=%s", (account,)).fetchone())
        except Exception as error:
            if mysql_error_code(error) == 1146:
                return None
            raise

    def create(self, account, request_id, source, send_when_ready):
        request_identity(request_id)
        run = {"version": contract.VERSION, "analysisId": digest([contract.VERSION, account, request_id]),
               "accountId": account, "requestId": request_id, "source": source,
               "worldId": source["subjects"][0]["worldId"], "createdAt": stamp(), "status": "pending",
               "phase": "read", "batchIndex": 0, "readRound": 0, "step": 0, "completedStep": -1,
               "results": [], "execution": [], "sendWhenReady": bool(send_when_ready)}
        job = contract.task(run, 0)
        run["taskId"] = job["taskId"]
        with self.transaction() as connection:
            inserted = connection.execute("INSERT IGNORE INTO ai_portfolio_analyses "
                "(analysis_id,account_id,request_id,active_account,status,task_id,created_at,updated_at,state_gzip) "
                "VALUES (%s,%s,%s,%s,'pending',%s,%s,%s,%s)",
                (run["analysisId"], account, request_id, account, job["taskId"], run["createdAt"], run["createdAt"], pack(run))).rowcount
            if inserted:
                MySQLAIControlStore.insert(connection, job)
                return run
            row = connection.execute("SELECT state_gzip FROM ai_portfolio_analyses WHERE account_id=%s AND request_id=%s", (account, request_id)).fetchone()
            return unpack(row or connection.execute("SELECT state_gzip FROM ai_portfolio_analyses WHERE active_account=%s", (account,)).fetchone())

    def checkpoint(self, job, run):
        with self.transaction() as connection:
            owned = connection.execute("SELECT task_id FROM ai_control_tasks WHERE task_id=%s AND status='processing' "
                "AND lease_token=%s AND lease_until>=%s FOR UPDATE", (job["taskId"], job["leaseToken"], stamp())).fetchone()
            if not owned:
                raise RuntimeError("portfolio-report-lease-lost")
            changed = connection.execute("UPDATE ai_portfolio_analyses SET state_gzip=%s,updated_at=%s WHERE analysis_id=%s "
                "AND account_id=%s AND task_id=%s", (pack(run), stamp(), run["analysisId"], job["accountId"], job["taskId"])).rowcount
            if not changed:
                raise RuntimeError("portfolio-report-state-ownership-lost")

    def complete(self, connection, job, result, children):
        if job.get("capability") != contract.CAPABILITY:
            return
        run = unpack(connection.execute("SELECT state_gzip FROM ai_portfolio_analyses WHERE analysis_id=%s AND account_id=%s "
            "AND task_id=%s FOR UPDATE", (job["analysisId"], job["accountId"], job["taskId"])).fetchone())
        if not run or run.get("completedStep") != job["step"]:
            raise ValueError("portfolio-report-transition-not-checkpointed")
        if run.get("report"):
            report = self.reports.save_with_connection(connection, run.pop("report"))
            run["reportId"] = report["reportId"]
            result["reportId"] = report["reportId"]
        if children:
            run["taskId"] = children[0]["taskId"]
        connection.execute("UPDATE ai_portfolio_analyses SET status=%s,task_id=%s,state_gzip=%s,updated_at=%s,active_account=%s "
            "WHERE analysis_id=%s AND account_id=%s", (run["status"], run["taskId"], pack(run), stamp(),
            None if run["status"] in {"ready", "completed", "failed"} else run["accountId"], run["analysisId"], run["accountId"]))

    def failed(self, connection, job):
        if job.get("capability") != contract.CAPABILITY:
            return
        row = connection.execute("SELECT state_gzip FROM ai_portfolio_analyses WHERE analysis_id=%s AND account_id=%s "
            "AND task_id=%s FOR UPDATE", (job["analysisId"], job["accountId"], job["taskId"])).fetchone()
        run = unpack(row)
        if run:
            run["status"] = "failed"
            connection.execute("UPDATE ai_portfolio_analyses SET status='failed',active_account=NULL,state_gzip=%s,updated_at=%s "
                "WHERE analysis_id=%s", (pack(run), stamp(), run["analysisId"]))

    def status(self, run):
        if not run:
            return None
        with self.connect() as connection:
            task = connection.execute("SELECT status,last_error,available_at FROM ai_control_tasks WHERE task_id=%s AND account_id=%s",
                                      (run["taskId"], run["accountId"])).fetchone() or {}
        runtime_wait = ""
        if task.get("status") == "pending":
            from ..domain.planning import enabled
            from digital_twin.infrastructure.background_capacity import host_memory_pressure
            if not enabled(self.runtime_settings):
                runtime_wait = "central-ai-paused"
            elif host_memory_pressure() == "critical":
                runtime_wait = "memory-pressure"
        return {key: deepcopy(run[key]) for key in ("analysisId", "accountId", "createdAt", "status", "phase", "step")} | {
            "totalSubjects": len(run["source"]["subjects"]), "completedSubjects": len(run["results"]),
            "reportId": run.get("reportId", ""), "taskStatus": task.get("status", "unknown"),
            "runtimeWait": runtime_wait,
            "waitReason": task.get("last_error", ""), "nextCheckAt": task.get("available_at", ""),
            "completedModelSteps": len(run["execution"]), "sendWhenReady": run["sendWhenReady"]}
