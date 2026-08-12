from dataclasses import dataclass
import json
from typing import Dict, List

from .analyze_article import annotate_evidence_eligibility
from ..domain.article_quality import inspect_article_body
from ..domain.entity_resolution import resolve_target_entity
from ..domain.version import NEWS_INTELLIGENCE_VERSION


@dataclass(frozen=True)
class NewsRevalidationResult:
    scanned_count: int
    changed_count: int
    saved_count: int
    blocked_subject_count: int
    blocked_body_count: int
    alert_eligible_count: int
    reasoning_eligible_count: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "scannedCount": self.scanned_count,
            "changedCount": self.changed_count,
            "savedCount": self.saved_count,
            "blockedSubjectCount": self.blocked_subject_count,
            "blockedBodyCount": self.blocked_body_count,
            "alertEligibleCount": self.alert_eligible_count,
            "reasoningEligibleCount": self.reasoning_eligible_count,
            "notificationReplay": False,
        }


class RevalidateNewsIntelligenceService:
    def __init__(self, repository):
        self.repository = repository

    def revalidate(self, symbol: str = "", limit: int = 500) -> NewsRevalidationResult:
        items = list(self.repository.latest(symbol=symbol, kind="news", limit=max(1, int(limit or 500))) or [])
        changed: List[object] = []
        blocked_subject = 0
        blocked_body = 0
        alert_eligible = 0
        reasoning_eligible = 0
        for item in items:
            payload = dict(getattr(item, "raw_payload", {}) or {})
            before = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
            facts = dict(payload.get("articleFacts") or {}) if isinstance(payload.get("articleFacts"), dict) else {}
            body = str(payload.get("articleText") or facts.get("articleText") or facts.get("bodyPreview") or "")
            if body:
                quality = inspect_article_body(body).to_dict()
                facts.update({
                    "bodyQualityState": quality["state"],
                    "bodyQualityPassed": quality["passed"],
                    "bodyQualityReason": quality["reason"],
                    "bodyQualityIssues": quality["issues"],
                    "bodyCharCount": quality["charCount"],
                })
                payload["bodyQualityState"] = quality["state"]
                payload["bodyQualityPassed"] = quality["passed"]
                payload["articleFacts"] = facts
                if not quality["passed"]:
                    blocked_body += 1
            resolution = resolve_target_entity(
                getattr(item, "title", ""),
                body or getattr(item, "summary", ""),
                getattr(item, "symbol", ""),
                payload.get("name") or payload.get("companyName") or "",
            )
            payload["entityResolution"] = resolution.to_dict()
            payload["newsIntelligenceVersion"] = NEWS_INTELLIGENCE_VERSION
            quality_gate = dict(payload.get("qualityGate") or {}) if isinstance(payload.get("qualityGate"), dict) else {}
            quality_gate["targetSubjectConfirmed"] = resolution.target_subject_confirmed
            quality_gate["entityResolution"] = resolution.to_dict()
            if str(payload.get("relationScope") or "").lower() == "direct" and not resolution.target_subject_confirmed:
                payload["relationScope"] = "entity_mismatch"
                payload["relevanceState"] = "unrelated"
                payload["dataState"] = "insufficient"
                payload["validationState"] = "blocked"
                payload["directMention"] = False
                payload["excludedReason"] = "기사 제목에서 대상 종목이 핵심 주어로 확인되지 않음"
                quality_gate["decision"] = "exclude"
                quality_gate["reason"] = payload["excludedReason"]
                blocked_subject += 1
            payload["qualityGate"] = quality_gate
            item.raw_payload = payload
            annotate_evidence_eligibility(item)
            eligibility = item.raw_payload.get("newsEligibility") if isinstance(item.raw_payload, dict) else {}
            alert_eligible += int(bool(eligibility.get("alertEligible")))
            reasoning_eligible += int(bool(eligibility.get("reasoningEligible")))
            after = json.dumps(item.raw_payload, ensure_ascii=False, sort_keys=True, default=str)
            if before != after:
                changed.append(item)
        saved = int(self.repository.upsert_many(changed) or 0) if changed else 0
        return NewsRevalidationResult(
            len(items),
            len(changed),
            saved,
            blocked_subject,
            blocked_body,
            alert_eligible,
            reasoning_eligible,
        )
