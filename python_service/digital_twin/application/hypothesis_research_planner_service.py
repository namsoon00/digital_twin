"""Application service for bounded AI research planning."""

from typing import Dict

from ..domain.hypothesis_research_planning import (
    apply_ai_research_guidance,
    baseline_research_plan,
    research_planner_input,
)
from ..domain.investment_evidence_governance import hypothesis_research_brief_from_brain


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


class HypothesisResearchPlanningService:
    """Lets AI propose collection work without giving it investment authority."""

    def __init__(self, advisor=None, settings: Dict[str, object] = None):
        self.advisor = advisor
        self.settings = dict(settings or {})

    def enabled(self) -> bool:
        value = str(self.settings.get("investmentBrainHypothesisResearchPlannerAiEnabled", "1")).strip().lower()
        return value not in DISABLED_VALUES

    def maximum_additional_tasks(self) -> int:
        try:
            value = int(self.settings.get("investmentBrainHypothesisResearchPlannerMaxTasks") or 3)
        except (TypeError, ValueError):
            value = 3
        return max(1, min(3, value))

    def plan(
        self,
        brain: Dict[str, object],
        question: Dict[str, object] = None,
        account_id: str = "",
        symbol: str = "",
    ) -> Dict[str, object]:
        baseline = baseline_research_plan(
            brain.get("researchPlan") if isinstance(brain, dict) and isinstance(brain.get("researchPlan"), dict) else {}
        )
        brief = hypothesis_research_brief_from_brain(brain)
        if not brief.candidate_hypotheses:
            audit = {
                "status": "not-required",
                "reason": "TypeDB 현재 세대에서 조사할 경쟁 가설을 찾지 못했습니다.",
                "preservesBaselineTasks": True,
                "decisionEligibility": "research-only",
            }
            return {
                "researchPlan": {**baseline, "planningAudit": audit},
                "hypothesisResearchBrief": brief.with_planning("not-required", "typedb-hypothesis-set", audit),
            }
        if not self.enabled():
            audit = {
                "status": "planner-disabled",
                "reason": "AI 조사 계획 기능이 설정에서 비활성화됐습니다.",
                "preservesBaselineTasks": True,
                "decisionEligibility": "research-only",
            }
            return {
                "researchPlan": {**baseline, "planningAudit": audit},
                "hypothesisResearchBrief": brief.with_planning("planner-disabled", "typedb-hypothesis-set", audit),
            }
        if not self.advisor or not hasattr(self.advisor, "plan"):
            audit = {
                "status": "planner-unavailable",
                "reason": "AI 조사 계획 어댑터가 구성되지 않았습니다.",
                "preservesBaselineTasks": True,
                "decisionEligibility": "research-only",
            }
            return {
                "researchPlan": {**baseline, "planningAudit": audit},
                "hypothesisResearchBrief": brief.with_planning("planner-unavailable", "typedb-hypothesis-set", audit),
            }
        context = research_planner_input(brief, baseline, question, account_id, symbol)
        try:
            guidance = self.advisor.plan(context)
        except Exception as error:  # noqa: BLE001 - research must retain the graph-derived baseline plan.
            audit = {
                "status": "planner-failed",
                "reason": str(error)[:180],
                "preservesBaselineTasks": True,
                "decisionEligibility": "research-only",
            }
            return {
                "researchPlan": {**baseline, "planningAudit": audit},
                "hypothesisResearchBrief": brief.with_planning("planner-failed", "typedb-hypothesis-set", audit),
            }
        planned, audit = apply_ai_research_guidance(
            baseline,
            brief,
            guidance if isinstance(guidance, dict) else {},
            self.maximum_additional_tasks(),
        )
        return {
            "researchPlan": planned,
            "hypothesisResearchBrief": brief.with_planning(
                audit.get("status") or "no-valid-guidance",
                audit.get("planningSource") or "typedb-hypothesis-set",
                audit,
            ),
        }
