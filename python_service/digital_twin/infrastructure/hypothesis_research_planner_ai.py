"""AI adapter for planning evidence collection around existing hypotheses."""

import json
import os
import re
import subprocess
from typing import Dict

from .model_reviewer import codex_command
from .settings import ROOT_DIR, runtime_settings


class HypothesisResearchPlanningAdvisor:
    def plan(self, context: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError


class LocalHypothesisResearchPlanningAdvisor(HypothesisResearchPlanningAdvisor):
    def plan(self, context: Dict[str, object]) -> Dict[str, object]:
        return {}


class CommandHypothesisResearchPlanningAdvisor(HypothesisResearchPlanningAdvisor):
    def __init__(self, command: str, timeout_seconds: int = 120):
        self.command = str(command or "").strip()
        self.timeout_seconds = max(30, int(timeout_seconds or 120))

    def plan(self, context: Dict[str, object]) -> Dict[str, object]:
        if not self.command:
            return {}
        completed = subprocess.run(
            self.command,
            input=hypothesis_research_planning_prompt(context),
            text=True,
            shell=True,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout_seconds,
            env=dict(os.environ),
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "hypothesis research planner failed").strip())
        return planning_payload_from_text(completed.stdout)


def hypothesis_research_planning_prompt(context: Dict[str, object]) -> str:
    return (
        "당신은 투자 가설의 AI 조사 분석가입니다. 투자 행동을 선택하거나 아직 수집하지 않은 사실을 사실처럼 쓰지 마세요. "
        "기존 TypeDB 가설을 검증할 수 있고, 입력의 dataCoverageMap에 없는 정보가 결론을 바꿀 수 있다면 새로운 조사 질문도 제안할 수 있습니다. "
        "새 조사 질문은 hypothesisId를 비우고 discoveryKind, decisionChangingRationale, expectedDecisionImpact를 반드시 채웁니다. "
        "sourceTypes와 requiredEvidenceTypes는 dataCoverageMap의 승인 목록만 사용하고, queryTerms에는 기업명과 결합할 구체적인 검색어만 넣습니다. "
        "기본 조사 작업은 제거할 수 없으며 출력은 조사 계획일 뿐 투자 판단이나 새 규칙이 아닙니다. "
        "출력은 JSON 객체 하나입니다. initialAssessment, decisionChangingGaps, focusHypothesisIds, tasks, unresolvedQuestions를 포함하세요. tasks 각 항목은 "
        "hypothesisId, counterHypothesisIds, discoveryKind, question, purpose, decisionChangingRationale, expectedDecisionImpact, "
        "requiredEvidenceTypes, sourceTypes, queryTerms, maxAgeMinutes, decisionRelevance를 포함합니다. "
        "유효한 추가 작업이 없으면 빈 배열을 반환하세요.\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )


def planning_payload_from_text(text: str) -> Dict[str, object]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "initialAssessment": str(payload.get("initialAssessment") or "")[:500],
        "decisionChangingGaps": list(payload.get("decisionChangingGaps") or [])[:8],
        "focusHypothesisIds": list(payload.get("focusHypothesisIds") or [])[:8],
        "tasks": [item for item in payload.get("tasks") or [] if isinstance(item, dict)][:3],
        "unresolvedQuestions": list(payload.get("unresolvedQuestions") or [])[:8],
    }


def hypothesis_research_planning_advisor_from_settings(settings: Dict[str, object] = None):
    settings = settings or runtime_settings()
    enabled = str(settings.get("investmentBrainHypothesisResearchPlannerAiEnabled", "1")).strip().lower()
    if enabled in {"0", "false", "off", "disabled"}:
        return LocalHypothesisResearchPlanningAdvisor()
    try:
        timeout = int(settings.get("investmentBrainHypothesisResearchPlannerAiTimeoutSeconds") or 120)
    except (TypeError, ValueError):
        timeout = 120
    command = codex_command() or ""
    if command:
        return CommandHypothesisResearchPlanningAdvisor(command, timeout)
    return LocalHypothesisResearchPlanningAdvisor()
