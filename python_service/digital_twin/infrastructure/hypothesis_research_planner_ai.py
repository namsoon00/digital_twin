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
        "당신은 투자 가설의 증거 수집 계획자입니다. 입력의 TypeDB 경쟁 가설을 새로 판단하거나 투자 행동을 추천하지 마세요. "
        "새 사실, 새 가설, 새 출처, 새 근거 유형을 만들지 말고 각 가설의 allowedCollectionPolicy 안에서만 조사 작업을 제안하세요. "
        "기본 조사 작업은 제거할 수 없으며, 당신의 출력은 추가 조사 계획일 뿐 투자 판단에 직접 쓰이지 않습니다. "
        "출력은 JSON 객체 하나입니다. focusHypothesisIds, tasks, unresolvedQuestions만 포함하세요. tasks 각 항목은 "
        "hypothesisId, counterHypothesisIds, question, purpose, requiredEvidenceTypes, sourceTypes, maxAgeMinutes, decisionRelevance를 포함합니다. "
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
        "focusHypothesisIds": list(payload.get("focusHypothesisIds") or [])[:8],
        "tasks": [item for item in payload.get("tasks") or [] if isinstance(item, dict)][:3],
        "unresolvedQuestions": list(payload.get("unresolvedQuestions") or [])[:8],
    }


def hypothesis_research_planning_advisor_from_settings(settings: Dict[str, object] = None):
    settings = settings or runtime_settings()
    enabled = str(settings.get("investmentBrainHypothesisResearchPlannerAiEnabled", "1")).strip().lower()
    if enabled in {"0", "false", "off", "disabled"}:
        return LocalHypothesisResearchPlanningAdvisor()
    command = str(
        settings.get("investmentBrainHypothesisResearchPlannerAiCommand")
        or os.environ.get("INVESTMENT_BRAIN_HYPOTHESIS_RESEARCH_PLANNER_AI_COMMAND")
        or ""
    ).strip()
    try:
        timeout = int(settings.get("investmentBrainHypothesisResearchPlannerAiTimeoutSeconds") or 120)
    except (TypeError, ValueError):
        timeout = 120
    if not command:
        command = codex_command(str(settings.get("notificationAiModel") or "gpt-5.4")) or ""
    if command:
        return CommandHypothesisResearchPlanningAdvisor(command, timeout)
    return LocalHypothesisResearchPlanningAdvisor()
