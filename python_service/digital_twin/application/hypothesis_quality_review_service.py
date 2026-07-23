"""Application service for governed hypothesis quality reviews."""

from typing import Dict, Mapping

from ..domain.hypothesis_quality_review import quality_review_workspace
from ..domain.investment_brain import LearningProposal


class HypothesisQualityReviewService:
    """Turns outcome/lifecycle review facts into human-review proposals only."""

    def __init__(self, decision_episode_store=None):
        self.decision_episode_store = decision_episode_store

    def assess(self, workspace: Mapping[str, object]) -> Dict[str, object]:
        source = dict(workspace or {}) if isinstance(workspace, Mapping) else {}
        return quality_review_workspace(source.get("items") or [])

    def propose(self, workspace: Mapping[str, object], reviewed_by: str = "") -> Dict[str, object]:
        review = self.assess(workspace)
        required = list(review.get("reviewRequired") or [])
        saved = []
        unavailable_reason = ""
        if not self.decision_episode_store or not hasattr(self.decision_episode_store, "save_learning_proposal"):
            unavailable_reason = "학습 제안 저장소가 구성되지 않았습니다."
        else:
            for item in required:
                proposal = LearningProposal(
                    proposal_id=str(item.get("reviewId") or ""),
                    title=(str(item.get("symbol") or "종목") + " 가설 품질 검토: " + str(item.get("qualityStateLabel") or "검토")),
                    reason=str(item.get("reason") or ""),
                    source_episode_ids=list(item.get("sourceEpisodeIds") or []),
                    affected_rule_ids=list(item.get("sourceRuleIds") or []),
                    proposed_change={
                        "changeType": item.get("changeType"),
                        "lifecycleKey": item.get("lifecycleKey"),
                        "scope": item.get("scope"),
                        "missingObservationDomains": item.get("missingObservationDomains") or [],
                        "freshnessProblemDomains": item.get("freshnessProblemDomains") or [],
                        "nextCheck": item.get("nextCheck"),
                        "reviewedBy": str(reviewed_by or ""),
                        "automaticDeployment": False,
                        "requiredValidation": [
                            "outcome-contract-review",
                            "historical-replay",
                            "TypeDB-rule-preview",
                            "human-approval",
                        ],
                    },
                )
                saved.append(self.decision_episode_store.save_learning_proposal(proposal).to_dict())
        return {
            "status": "proposed" if saved else ("unavailable" if unavailable_reason else "no-review-required"),
            "governance": "human-review-required-no-automatic-rulebox-deployment",
            "review": review,
            "proposalCount": len(saved),
            "proposals": saved,
            "reason": unavailable_reason,
        }
