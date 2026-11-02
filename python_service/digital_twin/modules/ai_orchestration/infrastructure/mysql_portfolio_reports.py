"""Immutable request snapshots, separate from short-lived worker/task retention."""
import json

from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection, mysql_error_code
from ..domain.portfolio_report import summary, verify_report

SCHEMA = """CREATE TABLE IF NOT EXISTS ai_portfolio_reports (
report_id CHAR(64) PRIMARY KEY, account_id VARCHAR(191) NOT NULL, request_id VARCHAR(64) NOT NULL,
created_at VARCHAR(40) NOT NULL, content_hash CHAR(64) NOT NULL, payload_json LONGTEXT NOT NULL, summary_json TEXT NOT NULL,
notification_job_id VARCHAR(64) NOT NULL DEFAULT '',
UNIQUE KEY report_request(account_id,request_id), INDEX report_account(account_id,created_at,report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""


class MySQLPortfolioReportStore(MySQLOperationalConnection):
    def __init__(self, settings, *, initialize=False):
        super().__init__({**settings, "_skipOperationalSchemaBootstrap": "1"})
        if initialize:
            with self.connect() as connection:
                connection.execute(SCHEMA)

    @staticmethod
    def decode(row):
        if not row:
            return None
        report = json.loads(row["payload_json"])
        if not verify_report(report) or report["contentHash"] != row["content_hash"]:
            raise ValueError("저장된 보고서의 내용 검증에 실패했습니다.")
        return report

    def _read(self, field, value, account):
        assert field in {"report_id", "request_id"}
        try:
            with self.connect() as connection:
                return self.decode(connection.execute("SELECT payload_json,content_hash FROM ai_portfolio_reports "
                    "WHERE account_id=%s AND " + field + "=%s", (account, value)).fetchone())
        except Exception as error:
            if mysql_error_code(error) == 1146:
                return None
            raise

    def get(self, account, report_id):
        return self._read("report_id", report_id, account)

    def by_request(self, account, request_id):
        return self._read("request_id", request_id, account)

    def save_once(self, report):
        if not verify_report(report):
            raise ValueError("보고서 내용과 해시가 일치하지 않습니다.")
        payload = json.dumps(report, ensure_ascii=False, allow_nan=False)
        if len(payload.encode()) > 8_000_000:
            raise ValueError("보고서 크기가 저장 한도를 넘었습니다.")
        with self.transaction() as connection:
            connection.execute("INSERT IGNORE INTO ai_portfolio_reports "
                "(report_id,account_id,request_id,created_at,content_hash,payload_json,summary_json) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (report["reportId"], report["accountId"], report["requestId"], report["createdAt"], report["contentHash"], payload,
                 json.dumps(summary(report), ensure_ascii=False)))
            # Concurrent retries return the first frozen snapshot, never replace it.
            return self.decode(connection.execute("SELECT payload_json,content_hash FROM ai_portfolio_reports "
                "WHERE account_id=%s AND request_id=%s", (report["accountId"], report["requestId"])).fetchone())

    def recent(self, account):
        try:
            with self.connect() as connection:
                ids = connection.execute("SELECT report_id FROM ai_portfolio_reports WHERE account_id=%s "
                    "ORDER BY created_at DESC,report_id DESC LIMIT 20", (account,)).fetchall()
                values = []
                for item in ids:
                    row = connection.execute("SELECT summary_json,content_hash FROM ai_portfolio_reports WHERE report_id=%s "
                        "AND account_id=%s", (item["report_id"], account)).fetchone()
                    value = json.loads(row["summary_json"])
                    if value.get("contentHash") != row["content_hash"] or value.get("accountId") != account:
                        raise ValueError("보고서 목록의 저장된 식별자가 일치하지 않습니다.")
                    values.append(value)
                return values
        except Exception as error:
            if mysql_error_code(error) == 1146:
                return []
            raise
