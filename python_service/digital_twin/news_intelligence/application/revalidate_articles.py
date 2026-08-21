from dataclasses import dataclass, field
import json
from typing import Dict, List

from .analyze_article import annotate_evidence_eligibility
from .normalize_sources import normalize_evidence_sources
from ..domain.article_quality import inspect_article_body
from ..domain.entity import target_aliases
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
    provenance_complete_count: int
    unresolved_publisher_count: int
    duplicate_publication_count: int
    independent_confirmation_count: int
    same_story_count: int
    follow_up_count: int
    content_invalid_review_count: int
    event_cluster_count: int
    changed_evidence_ids: List[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "scannedCount": self.scanned_count,
            "changedCount": self.changed_count,
            "savedCount": self.saved_count,
            "blockedSubjectCount": self.blocked_subject_count,
            "blockedBodyCount": self.blocked_body_count,
            "alertEligibleCount": self.alert_eligible_count,
            "reasoningEligibleCount": self.reasoning_eligible_count,
            "provenanceCompleteCount": self.provenance_complete_count,
            "unresolvedPublisherCount": self.unresolved_publisher_count,
            "duplicatePublicationCount": self.duplicate_publication_count,
            "independentConfirmationCount": self.independent_confirmation_count,
            "sameStoryCount": self.same_story_count,
            "followUpCount": self.follow_up_count,
            "contentInvalidReviewCount": self.content_invalid_review_count,
            "eventClusterCount": self.event_cluster_count,
            "changedEvidenceIds": list(self.changed_evidence_ids),
            "notificationReplay": False,
            "dryRun": self.dry_run,
        }


class RevalidateNewsIntelligenceService:
    def __init__(self, repository, source_registry: object = ""):
        self.repository = repository
        self.source_registry = source_registry

    def revalidate(self, symbol: str = "", limit: int = 500, dry_run: bool = False) -> NewsRevalidationResult:
        items = list(self.repository.latest(symbol=symbol, kind="news", limit=max(1, int(limit or 500))) or [])
        before_payloads = {
            str(getattr(item, "evidence_id", "") or id(item)): json.dumps(
                getattr(item, "raw_payload", {}) or {},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            for item in items
        }
        items = normalize_evidence_sources(items, self.source_registry)
        changed: List[object] = []
        blocked_subject = 0
        blocked_body = 0
        alert_eligible = 0
        reasoning_eligible = 0
        provenance_complete = 0
        unresolved_publisher = 0
        duplicate_publication = 0
        independent_confirmation = 0
        same_story = 0
        follow_up = 0
        content_invalid_review = 0
        event_clusters = set()
        for item in items:
            payload = dict(getattr(item, "raw_payload", {}) or {})
            before = before_payloads.get(str(getattr(item, "evidence_id", "") or id(item)), "")
            facts = dict(payload.get("articleFacts") or {}) if isinstance(payload.get("articleFacts"), dict) else {}
            body = str(payload.get("articleText") or facts.get("articleText") or facts.get("bodyPreview") or "")
            if body:
                quality = inspect_article_body(
                    body,
                    target_terms=target_aliases(
                        getattr(item, "symbol", ""),
                        payload.get("name") or payload.get("companyName") or "",
                    ),
                ).to_dict()
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
            payload["evidenceQualityAuthority"] = "revalidation-v1"
            item.raw_payload = payload
            annotate_evidence_eligibility(item, self.source_registry)
            eligibility = item.raw_payload.get("newsEligibility") if isinstance(item.raw_payload, dict) else {}
            alert_eligible += int(bool(eligibility.get("alertEligible")))
            reasoning_eligible += int(bool(eligibility.get("reasoningEligible")))
            provenance = item.raw_payload.get("sourceProvenance") if isinstance(item.raw_payload.get("sourceProvenance"), dict) else {}
            provenance_complete += int(bool(provenance.get("provenanceComplete")))
            original = provenance.get("originalPublisher") if isinstance(provenance.get("originalPublisher"), dict) else {}
            unresolved_publisher += int(not original.get("publisherId") or original.get("publisherId") == "unknown")
            relationship = str(provenance.get("evidenceRelationship") or "")
            duplicate_publication += int(relationship in {"exact-duplicate", "syndicated-copy"})
            independent_confirmation += int(relationship == "independent-confirmation")
            same_story += int(relationship == "same-story")
            follow_up += int(relationship == "follow-up")
            content_invalid_review += int(str(eligibility.get("reviewState") or "") == "content-invalid")
            cluster_id = str(payload.get("storyClusterId") or "")
            if cluster_id:
                event_clusters.add(cluster_id)
            after = json.dumps(item.raw_payload, ensure_ascii=False, sort_keys=True, default=str)
            if before != after:
                changed.append(item)
        saved = int(self.repository.upsert_many(changed) or 0) if changed and not dry_run else 0
        return NewsRevalidationResult(
            len(items),
            len(changed),
            saved,
            blocked_subject,
            blocked_body,
            alert_eligible,
            reasoning_eligible,
            provenance_complete,
            unresolved_publisher,
            duplicate_publication,
            independent_confirmation,
            same_story,
            follow_up,
            content_invalid_review,
            len(event_clusters),
            [str(getattr(item, "evidence_id", "") or "") for item in changed[:20]],
            bool(dry_run),
        )
