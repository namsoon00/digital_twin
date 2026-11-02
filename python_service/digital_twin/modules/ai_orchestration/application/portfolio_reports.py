"""Explicit request/report lifecycle; browsing never creates work or delivery."""
from datetime import datetime, timezone

from ..domain.portfolio_report import build_report, request_identity


class PortfolioReportService:
    def __init__(self, repository, source_reader, publisher, now=None):
        self.repository, self.sources, self.publisher = repository, source_reader, publisher
        self.now = now or (lambda: datetime.now(timezone.utc).isoformat())

    def accounts(self):
        return self.sources.accounts()

    def _account(self, account_id):
        if account_id not in {a["accountId"] for a in self.accounts()}:
            raise ValueError("사용 가능한 계정을 선택해 주세요.")
        return account_id

    def create(self, account_id, request_id):
        self._account(account_id)
        request_identity(request_id)
        old = self.repository.by_request(account_id, request_id)
        if old:
            return old
        captured_at = self.now()
        source = self.sources.capture(account_id, captured_at)
        report = build_report(account_id, captured_at, source["snapshotAt"], source["subjects"], request_id=request_id)
        return self.repository.save_once(report)

    def list(self, account_id):
        return self.repository.recent(self._account(account_id))

    def get(self, account_id, report_id):
        return self.repository.get(self._account(account_id), report_id)

    def send(self, account_id, report_id):
        report = self.get(account_id, report_id)
        if not report:
            raise LookupError("보고서를 찾지 못했습니다.")
        return self.publisher(report)
