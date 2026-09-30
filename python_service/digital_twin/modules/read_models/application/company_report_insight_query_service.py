"""Read a validated, generation-bound interpretation without running inference."""

from datetime import datetime
from collections.abc import Mapping

from digital_twin.modules.decisions.contracts import narrative_presentation_errors


def _mapping(value):
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return dict(value) if isinstance(value, Mapping) else {}


def _clock(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.timestamp() if stamp.tzinfo else None
    except (ValueError, TypeError):
        return None


def _revisions(packet):
    return {(str(row.get("datasetId")), str(row.get("revisionId")))
            for row in _mapping(packet.get("report")).get("sourceReferences", [])
            if isinstance(row, Mapping) and row.get("datasetId") and row.get("revisionId")}


class CompanyReportInsightQueryService:
    def __init__(self, subject_case_repository, ai_insight_repository):
        self.subject_case_repository = subject_case_repository
        self.ai_insight_repository = ai_insight_repository

    def query(self, account_id, symbol, financial_evidence, report_at):
        unavailable = {"state": "unavailable", "reason": "현재 보고서의 근거와 연결할 검증된 AI 해석이 없습니다. 수치로 확인되는 의미와 평가 조건을 먼저 확인하세요."}
        try:
            cases = self.subject_case_repository.latest(account_id, symbol, 1)
            case = _mapping(next(iter(cases or []), {}))
            if not case:
                return unavailable
            episodes = self.ai_insight_repository.latest_insight_episodes(account_id=account_id, symbol=symbol, limit=8)
        except Exception:  # A failed optional read cannot erase the factual report.
            return {"state": "unavailable", "reason": "저장된 해석의 조회가 완료되지 않았습니다. 기업 자료와 계산 근거는 계속 확인할 수 있습니다."}
        for raw in episodes or []:
            saved = _mapping(raw)
            if saved.get("subjectCaseId") != case.get("subjectCaseId"):
                continue
            identity = ("accountId", "symbol", "sourceAboxSnapshotId", "inferenceGenerationId")
            if (case.get("accountId") != account_id or case.get("symbol") != symbol
                    or not all(case.get(key) and saved.get(key) == case.get(key) for key in identity)
                    or not saved.get("candidateFingerprint")
                    or saved.get("candidateFingerprint") != _mapping(case.get("candidateSet")).get("fingerprint")):
                return unavailable
            if (saved.get("aiAuthored") is not True or saved.get("publicationContractPassed") is not True
                    or saved.get("contractFailureCode") or saved.get("publicationMode") == "typedb-fallback"):
                return unavailable
            created, cutoff = _clock(saved.get("createdAt")), _clock(report_at)
            if created is None or cutoff is None or created > cutoff:
                return unavailable
            insight = _mapping(saved.get("insight"))
            assessment = _mapping(insight.get("insightAssessment"))
            financial = _mapping(insight.get("financialEvidence"))
            if (not financial_evidence.get("decisionFingerprint")
                    or financial.get("decisionFingerprint") != financial_evidence.get("decisionFingerprint")
                    or not _revisions(financial_evidence) or _revisions(financial) != _revisions(financial_evidence)):
                return {"state": "basis-mismatch", "reason": "저장된 AI 해석과 현재 보고서의 재무 근거가 달라 이번 보고서의 해석으로 연결하지 않았습니다."}
            if assessment.get("publishable") is not True:
                return unavailable
            fields = {"thesis": assessment.get("dominantThesis"), "mechanism": assessment.get("causalMechanism"),
                      "meaning": assessment.get("investmentImplication"), "invalidation": assessment.get("invalidationCondition")}
            risks = [str(item) for item in assessment.get("risks", []) if isinstance(item, str)]
            if not all(fields.values()) or narrative_presentation_errors("NO_ACTION", [*fields.values(), *risks]):
                return unavailable
            evidence_ids = list(assessment.get("evidenceIds") or [])
            if not evidence_ids:
                return unavailable
            return {"state": "available", **fields, "risks": risks,
                    "episodeId": saved.get("episodeId"), "subjectCaseId": saved.get("subjectCaseId"),
                    "inferenceGenerationId": saved.get("inferenceGenerationId"),
                    "candidateFingerprint": saved.get("candidateFingerprint"),
                    "asOf": saved.get("createdAt"), "evidenceIds": evidence_ids,
                    "financialEvidence": financial,
                    "basis": "same-financial-revisions-analysis-time-market-context"}
        return unavailable
